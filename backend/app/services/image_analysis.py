from __future__ import annotations

import hashlib
import mimetypes
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from PIL import Image

from ..config import settings
from .generator_id import identify_generator
from .metadata import extract_image_metadata
from .model_registry import get_image_models
from .net_utils import parse_http_url, resolve_public_host
from .tile_analysis import analyze_tiles

IMAGE_LIMITATIONS = [
    "AI-image classifiers are probabilistic signals, not proof. They are trained on specific generators and can fail on new models, compression, screenshots, crops, and edits.",
    "The default Smaug detector is newer and covers Nano Banana/Imagen-era generators, but it is still a classifier, not provenance. A high score is a reason to investigate, not an accusation.",
    "This demo does not verify C2PA signatures cryptographically; it only detects whether a manifest appears to be present.",
    "Absence of EXIF/C2PA metadata is common on social platforms and is not evidence that an image is AI-generated.",
    "A human-made illustration or a heavily edited photo can be misclassified as AI. A real photo can be misclassified as human.",
    "For journalism, corroborate with provenance, reverse image search, source contact, and human review.",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _label_for(ai_probability: float | None, metadata: dict) -> str:
    if ai_probability is None:
        return "Uncertain (metadata only)"
    if ai_probability >= 0.80:
        return "Likely AI-generated"
    if ai_probability >= 0.60:
        return "Possibly AI-generated"
    if ai_probability <= 0.20:
        return "Likely real / not AI-generated"
    if ai_probability <= 0.40:
        return "Possibly real"
    return "Uncertain"


def _confidence(ai_probability: float | None, model_results: list[dict]) -> float:
    if ai_probability is None:
        return 0.15
    base = min(0.95, abs(ai_probability - 0.5) * 2.0)
    if len(model_results) > 1:
        probabilities = [r["ai_probability"] for r in model_results]
        agreement = 1.0 - min(1.0, max(probabilities) - min(probabilities))
        base *= 0.75 + 0.25 * agreement
    return round(max(0.05, min(0.95, base)), 3)


def analyze_image(
    path: Path,
    filename: str | None = None,
    source_url: str | None = None,
    tiles: bool = False,
) -> dict:
    filename = filename or path.name
    metadata = extract_image_metadata(path)
    # C2PA lives in a container box that PIL does not surface, so the generator
    # check gets a slice of the raw file as well as the parsed metadata.
    try:
        with path.open("rb") as handle:
            raw_head = handle.read(2_000_000)
    except Exception:
        raw_head = b""
    generator = identify_generator(metadata, raw_head)
    try:
        with Image.open(path) as image:
            image.load()
            rgb = image.convert("RGB")
            width, height = rgb.size
    except Exception as exc:
        return {
            "kind": "image",
            "error": f"Could not open image: {exc}",
            "filename": filename,
            "source_url": source_url,
        }

    models, errors = get_image_models()
    model_results: list[dict] = []
    model_errors = dict(errors)
    for model in models:
        try:
            model_results.append(model.predict(rgb))
        except Exception as exc:
            model_errors[model.model_id] = str(exc)

    ai_probability: float | None = None
    if model_results:
        # Simple average. If you add many models, calibrate and weight them.
        ai_probability = sum(r["ai_probability"] for r in model_results) / len(
            model_results
        )
        ai_probability = round(ai_probability, 6)

    # Opt-in: one inference per tile, so this multiplies the cost of a request.
    tile_report = (
        analyze_tiles(rgb, models, whole_score=ai_probability)
        if tiles
        else {"available": False, "reason": "Not requested. Pass tiles=true to scan regions."}
    )

    signals: list[dict[str, Any]] = []
    for result in model_results:
        signals.append(
            {
                "name": f"image_model:{result['model_id'].split('/')[-1]}",
                "value": result["ai_probability"],
                "direction": "AI-like" if result["ai_probability"] >= 0.5 else "real-like",
                "weight": "model",
                "explanation": (
                    f"Classifier output: {result['ai_probability']:.1%} AI-like "
                    f"({result['device']}, {result['latency_ms']} ms)."
                ),
            }
        )

    hints = metadata.get("ai_software_hints", [])
    if hints:
        signals.append(
            {
                "name": "metadata:ai_software_hint",
                "value": hints,
                "direction": "AI-like",
                "weight": "metadata",
                "explanation": "Generator software names were found in metadata. Metadata is editable and can be stripped.",
            }
        )
    if metadata.get("c2pa_present"):
        signals.append(
            {
                "name": "metadata:c2pa_present",
                "value": True,
                "direction": "provenance",
                "weight": "metadata",
                "explanation": "A C2PA/JUMBF manifest appears to be present. Its signature was not verified in this demo.",
            }
        )
    if "exif_missing" in metadata.get("metadata_flags", []):
        signals.append(
            {
                "name": "metadata:exif_missing",
                "value": True,
                "direction": "neutral",
                "weight": "weak",
                "explanation": "No EXIF block was found. Social platforms often remove metadata, so this is not evidence of AI.",
            }
        )

    if generator.get("generator"):
        signals.append(
            {
                "name": "provenance:generator_metadata",
                "value": generator["generator"],
                "direction": "AI-like",
                "weight": "metadata",
                "explanation": (
                    f"{generator['evidence']}. Confidence: {generator['confidence']}. "
                    "Read from the file, not inferred from pixels."
                ),
            }
        )
    if tile_report.get("available") and tile_report["pattern"] == "localized":
        signals.append(
            {
                "name": "tiles:localized_ai",
                "value": tile_report["ai_like_share"],
                "direction": "AI-like",
                "weight": "model",
                "explanation": tile_report["summary"],
            }
        )

    label = _label_for(ai_probability, metadata)
    confidence = _confidence(ai_probability, model_results)

    if model_results:
        model_names = ", ".join(
            result["model_id"].split("/")[-1] for result in model_results
        )
        summary = (
            f"The image classifier ({model_names}) estimates "
            f"{ai_probability:.0%} AI-like content. Result: {label}. "
            "This is a model signal, not proof. "
        )
    else:
        summary = (
            "No image classifier could be loaded, so this result is metadata-only. "
            "Install the backend models or check the server logs. "
        )
    if generator.get("generator"):
        named = generator.get("named_model")
        summary += (
            f"Metadata names the generator as {generator['generator']}"
            + (f" ({named})" if named else "")
            + f" — {generator['confidence']} confidence, from {generator['evidence']}. "
        )
    if tile_report.get("available"):
        summary += tile_report["summary"] + " "
    if hints:
        summary += f"Metadata contains generator hints: {', '.join(hints)}. "
    if metadata.get("c2pa_present"):
        summary += "A C2PA manifest was detected but not cryptographically verified. "
    if metadata.get("metadata_flags"):
        summary += f"Metadata flags: {', '.join(metadata['metadata_flags'])}."

    return {
        "kind": "image",
        "filename": filename,
        "source_url": source_url,
        "sha256": _sha256(path),
        "file_size_bytes": path.stat().st_size,
        "dimensions": {"width": width, "height": height},
        "label": label,
        "ai_probability": ai_probability,
        "confidence": confidence,
        "summary": summary.strip(),
        "models": model_results,
        "model_errors": model_errors,
        "signals": signals,
        "generator": generator,
        "tiles": tile_report,
        "metadata": metadata,
        "limitations": IMAGE_LIMITATIONS,
        "recommended_action": (
            "Treat the image as unverified until provenance is checked. "
            "Run a reverse image search, contact the original source, and look for "
            "camera/provenance records. Do not publish an accusation based on a classifier score."
        ),
    }


async def fetch_image_from_url(url: str) -> tuple[Path, str]:
    """Download a public image with basic SSRF protection.

    The backend fetches the image, not the user's browser. Private/loopback
    targets are blocked. Redirects are followed manually and validated.
    """
    current = url
    headers = {
        "User-Agent": "TAYO/0.1 (local fact-check demo)",
        "Accept": "image/*",
    }
    max_bytes = settings.max_image_url_mb * 1024 * 1024

    async with httpx.AsyncClient(
        timeout=settings.request_timeout, follow_redirects=False
    ) as client:
        for _ in range(5):
            parsed = parse_http_url(current)
            resolve_public_host(parsed.hostname, parsed.port or 443)
            async with client.stream("GET", current, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Image redirect had no Location header.")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0]
                if not content_type.startswith("image/"):
                    raise ValueError(
                        f"URL did not return an image (content-type: {content_type or 'unknown'})."
                    )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ValueError(
                            f"Image is larger than {settings.max_image_url_mb} MB."
                        )
                suffix = mimetypes.guess_extension(content_type) or ".img"
                temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                temp.write(bytes(data))
                temp.close()
                return Path(temp.name), current

    raise ValueError("Too many redirects while fetching the image.")


async def analyze_image_url(url: str, tiles: bool = False) -> dict:
    path, final_url = await fetch_image_from_url(url)
    try:
        return analyze_image(
            path,
            filename=final_url.rsplit("/", 1)[-1],
            source_url=final_url,
            tiles=tiles,
        )
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
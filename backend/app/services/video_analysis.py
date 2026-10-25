from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from PIL import Image

from ..config import settings
from .metadata import extract_frames, extract_video_metadata
from .model_registry import get_image_models

VIDEO_LIMITATIONS = [
    "This is frame sampling, not a dedicated video deepfake detector. It can miss temporal artifacts, lip-sync errors, and audio manipulations.",
    "Only a handful of frames are analyzed. A short manipulated segment between samples can be missed.",
    "Classifiers trained on still images may not generalize to video compression, motion blur, and frame interpolation.",
    "Metadata is easy to edit or strip. A missing creation record is not proof of manipulation.",
    "Treat the result as a triage signal and escalate to a human forensic analyst for high-stakes stories.",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _label(probability: float | None) -> str:
    if probability is None:
        return "Uncertain (metadata only)"
    if probability >= 0.80:
        return "Likely AI-generated / synthetic"
    if probability >= 0.60:
        return "Possibly synthetic"
    if probability <= 0.20:
        return "Likely authentic footage"
    if probability <= 0.40:
        return "Possibly authentic"
    return "Uncertain"


def analyze_video(path: Path, filename: str | None = None) -> dict:
    filename = filename or path.name
    metadata = extract_video_metadata(path)
    frames, frame_error = extract_frames(path, count=settings.video_frames)

    models, errors = get_image_models()
    model_errors = dict(errors)
    frame_results: list[dict] = []
    probabilities: list[float] = []

    for frame in frames:
        try:
            with Image.open(frame["path"]) as image:
                rgb = image.convert("RGB")
                per_model = []
                for model in models:
                    try:
                        result = model.predict(rgb)
                        per_model.append(result)
                    except Exception as exc:
                        model_errors[model.model_id] = str(exc)
                if per_model:
                    probability = sum(r["ai_probability"] for r in per_model) / len(
                        per_model
                    )
                    probabilities.append(probability)
                    frame_results.append(
                        {
                            "index": frame["index"],
                            "timestamp_seconds": frame["timestamp_seconds"],
                            "ai_probability": round(probability, 6),
                            "models": per_model,
                        }
                    )
        except Exception as exc:
            model_errors[f"frame_{frame['index']}"] = str(exc)

    ai_probability = (
        round(sum(probabilities) / len(probabilities), 6) if probabilities else None
    )
    max_probability = round(max(probabilities), 6) if probabilities else None
    frames_above = sum(1 for p in probabilities if p >= 0.8)
    label = _label(ai_probability)

    signals = []
    if ai_probability is not None:
        signals.append(
            {
                "name": "frame_classifier_mean",
                "value": ai_probability,
                "direction": "synthetic" if ai_probability >= 0.5 else "authentic",
                "weight": "model",
                "explanation": f"Mean AI-like probability across {len(probabilities)} sampled frames.",
            }
        )
        signals.append(
            {
                "name": "frame_classifier_max",
                "value": max_probability,
                "direction": "synthetic" if (max_probability or 0) >= 0.5 else "authentic",
                "weight": "model",
                "explanation": f"Highest AI-like probability in any sampled frame; {frames_above} frame(s) above 80%.",
            }
        )

    tags = metadata.get("tags") if isinstance(metadata.get("tags"), dict) else {}
    software_hints = [
        key
        for key in ("encoder", "software", "creation_time", "com.apple.quicktime.software")
        if key in tags
    ]
    if software_hints:
        signals.append(
            {
                "name": "container_metadata",
                "value": software_hints,
                "direction": "neutral",
                "weight": "metadata",
                "explanation": "Container tags are present. They can be edited and do not prove authenticity.",
            }
        )

    if ai_probability is not None:
        summary = (
            f"Frame sampling suggests a mean {ai_probability:.0%} AI-like score "
            f"({frames_above}/{len(probabilities)} frames above 80%). Result: {label}. "
            "This is a weak signal, not proof of a deepfake."
        )
    else:
        summary = (
            "No usable frame-level classifier output was produced. "
            f"Video metadata was extracted, but the result is inconclusive. {frame_error or ''}"
        ).strip()

    device = models[0].device if models else "n/a"
    return {
        "kind": "video",
        "filename": filename,
        "sha256": _sha256(path),
        "file_size_bytes": path.stat().st_size,
        "duration_seconds": metadata.get("duration_seconds"),
        "label": label,
        "ai_probability": ai_probability,
        "max_frame_probability": max_probability,
        "confidence": round(
            min(0.85, abs((ai_probability or 0.5) - 0.5) * 2.0 * 0.8), 3
        ),
        "frames_analyzed": len(probabilities),
        "frames_requested": settings.video_frames,
        "frames_above_80": frames_above,
        "frame_results": frame_results,
        "metadata": metadata,
        "model_errors": model_errors,
        "signals": signals,
        "frame_error": frame_error,
        "device": device,
        "limitations": VIDEO_LIMITATIONS,
        "summary": summary,
        "recommended_action": (
            "Do not publish a deepfake accusation from this score alone. "
            "Inspect the original file, request the source recording, check audio/lip-sync, "
            "and use a dedicated video-forensics workflow for high-stakes claims."
        ),
    }
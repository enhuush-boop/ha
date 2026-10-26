"""Name the tool that produced an image, from what it wrote into the file.

This is deliberately not a classifier. No published model reliably tells
Midjourney from DALL-E from Stable Diffusion by pixels alone, and the numbers
that do exist collapse on generators the model never saw and on recompressed
social-media copies. What *is* reliable is what the generator itself recorded:
Stable Diffusion front-ends write their full parameter block into a PNG text
chunk, C2PA carries a signed claim_generator, and IPTC has a standard field for
synthetic media.

So a result here is evidence, not a guess, and its absence means nothing:
every platform strips metadata on upload.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Order matters: the first rule that matches a field wins, so the more specific
# front-end (ComfyUI, A1111) is checked before the bare engine name.
_SOFTWARE_RULES: tuple[tuple[str, str], ...] = (
    ("comfyui", "ComfyUI (Stable Diffusion)"),
    ("automatic1111", "AUTOMATIC1111 (Stable Diffusion)"),
    ("stable diffusion web", "Stable Diffusion WebUI"),
    ("invokeai", "InvokeAI (Stable Diffusion)"),
    ("novelai", "NovelAI"),
    ("midjourney", "Midjourney"),
    ("dall-e", "DALL·E (OpenAI)"),
    ("dalle", "DALL·E (OpenAI)"),
    ("openai", "OpenAI"),
    ("firefly", "Adobe Firefly"),
    ("adobe photoshop", "Adobe Photoshop"),
    ("black forest labs", "FLUX (Black Forest Labs)"),
    ("flux", "FLUX (Black Forest Labs)"),
    ("imagen", "Imagen (Google)"),
    ("gemini", "Gemini / Nano Banana (Google)"),
    ("nano banana", "Gemini / Nano Banana (Google)"),
    ("grok", "Grok (xAI)"),
    ("leonardo", "Leonardo.ai"),
    ("ideogram", "Ideogram"),
    ("recraft", "Recraft"),
    ("stable diffusion", "Stable Diffusion"),
    ("sdxl", "Stable Diffusion XL"),
)

# IPTC's standard vocabulary for synthetic media. A generator that sets this is
# declaring itself, so it is strong evidence even without a vendor name.
_SYNTHETIC_SOURCE_TYPES = (
    "trainedalgorithmicmedia",
    "compositewithtrainedalgorithmicmedia",
    "algorithmicmedia",
)

_CLAIM_GENERATOR_RE = re.compile(
    rb"claim_generator(?:_info)?[\"']?\s*[:\x00-\x20]{0,4}([ -~]{3,120})"
)


def _match_software(value: str) -> str | None:
    lowered = value.lower()
    for needle, label in _SOFTWARE_RULES:
        if needle in lowered:
            return label
    return None


def _parse_a1111(block: str) -> dict[str, Any]:
    """Pull the key/value tail out of a Stable Diffusion WebUI parameter block.

    The format is a free-text prompt, an optional negative prompt, then a final
    comma-separated line of `Key: value` pairs.
    """
    details: dict[str, Any] = {}
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        return details

    tail = lines[-1]
    if ":" in tail and ("Steps:" in tail or "Sampler:" in tail or "Seed:" in tail):
        for part in tail.split(","):
            if ":" not in part:
                continue
            key, _, value = part.partition(":")
            key = key.strip().lower().replace(" ", "_")
            if key:
                details[key] = value.strip()
        prompt_lines = lines[:-1]
    else:
        prompt_lines = lines

    negative = next(
        (line for line in prompt_lines if line.lower().startswith("negative prompt:")),
        None,
    )
    if negative:
        details["negative_prompt"] = negative.split(":", 1)[1].strip()[:400]
        prompt_lines = [line for line in prompt_lines if line is not negative]
    if prompt_lines:
        details["prompt"] = " ".join(prompt_lines)[:400]
    return details


def _parse_comfy(raw: str) -> dict[str, Any]:
    """Read checkpoint and sampler names out of a ComfyUI workflow graph."""
    details: dict[str, Any] = {}
    try:
        graph = json.loads(raw)
    except Exception:
        return details
    nodes = graph.get("nodes", graph) if isinstance(graph, dict) else {}
    if not isinstance(nodes, (dict, list)):
        return details

    checkpoints: list[str] = []
    node_types: list[str] = []
    entries = nodes.values() if isinstance(nodes, dict) else nodes
    for node in entries:
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type") or node.get("type")
        if class_type:
            node_types.append(str(class_type))
        inputs = node.get("inputs") or node.get("widgets_values") or {}
        if isinstance(inputs, dict):
            for key, value in inputs.items():
                if "ckpt" in str(key).lower() or "model_name" in str(key).lower():
                    if isinstance(value, str):
                        checkpoints.append(value)
    if checkpoints:
        details["checkpoints"] = sorted(set(checkpoints))[:5]
    if node_types:
        details["node_count"] = len(node_types)
    return details


def identify_generator(meta: dict, raw_head: bytes = b"") -> dict:
    """Decide which tool made an image from metadata already extracted.

    `meta` is the dict built by extract_image_metadata; `raw_head` is a prefix of
    the file used only to look inside a C2PA manifest, which does not survive
    PIL's parsing.
    """
    info = meta.get("info") or {}
    exif = meta.get("exif") or {}
    signals: list[dict[str, Any]] = []
    details: dict[str, Any] = {}

    # 1. Stable Diffusion front-ends: the whole recipe, written in plain text.
    parameters = info.get("parameters")
    if isinstance(parameters, str) and parameters.strip():
        parsed = _parse_a1111(parameters)
        details.update(parsed)
        named = parsed.get("model") or parsed.get("model_hash")
        signals.append(
            {
                "generator": _match_software(parameters) or "Stable Diffusion (unspecified front-end)",
                "confidence": "high",
                "evidence": "PNG 'parameters' text chunk written by a Stable Diffusion WebUI",
                "named_model": named,
            }
        )

    # 2. ComfyUI stores the node graph itself.
    for key in ("workflow", "prompt"):
        value = info.get(key)
        if isinstance(value, str) and value.strip().startswith("{"):
            parsed = _parse_comfy(value)
            if parsed:
                details.update(parsed)
            signals.append(
                {
                    "generator": "ComfyUI (Stable Diffusion)",
                    "confidence": "high",
                    "evidence": f"PNG '{key}' chunk containing a ComfyUI node graph",
                    "named_model": (parsed.get("checkpoints") or [None])[0],
                }
            )
            break

    # 3. C2PA: the only signed claim in the list, though this demo does not
    #    verify the signature, so a forged manifest would still show up here.
    if meta.get("c2pa_present") and raw_head:
        match = _CLAIM_GENERATOR_RE.search(raw_head)
        claim = match.group(1).decode("ascii", "ignore").strip(" \"',\x00") if match else ""
        signals.append(
            {
                "generator": (_match_software(claim) or claim or "unnamed") if claim else "unnamed",
                "confidence": "medium",
                "evidence": (
                    "C2PA manifest claim_generator (signature NOT verified in this demo)"
                ),
                "named_model": claim or None,
            }
        )

    # 4. IPTC digital source type: a self-declaration that this is synthetic.
    haystack = " ".join(
        str(value) for value in list(info.values()) + list(exif.values())
    ).lower()
    if any(term in haystack for term in _SYNTHETIC_SOURCE_TYPES):
        signals.append(
            {
                "generator": _match_software(haystack) or "unnamed (IPTC declaration)",
                "confidence": "high",
                "evidence": "IPTC digitalSourceType declares trained-algorithmic media",
                "named_model": None,
            }
        )

    # 5. Plain Software / XMP tags. Weakest: trivially set by hand, and an
    #    editor's name here only means the file passed through that editor.
    #    JPEG keeps these in EXIF while PNG keeps them in a tEXt chunk, so both
    #    places have to be checked.
    for field in ("Software", "ProcessingSoftware", "HostComputer", "Artist", "Comment"):
        for container, origin in ((exif, "EXIF"), (info, "PNG text chunk")):
            value = container.get(field)
            label = _match_software(str(value)) if value else None
            if label:
                signals.append(
                    {
                        "generator": label,
                        "confidence": "low",
                        "evidence": f"{origin} {field} = {str(value)[:80]}",
                        "named_model": None,
                    }
                )
                break
        else:
            continue
        break

    if not signals:
        return {
            "generator": None,
            "confidence": "none",
            "evidence": None,
            "details": {},
            "signals": [],
            "note": (
                "No generator metadata survived in this file. That is the normal "
                "case for anything downloaded from a social platform, and it is "
                "not evidence either way. Which model made an image cannot be "
                "read from its pixels by any detector in this build."
            ),
        }

    rank = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda item: rank.get(item["confidence"], 3))
    best = signals[0]
    return {
        "generator": best["generator"],
        "confidence": best["confidence"],
        "evidence": best["evidence"],
        "named_model": best.get("named_model"),
        "details": details,
        "signals": signals,
        "note": (
            "Read from metadata the file carries, not inferred from pixels. "
            "Metadata can be edited or forged, so treat it as a lead to verify."
        ),
    }

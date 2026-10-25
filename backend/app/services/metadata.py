from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

try:
    import imageio_ffmpeg
except Exception:  # pragma: no cover - optional
    imageio_ffmpeg = None


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()[:1024]
    if isinstance(value, (tuple, list)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    try:
        return float(value)
    except Exception:
        return str(value)[:1024]


def extract_image_metadata(path: Path) -> dict:
    meta: dict[str, Any] = {
        "format": None,
        "mode": None,
        "width": None,
        "height": None,
        "exif_present": False,
        "exif": {},
        "info": {},
        "c2pa_present": False,
        "c2pa_note": "Presence only. Cryptographic verification is not performed in this demo.",
        "ai_software_hints": [],
        "metadata_flags": [],
    }

    try:
        with Image.open(path) as image:
            meta["format"] = image.format
            meta["mode"] = image.mode
            meta["width"], meta["height"] = image.size
            meta["info"] = {
                str(k): _safe(v)
                for k, v in image.info.items()
                if k not in {"icc_profile", "exif"}
            }
            exif = image.getexif()
            if exif:
                meta["exif_present"] = True
                for tag_id, value in exif.items():
                    tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                    meta["exif"][str(tag)] = _safe(value)
    except Exception as exc:
        meta["error"] = f"Could not read image metadata: {exc}"

    # C2PA / JUMBF manifests can live outside the EXIF block.
    try:
        raw = path.read_bytes()
        lower = raw[: min(len(raw), 25_000_000)].lower()
        meta["c2pa_present"] = any(
            marker in lower
            for marker in (b"c2pa", b"jumb", b"content credentials", b"c2pa.claim")
        )
        if meta["c2pa_present"]:
            meta["metadata_flags"].append("c2pa_manifest_detected")

        hint_patterns = {
            "midjourney": b"midjourney",
            "dall-e": b"dall-e",
            "dalle": b"dalle",
            "stable diffusion": b"stable diffusion",
            "sdxl": b"sdxl",
            "adobe firefly": b"firefly",
            "comfyui": b"comfyui",
            "automatic1111": b"automatic1111",
            "invokeai": b"invokeai",
            "flux": b"black forest labs",
        }
        for name, pattern in hint_patterns.items():
            if pattern in lower:
                meta["ai_software_hints"].append(name)

        if meta["ai_software_hints"]:
            meta["metadata_flags"].append("ai_software_hint")
    except Exception:
        pass

    exif = meta.get("exif", {})
    if not meta["exif_present"]:
        meta["metadata_flags"].append("exif_missing")
    elif not exif.get("Make") and not exif.get("Model"):
        meta["metadata_flags"].append("camera_make_model_missing")

    software = str(exif.get("Software", "")).lower()
    if software:
        meta["software"] = exif.get("Software")
        if any(
            token in software
            for token in ("midjourney", "dall", "stable", "firefly", "comfy")
        ):
            if "ai_software_hint" not in meta["metadata_flags"]:
                meta["metadata_flags"].append("ai_software_hint")

    return meta


def find_ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    if imageio_ffmpeg is not None:
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


def find_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def _parse_ffmpeg_stderr(stderr: str) -> dict:
    """Fallback metadata parser when ffprobe is not installed."""
    result: dict[str, Any] = {"source": "ffmpeg -i fallback"}
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if duration:
        hours, minutes, seconds = duration.groups()
        result["duration_seconds"] = (
            int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        )
    video = re.search(
        r"Stream #\d+:\d+.*?Video:\s*([^,\s]+).*?(\d{2,5})x(\d{2,5})", stderr
    )
    if video:
        result["video_codec"] = video.group(1)
        result["width"] = int(video.group(2))
        result["height"] = int(video.group(3))
    audio = re.search(r"Stream #\d+:\d+.*?Audio:\s*([^,\s]+)", stderr)
    if audio:
        result["audio_codec"] = audio.group(1)
    creation = re.search(r"creation_time\s*:\s*([^\s]+)", stderr)
    if creation:
        result["creation_time"] = creation.group(1)
    encoder = re.search(r"encoder\s*:\s*([^\n]+)", stderr)
    if encoder:
        result["encoder"] = encoder.group(1).strip()
    return result


def extract_video_metadata(path: Path) -> dict:
    ffprobe = find_ffprobe()
    ffmpeg = find_ffmpeg()

    if ffprobe:
        try:
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                fmt = data.get("format", {})
                streams = data.get("streams", [])
                video_stream = next(
                    (s for s in streams if s.get("codec_type") == "video"), {}
                )
                audio_stream = next(
                    (s for s in streams if s.get("codec_type") == "audio"), {}
                )
                return {
                    "source": "ffprobe",
                    "duration_seconds": _safe(fmt.get("duration")),
                    "size_bytes": _safe(fmt.get("size")),
                    "bit_rate": _safe(fmt.get("bit_rate")),
                    "format_name": fmt.get("format_name"),
                    "tags": _safe(fmt.get("tags", {})),
                    "video": {
                        "codec": video_stream.get("codec_name"),
                        "width": video_stream.get("width"),
                        "height": video_stream.get("height"),
                        "fps": video_stream.get("avg_frame_rate"),
                        "nb_frames": video_stream.get("nb_frames"),
                        "pix_fmt": video_stream.get("pix_fmt"),
                    },
                    "audio": {
                        "codec": audio_stream.get("codec_name"),
                        "channels": audio_stream.get("channels"),
                        "sample_rate": audio_stream.get("sample_rate"),
                    },
                }
        except Exception:
            pass

    if ffmpeg:
        try:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return _parse_ffmpeg_stderr(proc.stderr)
        except Exception:
            pass

    return {
        "source": "none",
        "error": "ffmpeg/ffprobe not available. Install ffmpeg for video analysis.",
        "size_bytes": path.stat().st_size if path.exists() else None,
    }


def extract_frames(path: Path, count: int = 8) -> tuple[list[dict], str | None]:
    """Extract evenly-spaced frames. Returns (frames, error)."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return [], "ffmpeg is not installed. Install ffmpeg or `pip install imageio-ffmpeg`."

    meta = extract_video_metadata(path)
    duration = meta.get("duration_seconds")
    try:
        duration = float(duration) if duration is not None else 0.0
    except Exception:
        duration = 0.0
    if duration <= 0:
        return [], "Could not determine video duration."

    out_dir = path.parent / f"{path.stem}_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict] = []
    for index in range(count):
        timestamp = duration * (index + 0.5) / count
        out_path = out_dir / f"frame_{index:02d}.jpg"
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.2f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=45, check=False)
            if out_path.exists() and out_path.stat().st_size > 0:
                frames.append(
                    {
                        "index": index,
                        "timestamp_seconds": round(timestamp, 2),
                        "path": out_path,
                    }
                )
        except Exception:
            continue

    if not frames:
        return [], "Frame extraction produced no frames."
    return frames, None

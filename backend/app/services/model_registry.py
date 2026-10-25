from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import settings


class ModelUnavailable(RuntimeError):
    """Raised when a model cannot be loaded or run."""


def _device() -> str:
    if settings.device != "auto":
        return settings.device
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _cache_dir() -> str:
    path = Path(settings.model_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _ai_index(labels: dict[int, str]) -> int:
    for idx, label in labels.items():
        text = str(label).strip().lower()
        if text in {"ai", "artificial", "fake", "generated", "synthetic"}:
            return int(idx)
        if "ai" == text or "generated" in text or "artificial" in text:
            return int(idx)
    return 0


@dataclass
class ImageModel:
    model_id: str
    processor: Any
    model: Any
    device: str
    labels: dict[int, str]
    revision: str | None = None

    def predict(self, image: Image.Image) -> dict:
        import torch

        started = time.perf_counter()
        inputs = self.processor(images=image, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with torch.inference_mode():
            logits = self.model(**inputs).logits
            probs = torch.softmax(logits.float(), dim=-1)[0].cpu().tolist()

        ai_idx = _ai_index(self.labels)
        scores = {
            str(self.labels.get(i, str(i))): round(float(p), 6)
            for i, p in enumerate(probs)
        }
        best_idx = max(range(len(probs)), key=lambda i: probs[i])
        return {
            "model_id": self.model_id,
            "device": self.device,
            "ai_probability": round(float(probs[ai_idx]), 6),
            "predicted_label": str(self.labels.get(best_idx, str(best_idx))),
            "scores": scores,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }


@dataclass
class SmaugImageModel:
    """Adapter for abacusai/Smaug-AI-Image-Detector's multimodal wrapper."""

    model_id: str
    detector: Any
    device: str
    crops: int = 1
    labels: dict[int, str] = field(
        default_factory=lambda: {0: "real", 1: "ai"}
    )
    revision: str | None = None

    def predict(self, image: Image.Image) -> dict:
        started = time.perf_counter()
        score = float(self.detector(image, crops=self.crops))
        score = max(0.0, min(1.0, score))
        return {
            "model_id": self.model_id,
            "device": self.device,
            "ai_probability": round(score, 6),
            "predicted_label": "ai" if score >= 0.5 else "real",
            "scores": {"real": round(1.0 - score, 6), "ai": round(score, 6)},
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }


@dataclass
class TextModel:
    model_id: str
    tokenizer: Any
    model: Any
    device: str
    labels: dict[int, str]
    temperature: float = 1.0
    max_length: int = 2048
    revision: str | None = None

    def predict(self, text: str) -> dict:
        import torch

        started = time.perf_counter()
        inputs = self.tokenizer(
            text,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with torch.inference_mode():
            logits = self.model(**inputs).logits.float() / max(self.temperature, 0.01)
            probs = torch.softmax(logits, dim=-1)[0].cpu().tolist()

        ai_idx = _ai_index(self.labels)
        scores = {
            str(self.labels.get(i, str(i))): round(float(p), 6)
            for i, p in enumerate(probs)
        }
        best_idx = max(range(len(probs)), key=lambda i: probs[i])
        return {
            "model_id": self.model_id,
            "device": self.device,
            "ai_probability": round(float(probs[ai_idx]), 6),
            "predicted_label": str(self.labels.get(best_idx, str(best_idx))),
            "scores": scores,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }


_image_models: list[ImageModel | SmaugImageModel] = []
_image_errors: dict[str, str] = {}
_image_lock = threading.Lock()

_text_model: TextModel | None = None
_text_error: str | None = None
_text_lock = threading.Lock()


def _is_smaug(model_id: str) -> bool:
    return "smaug" in model_id.lower()


def load_smaug_model(model_id: str, revision: str | None = None) -> SmaugImageModel:
    try:
        from huggingface_hub import snapshot_download

        from .smaug_image_detect import SmaugImageDetect
    except Exception as exc:  # pragma: no cover - depends on optional install
        raise ModelUnavailable(
            "Smaug needs sentence-transformers and torchvision. "
            "Run scripts/setup_backend.sh, or see docs/HOW_TO_RUN.md."
        ) from exc

    device = _device()
    try:
        # Download only the files the wrapper needs. The wrapper itself is
        # vendored in this repository, so no downloaded Python is executed.
        if settings.smaug_model_path:
            path = settings.smaug_model_path
        else:
            path = snapshot_download(
                model_id,
                revision=revision or None,
                cache_dir=_cache_dir(),
                allow_patterns=["*.json", "*.safetensors", "*.jinja"],
            )
        detector = SmaugImageDetect.from_pretrained(path, device=device)
    except Exception as exc:
        raise ModelUnavailable(f"Could not load Smaug image model {model_id}: {exc}") from exc

    crops = settings.smaug_crops if settings.smaug_crops in (1, 3) else 1
    return SmaugImageModel(
        model_id=model_id,
        detector=detector,
        device=device,
        crops=crops,
        revision=revision or None,
    )


def load_image_model(
    model_id: str, revision: str | None = None
) -> ImageModel | SmaugImageModel:
    if _is_smaug(model_id):
        return load_smaug_model(model_id, revision)

    try:
        from transformers import AutoImageProcessor, AutoModelForImageClassification
    except Exception as exc:  # pragma: no cover - depends on optional install
        raise ModelUnavailable(
            "transformers/torch are not installed. Run scripts/setup_backend.sh."
        ) from exc

    device = _device()
    try:
        processor = AutoImageProcessor.from_pretrained(
            model_id, revision=revision or None, cache_dir=_cache_dir()
        )
        model = AutoModelForImageClassification.from_pretrained(
            model_id, revision=revision or None, cache_dir=_cache_dir()
        )
        model.to(device)
        model.eval()
    except Exception as exc:
        raise ModelUnavailable(f"Could not load image model {model_id}: {exc}") from exc

    labels = {int(k): str(v) for k, v in model.config.id2label.items()}
    return ImageModel(
        model_id=model_id,
        processor=processor,
        model=model,
        device=device,
        labels=labels,
        revision=revision or None,
    )


def get_image_models() -> tuple[list[ImageModel | SmaugImageModel], dict[str, str]]:
    """Lazily load the configured image models. Returns (models, errors)."""
    global _image_models, _image_errors
    with _image_lock:
        if _image_models or _image_errors:
            return list(_image_models), dict(_image_errors)

        configured: list[tuple[str, str]] = [
            (settings.image_model_primary, settings.image_model_primary_revision)
        ]
        if settings.enable_secondary_image_model and settings.image_model_secondary:
            configured.append(
                (
                    settings.image_model_secondary,
                    settings.image_model_secondary_revision,
                )
            )

        loaded_ids: set[str] = set()
        for model_id, revision in configured:
            if not model_id or model_id in loaded_ids:
                continue
            try:
                _image_models.append(load_image_model(model_id, revision))
                loaded_ids.add(model_id)
            except Exception as exc:
                _image_errors[model_id] = str(exc)
                # If the primary cannot load, try a smaller/faster fallback so
                # the image tab still works on low-memory or offline machines.
                fallback = (settings.image_model_fallback or "").strip()
                if (
                    model_id == settings.image_model_primary
                    and fallback
                    and fallback != model_id
                    and fallback not in loaded_ids
                    and fallback not in {mid for mid, _ in configured[1:]}
                ):
                    try:
                        _image_models.append(
                            load_image_model(
                                fallback, settings.image_model_fallback_revision
                            )
                        )
                        loaded_ids.add(fallback)
                    except Exception as fallback_exc:
                        _image_errors[fallback] = str(fallback_exc)

        return list(_image_models), dict(_image_errors)


def load_text_model() -> TextModel:
    try:
        from huggingface_hub import hf_hub_download
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except Exception as exc:  # pragma: no cover
        raise ModelUnavailable(
            "transformers/torch are not installed. Run scripts/setup_backend.sh."
        ) from exc

    model_id = settings.text_model_id
    revision = settings.text_model_revision
    device = _device()
    temperature = 1.0
    max_length = 2048
    try:
        config_path = hf_hub_download(
            model_id,
            "detector-config.json",
            revision=revision or None,
            cache_dir=_cache_dir(),
        )
        with open(config_path, "r", encoding="utf-8") as handle:
            cfg = json.load(handle)
        temperature = float(cfg.get("temperature", 1.0))
        max_length = min(int(cfg.get("max_length", 2048)), 2048)
    except Exception:
        # Newer/custom repos may not have the calibration file. Defaults are fine.
        pass

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            model_id, revision=revision or None, cache_dir=_cache_dir()
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            model_id, revision=revision or None, cache_dir=_cache_dir()
        )
        model.to(device)
        model.eval()
    except Exception as exc:
        raise ModelUnavailable(f"Could not load text model {model_id}: {exc}") from exc

    labels = {int(k): str(v) for k, v in model.config.id2label.items()}
    return TextModel(
        model_id=model_id,
        tokenizer=tokenizer,
        model=model,
        device=device,
        labels=labels,
        temperature=temperature,
        max_length=max_length,
        revision=revision or None,
    )


def get_text_model() -> tuple[TextModel | None, str | None]:
    global _text_model, _text_error
    if not settings.enable_text_model:
        return None, "Text model disabled (ENABLE_TEXT_MODEL=0)."
    with _text_lock:
        if _text_model is not None or _text_error is not None:
            return _text_model, _text_error
        try:
            _text_model = load_text_model()
        except Exception as exc:
            _text_error = str(exc)
        return _text_model, _text_error


def model_status() -> dict:
    return {
        "image_primary": settings.image_model_primary,
        "image_primary_revision": settings.image_model_primary_revision or "main",
        "image_fallback": settings.image_model_fallback,
        "image_fallback_revision": settings.image_model_fallback_revision or "main",
        "image_secondary": (
            settings.image_model_secondary
            if settings.enable_secondary_image_model
            else None
        ),
        "image_secondary_revision": (
            settings.image_model_secondary_revision or "main"
            if settings.enable_secondary_image_model
            else None
        ),
        "image_loaded": [m.model_id for m in _image_models],
        "image_loaded_revisions": {
            m.model_id: (m.revision or "main") for m in _image_models
        },
        "image_errors": dict(_image_errors),
        "text_model": settings.text_model_id if settings.enable_text_model else None,
        "text_model_revision": (
            settings.text_model_revision or "main"
            if settings.enable_text_model
            else None
        ),
        "text_loaded": _text_model.model_id if _text_model else None,
        "text_loaded_revision": (
            (_text_model.revision or "main") if _text_model else None
        ),
        "text_error": _text_error,
        "device": _device(),
        "cache_dir": _cache_dir(),
    }

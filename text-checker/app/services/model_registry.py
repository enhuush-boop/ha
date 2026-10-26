from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        all_tokens = self.tokenizer(text, truncation=False)["input_ids"]
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
            "tokens": len(all_tokens),
            "truncated": len(all_tokens) > self.max_length,
            "model_id": self.model_id,
            "device": self.device,
            "ai_probability": round(float(probs[ai_idx]), 6),
            "predicted_label": str(self.labels.get(best_idx, str(best_idx))),
            "scores": scores,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }



_text_model = None
_text_error = None
_text_lock = threading.Lock()

def load_text_model() -> TextModel:
    try:
        from huggingface_hub import hf_hub_download
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except Exception as exc:  # pragma: no cover
        raise ModelUnavailable(
            "transformers/torch are not installed. Install requirements.txt."
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



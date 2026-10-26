from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
# Per-user local settings (backend/.env, gitignored) take priority. A committed
# backend/.env.shared then fills in anything the local file leaves unset — for
# this private repo that is the shared Google Fact Check key, so the few
# collaborators clone and run without each provisioning their own. load_dotenv
# does not override already-set vars, so load order is the precedence: .env
# wins, .env.shared is the fallback.
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env.shared")

# Pinned model IDs and revisions. A fresh clone therefore downloads exactly the
# weights used for the published evaluation, not whatever is on `main` later.
DEFAULT_IMAGE_MODEL_PRIMARY = "abacusai/Smaug-AI-Image-Detector"
DEFAULT_IMAGE_MODEL_PRIMARY_REVISION = "c7c95951e04a87b884a4ad479401b1128c3903bd"
DEFAULT_IMAGE_MODEL_FALLBACK = "Ateeqq/ai-vs-human-image-detector"
DEFAULT_IMAGE_MODEL_FALLBACK_REVISION = "60e82406916921b823616bee33397baab38af3f0"
DEFAULT_IMAGE_MODEL_SECONDARY = "umm-maybe/AI-image-detector"
DEFAULT_IMAGE_MODEL_SECONDARY_REVISION = "c7e223baf11bc40528af364ba7bdea030ef42f9e"
DEFAULT_TEXT_MODEL_ID = "rasbt/ai-text-detector-modernbert"
DEFAULT_TEXT_MODEL_REVISION = "a6efd4bf6ecd852f332b2569ae0f98cae13b7985"


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _revision(
    env_name: str,
    model_id: str,
    default_model_id: str,
    default_revision: str,
) -> str:
    """Return an explicit revision override, or the pin for a known model.

    If the model ID was changed but no revision was supplied, return an empty
    string so Hugging Face uses the new model's default branch. Otherwise the
    pin for the original model would be invalid.
    """
    override = os.getenv(env_name, "").strip()
    if override:
        return override
    return default_revision if model_id == default_model_id else ""


class Settings:
    """Runtime configuration. Everything is local by default."""

    def __init__(self) -> None:
        self.backend_dir = BACKEND_DIR
        self.data_dir = BACKEND_DIR / "app" / "data"

        # Models (IDs + exact revisions for reproducible downloads)
        self.image_model_primary = os.getenv(
            "IMAGE_MODEL_PRIMARY", DEFAULT_IMAGE_MODEL_PRIMARY
        )
        self.image_model_primary_revision = _revision(
            "IMAGE_MODEL_PRIMARY_REVISION",
            self.image_model_primary,
            DEFAULT_IMAGE_MODEL_PRIMARY,
            DEFAULT_IMAGE_MODEL_PRIMARY_REVISION,
        )
        self.image_model_fallback = os.getenv(
            "IMAGE_MODEL_FALLBACK", DEFAULT_IMAGE_MODEL_FALLBACK
        )
        self.image_model_fallback_revision = _revision(
            "IMAGE_MODEL_FALLBACK_REVISION",
            self.image_model_fallback,
            DEFAULT_IMAGE_MODEL_FALLBACK,
            DEFAULT_IMAGE_MODEL_FALLBACK_REVISION,
        )
        self.image_model_secondary = os.getenv(
            "IMAGE_MODEL_SECONDARY", DEFAULT_IMAGE_MODEL_SECONDARY
        )
        self.image_model_secondary_revision = _revision(
            "IMAGE_MODEL_SECONDARY_REVISION",
            self.image_model_secondary,
            DEFAULT_IMAGE_MODEL_SECONDARY,
            DEFAULT_IMAGE_MODEL_SECONDARY_REVISION,
        )
        self.enable_secondary_image_model = _bool("ENABLE_SECONDARY_IMAGE_MODEL", False)
        # Smaug accepts 1 (centre crop, ~3x faster) or 3 (start/centre/end).
        self.smaug_crops = int(os.getenv("SMAUG_CROPS", "1"))
        # Optional local snapshot directory (for offline use or a pre-seeded cache).
        self.smaug_model_path = os.getenv("SMAUG_MODEL_PATH", "").strip()
        self.text_model_id = os.getenv("TEXT_MODEL_ID", DEFAULT_TEXT_MODEL_ID)
        self.text_model_revision = _revision(
            "TEXT_MODEL_REVISION",
            self.text_model_id,
            DEFAULT_TEXT_MODEL_ID,
            DEFAULT_TEXT_MODEL_REVISION,
        )
        self.enable_text_model = _bool("ENABLE_TEXT_MODEL", True)
        self.device = os.getenv("DEVICE", "auto")
        self.model_cache_dir = os.getenv(
            "MODEL_CACHE_DIR", str(BACKEND_DIR / ".model_cache")
        )

        # Local LLM (optional)
        self.ollama_url = os.getenv("OLLAMA_URL", "").strip().rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "qwen3:4b")

        # Optional hosted services
        self.google_factcheck_api_key = os.getenv(
            "GOOGLE_FACTCHECK_API_KEY", ""
        ).strip()
        self.google_safe_browsing_api_key = os.getenv(
            "GOOGLE_SAFE_BROWSING_API_KEY", ""
        ).strip()
        self.virustotal_api_key = os.getenv("VIRUSTOTAL_API_KEY", "").strip()
        self.urlhaus_auth_key = os.getenv("URLHAUS_AUTH_KEY", "").strip()

        # Limits / networking
        self.max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "100"))
        self.max_image_url_mb = int(os.getenv("MAX_IMAGE_URL_MB", "20"))
        self.request_timeout = float(os.getenv("REQUEST_TIMEOUT", "15"))
        self.allowed_origins = [
            x.strip()
            for x in os.getenv("ALLOWED_ORIGINS", "*").split(",")
            if x.strip()
        ]
        self.cors_allow_origins = (
            ["*"] if "*" in self.allowed_origins else self.allowed_origins
        )

        # Video
        self.video_frames = int(os.getenv("VIDEO_FRAMES", "8"))


settings = Settings()
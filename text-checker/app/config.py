import os
from pathlib import Path
from types import SimpleNamespace
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
cache = Path(os.getenv("MODEL_CACHE_DIR", str(ROOT / ".model_cache"))).resolve()
os.environ.setdefault("HF_HOME", str(cache / "hf-home"))
os.environ.setdefault("HF_XET_CACHE", str(cache / "xet"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
settings = SimpleNamespace(
    text_model_id=os.getenv("TEXT_MODEL_ID", "rasbt/ai-text-detector-modernbert"),
    text_model_revision=os.getenv("TEXT_MODEL_REVISION", "a6efd4bf6ecd852f332b2569ae0f98cae13b7985"),
    model_cache_dir=str(cache),
    device=os.getenv("DEVICE", "cpu"),
    enable_text_model=os.getenv("ENABLE_TEXT_MODEL", "1") == "1",
)

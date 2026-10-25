#!/usr/bin/env python3
"""Pre-download and load the configured models so the demo starts warm.

Usage:
    .venv/bin/python scripts/warmup_models.py
    .venv/bin/python scripts/warmup_models.py --skip-text
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.model_registry import (  # noqa: E402
    get_image_models,
    get_text_model,
    model_status,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-text", action="store_true")
    parser.add_argument("--skip-image", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()

    if not args.skip_image:
        print(
            "Loading image model(s); the first run downloads the pinned Smaug "
            "revision (~870 MB)…",
            flush=True,
        )
        models, errors = get_image_models()
        for model in models:
            print(
                f"  ready: {model.model_id} @ {model.revision or 'main'} "
                f"on {model.device}"
            )
        for model_id, error in errors.items():
            print(f"  ERROR: {model_id}: {error}", file=sys.stderr)

    if not args.skip_text:
        print("Loading text model; the first run downloads ~300 MB…", flush=True)
        model, error = get_text_model()
        if model:
            print(
                f"  ready: {model.model_id} @ {model.revision or 'main'} "
                f"on {model.device}"
            )
        elif error:
            print(f"  ERROR: {error}", file=sys.stderr)

    print(f"\nStatus: {model_status()}")
    print(f"Finished in {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

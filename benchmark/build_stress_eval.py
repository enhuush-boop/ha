#!/usr/bin/env python3
"""Build a recompressed stress subset from the modern Google-AI evaluation set.

Takes the first N images per source, resizes the shorter side to 512 px, and
saves them as JPEG quality 60. This approximates messaging/social-media
recompression; it is a stress test, not a full robustness study.

Run after build_modern_eval.py:

    .venv/bin/python benchmark/build_modern_eval.py
    .venv/bin/python benchmark/build_stress_eval.py

Output defaults to benchmark/data/modern_stress/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "data" / "modern"
DEFAULT_OUTPUT = ROOT / "data" / "modern_stress"

GROUPS = [
    ("ai", "nano_banana"),
    ("ai", "imagen4"),
    ("real", "picsum"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--per-source", type=int, default=50)
    parser.add_argument("--side", type=int, default=512)
    parser.add_argument("--quality", type=int, default=60)
    args = parser.parse_args()

    if not args.source_root.exists():
        raise SystemExit(
            f"Missing {args.source_root}. Run benchmark/build_modern_eval.py first."
        )

    counts: dict[str, int] = {}
    for truth, source in GROUPS:
        source_dir = args.source_root / truth / source
        destination = args.out_root / truth / source
        if not source_dir.exists():
            print(f"warning: missing {source_dir}")
            continue
        paths = sorted(source_dir.glob("*.jpg"))[: args.per_source]
        saved = 0
        for path in paths:
            with Image.open(path) as image:
                image = image.convert("RGB")
                width, height = image.size
                scale = args.side / min(width, height)
                if scale < 1:
                    image = image.resize(
                        (round(width * scale), round(height * scale)), Image.LANCZOS
                    )
                destination.mkdir(parents=True, exist_ok=True)
                image.save(
                    destination / path.name,
                    quality=args.quality,
                    optimize=True,
                )
            saved += 1
        counts[source] = saved
        print(f"{source}: {saved}")

    manifest = {
        "source_root": str(args.source_root),
        "per_source": args.per_source,
        "side": args.side,
        "jpeg_quality": args.quality,
        "counts": counts,
        "note": (
            "First N images per source; shorter side resized to "
            f"{args.side} px and saved as JPEG quality {args.quality}. "
            "Approximates social-media/messaging recompression; not a full "
            "robustness study."
        ),
    }
    (args.out_root / "sources.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"\nSaved to {args.out_root}")
    print(
        "Run: .venv/bin/python benchmark/evaluate_modern.py "
        "--data-root benchmark/data/modern_stress --limit 50 "
        "--output-prefix stress_image_eval "
        '--dataset-note "Recompressed stress subset: first 50 per source, '
        'shorter side 512 px, JPEG quality 60."'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

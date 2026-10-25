#!/usr/bin/env python3
"""Build the modern Google-AI evaluation set.

Downloads one shard each from two public datasets and samples 100 images per
source, plus 100 real Unsplash photos via picsum.photos.

    Nano Banana: bitmind/nano-banana (MIT) — Gemini 2.5 Flash Image
    Imagen 4:    Rapidata/Imagen4_t2i_human_preference (CDLA-Permissive-2.0)
    Real:        picsum.photos (Unsplash License)

Output defaults to benchmark/data/modern/ (gitignored), about 80 MB.

Usage:
    .venv/bin/python benchmark/build_modern_eval.py
    .venv/bin/python benchmark/build_modern_eval.py --data-root /tmp/modern --per-source 50
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import sys
from pathlib import Path

import httpx
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download
from PIL import Image

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "modern"
CACHE = Path.home() / ".cache" / "tayo-eval"

DATASETS = {
    "nano_banana": {
        "repo": "bitmind/nano-banana",
        "filename": "data/train-00000-of-00031.parquet",
        "license": "MIT",
        "note": "Gemini 2.5 Flash Image (Nano Banana)",
    },
    "imagen4": {
        "repo": "Rapidata/Imagen4_t2i_human_preference",
        "filename": "data/train-00000-of-00045.parquet",
        "license": "CDLA-Permissive-2.0",
        "note": "Imagen 4 (imagen-4.0-ultra-generate-exp-05-20)",
    },
}


def save_image(data: bytes, destination: Path) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image.convert("RGB").save(destination, quality=95)
        return True
    except Exception as exc:
        print(f"  skip {destination.name}: {exc}", file=sys.stderr)
        return False


def build_nano_banana(data_root: Path, per_source: int) -> int:
    info = DATASETS["nano_banana"]
    path = hf_hub_download(
        repo_id=info["repo"],
        filename=info["filename"],
        repo_type="dataset",
        cache_dir=str(CACHE),
    )
    table = pq.read_table(path, columns=["id", "image", "format"])
    saved = 0
    folder = data_root / "ai" / "nano_banana"
    folder.mkdir(parents=True, exist_ok=True)
    for row in table.to_pylist():
        if saved >= per_source:
            break
        data = (row.get("image") or {}).get("bytes")
        if not data:
            continue
        if save_image(data, folder / f"nano_{saved:03d}.jpg"):
            saved += 1
    return saved


def build_imagen4(data_root: Path, per_source: int) -> int:
    info = DATASETS["imagen4"]
    path = hf_hub_download(
        repo_id=info["repo"],
        filename=info["filename"],
        repo_type="dataset",
        cache_dir=str(CACHE),
    )
    table = pq.read_table(
        path, columns=["image1", "image2", "model1", "model2"]
    )
    folder = data_root / "ai" / "imagen4"
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for row in table.to_pylist():
        for side in ("1", "2"):
            if saved >= per_source:
                break
            if "imagen-4" not in str(row.get(f"model{side}") or "").lower():
                continue
            data = (row.get(f"image{side}") or {}).get("bytes")
            if not data:
                continue
            if save_image(data, folder / f"imagen4_{saved:03d}.jpg"):
                saved += 1
        if saved >= per_source:
            break
    return saved


def fetch_real(index: int, folder: Path) -> bool:
    url = f"https://picsum.photos/seed/tayo-real-{index:03d}/768/768"
    headers = {"User-Agent": "TAYO-eval/0.1 (local benchmark)"}
    try:
        with httpx.Client(timeout=60, follow_redirects=True, headers=headers) as client:
            response = client.get(url)
            response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as image:
                image.load()
                image.convert("RGB").save(
                    folder / f"real_{index:03d}.jpg", quality=95
                )
        return True
    except Exception as exc:
        print(f"  real {index}: {exc}", file=sys.stderr)
        return False


def build_real(data_root: Path, per_source: int) -> int:
    folder = data_root / "real" / "picsum"
    folder.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: fetch_real(i, folder), range(per_source)))
    return sum(1 for result in results if result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--per-source", type=int, default=100)
    args = parser.parse_args()

    args.data_root.mkdir(parents=True, exist_ok=True)

    print("Nano Banana (Gemini 2.5 Flash Image) …")
    nano = build_nano_banana(args.data_root, args.per_source)
    print(f"  saved {nano}")

    print("Imagen 4 …")
    imagen = build_imagen4(args.data_root, args.per_source)
    print(f"  saved {imagen}")

    print("Real photos (picsum/Unsplash) …")
    real = build_real(args.data_root, args.per_source)
    print(f"  saved {real}")

    manifest = {
        "sources": {
            "nano_banana": {
                **DATASETS["nano_banana"],
                "count": nano,
                "truth": "ai",
            },
            "imagen4": {
                **DATASETS["imagen4"],
                "count": imagen,
                "truth": "ai",
            },
            "picsum": {
                "repo": "https://picsum.photos",
                "license": "Unsplash License",
                "count": real,
                "truth": "real",
            },
        }
    }
    (args.data_root / "sources.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"\nSaved to {args.data_root}")
    print("Run: .venv/bin/python benchmark/evaluate_modern.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Download the small demo benchmark set.

Real: five Unsplash photos served by picsum.photos (512x512 crops).
AI:   five generated outputs published in the Hugging Face diffusers docs.

This is a smoke test, not a scientific benchmark. Ten images cannot establish
real-world accuracy. Replace these files with a licensed, diverse evaluation
set before making claims about model quality.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
AI_BASE = "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/diffusers"

SAMPLES = {
    "real": {
        "real_01_picsum_10.jpg": "https://picsum.photos/id/10/512/512",
        "real_02_picsum_20.jpg": "https://picsum.photos/id/20/512/512",
        "real_03_picsum_30.jpg": "https://picsum.photos/id/30/512/512",
        "real_04_picsum_40.jpg": "https://picsum.photos/id/40/512/512",
        "real_05_picsum_50.jpg": "https://picsum.photos/id/50/512/512",
    },
    "ai": {
        # FLUX IP-Adapter output (photorealistic statue)
        "ai_01_flux_statue.jpg": f"{AI_BASE}/flux_ip_adapter_output.jpg",
        # SDXL output (surreal squirrel)
        "ai_02_sdxl_squirrel.png": f"{AI_BASE}/sdxl-freeu.png",
        # SDXL text-to-image (astronaut)
        "ai_03_sdxl_astronaut.png": f"{AI_BASE}/sdxl-text2img.png",
        # SDXL text-to-image (illustrative emoji)
        "ai_04_sdxl_emoji.png": f"{AI_BASE}/t2i-sdxl.png",
        # Stable Diffusion text-to-image (oil-painting style)
        "ai_05_sd_oil_astronaut.png": f"{AI_BASE}/text2img-2.png",
    },
}


def download(url: str, destination: Path, client: httpx.Client) -> None:
    if destination.exists() and destination.stat().st_size > 0:
        print(f"  exists  {destination.name}")
        return
    print(f"  fetch   {destination.name}")
    response = client.get(url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise RuntimeError(
            f"{url} returned {content_type!r}, not an image. The sample may have moved."
        )
    destination.write_bytes(response.content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=ROOT / "data", help="Output data directory"
    )
    args = parser.parse_args()

    with httpx.Client(
        timeout=90,
        follow_redirects=True,
        headers={"User-Agent": "TAYO-benchmark/0.1 (local demo)"},
    ) as client:
        for label, samples in SAMPLES.items():
            target = args.data_dir / label
            target.mkdir(parents=True, exist_ok=True)
            print(f"{label}:")
            for filename, url in samples.items():
                download(url, target / filename, client)

    print(f"\nSamples are in {args.data_dir}")
    print("Run: .venv/bin/python benchmark/benchmark.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

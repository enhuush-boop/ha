# Third-party notices

## Smaug AI Image Detector

- Upstream: <https://huggingface.co/abacusai/Smaug-AI-Image-Detector>
- Vendored file: `backend/app/services/smaug_image_detect.py`
- License: Apache License 2.0 (see `third_party/smaug/LICENSE`)

The wrapper is vendored so TAYO does not execute Python downloaded from the
Hugging Face Hub at runtime. The model weights are downloaded separately and are
not included in this repository.

## Datasets used by the benchmark

The evaluation images are downloaded locally and are not redistributed here.
See `benchmark/README.md` for sources and licenses:

- `bitmind/nano-banana` — MIT
- `Rapidata/Imagen4_t2i_human_preference` — CDLA-Permissive-2.0
- picsum.photos / Unsplash — Unsplash License

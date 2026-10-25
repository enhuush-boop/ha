# Modern AI-image detector evaluation

Generated: 2026-10-25T13:31:18+00:00

Device: `cpu` · Torch `2.14.1+cpu` · Transformers `5.20.2`

> This is a **Google-focused smoke evaluation**, not a universal benchmark. It measures 50 Nano Banana (Gemini 2.5 Flash Image), 50 Imagen 4, and 50 real Unsplash photos. Results will not transfer to every generator, social-media compression, or editing pipeline.

Dataset note: Recompressed stress subset: the first 50 images per source, shorter side resized to 512 px and saved as JPEG quality 60. This approximates social-media/messaging recompression; it is not a full robustness study.

## Overall (threshold 0.5)

| Model | N | Accuracy | AI recall | Real recall (specificity) | ROC-AUC | TPR @ 5% FPR | Mean P(AI) real | Mean P(AI) AI | Latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `Ateeqq/ai-vs-human-image-detector` | 150 | 94.0% | 98.0% | 86.0% | 0.98240 | 94.0% | 14.7% | 98.0% | 126 ms |
| `abacusai/Smaug-AI-Image-Detector` | 150 | 98.0% | 97.0% | 100.0% | 0.99860 | 98.0% | 0.6% | 97.1% | 1788 ms |

## Per-source recall at 0.5

| Model | imagen4 | nano_banana | picsum |
|---|---:|---:|---:|
| `ai-vs-human-image-detector` | 100.0% AI recall | 96.0% AI recall | 86.0% real recall |
| `Smaug-AI-Image-Detector` | 100.0% AI recall | 94.0% AI recall | 100.0% real recall |

## Calibration

| Model | Best threshold (all data, optimistic) | 5-fold CV threshold performance | CV AI recall | CV real recall |
|---|---:|---:|---:|---:|
| `ai-vs-human-image-detector` | 0.50 | 92.0% balanced | 98.0% | 86.0% |
| `Smaug-AI-Image-Detector` | 0.38 | 98.5% balanced | 97.0% | 100.0% |

## Full images at the default threshold

### `Ateeqq/ai-vs-human-image-detector`

- **imagen4** (AI, n=50): median P(AI)=1.000, range 0.999–1.000
- **nano_banana** (AI, n=50): median P(AI)=1.000, range 0.001–1.000
- **picsum** (real, n=50): median P(AI)=0.002, range 0.000–0.999

### `abacusai/Smaug-AI-Image-Detector`

- **imagen4** (AI, n=50): median P(AI)=1.000, range 1.000–1.000
- **nano_banana** (AI, n=50): median P(AI)=1.000, range 0.015–1.000
- **picsum** (real, n=50): median P(AI)=0.000, range 0.000–0.094

## Reproduce

```bash
.venv/bin/python benchmark/build_modern_eval.py
.venv/bin/python benchmark/evaluate_modern.py
```

The images are downloaded to `benchmark/data/modern/` and are excluded from git. Nano Banana data: `bitmind/nano-banana` (MIT). Imagen 4 data: `Rapidata/Imagen4_t2i_human_preference` (CDLA-Permissive-2.0). Real photos: picsum.photos (Unsplash License).

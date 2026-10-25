# Modern AI-image detector evaluation

Generated: 2026-10-25T13:24:17+00:00

Device: `cpu` · Torch `2.14.1+cpu` · Transformers `5.20.2`

> This is a **Google-focused smoke evaluation**, not a universal benchmark. It measures 100 Nano Banana (Gemini 2.5 Flash Image), 100 Imagen 4, and 100 real Unsplash photos. Results will not transfer to every generator, social-media compression, or editing pipeline.

Dataset note: Google-focused smoke evaluation; not a universal benchmark.

## Overall (threshold 0.5)

| Model | N | Accuracy | AI recall | Real recall (specificity) | ROC-AUC | TPR @ 5% FPR | Mean P(AI) real | Mean P(AI) AI | Latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `Ateeqq/ai-vs-human-image-detector` | 300 | 90.7% | 91.5% | 89.0% | 0.93807 | 70.0% | 11.1% | 91.5% | 143 ms |
| `abacusai/Smaug-AI-Image-Detector` | 300 | 99.0% | 98.5% | 100.0% | 0.99995 | 100.0% | 0.3% | 98.5% | 1923 ms |

## Per-source recall at 0.5

| Model | imagen4 | nano_banana | picsum |
|---|---:|---:|---:|
| `ai-vs-human-image-detector` | 100.0% AI recall | 83.0% AI recall | 89.0% real recall |
| `Smaug-AI-Image-Detector` | 100.0% AI recall | 97.0% AI recall | 100.0% real recall |

## Calibration

| Model | Best threshold (all data, optimistic) | 5-fold CV threshold performance | CV AI recall | CV real recall |
|---|---:|---:|---:|---:|
| `ai-vs-human-image-detector` | 0.53 | 90.2% balanced | 91.5% | 89.0% |
| `Smaug-AI-Image-Detector` | 0.08 | 99.0% balanced | 99.0% | 99.0% |

## Full images at the default threshold

### `Ateeqq/ai-vs-human-image-detector`

- **imagen4** (AI, n=100): median P(AI)=1.000, range 0.995–1.000
- **nano_banana** (AI, n=100): median P(AI)=1.000, range 0.000–1.000
- **picsum** (real, n=100): median P(AI)=0.001, range 0.000–1.000

### `abacusai/Smaug-AI-Image-Detector`

- **imagen4** (AI, n=100): median P(AI)=1.000, range 0.967–1.000
- **nano_banana** (AI, n=100): median P(AI)=1.000, range 0.051–1.000
- **picsum** (real, n=100): median P(AI)=0.000, range 0.000–0.073

## Reproduce

```bash
.venv/bin/python benchmark/build_modern_eval.py
.venv/bin/python benchmark/evaluate_modern.py
```

The images are downloaded to `benchmark/data/modern/` and are excluded from git. Nano Banana data: `bitmind/nano-banana` (MIT). Imagen 4 data: `Rapidata/Imagen4_t2i_human_preference` (CDLA-Permissive-2.0). Real photos: picsum.photos (Unsplash License).

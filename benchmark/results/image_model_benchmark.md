# Image model benchmark

Generated: 2026-10-23T17:23:04+00:00
Device: `cpu` · Torch `2.14.1+cpu` · Transformers `5.20.2` · Python `3.12.15`

> **Smoke test, not a scientific evaluation.** This set has only 10 images (5 AI, 5 real). The AI images are generated documentation examples and the real images are Unsplash photos. Results do not generalize to photorealistic deepfakes, social-media recompression, or modern generators.

## Results

| Model | Accuracy | Balanced accuracy | AI recall | Real recall | ROC-AUC | Mean P(AI), real | Mean P(AI), AI | Mean latency | Params |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `Ateeqq/ai-vs-human-image-detector` | 90.0% | 90.0% | 80.0% | 100.0% | 0.880 | 0.3% | 80.0% | 144 ms | 92.9M |
| `umm-maybe/AI-image-detector` | 70.0% | 70.0% | 40.0% | 100.0% | 0.960 | 3.4% | 46.8% | 158 ms | 86.7M |

## Per-image predictions

| Sample | Truth | `ai-vs-human-image-detector` P(AI) | `AI-image-detector` P(AI) |
|---|---|---:|---:|
| real_01_picsum_10.jpg | real | 0.1% (real) | 2.1% (real) |
| real_02_picsum_20.jpg | real | 1.2% (real) | 2.2% (real) |
| real_03_picsum_30.jpg | real | 0.1% (real) | 1.0% (real) |
| real_04_picsum_40.jpg | real | 0.0% (real) | 10.0% (real) |
| real_05_picsum_50.jpg | real | 0.3% (real) | 1.6% (real) |
| ai_01_flux_statue.jpg | ai | 100.0% (AI) | 70.9% (AI) |
| ai_02_sdxl_squirrel.png | ai | 100.0% (AI) | 98.6% (AI) |
| ai_03_sdxl_astronaut.png | ai | 100.0% (AI) | 38.6% (real) |
| ai_04_sdxl_emoji.png | ai | 100.0% (AI) | 8.4% (real) |
| ai_05_sd_oil_astronaut.png | ai | 0.1% (real) | 17.5% (real) |

## Model notes

### `Ateeqq/ai-vs-human-image-detector`

- Architecture: SigLIP for image classification
- License: Apache-2.0
- Reported accuracy: 99.2% on the author's test split
- Role in TAYO: Primary model in TAYO; enabled by default.
- Caveat: Hub users report overfitting; it is not a forensic detector.
- Load time: 31.33 s

### `umm-maybe/AI-image-detector`

- Architecture: Swin-B for image classification
- License: Hub metadata: CC-BY-4.0; model card: CC BY-ND 4.0 (treat as no-derivatives)
- Reported accuracy: 94.2% on the author's validation split
- Role in TAYO: Secondary model; disabled by default. Evaluation only.
- Caveat: Trained before modern generators; explicitly not a deepfake detector.
- Load time: 66.2 s

## Reproduce

```bash
.venv/bin/python benchmark/download_samples.py
.venv/bin/python benchmark/benchmark.py
```

Replace the samples with a licensed, demographically and generator-diverse evaluation set before drawing conclusions. Report confidence intervals: with n=10 they are very wide.

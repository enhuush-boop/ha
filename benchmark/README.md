# Benchmark

Three evaluations, from most to least useful:

| Track | Images | Purpose | Report |
|---|---:|---|---|
| Google-focused modern set | 300 | Model selection: Nano Banana, Imagen 4, real photos | [`results/modern_image_eval.md`](results/modern_image_eval.md) |
| Recompressed stress subset | 150 | Robustness check after 512 px / JPEG-60 recompression | [`results/stress_image_eval.md`](results/stress_image_eval.md) |
| Original smoke test | 10 | Integration sanity check on older generators | [`results/image_model_benchmark.md`](results/image_model_benchmark.md) |

None of these is a universal benchmark. They are small, licensed, reproducible
evaluations intended to justify the default model and expose failure modes.

---

## 1. Google-focused modern evaluation (300 images)

### Dataset

| Label | Files | Source | License / terms |
|---|---|---|---|
| Nano Banana (AI) | 100, 1024×1024 | [`bitmind/nano-banana`](https://huggingface.co/datasets/bitmind/nano-banana) — Gemini 2.5 Flash Image | MIT |
| Imagen 4 (AI) | 100, 1024×1024 | [`Rapidata/Imagen4_t2i_human_preference`](https://huggingface.co/datasets/Rapidata/Imagen4_t2i_human_preference) | CDLA-Permissive-2.0 |
| Real | 100, 768×768 | [picsum.photos](https://picsum.photos) (Unsplash photos) | [Unsplash License](https://unsplash.com/license) |

The builder downloads one shard per AI dataset and 100 photos, then writes
JPEGs at quality 95. The images are downloaded locally and excluded from git.
The AI sets use one prompt distribution per generator, so this measures
generator recognition — not every use of Google AI, and not social-media
robustness.

### Results at threshold 0.5 (CPU)

| Model | Accuracy | AI recall | Real recall | ROC-AUC | TPR @ 5% FPR | Mean P(AI), real | Mean P(AI), AI | Latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Smaug | **99.0%** | **98.5%** | **100%** | **0.99995** | **100%** | 0.3% | 98.5% | 1.9 s |
| Ateeqq | 90.7% | 91.5% | 89.0% | 0.93807 | 70% | 11.1% | 91.5% | 143 ms |

Per source:

| Model | Nano Banana | Imagen 4 | Real photos |
|---|---:|---:|---:|
| Smaug | 97/100 detected | 100/100 detected | 100/100 correctly not flagged |
| Ateeqq | 83/100 detected | 100/100 detected | 89/100 correctly not flagged |

**Interpretation**

- Smaug caught 197/200 AI images with **zero false positives** on 100 real
  photos. Its three misses were all Nano Banana, scoring 0.051, 0.082, and
  0.161. The highest real-photo score was 0.073.
- Ateeqq's failures are clustered: several real photos scored near 0.999, while
  many Nano Banana images scored below 0.01. That matches the Google-image
  false negative that prompted this work.
- A post-hoc 0.08 threshold would catch two of Smaug's three misses with no
  extra false positive on the same set. Five-fold cross-validated threshold
  selection did **not** beat 0.5, so 0.5 remains the default. Do not tune on the
  set you use to report accuracy.
- Smaug's model card reports 86.7% AI recall at 5% FPR on real-world Reddit
  images. The clean Google set is easier; do not expect 98.5% on compressed
  social-media images.

### Reproduce

```bash
.venv/bin/python benchmark/build_modern_eval.py
.venv/bin/python benchmark/evaluate_modern.py
```

The first run downloads the evaluation images (~80 MB) and model weights
(~1.2 GB). On CPU, the full 300-image run takes roughly 15 minutes.

---

## 2. Recompressed stress subset (150 images)

The first 50 images per source from the modern set are resized so the shorter
side is 512 px and saved at JPEG quality 60. This approximates messaging-app and
social-media recompression. It is **not** an independent sample — it is the same
images, degraded — so treat it as a robustness check, not a second benchmark.

| Model | Accuracy | AI recall | Real recall | ROC-AUC | TPR @ 5% FPR | False positives |
|---|---:|---:|---:|---:|---:|---:|
| Smaug | **98.0%** | 97.0% | **100%** | **0.99860** | 98.0% | 0/50 |
| Ateeqq | 94.0% | 98.0% | 86.0% | 0.98240 | 94.0% | 7/50 |

Per source:

| Model | Nano Banana | Imagen 4 | Real photos |
|---|---:|---:|---:|
| Smaug | 47/50 detected | 50/50 detected | 50/50 correctly not flagged |
| Ateeqq | 48/50 detected | 50/50 detected | 43/50 correctly not flagged |

**Interpretation:** Smaug stays at zero false positives and loses three Nano
Banana images (scores 0.389, 0.050, 0.015). Ateeqq recovers some Nano Banana
images but falsely flags seven real photos at near-1.0 confidence. For a
journalist, that false-positive pattern is worse than a missed AI image: it can
turn a real photograph into an accusation. Smaug remains the default.

### Reproduce

```bash
.venv/bin/python benchmark/build_modern_eval.py
.venv/bin/python benchmark/build_stress_eval.py
.venv/bin/python benchmark/evaluate_modern.py \
  --data-root benchmark/data/modern_stress \
  --limit 50 \
  --output-prefix stress_image_eval \
  --dataset-note "Recompressed stress subset: first 50 per source, shorter side 512 px, JPEG quality 60."
```

---

## 3. Original 10-image smoke test

This was the first integration test, kept for a quick check on older
generators.

### Dataset

| Label | Files | Source | License / terms |
|---|---|---|---|
| Real (5) | `picsum` IDs 10, 20, 30, 40, 50, 512×512 | picsum.photos (Unsplash photos) | Unsplash License |
| AI (5) | FLUX, SDXL, Stable Diffusion outputs | [huggingface/documentation-images](https://huggingface.co/datasets/huggingface/documentation-images) | **CC BY-NC-SA 4.0** |

| Local file | Source path | Generator |
|---|---|---|
| `ai_01_flux_statue.jpg` | `diffusers/flux_ip_adapter_output.jpg` | FLUX IP-Adapter |
| `ai_02_sdxl_squirrel.png` | `diffusers/sdxl-freeu.png` | SDXL |
| `ai_03_sdxl_astronaut.png` | `diffusers/sdxl-text2img.png` | SDXL |
| `ai_04_sdxl_emoji.png` | `diffusers/t2i-sdxl.png` | SDXL |
| `ai_05_sd_oil_astronaut.png` | `diffusers/text2img-2.png` | Stable Diffusion |

The images are downloaded locally and excluded from version control. Do not
redistribute the AI samples outside CC BY-NC-SA 4.0; this repository's MIT
license does **not** cover them.

### Run

```bash
.venv/bin/python benchmark/download_samples.py
.venv/bin/python benchmark/benchmark.py
```

Useful options:

```bash
# one model only
.venv/bin/python benchmark/benchmark.py --models Ateeqq/ai-vs-human-image-detector

# force CPU
.venv/bin/python benchmark/benchmark.py --device cpu

# custom folders with real/ and ai/ subdirectories
.venv/bin/python benchmark/benchmark.py --data-dir /path/to/eval
```

Outputs: `results/image_model_benchmark.json` and
`results/image_model_benchmark.md`.

### Snapshot (CPU, October 2026)

| Model | Accuracy | AI recall | Real recall | ROC-AUC | Mean P(AI), real | Mean P(AI), AI |
|---|---:|---:|---:|---:|---:|---:|
| `Ateeqq/ai-vs-human-image-detector` | 90% | 80% | 100% | 0.880 | 0.3% | 80.0% |
| `umm-maybe/AI-image-detector` | 70% | 40% | 100% | 0.960 | 3.4% | 46.8% |

- Ateeqq missed the oil-painting-style SD astronaut but was near-certain on the
  other four AI images.
- umm-maybe only crossed 0.5 on two AI images, but its ranking (AUC) was higher;
  its probabilities are under-confident and uncalibrated.
- `umm-maybe` is a 2022 art detector. Its own model card recommends a newer
  model (`Organika/sdxl-detector`) for modern generators, and it must not be
  fine-tuned under its CC BY-ND 4.0 model-card terms.

---

## Metrics

| Metric | Meaning |
|---|---|
| Accuracy | Correct calls at the stated threshold. |
| Balanced accuracy | Mean of AI recall and real recall; robust to class imbalance. |
| AI recall | Fraction of AI images called AI. |
| Real recall | Fraction of real images called real (1 − false-positive rate). |
| Precision / F1 | AI-class precision and F1. |
| ROC-AUC | Ranking quality independent of threshold. |
| TPR @ 5% FPR | Recall when at most 5% of real images are falsely flagged. |
| Mean P(AI) | Average AI probability per class; shows calibration/confidence. |
| Latency | Mean per-image inference after model load. |

The modern runner also records Brier score, threshold sweeps, and five-fold
cross-validated threshold selection.

---

## How to make this a real evaluation

1. License a diverse dataset spanning generators, resolutions, and
   post-processing.
2. Hold out entire generator families for testing.
3. Include hard negatives: illustrations, edited real photos, screenshots,
   memes, low-light and compressed images.
4. Calibrate thresholds on a development split; freeze them before testing.
5. Report per-class recall, false-positive rates, AUC, calibration (ECE), and
   confidence intervals.
6. Evaluate on the hardware and preprocessing used in production.
7. Repeat over time — generators and compression pipelines change.

Do not present these smoke evaluations as product accuracy.

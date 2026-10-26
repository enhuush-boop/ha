# Models, free local LLMs, and fine-tuning guidance

This document explains the model choices in TAYO, how the benchmark should
be read, which free/local models are practical on a 6 GB GPU, and what to
fine-tune (and what not to).

---

## 1. Models used by TAYO

| Role | Model | Params | Download | License | Default |
|---|---|---:|---:|---|---|
| Image (primary) | `abacusai/Smaug-AI-Image-Detector` | ~439M | ~870 MB | Apache-2.0 | ✅ |
| Image (fallback) | `Ateeqq/ai-vs-human-image-detector` | ~93M | ~371 MB | Apache-2.0 | auto if Smaug fails |
| Image (optional) | `umm-maybe/AI-image-detector` | ~87M | ~347 MB | CC BY-ND 4.0 (card) / CC-BY-4.0 (Hub metadata) | ❌ |
| Text | `rasbt/ai-text-detector-modernbert` | ~149M | ~299 MB | Apache-2.0 | ✅ |

### Smaug — primary image model

`abacusai/Smaug-AI-Image-Detector` is a 2026 EmbeddingGemma-2 vision encoder with
a one-unit classification head. It was trained specifically for modern
generators and its model card advertises coverage of Nano Banana/Gemini,
Imagen, GPT-Image, Midjourney, FLUX, and other current families. It is
Apache-2.0, ships with a small Python wrapper, and runs on CPU, CUDA, or Apple
MPS.

Why it is the default:

- On our 300-image Google-focused set it reached **99.0% accuracy, 98.5% AI
  recall, 100% real recall, and ROC-AUC 0.99995** at the default 0.5 threshold.
- It caught 97/100 Nano Banana and 100/100 Imagen 4 images with zero false
  positives on 100 real photos.
- It is slower (~1.9 s/image on CPU at `crops=1`) and larger (~870 MB), but the
  backend keeps it warm and the accuracy difference is decisive.

The wrapper is vendored in `backend/app/services/smaug_image_detect.py` so the
server does not execute Python downloaded from the model hub at runtime. The
model weights are still downloaded from Hugging Face on first use. `crops=1` is
the default; `crops=3` is slower and the model card's best-accuracy mode.

### Ateeqq — automatic fallback

SigLIP image classifier fine-tuned on 60,000 AI-generated + 60,000 human images
(120k total). The author reports 99.2% test accuracy, but the card also notes
that **users report overfitting**. It is not a forensic detector and has no
provenance capability.

It is fast (~143 ms/image on CPU), Apache-2.0, and small (~371 MB), so it is a
useful fallback when Smaug cannot load. It is **not** the primary model anymore:
on the same 300-image set it scored 90.7% accuracy, 91.5% AI recall, and 89.0%
real recall, with 11 false positives and 17 misses. Its errors were clustered:
some real photos received scores near 0.999, while many Nano Banana images
received scores below 0.01. That pattern matches the Google-image false negative
that prompted this evaluation.

### umm-maybe — optional, disabled

A Swin-B classifier created in **October 2022** for AI art vs. real images. The
card reports 94.2% validation accuracy. It explicitly says:

- its scope is artistic images, not deepfake photos;
- general imagery (webcams, screenshots) may confuse it;
- training data predates Midjourney 5, SDXL, and DALL·E 3;
- the author now recommends an updated model
  (`Organika/sdxl-detector`) for modern generators.

**License caution:** the Hub metadata says `cc-by-4.0`, but the model card's
License Notice says **CC BY-ND 4.0** (NoDerivatives). We treat the stricter
reading as authoritative: use it for evaluation, do **not** fine-tune it or
redistribute modified weights, and keep it disabled by default.

### rasbt ModernBERT — text model

A fully fine-tuned `ModernBERT-base` binary classifier (`HUMAN` = 0, `AI` = 1)
trained on `rasbt/human-vs-ai-50k`. Supports up to 8,192 tokens; TAYO caps
chunks at 2,048 for CPU memory. The model card reports 99.71% validation
accuracy and provides a temperature-scaling value in `detector-config.json`
(1.566…), which TAYO applies.

**Text detectors are the least reliable part of this stack.** Validation
accuracy on a matched dataset does not transfer to edited, paraphrased,
translated, or out-of-domain text. Use it as a conversation starter, never as
evidence of misconduct.

ModernBERT is used for English text only. For detected Mongolian Cyrillic or
informal Latin galig, TAYO returns Unicode-aware style statistics and an
uncertain result without an AI probability. Adding an AI-authorship verdict
requires a detector trained and evaluated on independently collected Mongolian
human and AI texts in both writing forms.

---

## 2. Reading the 300-image Google-focused evaluation

Full results: [`benchmark/results/modern_image_eval.md`](../benchmark/results/modern_image_eval.md).

Dataset: 100 Nano Banana (Gemini 2.5 Flash Image), 100 Imagen 4, and 100 real
Unsplash photos. The AI images are clean 1024×1024 outputs; real photos are
768×768. This measures generator recognition, not social-media robustness.

| Model | Accuracy | AI recall | Real recall | ROC-AUC | TPR @ 5% FPR | Mean P(AI), real | Mean P(AI), AI | Latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Smaug | **99.0%** | **98.5%** | **100%** | **0.99995** | **100%** | 0.3% | 98.5% | 1.9 s |
| Ateeqq | 90.7% | 91.5% | 89.0% | 0.93807 | 70% | 11.1% | 91.5% | 143 ms |

Per source, at 0.5:

| Model | Nano Banana | Imagen 4 | Real photos |
|---|---:|---:|---:|
| Smaug | 97/100 detected | 100/100 detected | 100/100 correctly not flagged |
| Ateeqq | 83/100 detected | 100/100 detected | 89/100 correctly not flagged |

### What the numbers mean

- Smaug made **three false negatives**, all Nano Banana, with scores 0.051,
  0.082, and 0.161. It made **zero false positives** on 100 real photos.
- Ateeqq made **17 false negatives and 11 false positives**. Its score
  distribution is badly separated for this generator mix: many missed Nano
  Banana images are below 0.01, while several real photos are above 0.99.
- A post-hoc threshold of 0.08 catches two of Smaug's three misses with no
  extra false positive on this set, but this is tuning on the test data.
  Five-fold cross-validated threshold selection did **not** beat the default
  0.5 (99.0% vs 99.25% balanced accuracy in-sample). Keep 0.5 unless you have a
  separate calibration set that justifies a change.
- The model card's Reddit-based benchmark reports 86.7% AI recall at a 5% FPR.
  Our clean Google-generator set is easier, so do not expect 98.5% recall on
  compressed social-media images.

### Recompression stress check

To approximate messaging/social-media recompression, the first 50 images per
source were resized to a 512 px shorter side and re-encoded at JPEG quality 60.

| Model | Accuracy | AI recall | Real recall | False positives |
|---|---:|---:|---:|---:|
| Smaug | **98.0%** | 97.0% | **100%** | 0/50 |
| Ateeqq | 94.0% | 98.0% | 86.0% | 7/50 |

Smaug's three remaining misses were Nano Banana images at 0.389, 0.050, and
0.015. Ateeqq gained AI recall but produced seven near-certain false positives
on real photos. The stress subset is degraded from the same 300 images, not an
independent sample, so it is a robustness check rather than a second benchmark.
Full report: [`benchmark/results/stress_image_eval.md`](../benchmark/results/stress_image_eval.md).

### Why we did not train on these 300 images

A 99% result does not mean the problem is solved, and fine-tuning on 300 images
would mostly memorize this prompt distribution. The three misses are
near-boundary Nano Banana examples; a 300-image fine-tune would have no way to
tell whether it learned generator artifacts or the specific prompts and
compositions. A defensible next step would be:

1. Collect a larger, licensed set with multiple prompts per generator.
2. Hold out entire generators and post-processing pipelines.
3. Calibrate on a development split and freeze the threshold.
4. Only then consider a linear probe/LoRA on Smaug features if a specific
   generator still fails.

For this demo, switching to a newer pretrained detector is better supported by
the evidence than training on a small evaluation set.

### Older 10-image smoke test

The original smoke test is still in `benchmark/results/image_model_benchmark.md`.
On that tiny stylized set Ateeqq scored 90% accuracy / 80% AI recall and
umm-maybe scored 70% / 40%. It is useful for integration checks but not for
model selection; the 300-image evaluation supersedes it.

### Reproduce

```bash
# 300-image modern evaluation
.venv/bin/python benchmark/build_modern_eval.py
.venv/bin/python benchmark/evaluate_modern.py

# older 10-image smoke test
.venv/bin/python benchmark/download_samples.py
.venv/bin/python benchmark/benchmark.py
```

The modern runner records per-image predictions, accuracy, balanced accuracy,
AI/real recall, precision/F1, ROC-AUC, TPR at 5% FPR, Brier score, threshold
sweeps, 5-fold cross-validated threshold selection, latency, parameter counts,
load time, and environment versions.

---

## 3. Free and local LLMs (Ollama)

TAYO uses an LLM only to rewrite a fact-check summary from retrieved
evidence. It is optional. The deterministic template is safer and is used when
Ollama is absent or fails.

### Install

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:4b
```

`backend/.env`:

```dotenv
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b
```

Restart the backend. Test:

```bash
curl -s http://127.0.0.1:11434/api/tags | python3 -m json.tool
```

### Practical tags for a 6 GB GPU / 8 GB RAM

| Tag | Approx. size | Use | Notes |
|---|---:|---|---|
| `qwen3:4b` | ~2.5 GB | General summary/reasoning | Recommended default; Q4 fits 6 GB with context. |
| `phi4-mini` | ~2.5 GB | Compact reasoning | Strong small model; test instruction-following. |
| `gemma3:4b` | ~3.4 GB | General + vision | 6 GB is tight with long context; close other apps. |
| `qwen2.5vl:3b` | ~3.2 GB | Vision experiments | Not wired into TAYO; could caption frames later. |
| `llama3.2:3b` | ~2 GB | Fast summaries | Lower quality on nuanced evidence. |
| `nomic-embed-text` | ~274 MB | Embeddings for RAG | Good first embedding model. |
| `mxbai-embed-large` | ~670 MB | Higher-quality embeddings | Slower on CPU. |
| `qwen3:8b` | ~4.7 GB | Better reasoning | Tight on 6 GB VRAM; may spill to CPU. |

Rules of thumb:

- 6 GB VRAM comfortably runs **3–4B Q4** models with a few thousand tokens of
  context. 7–8B is borderline and often spills to CPU.
- WSL2 with 7.7 GB total RAM is the bigger constraint when the backend models,
  browser, and Ollama run together. Stop Ollama when benchmarking image/video.
- CPU-only Ollama works for short summaries but is slow; keep context small
  (`num_ctx=2048–4096`).
- Quantized models trade accuracy for memory. For fact-check summaries, use
  low temperature and require citations to retrieved evidence.

### Free hosted options (not integrated)

If local hardware is insufficient, free tiers exist at Hugging Face Inference
Providers, Google AI Studio, Groq, and OpenRouter (model availability and terms
change). They are **not** used by this demo because they send claims/evidence to
a third party. If you add one:

- keep the API key in the backend, never in the extension;
- send only the minimum text;
- log which provider/model produced the summary;
- enforce the same “evidence only, no invented facts” prompt;
- add rate limits and a privacy notice.

Paid API costs are not required for this project. Local inference has zero
marginal token cost.

---

## 4. Local fine-tuning on this hardware

### What the hardware can do

Test machine: Intel i5-12400F, 7.7 GB RAM visible under WSL2, RTX 2060 6 GB,
CPU PyTorch. Rough guidance:

| Task | Feasible locally? | Notes |
|---|---|---|
| Full fine-tune 7B | ❌ | Weights + optimizer + activations do not fit 6 GB. |
| QLoRA 7–8B | ⚠️ borderline | 4-bit weights ~4–5 GB leaves little room; short context only; likely unstable. |
| QLoRA 3–4B | ✅ with care | Batch 1, gradient checkpointing, 1–2k context, 4-bit optimizer. |
| QLoRA 1.5B | ✅ | Comfortable; good for experiments. |
| Full fine-tune 100–350M classifier | ✅ / ⚠️ | Small ModernBERT/BERT classifiers can fit with batch 1–4; CPU is slow. |
| LoRA on a 90M image classifier | ✅ | Freeze the backbone, train attention/MLP adapters or a linear head. |
| Linear probe on Smaug/SigLIP/DINOv2 features | ✅ | Highest ROI for images: extract embeddings, train logistic regression. |
| RAG / embeddings / reranker | ✅ | No LLM training; best first investment. |

Training time is workload-dependent. A few thousand examples for 1–3 epochs on
a 3B QLoRA can take hours; do not promise “minutes”. Start with a 1.5B model and
a small, clean dataset to validate the pipeline.

### Do not start with fine-tuning

For a newsroom tool, the highest-value work is usually:

1. **A trustworthy evidence corpus + retrieval.** Collect allowlisted sources
   (WHO, CDC, NIH, NASA, IPCC, Reuters/AP fact checks, Full Fact, Snopes,
   PolitiFact), store metadata (publisher, date, URL, claim), and retrieve
   passages. Return citations and abstain when evidence is weak.
2. **A claim–evidence stance model.** Given a claim and retrieved passages,
   classify support/refute/not-enough-info. Small classifiers or an LLM with
   constrained output can do this; evaluate against human labels.
3. **Calibration and abstention.** A detector that says “Not Sure” is more
   useful than one that is confidently wrong. Tune thresholds on a held-out,
   diverse set and report per-domain performance.
4. **Only then:** domain-adapt a detector if you have a large, licensed,
   representative labeled set. Otherwise you are fitting noise.

### Recommended fine-tuning roadmap

#### Phase A — retrieval and embeddings (no LLM training)

- Build a trusted-source index with hybrid BM25 + dense retrieval.
- Fine-tune a small embedding model or reranker on claim–evidence pairs if you
  have labels. `bge-m3`, `nomic-embed-text`, and `bge-reranker-v2-m3` are good
  starting points.
- Evaluate retrieval recall@k and citation faithfulness. This is measurable and
  directly improves the product.

#### Phase B — text detector adaptation

- Base: `rasbt/ai-text-detector-modernbert` or a smaller DeBERTa/ModernBERT.
- Data: in-domain newsroom text, human edits, translations, non-native English,
  and modern LLM outputs. Include hard negatives: formal human writing,
  outlines, and mixed human/AI drafts.
- Method: LoRA/QLoRA (rank 8–32), freeze most of the model, class-weighted loss,
  early stopping, temperature calibration on a held-out set.
- Evaluate cross-domain and by subgroup. Report false-positive rates, not just
  accuracy. A detector that flags non-native English speakers is unusable.

#### Phase C — image detector adaptation

- Prefer a **linear probe** or LoRA on Smaug/SigLIP/DINOv2 features over full
  fine-tuning; 6 GB is enough for a frozen backbone plus a small head.
- Data: licensed real photos, generated images from multiple generator families,
  and hard negatives (illustrations, heavily edited photos, screenshots,
  compressed social-media images).
- Hold out entire generators, not just random images. Report performance on the
  holdout; this is the number that matters.
- Calibrate probabilities (temperature scaling / isotonic regression) and
  evaluate at the operating threshold you will actually use.
- Do not fine-tune `umm-maybe` under its no-derivatives license.

#### Phase D — C2PA and provenance (no training)

- Integrate the official C2PA SDK, verify signatures, and check the signer trust
  list. Presence-only detection is a demo shortcut.
- Combine provenance (strong when valid) with classifiers (weak, probabilistic).

### Minimal QLoRA sketch (illustrative, not executed here)

```python
import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
    Trainer, TrainingArguments,
)

model_id = "Qwen/Qwen3-1.7B"  # choose a model that fits your VRAM
bnb = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_use_double_quant=True,
)
tokenizer = AutoTokenizer.from_pretrained(model_id)
model = AutoModelForCausalLM.from_pretrained(
    model_id, quantization_config=bnb, device_map="auto"
)
model = prepare_model_for_kbit_training(model)
model = get_peft_model(
    model,
    LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    ),
)

dataset = load_dataset("json", data_files="train.jsonl", split="train")
def format_example(row):
    messages = [
        {"role": "system", "content": "Answer using only the supplied evidence."},
        {"role": "user", "content": row["prompt"]},
        {"role": "assistant", "content": row["answer"]},
    ]
    return {"text": tokenizer.apply_chat_template(messages, tokenize=False)}

dataset = dataset.map(format_example)
tokenized = dataset.map(
    lambda row: tokenizer(row["text"], truncation=True, max_length=1024),
    remove_columns=dataset.column_names,
)

trainer = Trainer(
    model=model,
    train_dataset=tokenized,
    args=TrainingArguments(
        output_dir="out/qwen3-1.7b-lora",
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,
        gradient_checkpointing=True,
        num_train_epochs=1,
        learning_rate=2e-4,
        bf16=True,
        logging_steps=10,
        save_strategy="epoch",
        optim="paged_adamw_8bit",
    ),
)
trainer.train()
trainer.model.save_pretrained("out/qwen3-1.7b-lora")
```

Notes:

- This is a template. Pin versions, validate the chat template, mask prompt
  tokens from the loss, and evaluate before/after.
- `bitsandbytes` support on WSL2 is workable but can be version-sensitive; use
  Linux and a recent CUDA build.
- Start with 512–1,024 token sequences. Long contexts explode memory.
- Save only adapters (tens of MB), not full merged weights, during experiments.
- Never train on the benchmark set. Keep a locked test set.

### Fine-tuning a text classifier instead

For a ModernBERT/DeBERTa classifier, full fine-tuning of a 100–150M model is
possible with a small batch and gradient checkpointing, but LoRA is cheaper.
Use `AutoModelForSequenceClassification`, freeze the encoder, train a linear
head first, then unfreeze the last layers if needed. Class imbalance and
domain shift matter more than architecture.

Evaluate:

- accuracy and **per-class recall**;
- false-positive rate on human formal writing and non-native English;
- ROC-AUC and calibration (ECE);
- performance by generator family, length, language, and edit type;
- abstention behavior near the threshold.

A model with 99% accuracy on a matched split and 70% on newsroom text is not
ready.

### Image fine-tuning: linear probe first

```python
# Pseudocode: freeze the backbone, extract embeddings, train a small head.
from transformers import AutoModel, AutoImageProcessor
import torch

backbone = AutoModel.from_pretrained("google/siglip-base-patch16-224").eval()
# Extract pooled embeddings for train/val images, then:
from sklearn.linear_model import LogisticRegression
clf = LogisticRegression(max_iter=2000, class_weight="balanced")
clf.fit(train_embeddings, train_labels)
```

If a linear probe is insufficient, try LoRA on attention layers. Full
fine-tuning of a 90M image model can fit on 6 GB at low resolution and batch 1,
but it is easier to overfit and harder to reproduce. Hold out generators.

---

## 5. Data sources and licensing

Always verify the license and terms on the dataset page. Some are
research-only, some prohibit redistribution, and deepfake datasets often
contain real people's faces.

### Fact-checking / claim verification

- **FEVER** — claim/evidence pairs, Wikipedia.
- **SciFact** — scientific claims and abstracts.
- **Climate-FEVER** — climate claims.
- **AVeriTeC** — real-world claims with evidence and questions.
- **HoVer** — multi-hop claim verification.
- **PubHealth** — public-health claims.
- **LIAR** — political statements with labels (license/terms vary).
- **ClaimBuster** — check-worthiness data.

Use them to train retrieval/stance components, not as a source of truth. Build
your own newsroom evaluation set with editor-reviewed labels.

### AI-generated text

- **HC3** (`Hello-SimpleAI/HC3`) — human vs. ChatGPT answers.
- **M4** — multi-generator, multi-domain, multilingual.
- **RAID** — robust AI detection benchmark with adversarial attacks.
- **`rasbt/human-vs-ai-50k`** — training data for the bundled text model.
- **SemEval / AuTexTification** shared tasks — domain and language diversity.

Beware: detector datasets age quickly. A model trained on 2023 outputs may not
recognize 2026 models.

### AI-generated images

- **CIFAKE** — small real vs. AI images; easy and low-resolution.
- **GenImage** — multiple generators; check license.
- **ArtiFact** — large, diverse generator set; check terms.
- **DiffusionDB** — Stable Diffusion prompts/images; check license and content.
- **Synthbuster** — modern generator benchmark.
- **Documentation outputs** — convenient but not a representative evaluation
  set.
- **`bitmind/nano-banana`** (MIT) — Gemini 2.5 Flash Image outputs; used for the
  100-image Nano Banana slice.
- **`Rapidata/Imagen4_t2i_human_preference`** (CDLA-Permissive-2.0) — Imagen 4
  outputs; used for the 100-image Imagen slice.
- **picsum.photos / Unsplash** (Unsplash License) — real-photo control set.

For deepfake video (not used here): **FaceForensics++**, **DFDC**,
**Celeb-DF**, **DeeperForensics**. These require access agreements and raise
consent/privacy issues.

### Building a newsroom evaluation set

- License and document every source.
- Include hard negatives: real photos with edits, illustrations, screenshots,
  memes, low-light/compressed images, non-Western faces and settings.
- Include generator holdouts and post-processing (JPEG, resize, crop, filters).
- Include human-written formal text, non-native English, and mixed drafts.
- Store provenance and consent; minimize personal data.
- Have at least two annotators for subjective labels; measure agreement.
- Keep a locked test set and never tune on it.

---

## 6. Evaluation that matters

### Image/video

- Per-class recall at the **deployed threshold**, not just accuracy.
- ROC-AUC for ranking, plus calibration (reliability diagram, ECE).
- Generator holdout performance (train on SDXL, test on FLUX, etc.).
- Robustness to JPEG, resize, crop, screenshot, filters, and memes.
- False-positive rate on illustrations, art, and edited real photos.
- Subgroup analysis (skin tone, gender, geography) where lawful and ethical.
- Video: temporal localization (can it find the manipulated segment?), audio,
  lip-sync, compression robustness.

### Text

- Cross-domain and cross-generator performance.
- False positives on formal human writing and non-native English.
- Robustness to paraphrase, translation, back-translation, and “humanizing”.
- Calibration and abstention.
- Length effects (short texts are unreliable).

### Fact-checking

- Retrieval recall@k, evidence precision, and citation faithfulness.
- Stance accuracy and verdict calibration.
- Abstention quality: does “Not Sure” correlate with missing/conflicting
  evidence?
- Freshness: does the answer account for the claim date and newer evidence?
- Human evaluation by editors; track corrections and harm.

### Benchmark hygiene

- Report n and confidence intervals.
- Report all thresholds you tried, or pre-register one.
- Separate development and test sets.
- Record model revisions and environment versions.
- Do not claim “state of the art” from a small demo.

---

## 7. Licensing and responsible use

- **Smaug:** Apache-2.0. The vendored wrapper keeps its license in
  `third_party/smaug/LICENSE`. Model weights are downloaded separately.
- **Ateeqq:** Apache-2.0; attribution required; keep the license notice.
- **rasbt ModernBERT:** Apache-2.0.
- **umm-maybe:** treat as CC BY-ND 4.0 (no derivatives). Do not fine-tune,
  merge, or redistribute modified weights. Evaluation and distribution with
  attribution are allowed under the card's terms; if in doubt, omit it.
- **Datasets:** licenses vary; some forbid commercial use or redistribution.
  Verify before training or shipping.
- **Deepfake datasets:** may contain real people; obtain ethics/IRB guidance,
  minimize retention, and do not publish identifying material.
- **Outputs:** a classifier score is not evidence of intent, authorship, or
  criminal conduct. Do not accuse individuals. Publish uncertainty and evidence
  links; keep a human editor in the loop.

---

## 8. Bottom line

- Use **Smaug** as the primary image model and keep **Ateeqq** as an automatic
  fallback. Keep **umm-maybe** disabled for evaluation only.
- Use **ModernBERT** for text triage, with explicit false-positive warnings.
- Add a trusted-source **RAG pipeline** before training anything.
- If you fine-tune on this hardware, start with **1.5–3B QLoRA** or a **linear
  probe/LoRA on a small classifier**; 7B full fine-tuning is not realistic on
  6 GB.
- Calibrate on a diverse, licensed, generator-held-out set. The 300-image
  benchmark in this repo is still a smoke evaluation, not a universal accuracy
  claim.

#!/usr/bin/env python3
"""Compare the two image classifiers on a tiny local sample set.

Models:
  * Ateeqq/ai-vs-human-image-detector  (~93M, SigLIP, Apache-2.0)
  * umm-maybe/AI-image-detector        (~87M, Swin-B, old AI-art model)

The second model is evaluation-only. Its model card says CC BY-ND 4.0 while
the Hub metadata says CC BY 4.0; either way, do not fine-tune or redistribute
modified weights.

Usage:
    .venv/bin/python benchmark/download_samples.py
    .venv/bin/python benchmark/benchmark.py
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"

MODEL_INFO = {
    "Ateeqq/ai-vs-human-image-detector": {
        "architecture": "SigLIP for image classification",
        "parameters": "~93M",
        "download_size": "~371 MB",
        "license": "Apache-2.0",
        "training_data": "~120k AI/real images (per model card)",
        "reported_accuracy": "99.2% on the author's test split",
        "role": "Primary model in TAYO; enabled by default.",
        "caveat": "Hub users report overfitting; it is not a forensic detector.",
    },
    "umm-maybe/AI-image-detector": {
        "architecture": "Swin-B for image classification",
        "parameters": "~87M",
        "download_size": "~347 MB",
        "license": "Hub metadata: CC-BY-4.0; model card: CC BY-ND 4.0 (treat as no-derivatives)",
        "training_data": "AI art vs. real images, published October 2022 (per model card)",
        "reported_accuracy": "94.2% on the author's validation split",
        "role": "Secondary model; disabled by default. Evaluation only.",
        "caveat": "Trained before modern generators; explicitly not a deepfake detector.",
    },
}


def ai_index(labels: dict[int, str]) -> int:
    for index, label in labels.items():
        text = str(label).strip().lower()
        if text in {"ai", "artificial", "fake", "generated", "synthetic"}:
            return int(index)
    for index, label in labels.items():
        if "ai" == str(label).lower() or "artificial" in str(label).lower():
            return int(index)
    return 0


def choose_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def load_model(model_id: str, device: str):
    from transformers import AutoImageProcessor, AutoModelForImageClassification

    started = time.perf_counter()
    processor = AutoImageProcessor.from_pretrained(model_id)
    model = AutoModelForImageClassification.from_pretrained(model_id)
    model.to(device)
    model.eval()
    load_seconds = time.perf_counter() - started
    labels = {int(key): str(value) for key, value in model.config.id2label.items()}
    params = sum(parameter.numel() for parameter in model.parameters())
    return processor, model, labels, load_seconds, params


def predict(processor, model, labels: dict[int, str], path: Path, device: str) -> dict:
    import torch

    with Image.open(path) as image:
        rgb = image.convert("RGB")

    started = time.perf_counter()
    inputs = processor(images=rgb, return_tensors="pt")
    inputs = {key: value.to(device) for key, value in inputs.items()}
    with torch.inference_mode():
        logits = model(**inputs).logits
        probabilities = torch.softmax(logits.float(), dim=-1)[0].cpu().tolist()
    latency_ms = (time.perf_counter() - started) * 1000

    index = ai_index(labels)
    ai_probability = float(probabilities[index])
    best_index = max(range(len(probabilities)), key=lambda i: probabilities[i])
    return {
        "ai_probability": round(ai_probability, 6),
        "predicted_label": labels.get(best_index, str(best_index)),
        "scores": {
            labels.get(i, str(i)): round(float(value), 6)
            for i, value in enumerate(probabilities)
        },
        "latency_ms": round(latency_ms, 1),
    }


def collect_samples(data_dir: Path) -> list[dict]:
    samples: list[dict] = []
    for truth, folder in (("real", data_dir / "real"), ("ai", data_dir / "ai")):
        if not folder.exists():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                samples.append({"path": path, "truth": truth, "name": path.name})
    if not samples:
        raise SystemExit(
            f"No images found under {data_dir}. Run benchmark/download_samples.py first."
        )
    return samples


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))
    return ordered[index]


def compute_metrics(predictions: list[dict]) -> dict[str, Any]:
    from sklearn.metrics import roc_auc_score

    truths = [1 if item["truth"] == "ai" else 0 for item in predictions]
    scores = [item["ai_probability"] for item in predictions]
    calls = [1 if score >= 0.5 else 0 for score in scores]

    tp = sum(1 for truth, call in zip(truths, calls) if truth == 1 and call == 1)
    tn = sum(1 for truth, call in zip(truths, calls) if truth == 0 and call == 0)
    fp = sum(1 for truth, call in zip(truths, calls) if truth == 0 and call == 1)
    fn = sum(1 for truth, call in zip(truths, calls) if truth == 1 and call == 0)

    ai_total = tp + fn
    real_total = tn + fp
    ai_recall = tp / ai_total if ai_total else 0.0
    real_recall = tn / real_total if real_total else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * precision * ai_recall / (precision + ai_recall) if precision + ai_recall else 0.0
    latencies = [item["latency_ms"] for item in predictions]

    try:
        auc = float(roc_auc_score(truths, scores))
    except ValueError:
        auc = float("nan")

    real_scores = [score for truth, score in zip(truths, scores) if truth == 0]
    ai_scores = [score for truth, score in zip(truths, scores) if truth == 1]

    return {
        "n": len(predictions),
        "accuracy": round((tp + tn) / len(predictions), 4),
        "balanced_accuracy": round((ai_recall + real_recall) / 2, 4),
        "ai_recall": round(ai_recall, 4),
        "real_recall": round(real_recall, 4),
        "ai_precision": round(precision, 4),
        "ai_f1": round(f1, 4),
        "roc_auc": round(auc, 4) if auc == auc else None,
        "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        "mean_ai_probability_real": round(statistics.mean(real_scores), 4) if real_scores else None,
        "mean_ai_probability_ai": round(statistics.mean(ai_scores), 4) if ai_scores else None,
        "mean_latency_ms": round(statistics.mean(latencies), 1) if latencies else None,
        "p95_latency_ms": round(percentile(latencies, 0.95), 1) if latencies else None,
    }


def benchmark_model(model_id: str, samples: list[dict], device: str) -> dict:
    print(f"\nLoading {model_id} on {device} …", flush=True)
    processor, model, labels, load_seconds, params = load_model(model_id, device)
    print(
        f"  labels={labels} params={params/1e6:.1f}M load={load_seconds:.1f}s",
        flush=True,
    )

    # Warm-up run, excluded from latency statistics.
    try:
        predict(processor, model, labels, samples[0]["path"], device)
    except Exception as exc:
        print(f"  warm-up failed: {exc}", file=sys.stderr)

    predictions = []
    for sample in samples:
        result = predict(processor, model, labels, sample["path"], device)
        result.update({"name": sample["name"], "truth": sample["truth"]})
        predictions.append(result)
        flag = "AI " if result["ai_probability"] >= 0.5 else "REAL"
        print(
            f"  {sample['truth']:>4} -> {flag:>4} "
            f"P(AI)={result['ai_probability']:.3f}  {sample['name']}",
            flush=True,
        )

    metrics = compute_metrics(predictions)
    result = {
        "model_id": model_id,
        "info": MODEL_INFO.get(model_id, {}),
        "labels": labels,
        "parameters": params,
        "load_seconds": round(load_seconds, 2),
        "metrics": metrics,
        "predictions": predictions,
    }

    del model, processor
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return result


def write_markdown(report: dict, path: Path) -> None:
    models = report["models"]
    lines = [
        "# Image model benchmark",
        "",
        f"Generated: {report['generated_at']}  ",
        f"Device: `{report['environment']['device']}` · "
        f"Torch `{report['environment']['torch']}` · "
        f"Transformers `{report['environment']['transformers']}` · "
        f"Python `{report['environment']['python']}`",
        "",
        "> **Smoke test, not a scientific evaluation.** This set has only "
        f"{report['dataset']['count']} images ({report['dataset']['ai']} AI, "
        f"{report['dataset']['real']} real). The AI images are generated "
        "documentation examples and the real images are Unsplash photos. "
        "Results do not generalize to photorealistic deepfakes, social-media "
        "recompression, or modern generators.",
        "",
        "## Results",
        "",
        "| Model | Accuracy | Balanced accuracy | AI recall | Real recall | ROC-AUC | Mean P(AI), real | Mean P(AI), AI | Mean latency | Params |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in models:
        m = model["metrics"]
        lines.append(
            f"| `{model['model_id']}` | {m['accuracy']:.1%} | "
            f"{m['balanced_accuracy']:.1%} | {m['ai_recall']:.1%} | "
            f"{m['real_recall']:.1%} | "
            f"{m['roc_auc']:.3f} | "
            f"{m['mean_ai_probability_real']:.1%} | "
            f"{m['mean_ai_probability_ai']:.1%} | "
            f"{m['mean_latency_ms']:.0f} ms | "
            f"{model['parameters']/1e6:.1f}M |"
        )

    lines += ["", "## Per-image predictions", ""]
    header = "| Sample | Truth | " + " | ".join(
        f"`{model['model_id'].split('/')[-1]}` P(AI)" for model in models
    ) + " |"
    lines.append(header)
    lines.append("|---|---|" + "---:|" * len(models))
    names = [prediction["name"] for prediction in models[0]["predictions"]]
    for index, name in enumerate(names):
        truth = models[0]["predictions"][index]["truth"]
        cells = []
        for model in models:
            prediction = model["predictions"][index]
            call = "AI" if prediction["ai_probability"] >= 0.5 else "real"
            cells.append(f"{prediction['ai_probability']:.1%} ({call})")
        lines.append(f"| {name} | {truth} | " + " | ".join(cells) + " |")

    lines += ["", "## Model notes", ""]
    for model in models:
        info = model["info"]
        lines.append(f"### `{model['model_id']}`")
        lines.append("")
        lines.append(f"- Architecture: {info.get('architecture', 'n/a')}")
        lines.append(f"- License: {info.get('license', 'n/a')}")
        lines.append(f"- Reported accuracy: {info.get('reported_accuracy', 'n/a')}")
        lines.append(f"- Role in TAYO: {info.get('role', 'n/a')}")
        lines.append(f"- Caveat: {info.get('caveat', 'n/a')}")
        lines.append(f"- Load time: {model['load_seconds']} s")
        lines.append("")

    lines += [
        "## Reproduce",
        "",
        "```bash",
        ".venv/bin/python benchmark/download_samples.py",
        ".venv/bin/python benchmark/benchmark.py",
        "```",
        "",
        "Replace the samples with a licensed, demographically and generator-diverse "
        "evaluation set before drawing conclusions. Report confidence intervals: "
        "with n=10 they are very wide.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=ROOT / "data", help="Folder with real/ and ai/"
    )
    parser.add_argument(
        "--out-dir", type=Path, default=RESULTS_DIR, help="Where to write results"
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["Ateeqq/ai-vs-human-image-detector", "umm-maybe/AI-image-detector"],
    )
    parser.add_argument("--device", default="auto", help="auto, cpu, or cuda")
    args = parser.parse_args()

    import torch
    import transformers

    samples = collect_samples(args.data_dir)
    device = choose_device(args.device)
    print(f"{len(samples)} samples · device={device}")

    models = [benchmark_model(model_id, samples, device) for model_id in args.models]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {
            "count": len(samples),
            "ai": sum(1 for sample in samples if sample["truth"] == "ai"),
            "real": sum(1 for sample in samples if sample["truth"] == "real"),
            "note": "Smoke-test set; not a scientific benchmark.",
            "sources": {
                "real": "picsum.photos (Unsplash photos), 512x512 crops",
                "ai": "huggingface/documentation-images diffusers generated examples",
            },
        },
        "environment": {
            "device": device,
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "platform": platform.platform(),
        },
        "models": models,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "image_model_benchmark.json"
    markdown_path = args.out_dir / "image_model_benchmark.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, markdown_path)

    print("\nSummary")
    for model in models:
        metrics = model["metrics"]
        print(
            f"  {model['model_id']}: acc={metrics['accuracy']:.1%} "
            f"balanced={metrics['balanced_accuracy']:.1%} "
            f"AUC={metrics['roc_auc']} "
            f"latency={metrics['mean_latency_ms']} ms"
        )
    print(f"\nWrote {json_path}")
    print(f"Wrote {markdown_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

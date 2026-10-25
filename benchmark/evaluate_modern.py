#!/usr/bin/env python3
"""Evaluate image AI detectors on modern Google-generated images.

Default data layout (created by benchmark/build_modern_eval.py):

    data/modern/ai/nano_banana/*.jpg    # Gemini 2.5 Flash Image ("Nano Banana")
    data/modern/ai/imagen4/*.jpg        # Imagen 4
    data/modern/real/picsum/*.jpg       # Unsplash photos via picsum.photos

Examples:
    .venv/bin/python benchmark/evaluate_modern.py --limit 20
    .venv/bin/python benchmark/evaluate_modern.py --models Ateeqq/ai-vs-human-image-detector
    .venv/bin/python benchmark/evaluate_modern.py --models abacusai/Smaug-AI-Image-Detector --crops 1
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
DEFAULT_DATA = ROOT / "data" / "modern"
RESULTS_DIR = ROOT / "results"

MODEL_INFO = {
    "Ateeqq/ai-vs-human-image-detector": {
        "architecture": "SigLIP image classifier (~93M)",
        "license": "Apache-2.0",
        "note": "Current TAYO model. Trained on 120k AI/real images.",
    },
    "abacusai/Smaug-AI-Image-Detector": {
        "architecture": "EmbeddingGemma-2 vision encoder + linear head (~439M)",
        "license": "Apache-2.0",
        "note": "2026 detector; model card claims Nano Banana Pro, Imagen, GPT-Image 2 coverage.",
    },
    "haywoodsloan/ai-image-detector-deploy": {
        "architecture": "SwinV2 image classifier",
        "license": "Apache-2.0",
        "note": "Community comparison model.",
    },
    "umm-maybe/AI-image-detector": {
        "architecture": "Swin-B AI-art detector (~87M)",
        "license": "CC BY-ND 4.0 (model card)",
        "note": "2022 art detector; evaluation only.",
    },
}

LABELS = {"ai": 1, "real": 0}


def ai_index(labels: dict[int, str]) -> int:
    for index, label in labels.items():
        text = str(label).strip().lower()
        if text in {"ai", "artificial", "fake", "generated", "synthetic"}:
            return int(index)
        if "generated" in text or "artificial" in text:
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


class TransformersDetector:
    """Standard transformers image-classification adapter."""

    def __init__(self, model_id: str, device: str) -> None:
        from transformers import AutoImageProcessor, AutoModelForImageClassification

        self.model_id = model_id
        self.device = device
        started = time.perf_counter()
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.model = AutoModelForImageClassification.from_pretrained(model_id)
        self.model.to(device).eval()
        self.load_seconds = time.perf_counter() - started
        self.labels = {
            int(key): str(value) for key, value in self.model.config.id2label.items()
        }
        self.params = sum(p.numel() for p in self.model.parameters())

    def predict_batch(self, paths: list[Path], batch_size: int = 8) -> list[float]:
        import torch

        scores: list[float] = []
        index = ai_index(self.labels)
        for start in range(0, len(paths), batch_size):
            batch = paths[start : start + batch_size]
            images = []
            for path in batch:
                with Image.open(path) as image:
                    images.append(image.convert("RGB"))
            inputs = self.processor(images=images, return_tensors="pt")
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with torch.inference_mode():
                logits = self.model(**inputs).logits.float()
                probs = torch.softmax(logits, dim=-1)[:, index].cpu().tolist()
            scores.extend(float(p) for p in probs)
        return scores

    def close(self) -> None:
        del self.model, self.processor
        gc.collect()


class SmaugDetector:
    """Adapter for abacusai/Smaug-AI-Image-Detector's shipped wrapper."""

    def __init__(self, model_id: str, device: str, local_path: Path | None = None) -> None:
        from huggingface_hub import snapshot_download

        path = str(local_path) if local_path else snapshot_download(model_id)
        if path not in sys.path:
            sys.path.insert(0, path)
        from smaug_image_detect import SmaugImageDetect

        self.model_id = model_id
        self.device = device
        started = time.perf_counter()
        self.detector = SmaugImageDetect.from_pretrained(path, device=device)
        self.load_seconds = time.perf_counter() - started
        self.params = 439_000_000
        self.labels = {0: "real", 1: "ai"}

    def predict_batch(self, paths: list[Path], batch_size: int = 8, crops: int = 1) -> list[float]:
        return [
            float(score)
            for score in self.detector(
                [str(path) for path in paths], crops=crops, batch_size=batch_size
            )
        ]

    def close(self) -> None:
        del self.detector
        gc.collect()


def collect_samples(data_root: Path, limit: int | None) -> list[dict]:
    groups = [
        ("ai", "nano_banana", data_root / "ai" / "nano_banana"),
        ("ai", "imagen4", data_root / "ai" / "imagen4"),
        ("real", "picsum", data_root / "real" / "picsum"),
    ]
    samples: list[dict] = []
    for truth, source, folder in groups:
        if not folder.exists():
            print(f"warning: missing {folder}", file=sys.stderr)
            continue
        paths = sorted(
            p
            for p in folder.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        if limit:
            paths = paths[:limit]
        for path in paths:
            samples.append({"path": path, "truth": truth, "source": source})
    if not samples:
        raise SystemExit(
            f"No images under {data_root}. Run benchmark/build_modern_eval.py first."
        )
    return samples


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))
    return ordered[index]


def metrics_at(truths: list[int], scores: list[float], threshold: float) -> dict:
    calls = [1 if score >= threshold else 0 for score in scores]
    tp = sum(1 for t, c in zip(truths, calls) if t == 1 and c == 1)
    tn = sum(1 for t, c in zip(truths, calls) if t == 0 and c == 0)
    fp = sum(1 for t, c in zip(truths, calls) if t == 0 and c == 1)
    fn = sum(1 for t, c in zip(truths, calls) if t == 1 and c == 0)
    ai_total, real_total = tp + fn, tn + fp
    ai_recall = tp / ai_total if ai_total else 0.0
    real_recall = tn / real_total if real_total else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = (
        2 * precision * ai_recall / (precision + ai_recall)
        if precision + ai_recall
        else 0.0
    )
    return {
        "threshold": round(threshold, 3),
        "accuracy": round((tp + tn) / len(truths), 4),
        "balanced_accuracy": round((ai_recall + real_recall) / 2, 4),
        "ai_recall": round(ai_recall, 4),
        "real_recall": round(real_recall, 4),
        "false_positive_rate": round(fp / real_total, 4) if real_total else None,
        "ai_precision": round(precision, 4),
        "ai_f1": round(f1, 4),
        "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
    }


def sweep_threshold(truths: list[int], scores: list[float]) -> tuple[float, list[dict]]:
    rows = []
    for step in range(1, 100):
        threshold = step / 100
        row = metrics_at(truths, scores, threshold)
        rows.append(row)
    # When several thresholds tie (common with a strongly separated model),
    # prefer the one closest to the model's documented 0.5 default instead of
    # reporting an arbitrary 0.01 cut-off.
    best = max(
        rows,
        key=lambda row: (
            row["balanced_accuracy"],
            row["accuracy"],
            -abs(row["threshold"] - 0.5),
        ),
    )
    return best["threshold"], rows


def cross_validated_threshold(truths: list[int], scores: list[float]) -> dict:
    """5-fold threshold selection; report performance on unseen folds."""
    from sklearn.model_selection import StratifiedKFold

    splits = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    rows = []
    for train_idx, test_idx in splits.split(scores, truths):
        train_truth = [truths[i] for i in train_idx]
        train_scores = [scores[i] for i in train_idx]
        threshold, _ = sweep_threshold(train_truth, train_scores)
        test_truth = [truths[i] for i in test_idx]
        test_scores = [scores[i] for i in test_idx]
        rows.append(metrics_at(test_truth, test_scores, threshold))
    return {
        "folds": rows,
        "mean_accuracy": round(statistics.mean(r["accuracy"] for r in rows), 4),
        "mean_balanced_accuracy": round(
            statistics.mean(r["balanced_accuracy"] for r in rows), 4
        ),
        "mean_ai_recall": round(statistics.mean(r["ai_recall"] for r in rows), 4),
        "mean_real_recall": round(statistics.mean(r["real_recall"] for r in rows), 4),
    }


def summarize(truths: list[int], scores: list[float], threshold: float) -> dict:
    from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve

    at = metrics_at(truths, scores, threshold)
    real_scores = [s for t, s in zip(truths, scores) if t == 0]
    ai_scores = [s for t, s in zip(truths, scores) if t == 1]

    # Recall at a 5% false-positive budget, interpolated from the ROC curve.
    tpr_at_5fpr = 0.0
    if real_scores and ai_scores:
        fpr, tpr, _ = roc_curve(truths, scores)
        within_budget = [value for value, rate in zip(tpr, fpr) if rate <= 0.05]
        if within_budget:
            tpr_at_5fpr = float(max(within_budget))

    try:
        auc = float(roc_auc_score(truths, scores))
    except ValueError:
        auc = float("nan")

    return {
        **at,
        "roc_auc": round(auc, 5) if auc == auc else None,
        "brier": round(brier_score_loss(truths, scores), 4),
        "tpr_at_5pct_fpr": round(tpr_at_5fpr, 4),
        "mean_ai_probability_real": (
            round(statistics.mean(real_scores), 4) if real_scores else None
        ),
        "mean_ai_probability_ai": (
            round(statistics.mean(ai_scores), 4) if ai_scores else None
        ),
        "median_ai_probability_real": (
            round(statistics.median(real_scores), 4) if real_scores else None
        ),
        "median_ai_probability_ai": (
            round(statistics.median(ai_scores), 4) if ai_scores else None
        ),
        "p95_ai_probability_real": round(percentile(real_scores, 0.95), 4),
        "p5_ai_probability_ai": round(percentile(ai_scores, 0.05), 4),
    }


def evaluate_model(
    model_id: str,
    samples: list[dict],
    device: str,
    crops: int,
    local_path: Path | None,
) -> dict:
    print(f"\nLoading {model_id} on {device} …", flush=True)
    if model_id.startswith("abacusai/Smaug"):
        detector = SmaugDetector(model_id, device, local_path)
        predict = lambda paths: detector.predict_batch(paths, crops=crops)
    else:
        detector = TransformersDetector(model_id, device)
        predict = detector.predict_batch

    paths = [s["path"] for s in samples]
    started = time.perf_counter()
    scores: list[float] = []
    batch_size = 8
    for start in range(0, len(paths), batch_size):
        batch = paths[start : start + batch_size]
        scores.extend(predict(batch))
        done = min(start + len(batch), len(paths))
        print(f"  {done}/{len(paths)}", end="\r", flush=True)
    elapsed = time.perf_counter() - started
    print()

    truths = [LABELS[s["truth"]] for s in samples]
    best_threshold, sweep = sweep_threshold(truths, scores)
    summary_all = summarize(truths, scores, 0.5)
    summary_best = summarize(truths, scores, best_threshold)
    cv = cross_validated_threshold(truths, scores)

    per_source = {}
    for source in sorted({s["source"] for s in samples}):
        indices = [i for i, s in enumerate(samples) if s["source"] == source]
        source_truth = [truths[i] for i in indices]
        source_scores = [scores[i] for i in indices]
        per_source[source] = {
            "n": len(indices),
            "truth": samples[indices[0]]["truth"],
            "summary_at_0.5": summarize(
                source_truth, source_scores, 0.5
            ),
        }

    result = {
        "model_id": model_id,
        "info": MODEL_INFO.get(model_id, {}),
        "device": device,
        "crops": crops if model_id.startswith("abacusai/Smaug") else None,
        "load_seconds": round(detector.load_seconds, 2),
        "parameters": detector.params,
        "elapsed_seconds": round(elapsed, 2),
        "mean_latency_ms": round(elapsed * 1000 / len(samples), 1),
        "n": len(samples),
        "summary_at_0.5": summary_all,
        "best_threshold": best_threshold,
        "summary_at_best_threshold": summary_best,
        "cross_validated_threshold": cv,
        "per_source_at_0.5": per_source,
        "threshold_sweep": sweep,
        "predictions": [
            {
                "name": s["path"].name,
                "source": s["source"],
                "truth": s["truth"],
                "ai_probability": round(score, 6),
            }
            for s, score in zip(samples, scores)
        ],
    }
    detector.close()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return result


SOURCE_LABELS = {
    "nano_banana": "Nano Banana (Gemini 2.5 Flash Image)",
    "imagen4": "Imagen 4",
    "picsum": "real Unsplash photos",
}


def write_markdown(report: dict, path: Path) -> None:
    sources = report["dataset"]["sources"]
    parts = [
        f"{value} {SOURCE_LABELS.get(name, name)}" for name, value in sources.items()
    ]
    counts = ", ".join(parts[:-1]) + f", and {parts[-1]}" if len(parts) > 1 else parts[0]
    note = report["dataset"].get("note") or (
        "Google-focused smoke evaluation; not a universal benchmark."
    )
    lines = [
        "# Modern AI-image detector evaluation",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"Device: `{report['environment']['device']}` · Torch `{report['environment']['torch']}` · "
        f"Transformers `{report['environment']['transformers']}`",
        "",
        f"> This is a **Google-focused smoke evaluation**, not a universal benchmark. "
        f"It measures {counts}. Results will not transfer to every generator, "
        "social-media compression, or editing pipeline.",
        "",
        f"Dataset note: {note}",
        "",
        "## Overall (threshold 0.5)",
        "",
        "| Model | N | Accuracy | AI recall | Real recall (specificity) | ROC-AUC | TPR @ 5% FPR | Mean P(AI) real | Mean P(AI) AI | Latency |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in report["models"]:
        s = model["summary_at_0.5"]
        lines.append(
            f"| `{model['model_id']}` | {model['n']} | {s['accuracy']:.1%} | "
            f"{s['ai_recall']:.1%} | {s['real_recall']:.1%} | {s['roc_auc']:.5f} | "
            f"{s['tpr_at_5pct_fpr']:.1%} | {s['mean_ai_probability_real']:.1%} | "
            f"{s['mean_ai_probability_ai']:.1%} | {model['mean_latency_ms']:.0f} ms |"
        )

    lines += ["", "## Per-source recall at 0.5", ""]
    source_names = sorted(
        {source for model in report["models"] for source in model["per_source_at_0.5"]}
    )
    header = "| Model | " + " | ".join(source_names) + " |"
    lines.append(header)
    lines.append("|---|" + "---:|" * len(source_names))
    for model in report["models"]:
        cells = []
        for source in source_names:
            row = model["per_source_at_0.5"].get(source, {})
            value = row.get("summary_at_0.5", {})
            if row.get("truth") == "ai":
                cells.append(f"{value.get('ai_recall', 0):.1%} AI recall")
            else:
                cells.append(f"{value.get('real_recall', 0):.1%} real recall")
        lines.append(f"| `{model['model_id'].split('/')[-1]}` | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Calibration",
        "",
        "| Model | Best threshold (all data, optimistic) | 5-fold CV threshold performance | CV AI recall | CV real recall |",
        "|---|---:|---:|---:|---:|",
    ]
    for model in report["models"]:
        cv = model["cross_validated_threshold"]
        lines.append(
            f"| `{model['model_id'].split('/')[-1]}` | {model['best_threshold']:.2f} | "
            f"{cv['mean_balanced_accuracy']:.1%} balanced | {cv['mean_ai_recall']:.1%} | "
            f"{cv['mean_real_recall']:.1%} |"
        )

    lines += ["", "## Full images at the default threshold", ""]
    for model in report["models"]:
        lines.append(f"### `{model['model_id']}`")
        lines.append("")
        for source in source_names:
            rows = [p for p in model["predictions"] if p["source"] == source]
            if not rows:
                continue
            scores = [p["ai_probability"] for p in rows]
            truth = "AI" if rows[0]["truth"] == "ai" else "real"
            lines.append(
                f"- **{source}** ({truth}, n={len(rows)}): "
                f"median P(AI)={statistics.median(scores):.3f}, "
                f"range {min(scores):.3f}–{max(scores):.3f}"
            )
        lines.append("")

    lines += [
        "## Reproduce",
        "",
        "```bash",
        ".venv/bin/python benchmark/build_modern_eval.py",
        ".venv/bin/python benchmark/evaluate_modern.py",
        "```",
        "",
        "The images are downloaded to `benchmark/data/modern/` and are excluded from git. "
        "Nano Banana data: `bitmind/nano-banana` (MIT). Imagen 4 data: "
        "`Rapidata/Imagen4_t2i_human_preference` (CDLA-Permissive-2.0). "
        "Real photos: picsum.photos (Unsplash License).",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument(
        "--models",
        nargs="+",
        default=[
            "Ateeqq/ai-vs-human-image-detector",
            "abacusai/Smaug-AI-Image-Detector",
        ],
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--limit", type=int, default=None, help="Images per source")
    parser.add_argument("--crops", type=int, default=1, choices=[1, 3])
    parser.add_argument("--smaug-path", type=Path, default=None)
    parser.add_argument(
        "--dataset-note",
        default=None,
        help="Optional note stored in the report (e.g. recompression details).",
    )
    parser.add_argument(
        "--output-prefix",
        default="modern_image_eval",
        help="Base filename for the JSON and Markdown reports.",
    )
    args = parser.parse_args()

    import torch
    import transformers

    samples = collect_samples(args.data_root, args.limit)
    device = choose_device(args.device)
    counts: dict[str, int] = {}
    for sample in samples:
        counts[sample["source"]] = counts.get(sample["source"], 0) + 1
    print(f"{len(samples)} images · {counts} · device={device}")

    models = []
    for model_id in args.models:
        try:
            models.append(
                evaluate_model(
                    model_id,
                    samples,
                    device,
                    args.crops,
                    args.smaug_path,
                )
            )
        except Exception as exc:
            print(f"FAILED {model_id}: {exc}", file=sys.stderr)

    if not models:
        return 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {
            "count": len(samples),
            "sources": counts,
            "note": args.dataset_note
            or "Google-focused smoke evaluation; not a universal benchmark.",
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
    json_path = args.out_dir / f"{args.output_prefix}.json"
    markdown_path = args.out_dir / f"{args.output_prefix}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, markdown_path)

    print("\nSummary at 0.5")
    for model in models:
        s = model["summary_at_0.5"]
        print(
            f"  {model['model_id']}: acc={s['accuracy']:.1%} "
            f"AI recall={s['ai_recall']:.1%} real recall={s['real_recall']:.1%} "
            f"AUC={s['roc_auc']} best_threshold={model['best_threshold']:.2f}"
        )
    print(f"\nWrote {json_path}\nWrote {markdown_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

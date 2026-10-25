from __future__ import annotations

import re
from statistics import mean, pstdev

from .model_registry import get_text_model

AI_PHRASES = [
    "delve into",
    "it is important to note",
    "in today's fast-paced",
    "in the ever-evolving",
    "tapestry",
    "multifaceted",
    "crucial",
    "pivotal",
    "seamless",
    "leverage",
    "furthermore",
    "moreover",
    "in conclusion",
    "overall,",
    "navigating",
    "landscape of",
    "testament to",
    "not only",
    "but also",
    "plays a vital role",
    "a wide range of",
]

TEXT_LIMITATIONS = [
    "AI-text detectors are unreliable. They can be fooled by paraphrasing, translation, editing, and 'humanizing' tools.",
    "False positives are common on formal writing, academic prose, and text by non-native English speakers.",
    "Short texts, outlines, bullet lists, and mixed human/AI writing are especially hard to classify.",
    "A detector score is not evidence of authorship or misconduct. Never use it as the sole basis for an accusation.",
]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 1]


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'-]*", text.lower())


def _stats(text: str) -> dict:
    sentences = _sentences(text)
    words = _words(text)
    lengths = [len(_words(s)) for s in sentences if _words(s)]
    if not words:
        return {
            "characters": len(text),
            "words": 0,
            "sentences": 0,
            "avg_sentence_words": 0,
            "sentence_length_cv": 0,
            "type_token_ratio": 0,
            "ai_phrase_hits": 0,
        }

    hits = sum(1 for phrase in AI_PHRASES if phrase in text.lower())
    trigrams = [
        tuple(words[i : i + 3]) for i in range(max(0, len(words) - 2))
    ]
    trigram_repetition = 0.0
    if trigrams:
        trigram_repetition = 1.0 - (len(set(trigrams)) / len(trigrams))

    avg_len = mean(lengths) if lengths else 0
    cv = (pstdev(lengths) / avg_len) if len(lengths) > 1 and avg_len else 0
    return {
        "characters": len(text),
        "words": len(words),
        "sentences": len(sentences),
        "avg_sentence_words": round(avg_len, 2),
        "sentence_length_cv": round(cv, 3),
        "type_token_ratio": round(len(set(words)) / len(words), 3),
        "trigram_repetition": round(trigram_repetition, 3),
        "ai_phrase_hits": hits,
        "ai_phrases_found": [
            phrase for phrase in AI_PHRASES if phrase in text.lower()
        ],
    }


def _heuristic_score(stats: dict) -> float:
    words = max(stats["words"], 1)
    phrase_density = min(1.0, stats["ai_phrase_hits"] / max(1.0, words / 250))
    # Human writing often has more sentence-length variation; this is a weak cue.
    uniformity = max(0.0, min(1.0, (0.65 - stats["sentence_length_cv"]) / 0.45))
    # Very high lexical diversity can be human or AI; only use as a tiny nudge.
    diversity = max(0.0, min(1.0, (0.62 - stats["type_token_ratio"]) / 0.25))
    score = 0.55 * phrase_density + 0.30 * uniformity + 0.15 * diversity
    return round(max(0.05, min(0.95, score)), 3)


def _chunks(text: str, max_chunks: int = 8) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for paragraph in paragraphs:
        count = len(paragraph.split())
        if current and current_words + count > 1200:
            chunks.append("\n\n".join(current))
            current = []
            current_words = 0
        current.append(paragraph)
        current_words += count
        if current_words >= 1200:
            chunks.append("\n\n".join(current))
            current = []
            current_words = 0
    if current:
        chunks.append("\n\n".join(current))
    if len(chunks) > max_chunks:
        chunks = chunks[:max_chunks]
    return chunks


def _label(probability: float | None) -> str:
    if probability is None:
        return "Uncertain"
    if probability >= 0.80:
        return "Likely AI-generated"
    if probability >= 0.60:
        return "Possibly AI-generated"
    if probability <= 0.20:
        return "Likely human-written"
    if probability <= 0.40:
        return "Possibly human-written"
    return "Uncertain"


def analyze_text(text: str) -> dict:
    stats = _stats(text)
    heuristic = _heuristic_score(stats)
    model, model_error = get_text_model()

    model_results: list[dict] = []
    chunk_scores: list[dict] = []
    ai_probability: float | None = None
    if model is not None:
        chunks = _chunks(text)
        for index, chunk in enumerate(chunks):
            try:
                result = model.predict(chunk)
                chunk_scores.append(
                    {
                        "index": index,
                        "words": len(chunk.split()),
                        "ai_probability": result["ai_probability"],
                        "latency_ms": result["latency_ms"],
                    }
                )
                model_results.append(result)
            except Exception as exc:
                model_error = f"Chunk {index} failed: {exc}"
        if model_results:
            ai_probability = round(
                sum(r["ai_probability"] for r in model_results) / len(model_results), 6
            )

    label = _label(ai_probability if ai_probability is not None else heuristic)
    signal_value = ai_probability if ai_probability is not None else heuristic

    signals = [
        {
            "name": "text_model" if ai_probability is not None else "heuristic_stylometry",
            "value": signal_value,
            "direction": "AI-like" if signal_value >= 0.5 else "human-like",
            "weight": "model" if ai_probability is not None else "weak",
            "explanation": (
                f"Mean AI-like probability across {len(model_results)} chunk(s)."
                if ai_probability is not None
                else "A transparent but weak stylometric heuristic. Not a reliable detector."
            ),
        },
        {
            "name": "ai_phrase_hits",
            "value": stats["ai_phrase_hits"],
            "direction": "AI-like" if stats["ai_phrase_hits"] >= 2 else "neutral",
            "weight": "weak",
            "explanation": "Common LLM-style phrases found. These phrases also occur in human writing.",
        },
        {
            "name": "sentence_length_cv",
            "value": stats["sentence_length_cv"],
            "direction": "neutral",
            "weight": "weak",
            "explanation": "Coefficient of variation of sentence lengths. Low variation is a weak AI-style cue, not proof.",
        },
        {
            "name": "trigram_repetition",
            "value": stats["trigram_repetition"],
            "direction": "neutral",
            "weight": "weak",
            "explanation": "Fraction of repeated three-word sequences. High repetition can occur in human drafts too.",
        },
    ]

    if ai_probability is not None:
        summary = (
            f"The text classifier estimates {ai_probability:.0%} AI-like content "
            f"across {len(model_results)} chunk(s). Result: {label}. "
            "This is a weak signal, not proof of authorship."
        )
    else:
        summary = (
            f"No text classifier was loaded, so this is a heuristic-only estimate "
            f"({heuristic:.0%}). Result: {label}. {model_error or ''}"
        ).strip()

    return {
        "kind": "text",
        "label": label,
        "ai_probability": ai_probability,
        "heuristic_score": heuristic,
        "confidence": round(
            min(0.85, abs((ai_probability if ai_probability is not None else heuristic) - 0.5) * 2.0 * 0.8),
            3,
        ),
        "model_used": model.model_id if model is not None and model_results else "heuristic",
        "model_error": model_error,
        "text_stats": stats,
        "chunks": chunk_scores,
        "signals": signals,
        "summary": summary,
        "limitations": TEXT_LIMITATIONS,
        "recommended_action": (
            "Use this as a triage signal only. Ask the author for drafts or provenance, "
            "and do not treat a detector score as proof of AI authorship."
        ),
    }

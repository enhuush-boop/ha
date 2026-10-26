from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from rapidfuzz import fuzz

from ..config import settings
from .mongolian import detect_language, has_negation, match_key


def _load_corpus() -> dict:
    path = settings.data_dir / "factchecks.json"
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {"entries": [], "notice": "Fact-check corpus could not be loaded."}


CORPUS = _load_corpus()

FALSE_TERMS = (
    "false",
    "fake",
    "incorrect",
    "wrong",
    "misleading",
    "not true",
    "hoax",
    "scam",
    "fabricated",
    "altered",
    "miscaptioned",
    "manipulated",
    "unsubstantiated",
    "no evidence",
    "худал",
    "худлаа",
    "хуурамч",
    "ташаа",
    "үнэн биш",
    "hudal",
    "hudlaa",
)
TRUE_TERMS = ("true", "correct", "accurate", "supported", "confirmed", "үнэн", "зөв", "unen")
MIXED_TERMS = ("half", "partly", "mixed", "хэсэгчлэн", "хагас үнэн", "зарим нь")


def _normalize(text: str) -> str:
    return match_key(text)


def _tokens(text: str) -> set[str]:
    return {t for t in _normalize(text).split() if len(t) > 1}


def _score(claim: str, candidate: str, keywords: list[str] | None = None) -> float:
    a, b = _normalize(claim), _normalize(candidate)
    if not a or not b:
        return 0.0
    # A near-identical sentence with reversed polarity must not inherit a
    # corpus verdict. This matters especially for -гүй / -gui suffixes.
    if has_negation(claim) != has_negation(candidate):
        return 0.0
    token_a, token_b = _tokens(claim), _tokens(candidate)
    jaccard = (
        len(token_a & token_b) / max(1, len(token_a | token_b))
        if token_a and token_b
        else 0.0
    )
    fuzzy = fuzz.token_set_ratio(a, b) / 100.0
    keyword_hits = 0
    if keywords:
        kw = {_normalize(k) for k in keywords}
        keyword_hits = len(token_a & kw) / max(1, len(kw))
    return round(0.45 * fuzzy + 0.35 * jaccard + 0.20 * keyword_hits, 3)


def find_local_match(claim: str, language: str | None = None) -> tuple[dict | None, float]:
    best_entry = None
    best_score = 0.0
    for entry in CORPUS.get("entries", []):
        candidates = [entry.get("claim", "")] + list(entry.get("aliases", []))
        score = max(
            _score(claim, candidate, entry.get("keywords", []))
            for candidate in candidates
        )
        if score > best_score:
            best_score = score
            best_entry = entry
    threshold = 0.75 if (language or detect_language(claim)) in {"mn_cyrl", "mn_latn"} else 0.58
    if best_score < threshold:
        return None, best_score
    return best_entry, best_score


def _search_evidence(claim: str) -> list[dict]:
    q = quote_plus(claim)
    return [
        {
            "title": "Search Google Fact Check Explorer",
            "source": "Google Fact Check Tools",
            "url": f"https://toolbox.google.com/factcheck/explorer/search/{q}",
            "snippet": "Search published fact-checks that may already address this claim.",
            "relation": "search",
        },
        {
            "title": "Search Snopes",
            "source": "Snopes",
            "url": f"https://www.snopes.com/?s={q}",
            "snippet": "Snopes fact-checks and investigations.",
            "relation": "search",
        },
        {
            "title": "Search Full Fact",
            "source": "Full Fact",
            "url": f"https://fullfact.org/search/?q={q}",
            "snippet": "UK-based independent fact-checking charity.",
            "relation": "search",
        },
        {
            "title": "Reuters Fact Check",
            "source": "Reuters",
            "url": "https://www.reuters.com/fact-check/",
            "snippet": "Reuters fact-check reporting.",
            "relation": "search",
        },
    ]


def _no_match(claim: str, reason: str | None = None, language: str | None = None) -> dict:
    language = language or detect_language(claim)
    mongolian = language in {"mn_cyrl", "mn_latn"}
    return {
        "claim": claim,
        "language": language,
        "verdict": "Not Sure",
        "confidence": 0.15,
        "summary": (
            "Энэ мэдэгдэлтэй баттай таарах баримт шалгалт олдсонгүй. "
            "Энэ нь үнэн эсвэл худал гэсэн дүгнэлт биш. Доорх эх сурвалжаас үргэлжлүүлэн шалгана уу."
            if mongolian else
            "I could not find a confident match in the local demo corpus. "
            "This does not mean the claim is true or false. Use the search links below "
            "or connect a fact-check API key."
        ) + (f" ({reason})" if reason else ""),
        "evidence": _search_evidence(claim),
        "matched_claim": None,
        "match_score": 0.0,
        "method": "no_match",
        "summary_source": "template",
        "limitations": [
            "The curated corpus is intentionally small and is not a complete fact-check database.",
            "No unrestricted web search is performed by default. A trusted-source retrieval layer is required for broad coverage.",
            "A 'Not Sure' result means the system lacks evidence, not that the claim is balanced or undecided in the scientific literature.",
        ],
    }


def _rating_to_verdict(rating: str) -> str:
    value = (rating or "").lower()
    if any(term in value for term in MIXED_TERMS):
        return "Not Sure"
    if any(term in value for term in FALSE_TERMS):
        return "False"
    if any(term in value for term in TRUE_TERMS):
        return "True"
    return "Not Sure"


async def google_factcheck(claim: str) -> dict | None:
    key = settings.google_factcheck_api_key
    if not key:
        return None
    url = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
    params = {"query": claim, "key": key, "pageSize": 10}
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return {"error": f"Google Fact Check API failed: {exc}"}

    claims = data.get("claims", [])
    if not claims:
        return None

    evidence: list[dict] = []
    verdicts: list[str] = []
    ratings: list[str] = []
    for item in claims:
        for review in item.get("claimReview", []):
            rating = review.get("textualRating", "")
            verdict = _rating_to_verdict(rating)
            verdicts.append(verdict)
            ratings.append(rating)
            evidence.append(
                {
                    "title": review.get("title") or item.get("text", "Fact-check"),
                    "source": review.get("publisher", {}).get("name", "Fact-checker"),
                    "url": review.get("url", ""),
                    "snippet": f"Published rating: {rating or 'not stated'}. Claim reviewed: {item.get('text', claim)}",
                    "relation": "contradicts" if verdict == "False" else "supports" if verdict == "True" else "neutral",
                    "review_date": review.get("reviewDate"),
                }
            )

    if not evidence:
        return None

    false_count = verdicts.count("False")
    true_count = verdicts.count("True")
    unsure_count = verdicts.count("Not Sure")
    if false_count > true_count and false_count >= unsure_count:
        verdict = "False"
    elif true_count > false_count and true_count >= unsure_count:
        verdict = "True"
    else:
        verdict = "Not Sure"

    summary = (
        f"Existing fact-checks were found for this claim. Ratings include: "
        f"{'; '.join(ratings[:5])}. The aggregate verdict is {verdict}. "
        "Read the linked fact-checks for the exact claim and context."
    )
    return {
        "claim": claim,
        "verdict": verdict,
        "confidence": 0.8 if len(evidence) >= 2 else 0.62,
        "summary": summary,
        "evidence": evidence[:8],
        "matched_claim": claims[0].get("text"),
        "match_score": 0.8,
        "method": "google_factcheck_tools",
        "summary_source": "template",
        "limitations": [
            "Fact-check ratings are for the specific claim and context reviewed by each publisher; wording may differ from the user's claim.",
            "Google Fact Check Tools returns publisher reviews, not an independent judgment by this app.",
            "Check publication dates: a fact-check may be outdated or may not address a new variant of the claim.",
        ],
    }


async def ollama_summary(claim: str, verdict: str, evidence: list[dict]) -> str | None:
    if not settings.ollama_url:
        return None
    lines = []
    for index, item in enumerate(evidence[:6], start=1):
        lines.append(
            f"[{index}] {item.get('source', 'source')} — {item.get('title', '')}: "
            f"{item.get('snippet', '')} ({item.get('url', '')})"
        )
    prompt = (
        "You are a careful fact-checking editor. Using ONLY the evidence below, write "
        "2-3 sentences for a journalist explaining the conclusion. Do not add facts that "
        "are not in the evidence. State the verdict explicitly. If the evidence is weak, "
        "say so.\n\n"
        f"Claim: {claim}\nVerdict: {verdict}\n\nEvidence:\n" + "\n".join(lines)
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{settings.ollama_url}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "stream": False,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You summarize retrieved evidence. You never invent sources or facts.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"temperature": 0.1, "num_ctx": 4096},
                },
            )
            response.raise_for_status()
            data = response.json()
            content = (data.get("message") or {}).get("content", "").strip()
            return content or None
    except Exception:
        return None


async def factcheck_claim(claim: str, language_hint: str = "auto") -> dict:
    claim = claim.strip()
    language = detect_language(claim) if language_hint == "auto" else language_hint
    entry, score = find_local_match(claim, language)

    if entry:
        result: dict[str, Any] = {
            "claim": claim,
            "language": language,
            "verdict": entry.get("verdict", "Not Sure"),
            "confidence": round(
                float(entry.get("confidence", 0.9)) * min(1.0, score + 0.1), 3
            ),
            "summary": entry.get("summary", ""),
            "evidence": entry.get("evidence", []),
            "matched_claim": entry.get("claim"),
            "match_score": score,
            "method": "curated_corpus",
            "topic": entry.get("topic"),
            "summary_source": "template",
            "limitations": [
                "This answer comes from a small curated demo corpus, not a live web search.",
                "Evidence links are provided so you can verify the original sources.",
                "The corpus is small and may become stale; check publication dates.",
            ],
        }
        if settings.ollama_url:
            summary = await ollama_summary(claim, result["verdict"], result["evidence"])
            if summary:
                result["summary"] = summary
                result["summary_source"] = f"ollama:{settings.ollama_model}"
        return result

    google = await google_factcheck(claim)
    if google and not google.get("error"):
        google["language"] = language
        if settings.ollama_url:
            summary = await ollama_summary(
                claim, google["verdict"], google.get("evidence", [])
            )
            if summary:
                google["summary"] = summary
                google["summary_source"] = f"ollama:{settings.ollama_model}"
        return google

    reason = None
    if google and google.get("error"):
        reason = google["error"]
    elif not settings.google_factcheck_api_key:
        reason = "Google Fact Check API key not configured."
    return _no_match(claim, reason, language)

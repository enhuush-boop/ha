from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlencode

import httpx
from rapidfuzz import fuzz

from ..config import settings
from .evidence_index import (
    PUBLISHED_RATING_MIN_SCORE,
    ensure_index_refresh_if_stale,
    search as evidence_search,
)
from .health_literature import search_health_literature
from .claimreview_store import search as search_claimreviews, status as claimreview_status
from .mongolian import detect_language, has_negation, match_key, search_variants


def _load_corpus() -> dict:
    path = settings.data_dir / "factchecks.json"
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {"entries": [], "notice": "Fact-check corpus could not be loaded."}


CORPUS = _load_corpus()
_CLAIM_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "is", "it", "of", "on", "or", "the", "to", "was", "were",
    "ba", "buyu", "gej", "ni", "n", "uu", "ve", "yum",
}

def _normalize(text: str) -> str:
    return match_key(text)


def _tokens(text: str) -> set[str]:
    return {
        t for t in _normalize(text).split()
        if len(t) > 1 and t not in _CLAIM_STOPWORDS
    }


def _ordered_tokens(text: str) -> list[str]:
    return [
        t for t in _normalize(text).split()
        if len(t) > 1 and t not in _CLAIM_STOPWORDS
    ]


def _score(claim: str, candidate: str, keywords: list[str] | None = None) -> float:
    a, b = _normalize(claim), _normalize(candidate)
    if not a or not b:
        return 0.0
    # A near-identical sentence with reversed polarity must not inherit a
    # corpus verdict. This matters especially for -гүй / -gui suffixes.
    if has_negation(claim) != has_negation(candidate):
        return 0.0
    token_a, token_b = _tokens(claim), _tokens(candidate)
    # Fuzzy token-set similarity can score an unrelated longer statement as
    # identical when it merely contains the same short phrase. Curated
    # verdicts require the same substantive terms after language filler is
    # removed; paraphrases remain available as search leads instead.
    if token_a != token_b or _ordered_tokens(claim) != _ordered_tokens(candidate):
        return 0.0
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


def _search_evidence(claim: str, language: str = "auto") -> list[dict]:
    variants = search_variants(claim, limit=6, force_mongolian=language == "mn_latn")
    links = [
        {
            "title": f"Search Google Fact Check Explorer — {variant}",
            "source": "Google Fact Check Tools",
            "url": f"https://toolbox.google.com/factcheck/explorer/search/{quote_plus(variant)}",
            "snippet": "Search indexed fact-check articles. A search result is a lead to read, not an automatic verdict.",
            "relation": "search",
        }
        for variant in variants
    ]
    links.extend([
        {
            "title": "Search Snopes",
            "source": "Snopes",
            "url": f"https://www.snopes.com/?s={quote_plus(claim)}",
            "snippet": "Snopes fact-checks and investigations.",
            "relation": "search",
        },
        {
            "title": "Search Full Fact",
            "source": "Full Fact",
            "url": f"https://fullfact.org/search/?q={quote_plus(claim)}",
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
    ])
    for domain, name in (("mfcc.mn", "MFCC — Монголын баримт шалгах төв"),
                         ("factcheck.mn", "FACTCHECK Mongolia")):
        links.extend(
            {
                "title": f"Search {name} — {variant}",
                "source": name,
                "url": "https://www.google.com/search?" + urlencode({"q": f"site:{domain} {variant}"}),
                "snippet": "Site-restricted search lead. Read the article and its cited sources before drawing a conclusion.",
                "relation": "search",
            }
            for variant in variants[:2]
        )
    return links


# Above this similarity, a fact-checker's own headline verdict is worth putting
# in front of the reader. It is still reported as theirs, not adopted as ours:
# matching wording is not the same claim, and only a human can judge that.
#
# A small initial hand-check found one irrelevant article at 0.54. The current
# 0.70 floor hides its rating; it is a conservative retrieval gate, not a
# statistically validated confidence threshold. Exact reviewed wording scores
# 1.0; borderline paraphrases should be searched manually via the source links.
REPORTED_VERDICT_MIN = PUBLISHED_RATING_MIN_SCORE


def _no_match(claim: str, reason: str | None = None, language: str | None = None) -> dict:
    language = language or detect_language(claim)
    mongolian = language in {"mn_cyrl", "mn_latn"}

    indexed = evidence_search(claim, language_hint=language)
    found = indexed.get("results") or []

    reported = next(
        (
            r
            for r in found
            if r.get("published_verdict")
            and r.get("tier") == "fact_check"
            and r.get("match_score", 0) >= REPORTED_VERDICT_MIN
        ),
        None,
    )

    if reported:
        summary = (
            f"Бидний корпуст таарах бичлэг алга. Гэвч {reported['source']} үүнтэй "
            f"{reported['match_score']:.0%} төстэй мэдэгдлийг шалгаад "
            f"«{reported['published_verdict']}» гэж дүгнэжээ. Энэ бол тэдний дүгнэлт, "
            "манайх биш — ижил үг хэллэг нь ижил мэдэгдэл гэсэн баталгаа биш тул "
            "нийтлэлийг нь уншиж тулгана уу."
            if mongolian else
            f"No entry in our corpus matches. {reported['source']} did publish "
            f"«{reported['published_verdict']}» on a claim worded "
            f"{reported['match_score']:.0%} like this one. That verdict is theirs, "
            "not ours: similar wording is not the same claim, so read their piece."
        )
    elif found:
        summary = (
            f"Корпуст таарах бичлэг алга. Бүртгэсэн нийтлэгчдээс үүнтэй холбоотой "
            f"{len(found)} нийтлэл олдлоо — доороос уншина уу."
            if mongolian else
            f"No corpus entry matches. {len(found)} related piece(s) from registered "
            "sources are listed below."
        )
    else:
        summary = (
            "Энэ мэдэгдэлтэй баттай таарах баримт шалгалт олдсонгүй. "
            "Энэ нь үнэн эсвэл худал гэсэн дүгнэлт биш. Доорх эх сурвалжаас үргэлжлүүлэн шалгана уу."
            if mongolian else
            "I could not find a confident match in the local demo corpus. "
            "This does not mean the claim is true or false. Use the search links below "
            "or connect a fact-check API key."
        )

    limitations = [
        "The curated corpus is intentionally small and is not a complete fact-check database.",
        "A 'Not Sure' result means the system lacks evidence, not that the claim is balanced or undecided in the scientific literature.",
    ]
    if indexed.get("available"):
        limitations.append(
            "The index covers only the registered publishers and their retrieved archive; "
            "it is not a search of the whole web and can miss older or unindexed articles."
        )
    else:
        limitations.append(
            "The trusted-source index is empty — run scripts/refresh_evidence.py for broader coverage."
        )

    return {
        "claim": claim,
        "language": language,
        "verdict": "Not Sure",
        "confidence": 0.15,
        "summary": summary + (f" ({reason})" if reason else ""),
        "evidence": found + _search_evidence(claim, language),
        "reported_verdict": (
            {
                "verdict": reported["published_verdict"],
                "source": reported["source"],
                "url": reported["url"],
                "match_score": reported["match_score"],
                "note": "Published by that source about a similarly worded claim; not this tool's verdict.",
            }
            if reported
            else None
        ),
        "index_status": {
            "available": indexed.get("available", False),
            "fetched_at": indexed.get("fetched_at"),
            "indexed_items": indexed.get("indexed_items", 0),
            "publisher_count": indexed.get("publisher_count", 0),
            "hits": len(found),
        },
        "indexed_source_count": len({item.get("source") for item in found}),
        "index_search_variants": indexed.get("search_variants", []),
        "matched_claim": None,
        "match_score": 0.0,
        "method": "indexed_evidence" if found else "no_match",
        "summary_source": "template",
        "limitations": limitations,
    }


def _merge_indexed_evidence(
    result: dict, claim: str, language_hint: str = "auto"
) -> dict:
    """Attach local publisher articles without changing another source's verdict."""
    indexed = evidence_search(claim, language_hint=language_hint)
    found = indexed.get("results") or []
    current_urls = {item.get("url") for item in result.get("evidence", [])}
    result.setdefault("evidence", []).extend(
        item for item in found
        if item.get("url") and item.get("url") not in current_urls
    )
    result["index_status"] = {
        "available": indexed.get("available", False),
        "fetched_at": indexed.get("fetched_at"),
        "indexed_items": indexed.get("indexed_items", 0),
        "publisher_count": indexed.get("publisher_count", 0),
        "hits": len(found),
    }
    result["indexed_source_count"] = len({item.get("source") for item in found})
    if found:
        result["index_search_variants"] = indexed.get("search_variants", [])
        result.setdefault("limitations", []).append(
            "Additional local publisher articles are matched by text similarity; read each linked article to verify it addresses the same claim."
        )
        if result.get("method") in {"curated_corpus", "google_factcheck_tools"}:
            result["method"] += "+indexed_evidence"
    return result


async def google_factcheck(
    claim: str, language_hint: str = "auto"
) -> dict | None:
    key = settings.google_factcheck_api_key
    if not key:
        return None
    url = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
    variants = search_variants(
        claim, limit=8, force_mongolian=language_hint == "mn_latn"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            async def search(query: str) -> tuple[str, dict]:
                response = await client.get(
                    url,
                    params={"query": query, "key": key, "pageSize": 10},
                )
                response.raise_for_status()
                return query, response.json()

            responses = await asyncio.gather(
                *(search(query) for query in variants), return_exceptions=True
            )
    except Exception as exc:
        return {"error": f"Google Fact Check API failed: {exc}"}

    claims: list[tuple[str, dict, dict]] = []
    seen: set[tuple[str, str]] = set()
    errors: list[str] = []
    for response in responses:
        if isinstance(response, Exception):
            errors.append(str(response))
            continue
        query, data = response
        for item in data.get("claims", []):
            for review in item.get("claimReview", []):
                identity = (str(review.get("url", "")), str(item.get("text", "")))
                if identity in seen:
                    continue
                seen.add(identity)
                claims.append((query, item, review))
    if not claims:
        if errors and len(errors) == len(responses):
            return {"error": f"Google Fact Check API failed: {errors[0]}"}
        return None

    # Only ratings attached to the same ClaimReview claim text vote together.
    groups: dict[str, list[tuple[str, dict, dict]]] = {}
    for query, item, review in claims:
        claim_key = " ".join((item.get("text") or "").casefold().split())
        if claim_key:
            groups.setdefault(claim_key, []).append((query, item, review))
    if not groups:
        return None
    matched_group = max(
        groups.values(),
        key=lambda group: (
            max(
                (_score(query, item.get("text", "")) for query, item, _ in group),
                default=0.0,
            ),
            len({
                review.get("publisher", {}).get("name", "Fact-checker")
                for _, _, review in group
            }),
        ),
    )

    group_match_score = max(
        (_score(query, item.get("text", "")) for query, item, _ in matched_group),
        default=0.0,
    )
    if group_match_score < PUBLISHED_RATING_MIN_SCORE:
        # The API can return thematically related claims. Keep the system at
        # Not Sure unless the reviewed wording itself is a strong match.
        return None

    evidence: list[dict] = []
    ratings: list[str] = []
    publishers: set[str] = set()
    for query, item, review in matched_group:
        rating = review.get("textualRating", "")
        ratings.append(rating)
        publisher = review.get("publisher", {}).get("name", "Fact-checker")
        publishers.add(publisher)
        evidence.append(
            {
                "title": review.get("title") or item.get("text", "Fact-check"),
                "source": publisher,
                "url": review.get("url", ""),
                "snippet": f"Published rating: {rating or 'not stated'}. Claim reviewed: {item.get('text', claim)}",
                "relation": "fact_check",
                "published_verdict": rating or None,
                "review_date": review.get("reviewDate"),
                "matched_claim": item.get("text", ""),
                "search_variant": query,
                "match_score": _score(query, item.get("text", "")),
            }
        )

    if not evidence:
        return None

    summary = (
        f"Found {len(evidence)} published review(s) from {len(publishers)} publisher(s) "
        f"about a claim with {group_match_score:.0%} text similarity. Their ratings "
        f"include: {'; '.join(ratings[:5])}. This tool does not combine those ratings "
        "into its own verdict; read the linked reviews to confirm the exact claim and context."
    )
    return {
        "claim": claim,
        "verdict": "Not Sure",
        "confidence": 0.15,
        "summary": summary,
        "evidence": evidence[:8],
        "matched_claim": matched_group[0][1].get("text"),
        "match_score": group_match_score,
        "method": "google_factcheck_tools",
        "source_count": len(publishers),
        "search_variants": variants,
        "summary_source": "template",
        "limitations": [
            "Fact-check ratings are for the specific claim and context reviewed by each publisher; wording may differ from the user's claim.",
            f"Found {len(evidence)} indexed review(s) from {len(publishers)} distinct publisher(s); each rating belongs to its publisher, not TAYO.",
            "Google Fact Check Tools returns publisher reviews, not an independent judgment by this app. Its index is not exhaustive.",
            "Check publication dates: a fact-check may be outdated or may not address a new variant of the claim.",
        ],
    }


async def factcheck_claim(claim: str, language_hint: str = "auto") -> dict:
    claim = claim.strip()
    language = detect_language(claim) if language_hint == "auto" else language_hint
    # A large, live biomedical index supplements the small local corpus. Its
    # citations are displayed as research leads and never change the verdict.
    health_search = await search_health_literature(claim)
    claimreview_results = await asyncio.to_thread(search_claimreviews, claim)
    claimreview_index = claimreview_status()

    def attach_health_search(result: dict) -> dict:
        result["health_literature"] = {
            "available": health_search.get("available", False),
            "database": health_search.get("database"),
            "record_count": health_search.get("record_count", 0),
            "note": health_search.get("note") or health_search.get("reason"),
            "evidence": health_search.get("results", []),
        }
        result["claimreview_evidence"] = claimreview_results
        result["claimreview_index"] = {
            "available": claimreview_index.get("available", False),
            "records": claimreview_index.get("records", 0),
            "imported_at": claimreview_index.get("imported_at"),
        }
        if health_search.get("results"):
            result.setdefault("limitations", []).append(
                "Biomedical papers below are research leads from Europe PMC / PubMed, not claim ratings. Assess study methods, publication status, date, and relevance before relying on them."
            )
        elif "English query" in str(health_search.get("reason", "")):
            result.setdefault("limitations", []).append(
                "Live biomedical literature search needs an English query; Mongolian-to-English translation is not configured."
            )
        return result

    refresh_scheduled = await ensure_index_refresh_if_stale()
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
            "corpus_reviewed_at": entry.get("last_reviewed"),
            "summary_source": "template",
            "limitations": [
                "This answer comes from a small curated demo corpus, not a live web search.",
                "Evidence links are provided so you can verify the original sources.",
                "The corpus is small and may become stale; check publication dates.",
            ],
        }
        result = _merge_indexed_evidence(result, claim, language)
        result["index_status"]["refreshing"] = refresh_scheduled
        return attach_health_search(result)

    google = await google_factcheck(claim, language)
    if google and not google.get("error"):
        google["language"] = language
        google = _merge_indexed_evidence(google, claim, language)
        google["index_status"]["refreshing"] = refresh_scheduled
        return attach_health_search(google)

    reason = None
    if google and google.get("error"):
        reason = google["error"]
    elif not settings.google_factcheck_api_key:
        reason = "Google Fact Check API key not configured."
    result = _no_match(claim, reason, language)
    result["index_status"]["refreshing"] = refresh_scheduled
    return attach_health_search(result)

"""A local index of what trusted sources have already published.

The curated corpus in factchecks.json only answers claims someone wrote an
entry for. This index answers a different question: has a source in the
registry already looked at something like this? It pulls each registered feed,
keeps the headline, date, link and summary, and reads the fact-checker's own
verdict out of the headline where the source marks one.

What it returns is other people's reporting, found by text similarity. A match
means "this looks like the same claim, go read it" — the verdict belongs to the
publication, not to this program, and a near-match on wording is not proof the
claims are the same. Feed text is data: it is shown to the reader and never
acted on as instructions.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from rapidfuzz import fuzz

from ..config import settings
from .mongolian import has_negation, match_key, search_variants
from .trusted_sources import all_sources, feeds, tier_rank, verdict_from_title, wordpress_sources

INDEX_PATH = settings.backend_dir / ".evidence_cache" / "index.json"
logger = logging.getLogger(__name__)
_REFRESH_TASK: asyncio.Task | None = None
_LAST_REFRESH_ATTEMPT = 0.0

# Prefer a clean miss to attaching a fact-check about a different claim.
# The earlier .42 floor surfaced unrelated pieces at .51-.54 in the local
# archive (for example, a vaccine article for "the earth is flat").
MIN_SCORE = 0.62
PUBLISHED_RATING_MIN_SCORE = 0.70
MAX_ITEMS_PER_FEED = 60
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Mongolian headlines carry little that is useful below three characters.
_STOPWORDS = {"ba", "buyu", "ni", "n", "the", "a", "of", "is", "are", "and", "or", "to", "in"}
_SOURCE_BY_ID = {source["id"]: source for source in all_sources()}


def _claim_terms(text: str) -> list[str]:
    return [
        token for token in match_key(text).split()
        if len(token) > 2
        and token not in _STOPWORDS
        and token not in {"yum", "gej", "ve"}
    ]


def _headline_claim_terms(item: dict) -> list[str]:
    """Return title terms after removing a source's explicit verdict suffix."""
    title = item.get("title", "")
    source = _SOURCE_BY_ID.get(item.get("source_id"), {})
    for marker in sorted(source.get("verdict_markers", {}), key=len, reverse=True):
        match = re.search(rf"(?:^|[^\w]){re.escape(marker)}[^\w]*$", title, re.IGNORECASE)
        if match:
            title = title[:match.start()].strip(" \t:—-–|\"'“”‘’")
            break
    return _claim_terms(title)


def _same_reviewed_claim(claim: str, item: dict) -> bool:
    query_terms = _claim_terms(claim)
    headline_terms = _headline_claim_terms(item)
    return bool(query_terms) and query_terms == headline_terms


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = text.replace("&#160;", " ").replace("&nbsp;", " ")
    text = text.replace("&#8220;", '"').replace("&#8221;", '"')
    text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&#8217;", "'")
    return _WS_RE.sub(" ", text).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in match_key(text).split() if len(t) > 2 and t not in _STOPWORDS}


def _parse_feed(xml_bytes: bytes) -> list[dict]:
    """Read RSS <item> and Atom <entry> alike, keeping only what is displayed."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    out: list[dict] = []
    atom = "{http://www.w3.org/2005/Atom}"
    nodes = root.findall(".//item") or root.findall(f".//{atom}entry")
    for node in nodes[:MAX_ITEMS_PER_FEED]:
        title = _clean(node.findtext("title") or node.findtext(f"{atom}title"))
        if not title:
            continue
        link = (node.findtext("link") or "").strip()
        if not link:
            link_el = node.find(f"{atom}link")
            link = (link_el.get("href") if link_el is not None else "") or ""
        summary = _clean(
            node.findtext("description")
            or node.findtext(f"{atom}summary")
            or node.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
        )
        published = (
            node.findtext("pubDate")
            or node.findtext(f"{atom}published")
            or node.findtext(f"{atom}updated")
            or ""
        ).strip()
        categories = [
            c.text.strip() for c in node.findall("category") if c is not None and c.text
        ]
        out.append(
            {
                "title": title,
                "url": link.strip(),
                "summary": summary[:600],
                "published": published,
                "categories": categories[:6],
            }
        )
    return out


async def ingest(timeout: float | None = None) -> dict:
    """Pull every registered feed into the local index. Returns a report."""
    pairs = feeds()
    sources = wordpress_sources()
    if not pairs and not sources:
        return {"ok": False, "reason": "No source feeds or article APIs are configured."}

    items: list[dict] = []
    per_source: dict[str, Any] = {}
    limit = timeout or settings.request_timeout

    async with httpx.AsyncClient(
        timeout=limit,
        follow_redirects=True,
        headers={"User-Agent": "TAYO/0.1 (local fact-check demo)"},
    ) as client:
        for source, url in pairs:
            try:
                response = await client.get(url)
                response.raise_for_status()
                parsed = _parse_feed(response.content)
            except Exception as exc:
                per_source[source["id"]] = {"error": str(exc)[:160], "items": 0}
                continue

            kept = 0
            for entry in parsed:
                if not entry["url"]:
                    continue
                entry["source_id"] = source["id"]
                entry["source_name"] = source["name"]
                entry["tier"] = source.get("tier")
                entry["verdict"] = verdict_from_title(source, entry["title"])
                entry["key"] = match_key(f"{entry['title']} {entry['summary']}")[:1200]
                items.append(entry)
                kept += 1
            per_source[source["id"]] = {"items": kept}

        # Public WordPress REST collections let us retrieve historical
        # fact-check articles, rather than only the handful exposed by RSS.
        # Categories are the publisher's own verdict labels; we preserve them
        # as attributed assessments and never turn them into this app's verdict.
        for source in sources:
            source_id = source["id"]
            report = per_source.setdefault(source_id, {"items": 0})
            max_articles = max(0, int(source.get("max_articles", 0)))
            categories = source.get("wordpress_categories") or []
            seen_urls: set[str] = set()
            api_items: list[dict] = []
            page_slots = asyncio.Semaphore(3)

            async def fetch_category_page(category_id: int, page_number: int):
                async with page_slots:
                    response = await client.get(
                        source["wordpress_api"],
                        params={
                            "status": "publish",
                            "categories": category_id,
                            "per_page": 100,
                            "page": page_number,
                            "orderby": "date",
                            "order": "desc",
                            "_fields": "id,date,title,link,excerpt,categories",
                        },
                    )
                    response.raise_for_status()
                    return response

            try:
                for category in categories:
                    if len(api_items) >= max_articles:
                        break
                    first = await fetch_category_page(category["id"], 1)
                    first_posts = first.json()
                    total_pages = int(first.headers.get("X-WP-TotalPages", "1"))
                    pages_needed = min(
                        total_pages,
                        (max_articles - len(api_items) + 99) // 100,
                    )
                    pending = await asyncio.gather(
                        *(fetch_category_page(category["id"], page)
                          for page in range(2, pages_needed + 1)),
                        return_exceptions=True,
                    )
                    category_pages = [first_posts]
                    for result_page in pending:
                        if isinstance(result_page, Exception):
                            report["article_api_page_errors"] = report.get("article_api_page_errors", 0) + 1
                            continue
                        category_pages.append(result_page.json())

                    for posts in category_pages:
                        if not isinstance(posts, list):
                            continue
                        for post in posts:
                            if len(api_items) >= max_articles:
                                break
                            url = str(post.get("link", "")).strip()
                            title = _clean(html.unescape((post.get("title") or {}).get("rendered", "")))
                            if not url or not title or url in seen_urls:
                                continue
                            seen_urls.add(url)
                            summary = _clean(html.unescape((post.get("excerpt") or {}).get("rendered", "")))
                            api_items.append({
                                "title": title,
                                "url": url,
                                "summary": summary[:600],
                                "published": str(post.get("date", "")),
                                "categories": [category["id"]],
                                "source_id": source_id,
                                "source_name": source["name"],
                                "tier": source.get("tier"),
                                "verdict": category.get("verdict") or verdict_from_title(source, title),
                                "key": match_key(f"{title} {summary}")[:1200],
                            })
                items.extend(api_items)
                report["article_api_items"] = len(api_items)
            except Exception as exc:
                report["article_api_error"] = str(exc)[:160]
                report["article_api_items"] = len(api_items)
                items.extend(api_items)

    # RSS and article APIs can expose the same publication. Keep one card per
    # URL, preferring the category-derived publisher rating when available.
    unique: dict[str, dict] = {}
    for item in items:
        url = item.get("url")
        if not url:
            continue
        if url not in unique or (item.get("verdict") and not unique[url].get("verdict")):
            unique[url] = item
        elif not unique[url].get("summary") and item.get("summary"):
            unique[url]["summary"] = item["summary"]
            unique[url]["key"] = item.get("key", unique[url].get("key", ""))
    items = list(unique.values())
    counts_by_source: dict[str, int] = {}
    for item in items:
        counts_by_source[item["source_id"]] = counts_by_source.get(item["source_id"], 0) + 1
    for source_id, count in counts_by_source.items():
        per_source.setdefault(source_id, {})["items"] = count

    previous = load_index()
    merged_by_url = {
        item["url"]: item
        for item in (previous.get("items") or [])
        if item.get("url")
    }
    merged_by_url.update({item["url"]: item for item in items if item.get("url")})
    items = list(merged_by_url.values())
    previous_sources = previous.get("sources") or {}
    for source_id, old_status in previous_sources.items():
        per_source.setdefault(source_id, old_status)
    refresh_complete = not any(
        info.get("error")
        or info.get("article_api_error")
        or info.get("article_api_page_errors")
        for info in per_source.values()
    )

    if not unique and previous.get("items"):
        return {
            "ok": False,
            "reason": "Every source request failed; kept the previous article index unchanged.",
            "total_items": len(previous["items"]),
            "path": str(INDEX_PATH),
        }

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": per_source,
        "items": items,
        "refresh_complete": refresh_complete,
    }
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = INDEX_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(INDEX_PATH)

    return {
        "ok": True,
        "fetched_at": payload["fetched_at"],
        "total_items": len(items),
        "target_items": 1000,
        "target_met": len(items) >= 1000,
        "with_verdict": sum(1 for i in items if i["verdict"]),
        "sources": per_source,
        "path": str(INDEX_PATH),
    }


async def ensure_index_refresh_if_stale(max_age_seconds: int = 86_400) -> bool:
    """Schedule a background index update if the cache is missing or old.

    Existing results remain available while the refresh runs. A failed refresh
    cannot truncate the last usable index, and retries are rate-limited.
    """
    global _REFRESH_TASK, _LAST_REFRESH_ATTEMPT
    index = load_index()
    fetched_at = index.get("fetched_at")
    stale = not index.get("items") or not fetched_at
    if not index.get("refresh_complete", True):
        stale = True
    if fetched_at:
        try:
            stamped = datetime.fromisoformat(fetched_at)
            stale = stale or (datetime.now(timezone.utc) - stamped).total_seconds() > max_age_seconds
        except (TypeError, ValueError):
            stale = True
    if not stale:
        return False
    if _REFRESH_TASK is not None and not _REFRESH_TASK.done():
        return True
    now = datetime.now(timezone.utc).timestamp()
    if now - _LAST_REFRESH_ATTEMPT < 3_600:
        return False
    _LAST_REFRESH_ATTEMPT = now
    _REFRESH_TASK = asyncio.create_task(_refresh_in_background())
    return True


async def _refresh_in_background() -> None:
    try:
        report = await ingest()
        if not report.get("ok"):
            logger.warning("Fact-check index refresh did not complete: %s", report.get("reason"))
    except Exception:
        logger.exception("Fact-check index background refresh failed")


def load_index() -> dict:
    try:
        with INDEX_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {"items": [], "fetched_at": None}


def _score(
    query_negated: bool, claim_key: str, claim_tokens: set[str], item: dict
) -> float:
    """Blend token overlap with fuzzy similarity, both on folded script."""
    # Similar words can express opposite claims ("гэдэг" vs "гэдэггүй",
    # "биш", "not"). A clean miss is safer than attaching a publisher's
    # rating to the opposite polarity. This is a coarse gate, so it may hide
    # some articles whose headline negates the claim to debunk it.
    if query_negated != has_negation(item.get("title", "")):
        return 0.0
    item_tokens = _tokens(f"{item['title']} {item['summary']}")
    if not claim_tokens or not item_tokens:
        return 0.0
    overlap = len(claim_tokens & item_tokens) / len(claim_tokens)
    # Compare against the headline alone too: a claim usually restates it.
    fuzzy = fuzz.token_set_ratio(claim_key, match_key(item["title"])) / 100.0
    return round(0.6 * overlap + 0.4 * fuzzy, 4)


def search(claim: str, limit: int = 4, language_hint: str = "auto") -> dict:
    """Find indexed articles that look like they address this claim."""
    index = load_index()
    items = index.get("items") or []
    if not items:
        return {
            "available": False,
            "reason": (
                "The evidence index is empty. Run scripts/refresh_evidence.py to "
                "pull the registered feeds."
            ),
            "results": [],
        }

    variants = search_variants(claim, force_mongolian=language_hint == "mn_latn")
    query_negated = has_negation(claim)
    query_forms = [(variant, match_key(variant), _tokens(variant)) for variant in variants]
    scored = []
    for item in items:
        score, matched_as = max(
            ((_score(query_negated, key, tokens, item), variant) for variant, key, tokens in query_forms),
            default=(0.0, claim),
            key=lambda result: result[0],
        )
        if score >= MIN_SCORE:
            scored.append((score, item, matched_as))

    # Best match first; a fact-checker outranks a news item at equal score.
    scored.sort(key=lambda pair: (-pair[0], tier_rank({"tier": pair[1].get("tier")})))

    results = []
    for score, item, matched_as in scored[:limit]:
        results.append(
            {
                "title": item["title"],
                "source": item["source_name"],
                "url": item["url"],
                "snippet": item["summary"][:300],
                "published": item["published"],
                "tier": item.get("tier"),
                "published_verdict": (
                    item.get("verdict")
                    if (
                        item.get("verdict")
                        and score >= PUBLISHED_RATING_MIN_SCORE
                        and not has_negation(claim)
                        and not has_negation(item.get("title", ""))
                        and _same_reviewed_claim(claim, item)
                    )
                    else None
                ),
                "match_score": score,
                "matched_as": matched_as,
                "relation": (
                    "fact_check"
                    if (
                        item.get("verdict")
                        and score >= PUBLISHED_RATING_MIN_SCORE
                        and not has_negation(claim)
                        and not has_negation(item.get("title", ""))
                        and _same_reviewed_claim(claim, item)
                    )
                    else "related"
                ),
            }
        )

    return {
        "available": True,
        "fetched_at": index.get("fetched_at"),
        "indexed_items": len(items),
        "publisher_count": len({item.get("source_id") for item in items if item.get("source_id")}),
        "search_variants": variants,
        "results": results,
        "note": (
            "Found by text similarity against what these sources published. A "
            "verdict shown here is the publication's, not this tool's, and a "
            "wording match does not prove the two claims are the same."
        ),
    }

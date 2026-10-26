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

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from rapidfuzz import fuzz

from ..config import settings
from .mongolian import match_key
from .trusted_sources import feeds, tier_rank, verdict_from_title

INDEX_PATH = settings.backend_dir / ".evidence_cache" / "index.json"

# Headlines are short; below this a shared word or two is coincidence.
MIN_SCORE = 0.42
MAX_ITEMS_PER_FEED = 60
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Mongolian headlines carry little that is useful below three characters.
_STOPWORDS = {"ba", "buyu", "ni", "n", "the", "a", "of", "is", "are", "and", "or", "to", "in"}


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
    if not pairs:
        return {"ok": False, "reason": "No feeds are configured in the registry."}

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

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": per_source,
        "items": items,
    }
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    return {
        "ok": True,
        "fetched_at": payload["fetched_at"],
        "total_items": len(items),
        "with_verdict": sum(1 for i in items if i["verdict"]),
        "sources": per_source,
        "path": str(INDEX_PATH),
    }


def load_index() -> dict:
    try:
        with INDEX_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {"items": [], "fetched_at": None}


def _score(claim_key: str, claim_tokens: set[str], item: dict) -> float:
    """Blend token overlap with fuzzy similarity, both on folded script."""
    item_tokens = _tokens(f"{item['title']} {item['summary']}")
    if not claim_tokens or not item_tokens:
        return 0.0
    overlap = len(claim_tokens & item_tokens) / len(claim_tokens)
    # Compare against the headline alone too: a claim usually restates it.
    fuzzy = fuzz.token_set_ratio(claim_key, match_key(item["title"])) / 100.0
    return round(0.6 * overlap + 0.4 * fuzzy, 4)


def search(claim: str, limit: int = 4) -> dict:
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

    claim_key = match_key(claim)
    claim_tokens = _tokens(claim)
    scored = []
    for item in items:
        score = _score(claim_key, claim_tokens, item)
        if score >= MIN_SCORE:
            scored.append((score, item))

    # Best match first; a fact-checker outranks a news item at equal score.
    scored.sort(key=lambda pair: (-pair[0], tier_rank({"tier": pair[1].get("tier")})))

    results = []
    for score, item in scored[:limit]:
        results.append(
            {
                "title": item["title"],
                "source": item["source_name"],
                "url": item["url"],
                "snippet": item["summary"][:300],
                "published": item["published"],
                "tier": item.get("tier"),
                "published_verdict": item.get("verdict"),
                "match_score": score,
                "relation": "fact_check" if item.get("verdict") else "related",
            }
        )

    return {
        "available": True,
        "fetched_at": index.get("fetched_at"),
        "indexed_items": len(items),
        "results": results,
        "note": (
            "Found by text similarity against what these sources published. A "
            "verdict shown here is the publication's, not this tool's, and a "
            "wording match does not prove the two claims are the same."
        ),
    }

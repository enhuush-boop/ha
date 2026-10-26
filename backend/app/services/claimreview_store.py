"""Local searchable store for structured Google ClaimReview metadata.

The feed contains publishers' own assessments and links back to their articles.
Those ratings are attributed to their publisher and never become a TAYO verdict.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from rapidfuzz import fuzz

from ..config import settings
from .mongolian import match_key

FEED_URL = "https://storage.googleapis.com/datacommons-feeds/claimreview/latest/data.json"
FEED_PATH = settings.backend_dir / ".data" / "claimreview_feed.json"
DB_PATH = settings.backend_dir / ".data" / "claimreview.sqlite3"
logger = logging.getLogger(__name__)


def _iter_array_items(handle, key: str = "dataFeedElement") -> Iterator[dict]:
    """Stream objects from a top-level JSON array without loading a 200MB file."""
    decoder = json.JSONDecoder()
    buffer = ""
    found_array = False
    while not found_array:
        marker = buffer.find(f'"{key}"')
        if marker >= 0:
            bracket = buffer.find("[", marker)
            if bracket >= 0:
                buffer = buffer[bracket + 1:]
                found_array = True
                break
        chunk = handle.read(64 * 1024)
        if not chunk:
            return
        buffer += chunk
        if len(buffer) > 256 * 1024 and marker < 0:
            buffer = buffer[-256:]

    while True:
        buffer = buffer.lstrip()
        while buffer.startswith(","):
            buffer = buffer[1:].lstrip()
        if buffer.startswith("]"):
            return
        try:
            value, end = decoder.raw_decode(buffer)
        except json.JSONDecodeError:
            chunk = handle.read(64 * 1024)
            if not chunk:
                return
            buffer += chunk
            continue
        buffer = buffer[end:]
        if isinstance(value, dict):
            yield value


def _url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    return value if parsed.scheme == "https" and parsed.hostname else ""


def _claim_reviews(feed_path: Path) -> Iterator[dict]:
    with feed_path.open("r", encoding="utf-8") as handle:
        for element in _iter_array_items(handle):
            items = element.get("item", [])
            if isinstance(items, dict):
                items = [items]
            for review in items if isinstance(items, list) else []:
                if isinstance(review, dict) and review.get("@type") == "ClaimReview":
                    yield review


def import_feed(feed_path: Path = FEED_PATH) -> dict:
    """Rebuild the local FTS index from a downloaded ClaimReview data feed."""
    if not feed_path.exists():
        return {"ok": False, "reason": f"Feed file not found: {feed_path}"}
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    staging = DB_PATH.with_suffix(".staging.sqlite3")
    staging.unlink(missing_ok=True)
    conn = sqlite3.connect(staging)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("""CREATE VIRTUAL TABLE reviews USING fts5(
        record_id UNINDEXED, claimReviewed, claimKey, publisher UNINDEXED,
        publisher_url UNINDEXED, article_url UNINDEXED, published UNINDEXED,
        rating UNINDEXED, explanation UNINDEXED, tokenize='unicode61')""")
    seen: set[str] = set()
    count = 0
    try:
        with conn:
            for review in _claim_reviews(feed_path):
                claim = str(review.get("claimReviewed") or "").strip()
                article_url = _url(review.get("url"))
                if not claim or not article_url:
                    continue
                review_id = str(review.get("@id") or review.get("id") or article_url)
                if review_id in seen:
                    continue
                seen.add(review_id)
                author = review.get("author") or {}
                if not isinstance(author, dict):
                    author = {}
                rating_obj = review.get("reviewRating") or {}
                if not isinstance(rating_obj, dict):
                    rating_obj = {}
                rating = rating_obj.get("alternateName") or rating_obj.get("ratingValue") or ""
                explanation = rating_obj.get("ratingExplanation") or ""
                if isinstance(explanation, dict):
                    explanation = explanation.get("text", "")
                conn.execute("""INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?)""", (
                    review_id, claim, match_key(claim),
                    str(author.get("name") or "ClaimReview publisher"),
                    _url(author.get("url")), article_url,
                    str(review.get("datePublished") or ""),
                    str(rating)[:240], str(explanation)[:1200],
                ))
                count += 1
        conn.close()
        staging.replace(DB_PATH)
    except Exception:
        conn.close()
        staging.unlink(missing_ok=True)
        raise
    meta = {
        "source": "Google Fact Check Data Feed / Data Commons",
        "feed_url": FEED_URL,
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "records": count,
        "license": "CC BY compilation; original article content remains on publisher sites",
    }
    DB_PATH.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, **meta, "database": str(DB_PATH)}


def search(claim: str, limit: int = 6) -> list[dict]:
    """Search imported ClaimReview rows; exact claim wording alone carries rating."""
    if not DB_PATH.exists():
        return []
    key = match_key(claim)
    terms = list(dict.fromkeys(token for token in key.split() if len(token) > 2))
    if not terms:
        return []
    # OR retrieval keeps paraphrases in reach; the local similarity pass ranks
    # them and we only expose a publisher's rating for identical normalized text.
    fts_query = " OR ".join(f'"{re.sub(chr(34), "", t)}"' for t in sorted(terms, key=len, reverse=True)[:6])
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1)
        rows = conn.execute("""SELECT claimReviewed, publisher, publisher_url,
            article_url, published, rating, explanation, claimKey
            FROM reviews WHERE reviews MATCH ? LIMIT 400""", (fts_query,)).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("ClaimReview search failed: %s", exc)
        return []
    ranked = []
    for claim_text, publisher, publisher_url, url, date, rating, explanation, key_text in rows:
        score = fuzz.token_set_ratio(key, key_text) / 100.0
        if score < 0.55:
            continue
        ranked.append((score, claim_text, publisher, publisher_url, url, date, rating, explanation, key_text))
    ranked.sort(key=lambda row: -row[0])
    return [{
        "title": item[1][:300],
        "source": item[2],
        "url": item[4],
        "snippet": item[7][:420] or f"Publisher's ClaimReview rating: {item[6] or 'not stated'}.",
        "published": item[5],
        "tier": "fact_check",
        "relation": "fact_check",
        "published_verdict": item[6] or None if item[8] == key else None,
        "matched_claim": item[1],
        "match_score": round(item[0], 3),
        "publisher_url": item[3],
    } for item in ranked[:limit]]


def status() -> dict:
    meta_path = DB_PATH.with_suffix(".json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return {"available": True, **meta}
    except Exception:
        return {"available": False, "records": 0, "database": str(DB_PATH)}

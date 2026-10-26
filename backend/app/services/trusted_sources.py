"""The registry of sources this build will quote, and what is known about each.

Two jobs. First, say whether a link a reader is looking at belongs to a source
worth starting from — a Facebook post that cites nso.mn is in a different
position from one that cites nothing. Second, supply the feeds that
evidence_index pulls from.

Nothing here decides whether a claim is true. A source's tier says what kind of
authority it has, not that a particular article of its is correct, and the
registry deliberately records a source's known slant (Montsame's state
ownership, for instance) rather than presenting every entry as neutral.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from ..config import settings

# A tier is a claim about what the source *is*, used to order evidence, never
# to settle a question on its own.
TIER_RANK = {
    "fact_check": 0,
    "official": 1,
    "international": 2,
    "news": 3,
}


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    path = settings.data_dir / "trusted_sources.json"
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        return {
            "sources": [],
            "error": f"Trusted-source registry could not be loaded: {exc}",
        }
    if not isinstance(data.get("sources"), list):
        return {"sources": [], "error": "Registry has no 'sources' list."}
    return data


def all_sources() -> list[dict]:
    return load_registry().get("sources", [])


def _host_matches(host: str, domain: str) -> bool:
    """True for the domain itself and for any subdomain of it."""
    host = host.lower().strip(".")
    domain = domain.lower().strip(".")
    return host == domain or host.endswith("." + domain)


def source_for_url(url: str) -> dict | None:
    """Return the registry entry a URL belongs to, or None if unlisted."""
    try:
        host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    except Exception:
        return None
    if not host:
        return None
    # Longest domain first, so factcheck.afp.com wins over a bare afp.com entry.
    for source in sorted(all_sources(), key=lambda s: -len(s.get("domain", ""))):
        if source.get("domain") and _host_matches(host, source["domain"]):
            return source
    return None


def describe_url(url: str) -> dict:
    """Say what is known about a link, for a reader deciding whether to trust it."""
    source = source_for_url(url)
    if not source:
        return {
            "listed": False,
            "url": url,
            "note": (
                "This domain is not in the registry. That is not a verdict on it — "
                "the list is short and deliberately conservative."
            ),
        }
    return {
        "listed": True,
        "url": url,
        "id": source["id"],
        "name": source["name"],
        "tier": source.get("tier"),
        "language": source.get("language"),
        "note": source.get("note"),
    }


def feeds() -> list[tuple[dict, str]]:
    """Every (source, feed_url) pair the index can ingest."""
    out: list[tuple[dict, str]] = []
    for source in all_sources():
        for url in source.get("feeds") or []:
            out.append((source, url))
    return out


def verdict_from_title(source: dict, title: str) -> str | None:
    """Read a fact-checker's own verdict out of its headline.

    Mongolian fact-check headlines put the verdict in capitals at the end
    ("… ТӨӨРӨГДҮҮЛСЭН"). The marker table per source is observed, not
    exhaustive: a headline with no known marker returns None rather than a
    guess, and the article still gets surfaced as related reading.
    """
    markers = source.get("verdict_markers") or {}
    upper = title.upper()
    for marker, verdict in markers.items():
        if marker.upper() in upper:
            return verdict
    return None


def tier_rank(source: dict | None) -> int:
    if not source:
        return 99
    return TIER_RANK.get(source.get("tier", ""), 50)

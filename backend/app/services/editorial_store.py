"""Persistent local registry of sources and append-only journalist assessments.

The store records editorial input as supplied. It does not certify a source,
verify a reviewer's identity, or turn a collection of reviews into a verdict.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..config import settings
from .mongolian import claim_identity_forms, has_negation, match_key

DB_PATH = settings.backend_dir / ".data" / "editorial.sqlite3"
VERDICTS = {"True", "False", "Partly true", "Misleading", "Unproven", "Satire", "Fabricated", "Other"}
TIERS = {"fact_check", "official", "research", "international", "news"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                domain TEXT NOT NULL UNIQUE,
                source_json TEXT NOT NULL,
                added_at TEXT NOT NULL,
                added_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY,
                claim TEXT NOT NULL,
                claim_key TEXT NOT NULL,
                claim_negated INTEGER NOT NULL,
                verdict TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                rationale TEXT NOT NULL,
                source_ids_json TEXT NOT NULL,
                evidence_urls_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS reviews_claim_key_created
                ON reviews(claim_key, created_at DESC);
            CREATE TABLE IF NOT EXISTS review_claim_forms (
                review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                claim_form TEXT NOT NULL,
                PRIMARY KEY (review_id, claim_form)
            );
            CREATE INDEX IF NOT EXISTS review_claim_forms_form
                ON review_claim_forms(claim_form);
            """
        )
        old_reviews = conn.execute(
            "SELECT id, claim FROM reviews WHERE id NOT IN (SELECT review_id FROM review_claim_forms)"
        ).fetchall()
        for row in old_reviews:
            conn.executemany(
                "INSERT OR IGNORE INTO review_claim_forms (review_id, claim_form) VALUES (?, ?)",
                [(row["id"], form) for form in claim_identity_forms(row["claim"])],
            )
        registry_path = settings.data_dir / "trusted_sources.json"
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            registry = {"sources": []}
        for source in registry.get("sources", []):
            if not source.get("id") or not source.get("domain"):
                continue
            existing = conn.execute(
                "SELECT source_json, added_by FROM sources WHERE id = ?",
                (source["id"],),
            ).fetchone()
            if existing and existing["added_by"] == "curated registry":
                old_source = json.loads(existing["source_json"])
                for field in ("verification_status", "source_reviewed_at", "source_reviewed_by", "verification_note"):
                    if field in old_source:
                        source[field] = old_source[field]
                conn.execute(
                    "UPDATE sources SET domain = ?, source_json = ? WHERE id = ?",
                    (source["domain"].lower(), json.dumps(source, ensure_ascii=False), source["id"]),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO sources (id, domain, source_json, added_at, added_by) VALUES (?, ?, ?, ?, ?)",
                    (
                        source["id"], source["domain"].lower(),
                        json.dumps(source, ensure_ascii=False), _now(),
                        "curated registry",
                    ),
                )


def _public_https_url(value: str) -> tuple[str, str]:
    raw = (value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a public https:// URL without a username or password.")
    host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValueError("The URL must point to a public host.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("The URL must point to a public host.")
    domain = host[4:] if host.startswith("www.") else host
    clean_url = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
    return domain, clean_url


def list_sources(query: str = "") -> list[dict[str, Any]]:
    initialize()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT source_json, added_at, added_by FROM sources ORDER BY domain"
        ).fetchall()
    out = []
    for row in rows:
        source = json.loads(row["source_json"])
        source["added_at"] = row["added_at"]
        source["added_by"] = row["added_by"]
        source["verification_status"] = source.get("verification_status", "curated list; not independently rechecked")
        out.append(source)
    needle = " ".join(query.casefold().split())
    if needle:
        out = [
            source for source in out
            if needle in " ".join((
                source.get("name", ""), source.get("domain", ""),
                source.get("note", ""), source.get("tier", ""),
            )).casefold()
        ]
    return out


def add_source(*, name: str, url: str, tier: str, note: str = "", language: str = "mn", added_by: str = "") -> dict:
    initialize()
    clean_name = " ".join(name.split())
    clean_note = " ".join(note.split())
    clean_by = " ".join(added_by.split()) or "local journalist"
    if not clean_name or len(clean_name) > 120:
        raise ValueError("Source name must be between 1 and 120 characters.")
    if tier not in TIERS:
        raise ValueError("Choose a source type from the list.")
    if len(clean_note) > 600 or len(clean_by) > 100:
        raise ValueError("Notes or contributor name are too long.")
    domain, homepage = _public_https_url(url)
    source_id = "saved-" + re.sub(r"[^a-z0-9]+", "-", domain).strip("-")[:80]
    source = {
        "id": source_id,
        "name": clean_name,
        "domain": domain,
        "tier": tier,
        "language": language or "mn",
        "feeds": [],
        "feed_verified": False,
        "homepage_url": homepage,
        "note": clean_note,
        "verification_status": "pending editorial review",
    }
    added_at = _now()
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO sources (id, domain, source_json, added_at, added_by) VALUES (?, ?, ?, ?, ?)",
                (source_id, domain, json.dumps(source, ensure_ascii=False), added_at, clean_by),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("That website is already in the source database.") from exc
    source["added_at"] = added_at
    source["added_by"] = clean_by
    return source


def mark_source_reviewed(source_id: str, reviewer: str, note: str = "") -> dict[str, Any]:
    initialize()
    clean_reviewer = " ".join(reviewer.split())
    clean_note = " ".join(note.split())
    if not clean_reviewer or len(clean_reviewer) > 100:
        raise ValueError("Enter who reviewed this source.")
    if len(clean_note) > 600:
        raise ValueError("Source review note is too long.")
    with _connect() as conn:
        row = conn.execute(
            "SELECT source_json, added_at, added_by FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if not row:
            raise KeyError("Source not found.")
        source = json.loads(row["source_json"])
        source.update({
            "verification_status": "journalist-reviewed",
            "source_reviewed_at": _now(),
            "source_reviewed_by": clean_reviewer,
            "verification_note": clean_note,
        })
        conn.execute(
            "UPDATE sources SET source_json = ? WHERE id = ?",
            (json.dumps(source, ensure_ascii=False), source_id),
        )
        source["added_at"] = row["added_at"]
        source["added_by"] = row["added_by"]
        return source


def _validated_evidence_url(value: str) -> str:
    _, clean = _public_https_url(value)
    return clean


def add_review(
    *, claim: str, reviewer: str, verdict: str, rationale: str,
    source_ids: list[str] | None = None, evidence_urls: list[str] | None = None,
) -> dict[str, Any]:
    initialize()
    clean_claim = " ".join(claim.split())
    clean_reviewer = " ".join(reviewer.split())
    clean_rationale = rationale.strip()
    if len(clean_claim) < 3 or len(clean_claim) > 10_000:
        raise ValueError("Claim must be between 3 and 10,000 characters.")
    if not clean_reviewer or len(clean_reviewer) > 100:
        raise ValueError("Enter the journalist's name (up to 100 characters).")
    if verdict not in VERDICTS:
        raise ValueError("Choose a rating from the list.")
    if len(clean_rationale) < 10 or len(clean_rationale) > 4_000:
        raise ValueError("Explain the rating in 10 to 4,000 characters.")
    source_ids = list(dict.fromkeys(source_ids or []))
    evidence_urls = list(dict.fromkeys(_validated_evidence_url(url) for url in (evidence_urls or []) if url.strip()))
    if len(source_ids) > 10 or len(evidence_urls) > 10:
        raise ValueError("A review can cite up to 10 saved sources and 10 URLs.")

    with _connect() as conn:
        rows = []
        if source_ids:
            marks = ",".join("?" for _ in source_ids)
            rows = conn.execute(
                f"SELECT id, source_json FROM sources WHERE id IN ({marks})",
                source_ids,
            ).fetchall()
        saved = {row["id"]: json.loads(row["source_json"]) for row in rows}
        missing = [source_id for source_id in source_ids if source_id not in saved]
        if missing:
            raise ValueError("One or more selected sources are no longer in the database.")
        review_id = uuid.uuid4().hex
        created_at = _now()
        claim_key = match_key(clean_claim)
        conn.execute(
            "INSERT INTO reviews (id, claim, claim_key, claim_negated, verdict, reviewer, rationale, source_ids_json, evidence_urls_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                review_id, clean_claim, claim_key, int(has_negation(clean_claim)),
                verdict, clean_reviewer, clean_rationale,
                json.dumps(source_ids), json.dumps(evidence_urls), created_at,
            ),
        )
        conn.executemany(
            "INSERT INTO review_claim_forms (review_id, claim_form) VALUES (?, ?)",
            [(review_id, form) for form in claim_identity_forms(clean_claim)],
        )
    return {
        "id": review_id,
        "claim": clean_claim,
        "verdict": verdict,
        "reviewer": clean_reviewer,
        "rationale": clean_rationale,
        "sources": [saved[source_id] for source_id in source_ids],
        "evidence_urls": evidence_urls,
        "created_at": created_at,
        "reviewer_identity_verified": False,
    }


def reviews_for_claim(claim: str, limit: int = 25) -> list[dict[str, Any]]:
    initialize()
    keys = set(claim_identity_forms(claim))
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT r.* FROM reviews r JOIN review_claim_forms f ON f.review_id = r.id WHERE f.claim_form IN ({marks}) ORDER BY r.created_at DESC LIMIT ?",
            (*keys, min(max(limit, 1), 100)),
        ).fetchall()
        source_rows = conn.execute("SELECT id, source_json FROM sources").fetchall()
    source_map = {row["id"]: json.loads(row["source_json"]) for row in source_rows}
    result = []
    query_polarity = has_negation(claim)
    for row in rows:
        if bool(row["claim_negated"]) != query_polarity:
            continue
        source_ids = json.loads(row["source_ids_json"])
        result.append({
            "id": row["id"],
            "claim": row["claim"],
            "verdict": row["verdict"],
            "reviewer": row["reviewer"],
            "rationale": row["rationale"],
            "sources": [source_map[source_id] for source_id in source_ids if source_id in source_map],
            "evidence_urls": json.loads(row["evidence_urls_json"]),
            "created_at": row["created_at"],
            "reviewer_identity_verified": False,
        })
    return result


def recent_reviews(limit: int = 50) -> list[dict[str, Any]]:
    initialize()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT claim FROM reviews ORDER BY created_at DESC LIMIT ?",
            (min(max(limit, 1), 100),),
        ).fetchall()
    seen: set[str] = set()
    out = []
    for row in rows:
        forms = claim_identity_forms(row["claim"])
        key = forms[-1] if forms else ""
        if key and key not in seen:
            seen.add(key)
            out.extend(reviews_for_claim(row["claim"], limit=10))
    return out[:100]


def export_bundle() -> dict[str, Any]:
    initialize()
    with _connect() as conn:
        source_rows = conn.execute(
            "SELECT id, source_json, added_at, added_by FROM sources ORDER BY domain"
        ).fetchall()
        review_rows = conn.execute(
            "SELECT * FROM reviews ORDER BY created_at"
        ).fetchall()
    return {
        "format": "tayo-editorial-backup-v1",
        "exported_at": _now(),
        "sources": [
            {
                **json.loads(row["source_json"]),
                "added_at": row["added_at"],
                "added_by": row["added_by"],
            }
            for row in source_rows
        ],
        "reviews": [
            {
                "id": row["id"],
                "claim": row["claim"],
                "verdict": row["verdict"],
                "reviewer": row["reviewer"],
                "rationale": row["rationale"],
                "source_ids": json.loads(row["source_ids_json"]),
                "evidence_urls": json.loads(row["evidence_urls_json"]),
                "created_at": row["created_at"],
                "reviewer_identity_verified": False,
            }
            for row in review_rows
        ],
    }

#!/usr/bin/env python3
"""Download Google's public ClaimReview feed and build TAYO's local index.

Usage: .venv/bin/python scripts/refresh_claimreview.py

The feed is large. It contains structured ratings and source links, not article
text. Each rating remains attributed to the publisher that submitted it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.claimreview_store import FEED_PATH, FEED_URL, import_feed, status  # noqa: E402


async def main() -> int:
    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = FEED_PATH.with_suffix(".json.part")
    size = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True,
                                     headers={"User-Agent": "TAYO/0.1 (ClaimReview index)"}) as client:
            async with client.stream("GET", FEED_URL) as response:
                response.raise_for_status()
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        handle.write(chunk)
                        size += len(chunk)
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    temp_path.replace(FEED_PATH)
    print(f"Downloaded {size:,} bytes from {FEED_URL}")
    report = import_feed(FEED_PATH)
    if not report.get("ok"):
        print(report.get("reason", "Import failed"), file=sys.stderr)
        return 1
    print(f"Indexed {report['records']:,} ClaimReview records into {report['database']}")
    print("Ratings remain publisher-attributed; the feed does not contain the full articles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

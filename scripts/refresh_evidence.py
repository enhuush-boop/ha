#!/usr/bin/env python3
"""Pull the registered trusted-source feeds into the local evidence index.

Usage:
    .venv/bin/python scripts/refresh_evidence.py
    .venv/bin/python scripts/refresh_evidence.py --show 5

Run it before relying on claim lookups, and again whenever you want fresher
coverage. The index is a cache of other people's headlines; it is written
under backend/.evidence_cache/ and is not committed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.evidence_index import ingest, load_index  # noqa: E402
from backend.app.services.trusted_sources import all_sources, feeds  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show", type=int, default=0, help="Print this many indexed headlines."
    )
    args = parser.parse_args()

    sources = all_sources()
    pairs = feeds()
    print(f"Registry: {len(sources)} sources, {len(pairs)} feed(s) to pull.")
    for source in sources:
        if not source.get("feeds"):
            continue
        print(f"  {source['name']}  [{source.get('tier')}]")

    print("\nFetching…")
    report = await ingest()
    if not report.get("ok"):
        print(f"  failed: {report.get('reason')}")
        return 1

    print(f"  fetched_at   : {report['fetched_at']}")
    print(f"  indexed items: {report['total_items']}")
    print(f"  with a published verdict: {report['with_verdict']}")
    for source_id, info in report["sources"].items():
        if info.get("error"):
            print(f"    {source_id}: ERROR {info['error']}")
        else:
            print(f"    {source_id}: {info['items']} item(s)")
    print(f"  written to   : {report['path']}")

    if args.show:
        print(f"\nFirst {args.show} indexed headlines:")
        for item in (load_index().get("items") or [])[: args.show]:
            verdict = item.get("verdict")
            tag = f"[{verdict}] " if verdict else ""
            print(f"  {tag}{item['title'][:88]}")
            print(f"      {item['source_name']} · {item.get('published', '')[:25]}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

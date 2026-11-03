#!/usr/bin/env python3
"""Rebuild PaLLi's Phase 7c inner-memory FTS index from local archives."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.inner_memory_index import rebuild_from_archive


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild the inner-memory FTS index.")
    parser.add_argument("--since", help="Optional earliest source date, YYYY-MM-DD.")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    since = date.fromisoformat(args.since) if args.since else None
    return await rebuild_from_archive(since=since)


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(_run(_parse_args(argv)))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

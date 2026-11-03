#!/usr/bin/env python3
"""Backfill inner-monologue weekly/monthly digest artifacts.

Usage:
    venv/Scripts/python.exe scripts/build_monologue_digests.py --since 2026-04-01
"""

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

from ai.monologue_compaction import build_historical_digests


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build PaLLi inner-monologue digests.")
    parser.add_argument("--since", required=True, help="Earliest source date to scan, YYYY-MM-DD.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing digest artifacts.")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    return await build_historical_digests(
        since=date.fromisoformat(args.since),
        force=bool(args.force),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = asyncio.run(_run(args))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

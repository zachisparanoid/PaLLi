#!/usr/bin/env python3
"""How much of PaLLi's prompt the provider served from its cache, per lane.

Read-only: it reads the daily logs and prints a table. Nothing else.

Usage (from the repo root):
    venv/bin/python scripts/cache_report.py                      # today
    venv/bin/python scripts/cache_report.py --since 2026-09-27   # that day onward
    venv/bin/python scripts/cache_report.py --since 2026-09-27 --logs-dir /some/logs

Two kinds of log line carry the counters (Cut the Cost, Task 1, 2026-09-27):
  - the reply lane:        ``llm_call session=... cache_hit=N cache_miss=N``  (lane "reply")
  - each background lane:  ``[reflect_turn] cache_hit=N cache_miss=N``
Lines written before the counters existed carry none and are not counted.

Which files: a daily log is named for the day the PROCESS STARTED
(``palli_<start>.log``, main.py) and rotated at midnight into
``palli_<start>.log._<day>``, so a process that has run for days writes today's
lines into a file named for an older day. The report therefore reads every
daily log (``palli_[0-9]*.log*`` — not ``palli_verbose_*`` / ``palli_debug_*``)
and keeps each line by its own leading ``YYYY-MM-DD``, on or after ``--since``.
A daily log over a week old is gzipped by the retention sweep
(``palli_<start>.log._<day>.gz``, core/retention.py) and read the same way; a
broken one costs only its own lines.

"share" is hit / (hit + miss): the part of the prompt billed at the cached
price. A lane with no prompt tokens at all shows 0.0%.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import os
import re
from typing import Iterable, Iterator

_LINE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2}) ")
_REPLY_LINE = re.compile(r"\bllm_call session=\S+ .*\bcache_hit=(\d+) cache_miss=(\d+)")
_LANE_LINE = re.compile(r"\[([^\]]+)\] cache_hit=(\d+) cache_miss=(\d+)")


def _share(hit: int, miss: int) -> float:
    total = hit + miss
    return hit / total if total else 0.0


def summarize(lines: Iterable[str]) -> dict[str, dict]:
    """Per lane: ``{"hit": int, "miss": int, "share": float}``. The reply
    lane's ``llm_call`` lines are lane ``reply``."""
    totals: dict[str, list[int]] = {}
    for line in lines:
        match = _REPLY_LINE.search(line)
        if match:
            lane, hit, miss = "reply", match.group(1), match.group(2)
        else:
            match = _LANE_LINE.search(line)
            if not match:
                continue
            lane, hit, miss = match.group(1), match.group(2), match.group(3)
        counts = totals.setdefault(lane, [0, 0])
        counts[0] += int(hit)
        counts[1] += int(miss)
    return {
        lane: {"hit": hit, "miss": miss, "share": _share(hit, miss)}
        for lane, (hit, miss) in totals.items()
    }


def _default_logs_dir() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, "logs")


def _log_files(logs_dir: str) -> list[str]:
    """Every daily log and its rotated copies, whatever day each is named for."""
    return sorted(glob.glob(os.path.join(logs_dir, "palli_[0-9]*.log*")))


def _open_log(path: str):
    """A daily log as text, plain or gzipped (retention, Batch 8 task 5)."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def _lines_since(paths: Iterable[str], since: dt.date) -> Iterator[str]:
    """The lines of ``paths`` whose leading ``YYYY-MM-DD`` is on or after
    ``since``. A line with no leading date (a traceback's tail) is skipped.
    A ``.gz`` that will not decompress costs only its own lines."""
    cutoff = since.isoformat()
    for path in paths:
        try:
            with _open_log(path) as fh:
                for line in fh:
                    match = _LINE_DATE.match(line)
                    if match and match.group(1) >= cutoff:
                        yield line
        except (OSError, EOFError):
            if not path.endswith(".gz"):
                raise


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--since", type=dt.date.fromisoformat, default=dt.date.today(),
        help="first day to count, YYYY-MM-DD (default: today); every later line counts too",
    )
    parser.add_argument(
        "--logs-dir", default=_default_logs_dir(),
        help="where the daily palli_YYYY-MM-DD.log files live (default: the repo's logs/)",
    )
    args = parser.parse_args(argv)

    files = _log_files(args.logs_dir)
    lanes = summarize(_lines_since(files, args.since))

    print(f"cache counters since {args.since.isoformat()}, from {len(files)} log file(s)")
    print(f"{'lane':<40} {'hit':>14} {'miss':>14} {'share':>7}")
    for lane, row in sorted(lanes.items(), key=lambda kv: (-(kv[1]["hit"] + kv[1]["miss"]), kv[0])):
        print(f"{lane:<40} {row['hit']:>14} {row['miss']:>14} {row['share']:>7.1%}")
    hit = sum(row["hit"] for row in lanes.values())
    miss = sum(row["miss"] for row in lanes.values())
    print(f"{'total':<40} {hit:>14} {miss:>14} {_share(hit, miss):>7.1%}")


if __name__ == "__main__":
    main()

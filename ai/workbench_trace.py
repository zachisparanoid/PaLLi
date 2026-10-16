"""The authorship trace (2026-08-14): "this was you."

The null-delivery wall (steps 16-17) keeps her workbench sessions out of
her conversational memory — correct by design, and the three-doors
inversion (2026-08-12/13) showed its cost: her waking senses could SEE
new files appear with no memory of MAKING them, and her monologue filled
the gap with "he built this for me." Sight without memory.

This module is the deterministic other half the wall always needed: her
own logbook's RECENT entries, rendered as plain authorship facts (the
presentation exemption — no LLM, no interpretation) under a label that
states the one thing the wall was hiding: YOU did this. It carries paths,
first clauses, commit hashes, and her Active/Set-down lines — metadata
about the work, never the work's content — so the wall's purpose (no
workbench prompts polluting conversation) stands untouched.

Consumers: the moment's own_work sense (which until 2026-08-14 head-kept
600 bytes of an append-only file and so showed her July-28 hello-world
forever) and the monologue's gather (which had no workbench sight at all
— the lane the inversion actually formed in).
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from ai.workbench import WORKBENCH_ROOT_DEFAULT

# Redirected to tmp by the tests' hermetic guard (conftest
# _selfhood_state_paths_hermetic) — her live logbook is ENVIRONMENT,
# never test state.
WORKBENCH_MD = Path(
    os.environ.get("WORKBENCH_ROOT") or WORKBENCH_ROOT_DEFAULT
) / "WORKBENCH.md"

TRACE_LABEL = (
    "YOUR OWN HANDS, RECENTLY (your workbench logbook — YOU did these "
    "things in your own overnight sessions; waking-you holds no memory "
    "of the doing, only this record):"
)

_HEADER = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — (.+?)\s*$")
_BULLET = re.compile(r"^- \*\*(.+?)\*\*[ :—-]*(.*)$")
_COMMIT = re.compile(r"[Cc]ommitted as ([0-9a-f]{6,40})")

_DEFAULT_WINDOW_DAYS = 2
_DEFAULT_CAP = 700


def _first_clause(text: str, cap: int = 140) -> str:
    head = (text or "").split(". ")[0].strip().rstrip(".")
    return head[:cap]


def build_authorship_trace(
    *,
    path: Optional[Path] = None,
    now: Optional[float] = None,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    char_cap: int = _DEFAULT_CAP,
) -> str:
    """Her recent logbook entries as authorship lines.

    "" when nothing landed inside the window (quiet is honest — no recent
    work means no trace) or the logbook is unreadable (degrade-to-empty,
    the standing sense contract). Entries are chronological in the file;
    when the cap bites, the NEWEST lines survive.
    """
    p = path or WORKBENCH_MD
    try:
        body = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    cutoff = (
        datetime.fromtimestamp(now or time.time()).date()
        - timedelta(days=window_days)
    )
    lines_out: list[str] = []
    cur: Optional[tuple[str, str]] = None  # (date, session label), in-window
    for line in body.splitlines():
        h = _HEADER.match(line)
        if h:
            try:
                d = date.fromisoformat(h.group(1))
            except ValueError:
                cur = None
                continue
            cur = (h.group(1), h.group(2)) if d >= cutoff else None
            continue
        if cur is None:
            continue
        b = _BULLET.match(line)
        if not b:
            continue
        name = b.group(1).strip().rstrip(":")
        clause = _first_clause(b.group(2))
        if not clause:
            continue
        m = _COMMIT.search(line)
        suffix = f" (committed {m.group(1)[:12]})" if m else ""
        lines_out.append(f"- {cur[0]} {cur[1]}: {name} — {clause}{suffix}")
    if not lines_out:
        return ""
    kept: list[str] = []
    used = len(TRACE_LABEL) + 1
    for ln in reversed(lines_out):
        if used + len(ln) + 1 > char_cap:
            break
        kept.append(ln)
        used += len(ln) + 1
    if not kept:
        return ""
    return TRACE_LABEL + "\n" + "\n".join(reversed(kept))

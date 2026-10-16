"""The origin lookup — Shared Idiolect (organ 4.12), spec §3.

Search, never an LLM: the earliest user/assistant row of the CURRENT
session's transcript whose normalized text contains the normalized phrase.
`user` → him, `assistant` → her. Reads the same append-only session files
the runner writes (core/agent/session.py), under the same root the
dispatcher resolves (PALLI_AGENT_SESSIONS_ROOT or data/agent_sessions).
Sync file reads run off the loop (find_origin_async) — the §12 lesson.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional

from ai.callbacks_store import CONVERSATIONAL_SURFACES, LINE_CAP, normalize
from core import session_keys as _session_keys

logger = logging.getLogger(__name__)

PHRASE_MIN = 3
PHRASE_MAX = 80
_ROLES = {"user": "him", "assistant": "her"}

# `role: "user"` is a TRANSPORT map, not a speaker map (review I1). On the
# reminder-fire and proactive surfaces every user row is machine-authored — all
# 291 of them in the live reminder file — and since DEFECT_RECORD §16 the
# synthesized row QUOTES HER RECENT LINES back inside it. Searching one of
# those and stamping `who: "him"` would put her own words in his mouth on a
# durable entry, which is the exact law this organ exists to keep. So only
# the files that are records of the two of them talking are ever searched.
#
# The tuple itself moved to ai/callbacks_store.py at the final review, when
# the BIRTH door was found to need the same law: re-exported here (imported
# above) so `lk.CONVERSATIONAL_SURFACES` still names it, one tuple for both.

# The window scan's stride. 240-wide windows at a 60 stride overlap by 180,
# so any match shorter than that is whole inside at least one of them.
_WINDOW_STEP = 60


def sessions_root() -> Path:
    """The dispatcher's own resolution (core/agent/dispatch.py)."""
    return Path(os.environ.get("PALLI_AGENT_SESSIONS_ROOT") or "data/agent_sessions")


def session_key_for(*, surface: str, channel_id: int, owner_id: int) -> str:
    """Byte-equal to core.agent.runner._derive_session_key for a plain
    (non-sidecar) turn — pinned by a test so the two can never drift. Since
    Batch 8 (ruling R7) both delegate to core.session_keys, the one place a
    session key is built."""
    return _session_keys.session_key_for(surface, channel_id, owner_id)


def session_path_for(*, surface: str, channel_id: int, owner_id: int) -> Path:
    from core.agent.session import _sanitize_session_key

    key = session_key_for(surface=surface, channel_id=channel_id, owner_id=owner_id)
    return sessions_root() / f"{_sanitize_session_key(key)}.jsonl"


def _row_text(row: Dict[str, Any]) -> str:
    content = row.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type", "text") == "text":
                parts.append(str(block.get("text") or ""))
        return " ".join(parts)
    return ""


def _receipt_line(collapsed: str, key: str) -> Optional[str]:
    """The ≤LINE_CAP window of the row that CONTAINS the match — never the
    row's bare head.

    The search runs on the whole row, so a head slice mints a receipt that
    omits the very bit it certifies whenever the phrase lands past character
    240 (review C1: measured at 7% of his live rows and 37% of hers, longest
    row 7142 chars). She then speaks that line as her proof. A receipt IS the
    line the bit was born in — the same law task 2 put at reflect's door.

    Returns None when no window holds it, which the caller reads as "this row
    cannot prove it" and keeps scanning. Unreachable in practice (a needle is
    at most 80 normalized characters and the windows overlap by 180), and
    fail-closed if it ever is: the store's rail would refuse such a plant.
    """
    if len(collapsed) <= LINE_CAP:
        return collapsed
    for start in range(0, len(collapsed), _WINDOW_STEP):
        window = collapsed[start:start + LINE_CAP]
        if key in normalize(window):
            return window
        if start + LINE_CAP >= len(collapsed):
            break
    return None


def find_origin(phrase: str, *, session_path: Path) -> Optional[Dict[str, str]]:
    """The earliest user/assistant row containing the phrase, or None.
    Returns {who, when, line, source}; never raises.

    The needle is normalized ONCE, above the loop: normalize() walks every
    character (NFC, casefold, the noise fold), and the owner's DM
    transcript is thousands of rows — a per-row re-fold of the same phrase
    would pay that cost again on each one.
    """
    key = normalize(phrase)
    if len(key) < PHRASE_MIN or len(str(phrase or "").strip()) > PHRASE_MAX:
        return None
    path = Path(session_path)
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.debug("callbacks_lookup: unreadable session %s (%s)", path, exc)
        return None
    for lineno, raw in enumerate(lines, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        who = _ROLES.get(str(row.get("role") or ""))
        if who is None:
            continue
        text = _row_text(row)
        if key and key in normalize(text):
            when = str(row.get("timestamp") or "").strip()
            if not when:
                continue
            # Compose, THEN collapse, THEN cut — `callbacks_store._clean`'s
            # exact order (task 1, round 3, N2). The store re-cleans whatever
            # line it is handed, so a window cut on RAW decomposed text is not
            # the string the book files; and where NFC EXPANDS (composition
            # exclusions like U+0958), the store's own 240-cap then falls
            # inside the text and can take the bit out of its own receipt,
            # which the C1 rail correctly refuses. Composing first makes the
            # window she quotes and the line the book holds one string.
            composed = " ".join(unicodedata.normalize("NFC", text).split())
            line = _receipt_line(composed, key)
            if line is None:
                logger.debug("callbacks_lookup: %s:%d holds the match but no "
                             "window proves it", path.name, lineno)
                continue
            return {"who": who, "when": when, "line": line,
                    "source": f"{path.name}:{lineno}"}
    return None


async def find_origin_async(phrase: str, *, session_path: Path) -> Optional[Dict[str, str]]:
    return await asyncio.to_thread(find_origin, phrase, session_path=session_path)

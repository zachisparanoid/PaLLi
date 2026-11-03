"""The grammar of a memory bullet (Batch 5, memory that picks, 2026-09-28).

One ``- `` line of MEMORY.md or MEMORY_ARCHIVE.md is one memory bullet. This
leaf module says what a bullet is made of — its leading ``[YYYY-MM-DD]``
stamps, its text, its trailing ``[archived YYYY-MM-DD]`` marker — and names
it: ``item_id_for`` is the ``mcb_<sha12>`` id the search index, the refs
table, the nightly sweep, ``memory restore`` and the core render all agree
on (ruling R4). The id hashes the bullet's TEXT alone, so the sweep's stamp
write and its move to the archive never change it, and a line keeps its
reference stamps wherever it sits.

A leaf on purpose (ruling R35): re, hashlib and time only. The identity
compiler renders the core from here without touching the memory service
(which imports the brain); ``core.memory_service`` re-exports these names so
its callers and tests need no change.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Optional

_BULLET_STAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\]\s*")
_ARCHIVED_MARKER_RE = re.compile(r"\s*\[archived \d{4}-\d{2}-\d{2}\]\s*$")
# The fixed bullets of identity/templates/MEMORY.md and MEMORY_ARCHIVE.md —
# never search items (tests/core/test_memory_index_bullets.py pins the two
# templates against this set).
_TEMPLATE_PLACEHOLDER_BULLETS = frozenset({
    "No active curated threads yet.",
    "No important decisions recorded yet.",
    "No durable references recorded yet.",
    "Nothing archived yet.",
    # The rendered form of MEMORY.md's `- Interests: {interests}` when the
    # profile lists none (document_store._build_template_context) — a fresh
    # install's first line, not a fact about anyone.
    "Interests: No explicit interest list yet.",
})


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def _sha(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _stamp_time(stamp: Optional[str]) -> Optional[float]:
    """A bullet's ``YYYY-MM-DD`` stamp as a local-midnight timestamp (the way
    reflect's sweep reads it), or None when there is no stamp or it is not a
    real date."""
    if not stamp:
        return None
    try:
        return time.mktime(time.strptime(stamp, "%Y-%m-%d"))
    except (ValueError, OverflowError):
        return None


def _split_bullet(line: str) -> tuple[Optional[str], str, str]:
    """A memory bullet line (or its bare body) -> ``(first_stamp, body, key)``:
    the first leading ``[YYYY-MM-DD]`` stamp or None; the text after the
    ``- `` and every leading stamp (what the item shows); and the id text —
    that text without a trailing ``[archived YYYY-MM-DD]`` marker, whitespace
    collapsed (what ``item_id_for`` hashes)."""
    text = str(line or "").strip()
    if text.startswith("- "):
        text = text[2:].lstrip()
    stamp: Optional[str] = None
    while True:
        match = _BULLET_STAMP_RE.match(text)
        if not match:
            break
        stamp = stamp or match.group(1)
        text = text[match.end():]
    body = _normalize_text(text)
    key = _normalize_text(_ARCHIVED_MARKER_RE.sub("", text))
    return stamp, body, key


def item_id_for(line: str) -> str:
    """The search-index id of a memory bullet, from the whole line or the bare
    body alike: ``mcb_`` + sha1 of its text (see ``_split_bullet``), 12 hex
    like ``mcs_``/``mss_``. The sweep, the refs table, ``memory restore`` and
    the core render all name a line by this."""
    return f"mcb_{_sha(_split_bullet(line)[2])[:12]}"


__all__ = [
    "_ARCHIVED_MARKER_RE",
    "_BULLET_STAMP_RE",
    "_TEMPLATE_PLACEHOLDER_BULLETS",
    "_normalize_text",
    "_sha",
    "_split_bullet",
    "_stamp_time",
    "item_id_for",
]

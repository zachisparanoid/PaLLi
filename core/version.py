"""One version, read from one file (week 1 task 5).

Three literals said what PaLLi's version was — `core/main.py`'s FastAPI title
block, `/api/health`, and `palli_config.__version__` (which said `0.1.0` while
the other two said `2.0.0`). A stranger asking "what am I running" got a
different answer depending on where they asked.

`VERSION` at the repo root is the file. Everything else reads it through here.
"""
from __future__ import annotations

from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"

# A missing VERSION means the tree was assembled by something that dropped it —
# a partial copy, a zip of the source directory. Say so in the number rather
# than crashing a boot over it.
FALLBACK = "0.0.0"

# The version cannot change while the process runs, and /api/health calls this
# on every request. Read the file once. `_CACHE = None` re-arms it, which is
# what a test that swaps VERSION_FILE has to do.
_CACHE: str | None = None


def read_version() -> str:
    """The contents of `VERSION`, or `0.0.0` if the file is not there."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    _CACHE = text or FALLBACK
    return _CACHE

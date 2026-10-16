"""The inbox — where a non-text file he sends her lands so her hands can
reach it (DEFECT_RECORD §19, 2026-09-02).

Intake used to drop anything that was not an image, a voice note, or a
text-by-suffix file: the row went empty and she never knew. Now every such
file is downloaded once into data/inbox/ and the turn carries a line that
names the file, its path, its size and type. The line's first line is the
filename — the shape persistence already bakes into history as
``[file attached — <name>]`` — so a later turn still knows where it is.

Caps and honesty: a file over the cap is not downloaded and the line says
so; a failed download is a line too, never a silence. Filenames are
flattened to a safe basename so nothing can escape the inbox.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

INBOX_MAX_BYTES = int(os.environ.get("PALLI_INBOX_MAX_BYTES") or 25 * 1024 * 1024)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def inbox_root() -> Path:
    """data/inbox by default (relative to the process root, like the other
    data/ stores); PALLI_INBOX_DIR pins it — tests point it at tmp_path."""
    return Path(os.environ.get("PALLI_INBOX_DIR") or "data/inbox")


def safe_name(filename: Any) -> str:
    base = Path(str(filename or "file")).name        # drops any directory part
    base = _UNSAFE.sub("_", base).strip("._") or "file"
    return base[:120]


def save_bytes(
    data: bytes,
    filename: Any,
    *,
    root: Optional[Path] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> Path:
    """Write one file into the inbox and return its path (split out of
    receive_file for Batch 3, 1.1: the image read keeps what she saw so she
    can look again). The name is ``<YYYYmmdd-HHMMSS>-<safe name>``; a
    collision gets ``-<n>`` after the stamp, so nothing is ever overwritten.
    Bytes over INBOX_MAX_BYTES raise ValueError and nothing is written —
    receive_file checks the cap itself first so its line can say so."""
    if len(data) > INBOX_MAX_BYTES:
        raise ValueError(f"{len(data)} bytes is over the {INBOX_MAX_BYTES}-byte inbox cap")
    root = Path(root) if root is not None else inbox_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now)().strftime("%Y%m%d-%H%M%S")
    name = safe_name(filename)
    path = root / f"{stamp}-{name}"
    n = 1
    while path.exists():
        n += 1
        path = root / f"{stamp}-{n}-{name}"
    path.write_bytes(data)
    return path


async def receive_file(
    ref: Any,
    *,
    fetch: Callable[[str], Awaitable[Optional[bytes]]],
    root: Optional[Path] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> str:
    """Download one non-text attachment into the inbox. Returns the context
    line for the turn: ``<filename>\\n[...where it went, or why not...]``."""
    filename = str(getattr(ref, "filename", None) or "file")
    mime = str(getattr(ref, "mime", None) or "unknown type")
    declared = getattr(ref, "size_bytes", None)
    url = str(getattr(ref, "url", None) or "")

    if isinstance(declared, int) and declared > INBOX_MAX_BYTES:
        logger.warning("inbox: %s not downloaded — %d bytes over the %d-byte cap", filename, declared, INBOX_MAX_BYTES)
        return (f"{filename}\n[not downloaded — {declared} bytes is over the {INBOX_MAX_BYTES}-byte inbox cap; "
                f"ask them for a smaller file or a link]")

    data = await fetch(url) if url else None
    if not data:
        logger.warning("inbox: download failed for %s (%s)", filename, mime)
        return f"{filename}\n[download failed — the file never arrived here; tell them]"
    if len(data) > INBOX_MAX_BYTES:
        logger.warning("inbox: %s discarded — %d bytes over the %d-byte cap", filename, len(data), INBOX_MAX_BYTES)
        return (f"{filename}\n[not downloaded — {len(data)} bytes is over the {INBOX_MAX_BYTES}-byte inbox cap; "
                f"ask them for a smaller file or a link]")

    path = save_bytes(data, filename, root=root, now=now)
    size = declared if isinstance(declared, int) else len(data)
    logger.info("inbox: saved %s → %s (%d bytes, %s)", filename, path, len(data), mime)
    return (f"{filename}\n[binary file — saved to {path} ({size} bytes, {mime}). Not text: "
            f"use local_shell to inspect or convert it; read_file would only show raw bytes.]")

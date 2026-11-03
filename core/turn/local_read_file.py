"""Local file-read tier-1 tool — let PaLLi look at a file when discussing it.

Path resolution is constrained to a small set of allowed roots:
- AGENTIC_WORKSPACE_ROOT (defaults to repo root)
- DESKTOP_ACTIVITY_WATCH_DIRS

Anything outside those roots is refused — the goal is to let PaLLi reference
files the user is actively working in, not to enable arbitrary disk
traversal. The file is read with size cap (LOCAL_READ_FILE_MAX_BYTES) and
binary content is detected via a null-byte check + decode attempt.

Output is plain text suitable for tier-0 reply context. Files larger than
the cap return a head excerpt with "[truncated, file is N bytes]" suffix.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.config import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileReadResult:
    """Outcome of a read_file invocation."""

    requested_path: str
    resolved_path: str
    allowed: bool
    refused_reason: str
    size_bytes: int
    is_binary: bool
    text: str
    truncated: bool

    def to_text_for_voicer(self) -> str:
        if not self.allowed:
            return f"[read_file REFUSED: {self.refused_reason}] {self.requested_path}"
        if self.is_binary:
            return f"[read_file] {self.resolved_path} appears to be binary ({self.size_bytes} bytes); skipping content."
        head = f"--- {self.resolved_path} ({self.size_bytes} bytes) ---"
        body = self.text
        suffix = f"\n[truncated, file is {self.size_bytes} bytes]" if self.truncated else ""
        return f"{head}\n{body}{suffix}"


def _allowed_roots() -> List[Path]:
    """Compute the set of directories `read_file` is permitted to traverse into."""
    roots: List[Path] = []
    workspace = str(getattr(config, "AGENTIC_WORKSPACE_ROOT", "") or ".").strip() or "."
    try:
        roots.append(Path(workspace).expanduser().resolve())
    except Exception:
        pass
    raw = getattr(config, "DESKTOP_ACTIVITY_WATCH_DIRS", "") or ""
    if isinstance(raw, str):
        entries = [item.strip() for item in raw.split(";") if item.strip()]
    elif isinstance(raw, (list, tuple)):
        entries = [str(item).strip() for item in raw if str(item).strip()]
    else:
        entries = []
    for entry in entries:
        try:
            p = Path(entry).expanduser().resolve()
        except Exception:
            continue
        if p.exists() and p.is_dir():
            roots.append(p)
    # Deduplicate while preserving order.
    seen: set[Path] = set()
    unique: List[Path] = []
    for r in roots:
        if r in seen:
            continue
        seen.add(r)
        unique.append(r)
    return unique


def _is_inside_any(target: Path, roots: List[Path]) -> bool:
    for root in roots:
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def execute_local_read_file(
    *,
    path: str,
    max_bytes: Optional[int] = None,
) -> FileReadResult:
    """Resolve `path` against the allowed roots and read up to `max_bytes`.

    Caller responsibility:
    - `path` may be absolute or relative; relative paths are resolved
      against AGENTIC_WORKSPACE_ROOT first, then each watch dir.
    - max_bytes overrides LOCAL_READ_FILE_MAX_BYTES if provided.

    Always returns a FileReadResult; never raises for normal denial paths.
    """
    raw_path = str(path or "").strip()
    if not raw_path:
        return FileReadResult(
            requested_path=raw_path,
            resolved_path="",
            allowed=False,
            refused_reason="empty path",
            size_bytes=0,
            is_binary=False,
            text="",
            truncated=False,
        )
    if not bool(getattr(config, "LOCAL_READ_FILE_ENABLED", False)):
        return FileReadResult(
            requested_path=raw_path,
            resolved_path="",
            allowed=False,
            refused_reason="local_read_file is disabled (LOCAL_READ_FILE_ENABLED=false)",
            size_bytes=0,
            is_binary=False,
            text="",
            truncated=False,
        )

    roots = _allowed_roots()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        # Try each root in turn.
        resolved: Optional[Path] = None
        for root in roots:
            tentative = (root / candidate).resolve()
            if tentative.exists() and tentative.is_file():
                resolved = tentative
                break
        if resolved is None:
            return FileReadResult(
                requested_path=raw_path,
                resolved_path="",
                allowed=False,
                refused_reason="file not found in any allowed root",
                size_bytes=0,
                is_binary=False,
                text="",
                truncated=False,
            )
        candidate = resolved
    else:
        try:
            candidate = candidate.resolve()
        except Exception as exc:
            return FileReadResult(
                requested_path=raw_path,
                resolved_path="",
                allowed=False,
                refused_reason=f"path resolution failed: {exc}",
                size_bytes=0,
                is_binary=False,
                text="",
                truncated=False,
            )

    if not candidate.exists():
        return FileReadResult(
            requested_path=raw_path,
            resolved_path=str(candidate),
            allowed=False,
            refused_reason="file does not exist",
            size_bytes=0,
            is_binary=False,
            text="",
            truncated=False,
        )
    if not candidate.is_file():
        return FileReadResult(
            requested_path=raw_path,
            resolved_path=str(candidate),
            allowed=False,
            refused_reason="path is not a regular file",
            size_bytes=0,
            is_binary=False,
            text="",
            truncated=False,
        )
    if not _is_inside_any(candidate, roots):
        return FileReadResult(
            requested_path=raw_path,
            resolved_path=str(candidate),
            allowed=False,
            refused_reason="path is outside the allowed roots (workspace + watch dirs)",
            size_bytes=0,
            is_binary=False,
            text="",
            truncated=False,
        )

    cap = int(max_bytes if max_bytes is not None else getattr(config, "LOCAL_READ_FILE_MAX_BYTES", 120000) or 120000)
    try:
        size = candidate.stat().st_size
        with open(candidate, "rb") as fh:
            raw = fh.read(cap + 1)  # one extra to detect truncation
    except (OSError, PermissionError) as exc:
        return FileReadResult(
            requested_path=raw_path,
            resolved_path=str(candidate),
            allowed=False,
            refused_reason=f"read failed: {exc}",
            size_bytes=0,
            is_binary=False,
            text="",
            truncated=False,
        )
    truncated = len(raw) > cap
    if truncated:
        raw = raw[:cap]
    is_binary = b"\x00" in raw
    text = ""
    if not is_binary:
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = ""
            is_binary = True
    logger.info(
        "local_read_file: path=%s size=%d allowed=True binary=%s truncated=%s",
        candidate, size, is_binary, truncated,
    )
    return FileReadResult(
        requested_path=raw_path,
        resolved_path=str(candidate),
        allowed=True,
        refused_reason="",
        size_bytes=size,
        is_binary=is_binary,
        text=text,
        truncated=truncated,
    )

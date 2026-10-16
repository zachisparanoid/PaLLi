#!/usr/bin/env python3
"""One-shot utility to back-fill workspace_path + file_outputs on a past
delegation session.

Use case: Phase 7.3 added per-delegation workspace tracking and post-run
file inventory, but sessions that ran before that code went live have
neither field populated. For sessions where we happen to KNOW where
Claude wrote (e.g. the 2026-04-23 example-project build on Desktop),
this script walks that directory, builds a file_outputs list identical
in shape to what the live bridge would record, and writes it to the
delegation_sessions row.

After back-fill, PaLLi's get_delegation_summary returns real data for
that session — so when the user asks 'what did you build in that
task?' PaLLi can reply like a developer who actually did the work.

Usage:
    python scripts/palli_delegation_backfill.py <session_id> <workspace_path>

Example:
    python scripts/palli_delegation_backfill.py \\
        deleg_b624df5d63584237 \\
        C:\\Users\\<you>\\Desktop\\example-project
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Use raw sqlite3 instead of going through PalliBrain. That avoids
# triggering core.database's full DB init + migrations as a side effect,
# which would be disruptive when run alongside a live PaLLi process.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _REPO_ROOT / "data" / "palli-local.sqlite3"


def _inventory(workspace_path: str, *, max_files: int = 500, max_file_bytes: int = 1_000_000) -> List[Dict[str, Any]]:
    """Walk a directory and produce a file_outputs list.

    Matches bridge._inventory_workspace byte-for-byte (same shape, same
    filters) so back-filled data is indistinguishable from data written
    by a live delegation.
    """
    ws = Path(workspace_path)
    if not ws.exists() or not ws.is_dir():
        sys.exit(f"error: workspace not found or not a directory: {workspace_path}")

    skip_dir_names = {"node_modules", "venv", ".venv", ".git", "__pycache__", "dist", "build"}

    entries: List[Dict[str, Any]] = []
    for p in sorted(ws.rglob("*")):
        if any(part in skip_dir_names for part in p.relative_to(ws).parts):
            continue
        if not p.is_file():
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        rel = p.relative_to(ws).as_posix()

        sha256 = None
        if stat.st_size <= max_file_bytes:
            try:
                h = hashlib.sha256()
                with p.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
                sha256 = h.hexdigest()
            except OSError:
                sha256 = None

        entries.append({
            "path": rel,
            "abs_path": str(p.resolve()),
            "size": int(stat.st_size),
            "mtime": float(stat.st_mtime),
            "sha256": sha256,
        })
        if len(entries) >= max_files:
            break
    return entries


def _fetch_session(con: sqlite3.Connection, session_id: str) -> Optional[Dict[str, Any]]:
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT session_id, workspace_path, file_outputs FROM delegation_sessions "
        "WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def _backfill(session_id: str, workspace_path: str) -> None:
    if not _DB_PATH.exists():
        sys.exit(f"error: DB not found at {_DB_PATH}")

    con = sqlite3.connect(str(_DB_PATH))
    try:
        existing = _fetch_session(con, session_id)
        if existing is None:
            sys.exit(f"error: no delegation session with id {session_id!r}")

        current_outputs_raw = existing.get("file_outputs") or ""
        try:
            current_outputs = json.loads(current_outputs_raw) if current_outputs_raw else []
        except (TypeError, json.JSONDecodeError):
            current_outputs = []

        if existing.get("workspace_path") and current_outputs:
            print(
                f"Session {session_id} already has workspace_path + "
                f"{len(current_outputs)} file_outputs entries. Overwrite? [y/N] ",
                end="",
            )
            resp = input().strip().lower()
            if resp != "y":
                print("aborted.")
                return

        print(f"Walking {workspace_path} ...")
        inventory = _inventory(workspace_path)
        print(f"Found {len(inventory)} files (excluding node_modules/.git/etc.)")

        total_bytes = sum(f.get("size") or 0 for f in inventory)
        print(f"Total size: {total_bytes / 1024:.1f} KB")

        resolved = str(Path(workspace_path).resolve())
        con.execute(
            "UPDATE delegation_sessions SET workspace_path = ?, file_outputs = ? "
            "WHERE session_id = ?",
            (resolved, json.dumps(inventory), session_id),
        )
        con.commit()
        print(f"Back-filled session {session_id}.")
        print(f"  workspace_path = {resolved}")
        print(f"  file_outputs   = {len(inventory)} entries")
    finally:
        con.close()


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    session_id = argv[1]
    workspace_path = argv[2]
    _backfill(session_id, workspace_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

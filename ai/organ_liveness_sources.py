"""What the smoke detector reads (ai/organ_liveness.py): each store an
organ's outcome lives in, read-only.

Each reader answers one plain question about one store and never raises.
A store that cannot be read comes back with the reason, so the report can
say so on the organ's line instead of crashing the check (Batch 6, ruling
R54). Nothing here writes: the database is opened with a plain read-only
connection — never through stake_ledger, whose readers create and alter
tables — and git is only asked for a log.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
# Her repo is small and local; this only bounds a git that hangs. Short,
# because `configure` runs the whole doctor under a 30 s budget.
GIT_TIMEOUT_S = 10.0
# What last_commit_at says when the folder it was given holds no repo.
NO_REPO = "no repo at"


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())[:160]


def dated_files(directory: Path, suffix: str) -> List[Tuple[date, Path]]:
    """``YYYY-MM-DD<suffix>`` files in ``directory``, oldest first."""
    out: List[Tuple[date, Path]] = []
    if not directory.is_dir():
        return out
    for entry in directory.iterdir():
        stem = entry.name[: -len(suffix)] if entry.name.endswith(suffix) else ""
        if not _DAY.fullmatch(stem):
            continue
        try:
            out.append((date.fromisoformat(stem), entry))
        except ValueError:
            continue
    return sorted(out)


def newest_dated(directory: Path, suffix: str = ".jsonl") -> Optional[date]:
    files = dated_files(directory, suffix)
    return files[-1][0] if files else None


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    """A JSON object from ``path``, or None when there is no readable one."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def dated_records(directory: Path, suffix: str, since: date) -> List[Tuple[date, Dict[str, Any]]]:
    """The readable JSON objects in ``YYYY-MM-DD<suffix>`` files dated at or
    after ``since``, newest first."""
    out: List[Tuple[date, Dict[str, Any]]] = []
    for day, path in reversed(dated_files(directory, suffix)):
        if day < since:
            break
        record = read_json(path)
        if record is not None:
            out.append((day, record))
    return out


def stake_rows_since(db_path: Path, since: float) -> Tuple[Dict[str, int], str]:
    """``({kind: rows updated at or after since}, "")`` from her stakes
    table, or ``({}, why)`` when the database cannot be read. A database
    with no stakes table yet has no rows. Opened read-only (mode=ro), so
    this never creates the file, a table or a column."""
    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT kind, COUNT(*) FROM stakes WHERE updated_at >= ? GROUP BY kind",
                (float(since),),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {}, ""
        return {}, _one_line(exc)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {}, _one_line(exc)
    return {str(kind or ""): int(n) for kind, n in rows}, ""


def last_commit_at(repo_root: Path, paths: Iterable[str]) -> Tuple[Optional[float], str]:
    """When her repo last committed a change to ``paths`` (epoch seconds).
    Filtered by path: her story pages, her journal and her interests page
    all commit to the one repo. ``(None, "")`` when there is no such
    commit; ``(None, why)`` when git cannot answer, or when there is no
    repo there at all — a wrong IDENTITY_ROOT_PATH must not read as "no
    page committed"."""
    root = Path(repo_root)
    if not (root / ".git").exists():
        return None, f"{NO_REPO} {root}"
    # Her repo, whatever repo the caller's environment points git at.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        done = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", *paths],
            cwd=str(root), env=env, capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, _one_line(exc) or type(exc).__name__
    if done.returncode != 0:
        why = _one_line(done.stderr) or f"git exited {done.returncode}"
        if "does not have any commits" in why:
            return None, ""
        return None, why
    out = done.stdout.strip()
    if not out:
        return None, ""
    try:
        return float(out.splitlines()[0]), ""
    except ValueError:
        return None, f"git answered {out[:40]!r}"


def events_since(book: Path, since: float, ops: Iterable[str]) -> Tuple[Dict[str, int], str]:
    """``({op: events at or after since}, "")`` for the ``ops`` named, from
    an organ's append-only book of ``{"at", "op", ...}`` lines; ``({}, why)``
    when the book cannot be read. No book yet is an empty one."""
    wanted = set(ops)
    try:
        text = Path(book).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        return {}, _one_line(exc)
    counts: Dict[str, int] = {}
    for line in text.splitlines():
        try:
            event = json.loads(line)
            op = str(event.get("op") or "")
            at = float(event.get("at") or 0.0)
        except (ValueError, TypeError, AttributeError):
            continue
        if op in wanted and at >= since:
            counts[op] = counts.get(op, 0) + 1
    return counts, ""


def forged_since(manifest: Path, since: float) -> Tuple[int, str]:
    """``(tools forged at or after since, "")`` from the forge's manifest,
    whose ``created_at`` is an ISO-8601 string; ``(0, why)`` when the
    manifest cannot be read. No manifest yet is no tools."""
    try:
        raw = Path(manifest).read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0, ""
    except OSError as exc:
        return 0, _one_line(exc)
    try:
        entries = json.loads(raw)
    except ValueError:
        return 0, "the manifest is not JSON"
    if not isinstance(entries, list):
        return 0, "the manifest is not a list"
    made = 0
    for entry in entries:
        try:
            when = datetime.fromisoformat(str(entry.get("created_at") or ""))
        except (ValueError, AttributeError):
            continue
        if when.timestamp() >= since:
            made += 1
    return made, ""


def moments_since(directory: Path, since: date) -> Tuple[int, int]:
    """``(moments written, moments that carried a surprise)`` in the
    present moment's dated files at or after ``since``. A line with no
    text is not a moment (the store's own reader skips it too)."""
    written = surprised = 0
    for day, path in dated_files(directory, ".jsonl"):
        if day < since:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                moment = json.loads(line)
            except ValueError:
                continue
            if not isinstance(moment, dict) or not moment.get("text"):
                continue
            written += 1
            if isinstance(moment.get("meta"), dict) and moment["meta"].get("surprise"):
                surprised += 1
    return written, surprised

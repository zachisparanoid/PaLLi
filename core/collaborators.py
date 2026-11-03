"""Collaborators: the people the owner names (Batch 8, task 1 — B8-1, ruling R8).

In a shared room (a Discord server channel, a Telegram group) she takes part
as a participant who knows she is an AI, and everyone who speaks there reads
the room's conversation. How much of HER WORK in the room they read depends
on who they are:

- the owner reads everything;
- a COLLABORATOR, someone the owner named here, also reads the work she did
  in that room (her tool receipts);
- anyone else is a STRANGER and reads only what was said out loud.

Her private sections about the owner (memory, moods, relationship, the
house, hopes and worries, the story of us, her private pages) stay the
owner's for everyone, collaborators included.

The list is owner-managed and outside the repo's tracked files:
``data/collaborators.json`` (gitignored; ``PALLI_COLLABORATORS_PATH``
overrides it, which is how the test suite keeps off the live list). It has
two writers — her owner-only ``collaborators`` tool and ``palli_ctl
collaborators`` — so every call re-reads the file (a change is live on the
very next turn, no restart) and every write is atomic (temp file +
``os.replace``), the forge manifest's pattern.

A collaborator is named per door: a Discord id on Discord, a Telegram id on
Telegram. Asking is never an error — an unknown door, a bad id, a missing or
corrupt file all answer "not a collaborator" (fail closed: a stranger). A
WRITE over a corrupt file is refused rather than overwriting what the owner
wrote.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLATFORMS = ("discord", "telegram")
_REPO_ROOT = Path(__file__).resolve().parents[1]
_ID_RE = re.compile(r"-?\d{1,25}")


class CollaboratorStoreError(RuntimeError):
    """The list on disk cannot be read, so it will not be overwritten."""


def store_path() -> Path:
    raw = os.environ.get("PALLI_COLLABORATORS_PATH")
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "collaborators.json"


def normalize_platform(platform: Any) -> str:
    """"discord" / "telegram", or ValueError."""
    p = str(platform or "").strip().lower()
    if p not in PLATFORMS:
        raise ValueError(f"a collaborator is named on {' or '.join(PLATFORMS)}, not {platform!r}")
    return p


def normalize_user_id(user_id: Any) -> str:
    """A platform user id as its digits, or ValueError."""
    raw = str(user_id if user_id is not None else "").strip()
    if not _ID_RE.fullmatch(raw):
        raise ValueError(f"a user id is a number, not {user_id!r}")
    return str(int(raw))


def _read() -> list[dict[str, Any]]:
    """The entries on disk. Missing file = nobody yet. Unreadable or not a
    list of entries = CollaboratorStoreError (logged)."""
    path = store_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.error("collaborators: %s is unreadable: %s", path, exc)
        raise CollaboratorStoreError(f"{path} is unreadable: {exc}") from exc
    try:
        entries = json.loads(raw) if raw.strip() else []
    except ValueError as exc:
        logger.error("collaborators: %s is not valid JSON: %s", path, exc)
        raise CollaboratorStoreError(f"{path} is not valid JSON") from exc
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        logger.error("collaborators: %s is not a list of entries", path)
        raise CollaboratorStoreError(f"{path} is not a list of entries")
    return entries


def _write(entries: list[dict[str, Any]]) -> None:
    """Beside, then replace; the file keeps its permissions (a new file
    takes the umask's)."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode: int | None = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = None
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        if mode is not None:
            os.fchmod(fh.fileno(), mode)
        fh.write(json.dumps(entries, indent=2) + "\n")
    os.replace(tmp, path)


def _matches(entry: dict[str, Any], platform: str, user_id: str) -> bool:
    return str(entry.get("platform") or "") == platform and str(entry.get("user_id") or "") == user_id


def list_collaborators() -> list[dict[str, Any]]:
    """Every named collaborator, in the order the owner named them. Raises
    CollaboratorStoreError when the file cannot be read."""
    return [dict(e) for e in _read()]


def is_collaborator(platform: Any, user_id: Any) -> bool:
    """True only when the owner named this id on this door. Never raises."""
    try:
        p = normalize_platform(platform)
        uid = normalize_user_id(user_id)
        return any(_matches(e, p, uid) for e in _read())
    except (ValueError, CollaboratorStoreError):
        return False
    except Exception as exc:  # noqa: BLE001 — a question about a guest never breaks their turn
        logger.warning("collaborators: lookup failed (%s); treated as a stranger", type(exc).__name__)
        return False


def add_collaborator(platform: Any, user_id: Any, *, name: str = "") -> bool:
    """Name someone a collaborator. True when they are new; False when they
    already were (the name is refreshed when one is given)."""
    p = normalize_platform(platform)
    uid = normalize_user_id(user_id)
    label = " ".join(str(name or "").split())
    entries = _read()
    for e in entries:
        if _matches(e, p, uid):
            if label and e.get("name") != label:
                e["name"] = label
                _write(entries)
            return False
    entries.append({
        "platform": p,
        "user_id": uid,
        "name": label,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _write(entries)
    return True


def remove_collaborator(platform: Any, user_id: Any) -> bool:
    """Make them a stranger again. True when they were on the list."""
    p = normalize_platform(platform)
    uid = normalize_user_id(user_id)
    entries = _read()
    kept = [e for e in entries if not _matches(e, p, uid)]
    if len(kept) == len(entries):
        return False
    _write(kept)
    return True


def describe(entry: dict[str, Any]) -> str:
    """One line for a listing: ``Sam (discord 1234)``."""
    name = str(entry.get("name") or "").strip()
    where = f"{entry.get('platform')} {entry.get('user_id')}"
    return f"{name} ({where})" if name else where

"""Her record of quiet choices (Batch 8, task 2a — B8-2, ruling T2-4).

Every time she chooses not to answer a message, one line is written here:
when, on which door, in which conversation, who sent it, the start of what
they said, and why — a one-line reason in her own words, given as part of
choosing. The owner asked for it on 10-01 so that a quiet choice is never a
mystery: she can answer "why didn't you answer that?" from it (her
owner-only ``introspect`` tool), and the owner can read it with
``palli_ctl quiet [--days N]``.

It is one of her records: owner-only (never rendered to a guest), and
retention never touches it. Files are ``data/quiet_choices/<YYYY-MM-DD>.jsonl``
(gitignored; ``PALLI_QUIET_CHOICES_DIR`` overrides the folder, which is how
the test suite keeps off the live record). One JSON object per line:

- ``ts``: local time, ISO 8601;
- ``door``: the surface (``discord_text``, ``telegram_text``, ``dashboard``…);
- ``conversation``: the conversation key (``core.session_keys``);
- ``who``: ``{"id", "name", "owner"}`` — the sender's platform user id when
  the door gives one, their display name, and whether it was the owner;
- ``excerpt``: the message's first 120 characters;
- ``reason``: her one line;
- ``source``: ``"reply"`` (her reply call chose it), ``"first_pass"`` (the
  room's quick first pass chose it) or ``"spoke_during_note"`` (she spoke in
  a room while an owner's quiet note was active there).

Writing never raises — a quiet choice is honoured whether or not its line
could be written; a failure is logged. Reading skips a line it cannot parse.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
EXCERPT_CHARS = 120
SOURCES = ("reply", "first_pass", "spoke_during_note")


def record_dir() -> Path:
    raw = os.environ.get("PALLI_QUIET_CHOICES_DIR")
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "quiet_choices"


def _excerpt(text: Any) -> str:
    return str(text or "")[:EXCERPT_CHARS]


def record(
    *,
    door: str,
    conversation: str,
    who_id: Any,
    who_name: Any,
    owner: bool,
    text: Any,
    reason: Any,
    source: str = "reply",
    now: datetime | None = None,
) -> bool:
    """Append one quiet choice. True when the line was written. An unknown
    ``source`` is a programming error (ValueError); anything on disk going
    wrong is logged and returns False."""
    if source not in SOURCES:
        raise ValueError(f"a quiet choice's source is one of {SOURCES}, not {source!r}")
    when = now or datetime.now()
    row = {
        "ts": when.isoformat(timespec="seconds"),
        "door": str(door or ""),
        "conversation": str(conversation or ""),
        "who": {
            "id": "" if who_id is None else str(who_id),
            "name": str(who_name or ""),
            "owner": bool(owner),
        },
        "excerpt": _excerpt(text),
        "reason": " ".join(str(reason or "").split()),
        "source": source,
    }
    try:
        folder = record_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{when:%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        logger.warning("quiet_record: could not write a quiet choice (%s: %s)",
                       type(exc).__name__, exc)
        return False


def read(days: int = 7, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every quiet choice from the last ``days`` days (today included), oldest
    first. A missing folder is an empty record."""
    days = max(1, int(days or 1))
    today = (now or datetime.now()).date()
    folder = record_dir()
    rows: list[dict[str, Any]] = []
    for back in range(days - 1, -1, -1):
        path = folder / f"{today - timedelta(days=back):%Y-%m-%d}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning("quiet_record: could not read %s (%s)", path.name, exc)
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                logger.warning("quiet_record: skipped an unreadable line in %s", path.name)
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def describe(row: dict[str, Any]) -> str:
    """One line for a reader: when, where, who, what they said, and why."""
    who = row.get("who") if isinstance(row.get("who"), dict) else {}
    name = str(who.get("name") or "") or ("the owner" if who.get("owner") else str(who.get("id") or "someone"))
    reason = str(row.get("reason") or "") or "(no reason given)"
    return (f"{str(row.get('ts') or '')[:16].replace('T', ' ')} · {row.get('door', '')} · "
            f"{name}: \"{row.get('excerpt', '')}\" — why: {reason}")

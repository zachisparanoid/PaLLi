"""What a reply used (R15, memory that reaches; plan
docs/2026-10-13-memory-that-reaches-plan.md, T13 and W3).

After each reply on a conversation turn, the owner's or a guest's, the runner
writes one line here: what PaLLi's reply drew on. The owner reads it on the
dashboards' ``/replies`` page and with ``palli_ctl replies [--days N]
[--last N]``. It never reaches a chat, and PaLLi doesn't read it. It is
written only while companion.memory_reach is on (``enabled``), and never for
a quiet choice.

It holds the owner's private lines, so it is treated like the rest of
``data/``: files are ``data/reply_sources/<YYYY-MM-DD>.jsonl`` (gitignored,
never in the public set; ``PALLI_REPLY_SOURCES_DIR`` overrides the folder,
which is how the suite keeps off the live record), and the night's
housekeeping removes a day's file once it is more than ``KEEP_DAYS`` days
old (``prune``, called by ``core.retention``). One JSON object per line:

- ``ts``: local time, ISO 8601;
- ``door``: the surface (``discord_text``, ``telegram_text``, ``dashboard``...);
- ``conversation``: the conversation key (``core.session_keys``);
- ``who``: ``{"name", "owner"}``: the sender's display name, and whether it
  was the owner;
- ``reply``: the start of PaLLi's reply (``REPLY_CHARS``), PaLLi's side only;
- ``record_lines``: the dated lines her search of the record
  (``search_what_we_said``) returned to the reply, each once;
- ``notes``: the memory notes her prompt carried (``NOTE_CHARS`` each);
- ``facts``: the facts band's lines; ``today``: today's lines (the today
  slot), newest first;
- ``room_lines``: a guest's own-room section (filled by the guest step);
- ``queries``: what she searched the record for;
- ``look``: ``{"rounds", "calls", "seconds", "thin"}`` over her searches in
  this reply, or null when she ran none.

Every text is put on one line, cut to its size (``LINE_CHARS`` unless named
above) and passed through the owner's secrets denylist
(``core.secrets_redaction.redact``). Writing never raises: a failure is
logged by its type and returns False, so a reply never waits on its record.
Reading skips a line it cannot parse. Log lines carry counts only, never the
words.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
REPLY_CHARS = 120
NOTE_CHARS = 120
LINE_CHARS = 160
KEEP_DAYS = 14            # a day's file is removed once it is more than this many days old
SHOWN_NOTES = 3           # the memory notes the prompt shows (context_adapter._render_memory)
SHOWN_LINES = 8           # the band's lines and today's lines the prompt shows (_render_facts)
_LOOK_KEYS = ("rounds", "calls", "seconds", "thin")
_DAY_FILE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.jsonl$")


def record_dir() -> Path:
    raw = os.environ.get("PALLI_REPLY_SOURCES_DIR")
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "reply_sources"


def enabled() -> bool:
    """companion.memory_reach, read through the record's own accessor
    (``ai.memory_reach_index.enabled``) at call time, so the suite's pin
    holds; fail-closed."""
    try:
        from ai import memory_reach_index

        return bool(memory_reach_index.enabled())
    except Exception:
        return False


def _one_line(text: Any, cap: int) -> str:
    from core.secrets_redaction import redact

    flat = " ".join(redact(str(text or "")).split())
    return flat if len(flat) <= cap else flat[: cap - 1].rstrip() + "…"


def _lines(items: Iterable[Any] | None, cap: int, *, once: bool = False) -> list[str]:
    out: list[str] = []
    for item in items or ():
        line = _one_line(item, cap)
        if line and not (once and line in out):
            out.append(line)
    return out


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _seconds(value: Any) -> float:
    try:
        return round(max(0.0, float(value)), 1)
    except (TypeError, ValueError):
        return 0.0


def _look(look: Any) -> dict | None:
    if not isinstance(look, dict):
        return None
    return {"rounds": _count(look.get("rounds")), "calls": _count(look.get("calls")),
            "seconds": _seconds(look.get("seconds")), "thin": bool(look.get("thin"))}


def record(*, door: str, conversation: str, who_name: str, owner: bool, reply_excerpt: str,
           record_lines: list[str], notes: list[str], facts: list[str], today: list[str],
           room_lines: list[str], queries: list[str], look: dict | None,
           now: datetime | None = None) -> bool:
    """Append one reply's line. True when it was written; anything on disk
    going wrong is logged by its type (never the words) and returns False."""
    when = now or datetime.now()
    row = {
        "ts": when.isoformat(timespec="seconds"),
        "door": str(door or ""),
        "conversation": str(conversation or ""),
        "who": {"name": _one_line(who_name, NOTE_CHARS), "owner": bool(owner)},
        "reply": _one_line(reply_excerpt, REPLY_CHARS),
        "record_lines": _lines(record_lines, LINE_CHARS, once=True),
        "notes": _lines(notes, NOTE_CHARS),
        "facts": _lines(facts, LINE_CHARS),
        "today": _lines(today, LINE_CHARS),
        "room_lines": _lines(room_lines, LINE_CHARS, once=True),
        "queries": _lines(queries, LINE_CHARS),
        "look": _look(look),
    }
    try:
        folder = record_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{when:%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("reply_sources: could not write a reply's line (%s)", type(exc).__name__)
        return False
    logger.info("reply_sources: lines=%d notes=%d facts=%d today=%d room_lines=%d queries=%d",
                len(row["record_lines"]), len(row["notes"]), len(row["facts"]), len(row["today"]),
                len(row["room_lines"]), len(row["queries"]))
    return True


def read(days: int = 3, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every reply's line from the last ``days`` days (today included),
    oldest first. A missing folder is an empty record."""
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
            logger.warning("reply_sources: could not read %s (%s)", path.name, type(exc).__name__)
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                logger.warning("reply_sources: skipped an unreadable line in %s", path.name)
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _items(row: dict[str, Any], key: str) -> list[str]:
    value = row.get(key)
    return [str(v) for v in value] if isinstance(value, list) else []


def describe(row: dict[str, Any]) -> str:
    """One line for a reader: ``ts · who · "reply" · N record lines · N notes
    · N facts`` (Fable's ruling W4: the page's "lines from the record")."""
    who = row.get("who") if isinstance(row.get("who"), dict) else {}
    name = str(who.get("name") or "") or ("the owner" if who.get("owner") else "a guest")
    record = _plural(len(_items(row, "record_lines")), "record line")
    return (f"{str(row.get('ts') or '')[:16].replace('T', ' ')} · {name} · "
            f"\"{row.get('reply', '')}\" · {record} · "
            f"{_plural(len(_items(row, 'notes')), 'note')} · {_plural(len(_items(row, 'facts')), 'fact')}")


def details(row: dict[str, Any]) -> list[str]:
    """What the reply drew on, one item a line under a plain label (Fable's
    ruling W4, the page's groups shortened): the searches and the look
    first, then the record's lines, notes, facts, today's lines and a
    guest's room lines. Nothing for a reply that drew on nothing."""
    out: list[str] = []
    queries = _items(row, "queries")
    if queries:
        out.append("searched: " + " · ".join(f'"{q}"' for q in queries))
    look = row.get("look")
    if isinstance(look, dict):
        out.append(f"look: {_plural(_count(look.get('rounds')), 'round')}, "
                   f"{_plural(_count(look.get('calls')), 'call')}, {_seconds(look.get('seconds'))} s"
                   + (", thin" if look.get("thin") else ""))
    for key, label in (("record_lines", "record"), ("notes", "note"), ("facts", "fact"),
                       ("today", "today"), ("room_lines", "this room")):
        out.extend(f"{label}: {item}" for item in _items(row, key))
    return out


# --- what the runner hands over -------------------------------------------------

def look_of(entries: Iterable[dict] | None) -> dict | None:
    """The reply's look from each search's counts (``ToolContext.record_looks``,
    the same counts as the tool's log line): rounds, calls and seconds added
    up; thin only when every search came back thin. None when she ran none."""
    entries = [e for e in (entries or ()) if isinstance(e, dict)]
    if not entries:
        return None
    return {"rounds": sum(_count(e.get("rounds")) for e in entries),
            "calls": sum(_count(e.get("calls")) for e in entries),
            "seconds": _seconds(sum(_seconds(e.get("seconds")) for e in entries)),
            "thin": all(bool(e.get("thin")) for e in entries)}


def _fact_line(fact: Any) -> str:
    """A band entry as the prompt shows it, without its "- ": a memory
    bullet's own line, else ``key: value``."""
    if not isinstance(fact, dict):
        return ""
    if "line" in fact:
        line = str(fact.get("line") or "").strip()
        return line[2:].strip() if line.startswith("- ") else line
    value = str(fact.get("value") or "").strip()
    if not value:
        return ""
    return f"{str(fact.get('key') or 'fact').strip()}: {value}"


def context_sources(turn_ctx: Any, *, shown: bool) -> dict[str, list[str]]:
    """The memory notes, the facts band's lines and today's lines the reply's
    prompt carried, as the context adapter renders them
    (``core.agent.context_adapter._render_memory`` and ``_render_facts``):
    the first ``SHOWN_NOTES`` notes, the band's first ``SHOWN_LINES`` lines,
    and today's rows (``memory_type`` today, split by
    ``core.turn.memory_lookup.split_today``, newest first) only while the
    today slot is on. ``shown`` is whether the owner's bands were rendered
    for this turn (False on a guest's turn: none of them are)."""
    empty: dict[str, list[str]] = {"notes": [], "facts": [], "today": []}
    if not shown or turn_ctx is None:
        return empty
    from core.turn.companion_flags import today_slot_on
    from core.turn.memory_lookup import split_today

    hits = tuple(getattr(turn_ctx, "recent_memory_hits", ()) or ())[:SHOWN_NOTES]
    band, today = split_today(tuple(getattr(turn_ctx, "relevant_facts", ()) or ()))
    return {
        "notes": [s for s in (str(h or "").strip() for h in hits) if s],
        "facts": [s for s in (_fact_line(f) for f in band[:SHOWN_LINES]) if s],
        "today": ([s for s in (str(f.get("value") or "").strip() for f in today[:SHOWN_LINES]) if s]
                  if today and today_slot_on() else []),
    }


def who_name(turn: Any) -> str:
    """The sender's display name as the door gave it (Discord's username,
    else Telegram's), "" when the door gave none."""
    md = getattr(turn, "metadata", None)
    if md is None:
        return ""
    extra = getattr(md, "extra", None)
    return str(getattr(md, "discord_username", None)
               or (extra.get("telegram_username") if isinstance(extra, dict) else None) or "")


# --- retention ------------------------------------------------------------------------

def prune(*, now: float | datetime | None = None, keep_days: int = KEEP_DAYS) -> dict[str, Any]:
    """Remove the day files more than ``keep_days`` days old, by the date in
    the name (on 10-13, 09-28 goes and 09-29 stays). Only regular
    ``YYYY-MM-DD.jsonl`` files directly in the folder: a link, a folder or
    any other name is left alone. ``{"removed": n}``, with ``"error"`` naming
    how many could not be removed and the first one's type; a missing
    folder removes nothing."""
    if isinstance(now, (int, float)):
        now = datetime.fromtimestamp(now)
    cutoff = (now or datetime.now()).date() - timedelta(days=max(0, int(keep_days)))
    folder = record_dir()
    removed, failed, first = 0, 0, ""
    try:
        names = sorted(os.listdir(folder))
    except FileNotFoundError:
        return {"removed": 0}
    for name in names:
        match = _DAY_FILE.match(name)
        if not match:
            continue
        try:
            day = datetime(int(match[1]), int(match[2]), int(match[3])).date()
        except ValueError:
            continue
        path = folder / name
        if day >= cutoff or path.is_symlink() or not path.is_file():
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            failed += 1
            first = first or type(exc).__name__
    out: dict[str, Any] = {"removed": removed}
    if failed:
        out["error"] = f"{failed} could not be removed; first: {first}"
    if removed or failed:
        logger.info("reply_sources: pruned %d day files, %d could not be removed", removed, failed)
    return out

"""Group openers: PaLLi starting something in a group chat on its own (the
presence slice, item 4; the owner, 2026-10-13).

The owner's rulings, each enforced here in code:

- At most ``companion.group_openers_per_day`` openers a day per group
  (default 1; 0 = off), behind the switch ``companion.group_openers``
  (default on, for strangers too). Any time of day or night.
- Only a room that is positively a group: a Telegram group (its id is below
  zero) or a Discord channel in a server, that the door does not show to be a
  private chat. Anything that cannot be shown is skipped. Never a room with a
  standing owner's room note (``core/room_notes.py``); never while the room is
  mid-conversation (any line in the last 10 minutes); only rooms where someone
  other than PaLLi wrote in the last 14 days.
- A quiet room is fine to speak into, but not after an opener nobody answered:
  if PaLLi's last opener in a room has no later line from anyone else, that
  room is skipped.
- What the call sees is ONLY the room's own recent lines (as
  ``core/agent/room_tail.py`` renders them), its own earlier openers there,
  and its own-life items that passed the room check
  (``core.agent.own_life.room_safe_lines``). Nothing of the owner's private
  life: no memory, stakes, tonight's pick, small truths, relationship, moods,
  profile or tools. This module imports none of them (pinned by
  tests/turn/test_group_openers.py).

The loop: ``run_pass`` rides ``core/turn/proactive.run_outreach_residuals``
(the moment engine's loop, about every 20 minutes). Each pass considers at
most ONE room, the one looked at least recently, and a room is looked at
every 3 hours at most (``state.json``). A skip counts as a look.

The ledger: ``data/group_openers/<YYYY-MM-DD>.jsonl`` (``PALLI_GROUP_OPENERS_DIR``
overrides the folder), one row per send ``{at, platform, channel_id, text,
chars, disposition: "sent_group"}`` and one per look that chose not to start
``{at, platform, channel_id, disposition: "no_start", why}``. It is a folder
of its own, so the arbiter's daily ceiling for the owner's private chat
(``ai/arbiter.py::sends_today``) never counts a group opener. A ledger that
cannot be read counts the day as full.

Delivery: ``ai.arbiter._release_outbound`` to the room's own door, which sends
and records it the way PaLLi's other self-started messages are recorded (the
room's transcript, so the room's next turn sees it; the DB row; a deed).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

LANE = "group_opener"
# One prompt, no tools, no thinking (the lane is no-think and PATIENT in
# ai/llm_openai_compatible.py): nobody is waiting on it. A literal, so the lane
# scanner (tests/ai/test_request_timeout_lanes.py) sees the lane at the call.
CALL_ARGS = {"temperature": 0.7, "max_tokens": 700, "log_context": "group_opener"}
CALL_TIMEOUT_SECONDS = 120.0

LOOK_EVERY_SECONDS = 3 * 3600
ACTIVE_SECONDS = 14 * 86400
MID_CONVERSATION_SECONDS = 600
OPENERS_SHOWN = 3
MESSAGE_MAX_CHARS = 400
WHY_LOG_CHARS = 120
WHY_LEDGER_CHARS = 300

NOTHING_OWN = "(nothing right now)"
NO_OPENERS = "(none yet)"

_ROOM_FILE = re.compile(r"^(discord|telegram)_(-?\d+)\.jsonl$")
_SURFACE = {"discord": "discord_text", "telegram": "telegram_text"}

LookLLM = Callable[[str], Awaitable[str]]


# ── seams (tests replace these) ────────────────────────────────────────────

def _settings() -> Any:
    from core.turn.companion_flags import companion_settings

    return companion_settings()


def _idle() -> bool:
    from core.setup_mode import lane_idle

    return lane_idle("group openers")


def _discord_channel(channel_id: int) -> Any:
    """The Discord door's channel object, or None when the door is down or
    does not know it."""
    try:
        from core.agent import hands_runtime as _hr

        client = _hr.get_client()
        get = getattr(client, "get_channel", None)
        return get(int(channel_id)) if callable(get) else None
    except Exception:
        return None


async def _is_private(platform: str, channel_id: int) -> bool:
    from core.doors import is_private_chat

    return await is_private_chat(platform, channel_id, marked_private=False, marked_group=False)


async def _own_life_lines() -> str:
    """The own-life lines that passed the room check, or the words for none.
    The module is built in another branch; missing or failing, it is none."""
    try:
        from core.agent.own_life import room_safe_lines

        text = str(await room_safe_lines() or "").strip()
    except Exception as exc:  # noqa: BLE001 — none is the safe answer
        logger.debug("group opener: own life unavailable (%s)", type(exc).__name__)
        return NOTHING_OWN
    return text or NOTHING_OWN


async def _own_life() -> str:
    return await _own_life_lines()


async def _release(**kwargs: Any) -> Any:
    """The arbiter's one release leg: it sends through the room's door and
    records the line (core/turn/outbound.py::record_outbound: the room's
    transcript, the DB row, a deed). Text only (``vm=None``)."""
    from ai import arbiter

    return await arbiter._release_outbound(**kwargs)


async def _deliver(**kwargs: Any) -> Any:
    return await _release(**kwargs)


def build_llm() -> LookLLM:
    """The quick reads' model, as the first look picks it (``CADENCE_MODEL``;
    unset = the provider's default)."""
    from ai.llm_client import llm as llm_client_singleton
    from core.config import config

    model = (getattr(config, "CADENCE_MODEL", "")
             or os.environ.get("PALLI_CADENCE_MODEL", "")
             or os.environ.get("CADENCE_MODEL", ""))

    async def opener_llm(prompt: str) -> str:
        kwargs: dict[str, Any] = {"messages": [{"role": "user", "content": prompt}]}
        if model:
            kwargs["model"] = model
        return await llm_client_singleton.chat_completion(**kwargs, **CALL_ARGS) or ""

    return opener_llm


# ── paths, days ────────────────────────────────────────────────────────────

def ledger_dir() -> Path:
    raw = os.environ.get("PALLI_GROUP_OPENERS_DIR")
    return Path(raw) if raw else _REPO_ROOT / "data" / "group_openers"


def sessions_root() -> Path:
    # The same root core/turn/outbound.py writes her sends into.
    return Path(os.environ.get("PALLI_AGENT_SESSIONS_ROOT") or "data/agent_sessions")


def day_of(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(at))


def _room_id(platform: str, channel_id: int) -> str:
    return f"{platform}:{int(channel_id)}"


# ── the ledger ─────────────────────────────────────────────────────────────

class _Unreadable(Exception):
    pass


def _read_day(day: str) -> list[Optional[dict]]:
    """A day's rows; a line that cannot be read is None. A missing file is no
    rows; a file that cannot be read raises ``_Unreadable``."""
    path = ledger_dir() / f"{day}.jsonl"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    except Exception as exc:
        raise _Unreadable(str(exc)) from exc
    rows: list[Optional[dict]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        rows.append(row if isinstance(row, dict) else None)
    return rows


def _is_room(row: dict, platform: str, channel_id: int) -> bool:
    try:
        return str(row.get("platform")) == platform and int(row.get("channel_id")) == int(channel_id)
    except (TypeError, ValueError):
        return False


def _is_send(row: dict) -> bool:
    return row.get("disposition") != "no_start"


def sends_today_in(platform: str, channel_id: int, *, now: float) -> int:
    """Openers sent in this room today. A damaged line counts as one of this
    room's sends; an unreadable ledger counts as a full day."""
    try:
        rows = _read_day(day_of(now))
    except _Unreadable as exc:
        logger.warning("group opener: today's ledger is unreadable, the day counts as full: %s", exc)
        return 10 ** 6
    return sum(1 for r in rows if r is None or (_is_room(r, platform, channel_id) and _is_send(r)))


def openers_in(platform: str, channel_id: int, *, now: float) -> list[dict]:
    """Her openers in this room over the last 14 days, oldest first. Raises
    ``_Unreadable`` when a day's ledger cannot be read."""
    out: list[dict] = []
    for back in range(int(ACTIVE_SECONDS // 86400), -1, -1):
        for row in _read_day(day_of(now - back * 86400)):
            if row is None:
                continue
            if _is_room(row, platform, channel_id) and _is_send(row) and str(row.get("text") or "").strip():
                try:
                    out.append({"at": float(row["at"]), "text": str(row["text"])})
                except (KeyError, TypeError, ValueError):
                    continue
    out.sort(key=lambda r: r["at"])
    return out


def _append(row: dict) -> None:
    try:
        d = ledger_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{day_of(float(row['at']))}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("group opener: ledger append failed: %s", exc)


# ── the look state ─────────────────────────────────────────────────────────

def _state_path() -> Path:
    return ledger_dir() / "state.json"


def _load_state() -> Optional[dict]:
    """``{"looked": {room: at}}``; None when the file exists but cannot be
    read (then nothing is looked at, and the file is never overwritten)."""
    path = _state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"looked": {}}
    except Exception as exc:
        logger.warning("group opener: state.json is unreadable (%s); looking at nothing", exc)
        return None
    try:
        data = json.loads(raw) if raw.strip() else {}
        looked = data.get("looked", {}) if isinstance(data, dict) else None
        if not isinstance(looked, dict):
            raise ValueError("not a state")
        return {"looked": {str(k): float(v) for k, v in looked.items()}}
    except (ValueError, TypeError, AttributeError) as exc:
        logger.warning("group opener: state.json is unreadable (%s); looking at nothing", exc)
        return None


def _save_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("group opener: could not save state.json: %s", exc)


# ── the rooms ──────────────────────────────────────────────────────────────

def _candidate_files(*, now: float) -> list[tuple[str, int]]:
    """Every chat-door transcript touched in the last 14 days, as
    ``(platform, channel_id)``. Whether it is a group is decided later."""
    root = sessions_root()
    out: list[tuple[str, int]] = []
    try:
        entries = list(root.iterdir())
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("group opener: could not list the rooms: %s", exc)
        return []
    for path in entries:
        m = _ROOM_FILE.match(path.name)
        if not m:
            continue
        try:
            if now - path.stat().st_mtime > ACTIVE_SECONDS:
                continue
        except OSError:
            continue
        try:
            channel_id = int(m.group(2))
        except ValueError:
            continue
        if channel_id:
            out.append((m.group(1), channel_id))
    return sorted(out)


def _when(m: Any) -> float:
    """A line's time; a naive stamp is UTC, as message_times reads it."""
    from datetime import timezone

    try:
        ts = m.timestamp
        return float((ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).timestamp())
    except Exception:
        return 0.0


def _said(m: Any) -> bool:
    if getattr(m, "role", "") not in ("user", "assistant"):
        return False
    from core.agent.room_tail import _words

    return bool(_words(m).strip())


def _someone_else(m: Any) -> bool:
    """A line from someone other than PaLLi: a person's line, not a system note."""
    if getattr(m, "role", "") != "user" or not _said(m):
        return False
    speaker = getattr(m, "speaker", None) or {}
    return speaker.get("kind") != "system"


async def _load_room(platform: str, channel_id: int) -> Optional[list]:
    from core.agent import room_tail

    return await room_tail.load(_room_id(platform, channel_id), root=sessions_root())


async def _is_group(platform: str, channel_id: int) -> tuple[bool, Any]:
    """(positively a group, the Discord channel when there is one)."""
    channel = None
    if platform == "telegram":
        if int(channel_id) >= 0:
            return False, None
    elif platform == "discord":
        channel = _discord_channel(channel_id)
        if channel is None or getattr(channel, "guild", None) is None:
            return False, None
    else:
        return False, None
    try:
        if await _is_private(platform, channel_id):
            return False, None
    except Exception as exc:  # noqa: BLE001 — not shown is not a group
        logger.info("group opener: the door could not say whether %s:%s is private (%s)",
                    platform, channel_id, type(exc).__name__)
        return False, None
    return True, channel


def _note_stands(platform: str, channel_id: int, *, now: float) -> bool:
    """A standing owner's room note, or a notes file that cannot be read."""
    from datetime import datetime

    from core import room_notes

    try:
        notes = room_notes._load(strict=True)
    except Exception:
        return True
    note = notes.get(_room_id(platform, channel_id))
    if note is None:
        return False
    try:
        return bool(room_notes._is_active(note, datetime.fromtimestamp(now)))
    except Exception:
        return True


# ── what it sees ───────────────────────────────────────────────────────────

def _ago(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 3600:
        n, unit = max(1, int(s // 60)), "minute"
    elif s < 48 * 3600:
        n, unit = int(s // 3600), "hour"
    else:
        n, unit = int(s // 86400), "day"
    return f"{n} {unit}{'' if n == 1 else 's'} ago"


def render_openers(openers: list[dict], messages: list, *, now: float) -> str:
    """Her last few openers here, newest last, each with its age and whether
    anyone wrote in the room after it."""
    if not openers:
        return NO_OPENERS
    lines = []
    for o in openers[-OPENERS_SHOWN:]:
        answered = any(_someone_else(m) and _when(m) > o["at"] for m in messages)
        fact = "someone wrote in the room after it" if answered else "nobody wrote in the room after it"
        text = " ".join(str(o["text"]).split())
        lines.append(f'- {_ago(now - o["at"])}: "{text}" ({fact})')
    return "\n".join(lines)


def _room_card(platform: str, channel_id: int, channel: Any, messages: list) -> str:
    """The room card as a guest's turn sees it (core/agent/room_card.py),
    without its heading: where this is, who has spoken here lately."""
    meta = SimpleNamespace(channel_id=int(channel_id), is_dm=False, origin="guest",
                           channel_name=None, guild_name=None, extra={}, speaker=None)
    if channel is not None:
        meta.channel_name = getattr(channel, "name", None)
        meta.guild_name = getattr(getattr(channel, "guild", None), "name", None)
        parent = getattr(channel, "parent", None)
        if parent is not None:
            meta.extra = {"thread_in": getattr(parent, "name", "") or ""}
    turn = SimpleNamespace(surface=_SURFACE[platform], content="", metadata=meta)
    try:
        from core.agent import room_card

        card = room_card.room_section(turn, messages, _room_id(platform, channel_id))
        lines = [ln for ln in card.splitlines() if ln.strip() and ln != room_card.ROOM_HEADING]
        if lines:
            return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 — a plain card beats none
        logger.info("group opener: the room card could not be built (%s)", type(exc).__name__)
    return "This is a Telegram group." if platform == "telegram" else "This is a group chat on Discord."


def build_prompt(*, current_time: str, room_card: str, room_lines: str, since_last: str,
                 my_openers: str, my_own_life: str) -> str:
    from ai.prompt_loader import prompt_loader

    template = prompt_loader.load_prompt("group_opener")
    if not template:
        raise RuntimeError("prompts/group_opener.txt is missing")
    return template.format(current_time=current_time, room_card=room_card, room_lines=room_lines,
                           since_last=since_last, my_openers=my_openers, my_own_life=my_own_life)


def parse(raw: Any) -> Optional[tuple[bool, str, str]]:
    """``(start, message, why)`` from strict JSON, or None. ``start`` must be
    literally true or false; a start needs a non-empty message."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    from core.llm_json import strip_markdown_fences

    try:
        data = json.loads(strip_markdown_fences(raw))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    start = data.get("start")
    why = data.get("why")
    why = " ".join(why.split()) if isinstance(why, str) else ""
    if start is False:
        return (False, "", why)
    if start is not True:
        return None
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    return (True, message.strip(), why)


# ── one pass ───────────────────────────────────────────────────────────────

def _per_day() -> int:
    from core.turn.companion_flags import speaks_first_on

    if not speaks_first_on():
        return 0
    s = _settings()
    if s is None or not bool(getattr(s, "group_openers", False)):
        return 0
    try:
        return max(0, int(getattr(s, "group_openers_per_day", 0)))
    except (TypeError, ValueError):
        return 0


def _skip(platform: str, channel_id: int, why: str, detail: str = "") -> str:
    logger.info("group opener: skipped %s:%s — %s%s", platform, channel_id, why,
                f" ({detail})" if detail else "")
    return f"skipped:{why}"


async def run_pass(*, owner_id: int, now: Optional[float] = None,
                   llm: Optional[LookLLM] = None) -> str:
    """One pass: at most one room, the one looked at least recently, at most
    every 3 hours per room. Returns what happened (for logs and tests): off,
    none, state_unreadable, skipped:<why>, no_start, sent, too_long,
    unreadable, call_failed, failed. Never raises on a room's account."""
    now = time.time() if now is None else float(now)
    per_day = _per_day()
    if per_day <= 0:
        return "off"
    if _idle():
        return "off"
    state = _load_state()
    if state is None:
        return "state_unreadable"
    looked = state["looked"]
    due = [(looked.get(_room_id(p, c), 0.0), p, c) for p, c in _candidate_files(now=now)
           if now - looked.get(_room_id(p, c), 0.0) >= LOOK_EVERY_SECONDS]
    if not due:
        return "none"

    # Least recently looked at first; a room with no recent line from anyone
    # but her is passed over without counting as a look.
    for _, platform, channel_id in sorted(due):
        messages = await _load_room(platform, channel_id)
        if messages is None:
            continue
        if not any(_someone_else(m) and now - _when(m) <= ACTIVE_SECONDS for m in messages):
            continue
        looked[_room_id(platform, channel_id)] = now
        _save_state(state)
        logger.info("group opener: looked at %s:%s", platform, channel_id)
        return await _consider(platform, channel_id, messages, owner_id=owner_id, now=now,
                               per_day=per_day, llm=llm)
    return "none"


async def _consider(platform: str, channel_id: int, messages: list, *, owner_id: int,
                    now: float, per_day: int, llm: Optional[LookLLM]) -> str:
    is_group, channel = await _is_group(platform, channel_id)
    if not is_group:
        return _skip(platform, channel_id, "not_a_group")
    if _note_stands(platform, channel_id, now=now):
        return _skip(platform, channel_id, "note", "a standing owner's room note")
    said = [m for m in messages if _said(m)]
    last_at = max((_when(m) for m in said), default=0.0)
    if now - last_at < MID_CONVERSATION_SECONDS:
        return _skip(platform, channel_id, "mid_conversation", f"last line {_ago(now - last_at)}")
    if sends_today_in(platform, channel_id, now=now) >= per_day:
        return _skip(platform, channel_id, "cap", f"{per_day} a day")
    try:
        openers = openers_in(platform, channel_id, now=now)
    except _Unreadable as exc:
        return _skip(platform, channel_id, "unanswered", f"the ledger is unreadable: {exc}")
    if openers and not any(_someone_else(m) and _when(m) > openers[-1]["at"] for m in messages):
        return _skip(platform, channel_id, "unanswered", "nobody wrote after the last opener")

    from core.agent import room_tail

    try:
        prompt = build_prompt(
            current_time=time.strftime("%A, %Y-%m-%d %I:%M %p", time.localtime(now)),
            room_card=_room_card(platform, channel_id, channel, messages),
            room_lines=room_tail.render(messages) or "(nothing yet)",
            since_last=_ago(now - last_at),
            my_openers=render_openers(openers, messages, now=now),
            my_own_life=(await _own_life()) or NOTHING_OWN,
        )
    except Exception as exc:
        logger.warning("group opener: the prompt could not be built (%s)", exc)
        return "call_failed"

    call = llm if llm is not None else build_llm()
    try:
        raw = await asyncio.wait_for(call(prompt), timeout=CALL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.info("group opener: no answer from the model in %.0f s", CALL_TIMEOUT_SECONDS)
        return "call_failed"
    except Exception as exc:
        logger.info("group opener: the call failed (%s)", type(exc).__name__)
        return "call_failed"

    parsed = parse(raw)
    if parsed is None:
        logger.info("group opener: unreadable answer (%d chars); nothing sent", len(str(raw or "")))
        return "unreadable"
    start, message, why = parsed
    if not start:
        logger.info("group opener: decided not to start in %s:%s — %s",
                    platform, channel_id, why[:WHY_LOG_CHARS] or "no reason given")
        _append({"at": now, "platform": platform, "channel_id": int(channel_id),
                 "disposition": "no_start", "why": why[:WHY_LEDGER_CHARS]})
        return "no_start"
    if len(message) > MESSAGE_MAX_CHARS:
        logger.info("group opener: the message is %d characters, over %d; not sent",
                    len(message), MESSAGE_MAX_CHARS)
        return "too_long"
    try:
        await _deliver(owner_user_id=int(owner_id), channel_id=int(channel_id), text=message,
                       origin="group_opener", extra={"group_opener": True}, vm=None,
                       platform=platform)
    except Exception as exc:
        logger.warning("group opener: the send to %s:%s failed (%s)", platform, channel_id,
                       " ".join(str(exc).split())[:WHY_LOG_CHARS])
        return "failed"
    _append({"at": now, "platform": platform, "channel_id": int(channel_id),
             "disposition": "sent_group", "text": message, "chars": len(message)})
    logger.info("group opener: started in %s:%s (%d chars)", platform, channel_id, len(message))
    return "sent"

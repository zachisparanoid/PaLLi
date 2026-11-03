"""The weekly harvest of their shared language — the writer of her book of
callbacks (Batch 6, task 2; item 3.3 of docs/2026-09-25-fix-plan.md).

A bit becomes theirs when it is thrown back, and that is a pattern across
days. Judged seconds after each message, from one exchange, it cannot be
seen: the book stayed empty. So once a week, as a step of the night cycle
right after the weekly ritual, she looks over the week before, Monday to
Sunday, of the two of them talking and names at most two bits that became
theirs.

Who decides what (owner rule 07-16):
- the model names the bit, says what kind it is and whose, and quotes the
  line it was said in;
- code keeps the rails and the budgets. The quoted line must be in ONE row
  of the week; the bit must be inside the line; who said it and when come
  from that row and from nowhere else; a bit already in the book — open,
  or retired by their word — is never planted again; at most two births a
  week; once per ISO week.

The store's own rails (a whole receipt, the bit in its line, no twin of an
open bit) are untouched and still apply to every plant made here.

Only rows of the doors where the two of them talk are read: the callbacks
organ's own list of surfaces, through the mapping the rows were written
with. Their "that's not ours" still retires a bit in the turn
(core/turn/reflect.py); finding new ones is this module's alone.

When the harvest completes, a bit planted or not, it writes
``.state.json`` beside the book, so "nothing this week" and "never ran"
look different to whoever reads the folder. A failed call and an answer
outside the contract write nothing, and the next night tries again.

Ships behind ``companion.idiolect`` and nothing else.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

MAX_BIRTHS_PER_WEEK = 2
# How far down a list of proposals the harvest reads: enough that a few
# refused ones do not cost a real bit its place, and a runaway list ends.
MAX_PROPOSALS_READ = 10
WINDOW_DAYS = 7
STATE_NAME = ".state.json"
# Clears the provider's whole DEEP ladder (4 x 240 s + backoff = 970 s), the
# disclosure harvest's and the stake review's envelope; pinned by
# test_the_envelope_clears_the_whole_deep_ladder.
_HARVEST_TIMEOUT_S = 1000.0
_MIN_LINE_CHARS = 4
_BOOK_ROWS = 40
# The week is shown to her as "HH:MM  them: …" lines, so a quote can come
# back with that label still on it. The label is this module's own
# rendering, never something either of them said.
_LABEL_RE = re.compile(r"^\s*\d{1,2}:\d{2}\s+(?:them|me):\s*")
_SLOT_RE = re.compile(r"<<([A-Z_]+)>>")
_NOTHING_YET = "(nothing yet)"
_WHO = {"user": "him", "assistant": "her"}
# She is asked whose a bit is in the words the week is shown to her in
# ("them:", "me:"); the book files a coiner in its own words. The book's
# own words pass as they are.
_COINER = {"them": "him", "me": "her", "both": "both", "him": "him", "her": "her"}


@dataclass(frozen=True)
class _Row:
    id: int
    at: float
    role: str
    content: str


class _AnswerRefused(ValueError):
    """The model answered outside the contract. Nothing is written, so the
    week reads as "never ran" and the health line names the reason."""


# ── the week's record ────────────────────────────────────────────────────

def week_key_for(now: float) -> str:
    """The ISO week ``now`` is in, as ``YYYY-Www``."""
    iso = datetime.fromtimestamp(float(now)).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _state_path(root: Optional[Path]) -> Path:
    """Beside the book, wherever the book is (call-time resolution)."""
    from ai import callbacks_store

    return callbacks_store._resolve_root(root) / STATE_NAME


def _read_state(root: Optional[Path]) -> Dict[str, Any]:
    try:
        state = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(root: Optional[Path], week: str, *, planted: int, now: float) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps({"last_week": week, "planted": int(planted), "at": float(now)}) + "\n",
        encoding="utf-8", newline="\n")
    os.replace(tmp, path)


# ── the seven days ───────────────────────────────────────────────────────

def _conversational_platforms() -> frozenset:
    """The platform names the rows of a conversation are stored under: the
    callbacks organ's own list of surfaces, through the mapping the rows
    were written with. A reminder's row is stored under another name
    (reminder_fire), so it is never read. Her reach-outs are read: each is
    filed under the platform of the door it went out through
    (core/turn/outbound.py), so they are in the week — on purpose, since a
    line of hers that the owner throws back is part of their talk."""
    from ai.callbacks_store import CONVERSATIONAL_SURFACES
    from core.turn.recall import platform_for_surface

    return frozenset(platform_for_surface(s) for s in CONVERSATIONAL_SURFACES)


def _window_days(now: float) -> List[str]:
    """The ISO week before the one ``now`` claims (week_key_for, the same
    local calendar): Monday to Sunday, whole local days, oldest first.

    Tied to the claim, not to the night: a week is claimed only when a
    harvest completes, so a failed call is tried again on a later night of
    the same week, and that night must read the same seven days. A window
    that slid with the night would never read the days between the last
    week read and the new one."""
    today = datetime.fromtimestamp(float(now)).date()
    first = today - timedelta(days=today.weekday() + WINDOW_DAYS)
    return [(first + timedelta(days=n)).strftime("%Y-%m-%d") for n in range(WINDOW_DAYS)]


async def _load_week(
    owner_id: int, now: float, *, brain_client: Any = None,
) -> List[Tuple[str, List[_Row]]]:
    """→ [(day, the owner's conversation rows of that day, oldest first)],
    one store call per day. Raises when the store cannot be read: a week
    that could not be read is not a quiet week."""
    from ai.consolidation import _day_bounds

    if brain_client is None:
        from core.database import brain as brain_client
    platforms = _conversational_platforms()
    week: List[Tuple[str, List[_Row]]] = []
    for day in _window_days(now):
        start_ts, end_ts = _day_bounds(day)
        found = await brain_client.get_conversations_for_day(
            day_start_ts=start_ts, day_end_ts=end_ts)
        rows: List[_Row] = []
        for r in found or []:
            if int(r.get("user_id") or 0) != int(owner_id):
                continue
            if str(r.get("platform") or "") not in platforms:
                continue
            role = str(r.get("role") or "")
            content = str(r.get("content") or "")
            if role not in _WHO or not content.strip():
                continue
            rows.append(_Row(id=int(r.get("id") or 0),
                             at=float(r.get("timestamp") or start_ts),
                             role=role, content=content))
        rows.sort(key=lambda row: (row.at, row.id))
        week.append((day, rows))
    return week


def _render_week(week: List[Tuple[str, List[_Row]]]) -> str:
    """The week as the night renders a day, under one line per day; a day
    with nothing said is left out. "" for a week with nothing said."""
    from ai.night_cycle import _render_day_conversation

    parts: List[str] = []
    for day, rows in week:
        rendered = _render_day_conversation([(r.at, "", r.role, r.content) for r in rows])
        if rendered:
            heading = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %Y-%m-%d")
            parts.append(f"{heading}\n{rendered}")
    return "\n\n".join(parts)


# ── the book she already has ─────────────────────────────────────────────

def _book_section(root: Optional[Path]) -> str:
    """The bits already in the book, newest first: the bit and nothing
    else (the receipt is why she can say it, never what she is shown), and
    a mark on the ones they took back."""
    from ai import callbacks_store

    rows = sorted(callbacks_store.load_callbacks(root=root).values(),
                  key=lambda c: c.born_at, reverse=True)[:_BOOK_ROWS]
    if not rows:
        return _NOTHING_YET
    return "\n".join(
        f'- "{c.bit}"' + (" (taken back: they said it is not yours together)"
                          if c.retired_at is not None else "")
        for c in rows)


def _already_in_the_book(bit: str, root: Optional[Path]) -> str:
    """"" when the bit is new; otherwise why it is refused. The store only
    knows a twin of an OPEN bit, so a bit they retired would be planted
    again the week after: their word is kept here."""
    from ai import callbacks_store

    key = callbacks_store.normalize(bit)
    found = ""
    for c in callbacks_store.load_callbacks(root=root).values():
        if callbacks_store.normalize(c.bit) != key:
            continue
        if c.retired_at is None:
            return "already in the book"
        found = "retired by their word"
    return found


# ── the rails ────────────────────────────────────────────────────────────

def _refuse(bit: Any, reason: str) -> None:
    """ONE log line per refusal, naming the rail that turned it away."""
    logger.info("callbacks_harvest: birth refused: %s (%r)", reason, bit)
    return None


def _well_formed(item: Any) -> Optional[Dict[str, str]]:
    """A proposal with its four fields, each of them text, the kind one of
    the book's own and the coiner one this module can file; None
    otherwise. Rebuilt from the four known keys, so a receipt the model
    wrote never travels."""
    from ai.callbacks_store import BIT_CAP, KINDS

    if not isinstance(item, dict):
        return None
    bit, kind, coined_by, line = (item.get(k) for k in ("bit", "kind", "coined_by", "line"))
    if not all(isinstance(v, str) for v in (bit, kind, coined_by, line)):
        return None         # never made into text: her book is words that were said
    op = {"bit": bit.strip()[:BIT_CAP], "kind": kind.strip(),
          "coined_by": _COINER.get(coined_by.strip().lower(), ""), "line": line.strip()}
    if not op["bit"] or op["kind"] not in KINDS or not op["coined_by"] or not op["line"]:
        return None
    return op


def verify_birth_in_week(op: Dict[str, str], rows: List[_Row]) -> Optional[Dict[str, str]]:
    """The receipts rail. The quoted line must be in ONE row of the week
    (compared the way every rail of this organ compares, callbacks_store
    .normalize), and the bit must be inside the line. The first row that
    carries the line is the receipt: who from its speaker, when from its
    timestamp, source its row. None ⇒ nothing is written."""
    from ai.callbacks_store import LINE_CAP, normalize

    line = _LABEL_RE.sub("", str(op.get("line") or ""), count=1).strip()[:LINE_CAP]
    key = normalize(line)
    if len(key) < _MIN_LINE_CHARS:
        return _refuse(op.get("bit"), "line not in the week")
    said = next((r for r in rows if key in normalize(r.content)), None)
    if said is None:
        return _refuse(op.get("bit"), "line not in the week")
    # A real line would otherwise vouch for a bit nobody said. An empty key
    # is inside everything: refuse that too.
    bit_key = normalize(op.get("bit") or "")
    if not bit_key or bit_key not in key:
        return _refuse(op.get("bit"), "bit not in its line")
    return {
        "who": _WHO[said.role],
        "when": datetime.fromtimestamp(said.at, tz=timezone.utc).isoformat(),
        "line": line,
        "source": f"conversation:{said.id}",
    }


def _parse_born(raw: Any) -> List[Any]:
    """Her answer → the proposals as she sent them ([] is the honest
    "nothing this week"). Raises _AnswerRefused outside the contract."""
    from core.llm_json import safe_parse_json_object

    obj = safe_parse_json_object(raw if isinstance(raw, str) else "")
    if not isinstance(obj, dict) or not isinstance(obj.get("born"), list):
        raise _AnswerRefused("unparseable")
    return obj["born"]


# ── the harvest ──────────────────────────────────────────────────────────

async def run_weekly_callbacks_harvest(
    owner_id: int,
    *,
    llm_call: Callable[[str], Awaitable[str]],
    now: float,
    root: Optional[Path] = None,
    brain_client: Any = None,
) -> Dict[str, Any]:
    """The night's weekly step. Never raises (a cancel goes through).

    Reads the ISO week before the one it claims (the same seven days on
    whichever night of the week it runs), asks her once, and
    plants at most MAX_BIRTHS_PER_WEEK bits, each through the rails above
    and then the store's own. A week already recorded is not run again."""
    from ai import callbacks_store

    if not callbacks_store.idiolect_enabled():
        return {"skipped": "flag_off"}
    try:
        week = week_key_for(now)
    except Exception as exc:
        return {"error": f"bad_now: {exc}"}
    if _read_state(root).get("last_week") == week:
        return {"skipped": "already_ran", "week": week}

    result: Dict[str, Any] = {"week": week, "planted": []}
    refused = over_the_cap = 0
    try:
        days = await _load_week(int(owner_id), now, brain_client=brain_client)
        rows = [r for _day, day_rows in days for r in day_rows]
        conversation = _render_week(days)
        if not conversation:
            result["skipped"] = "no_conversation"
        else:
            from ai.prompt_loader import prompt_loader

            template = prompt_loader.load_prompt("night_cycle_callbacks_harvest")
            if not template:
                raise RuntimeError("night_cycle_callbacks_harvest prompt missing")
            values = {
                "WEEK_DATES": f"{days[0][0]} to {days[-1][0]}",
                "WEEK_CONVERSATION": conversation,
                "ALREADY_IN_THE_BOOK": _book_section(root),
            }
            # One pass: a slot name typed inside the week is never a slot.
            prompt = _SLOT_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)
            raw = await asyncio.wait_for(llm_call(prompt), timeout=_HARVEST_TIMEOUT_S)
            for item in _parse_born(raw)[:MAX_PROPOSALS_READ]:
                if len(result["planted"]) >= MAX_BIRTHS_PER_WEEK:
                    over_the_cap += 1
                    continue
                if not _plant_one(item, rows, root, result["planted"]):
                    refused += 1
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # The type name is the fallback: a timeout's str() is "", and an
        # empty error reads as a healthy week on the health line.
        why = (" ".join(str(exc).split()) or type(exc).__name__)[:160]
        logger.warning("callbacks_harvest: the harvest failed for %s: %s", week, why)
        return {"week": week, "error": why}

    if refused:
        result["refused"] = refused
    if over_the_cap:
        result["over_the_cap"] = over_the_cap
    try:
        _write_state(root, week, planted=len(result["planted"]), now=float(now))
    except OSError as exc:
        logger.warning("callbacks_harvest: could not write the week's record for %s: %s",
                       week, exc)
        result["error"] = f"state_unwritable: {' '.join(str(exc).split())}"[:160]
    essential_logger.info(
        "callbacks_harvest: %s — planted=%d refused=%d%s%s", week, len(result["planted"]),
        refused, f" over_the_cap={over_the_cap}" if over_the_cap else "",
        f" ({result['skipped']})" if result.get("skipped") else "")
    return result


def _plant_one(item: Any, rows: List[_Row], root: Optional[Path], planted: List[str]) -> bool:
    """One proposal through every rail. True when it is in the book now;
    a refusal is logged where its reason is known. Never raises: one
    failing plant must not cost the proposals after it."""
    from ai import callbacks_store

    op = _well_formed(item)
    if op is None:
        _refuse(item.get("bit") if isinstance(item, dict) else item, "not well formed")
        return False
    try:
        why = _already_in_the_book(op["bit"], root)
        if why:
            _refuse(op["bit"], why)
            return False
        receipt = verify_birth_in_week(op, rows)
        if receipt is None:
            return False            # verify_birth_in_week logged which rail refused
        born = callbacks_store.plant(bit=op["bit"], kind=op["kind"],
                                     coined_by=op["coined_by"], receipt=receipt, root=root)
        if born is None:
            _refuse(op["bit"], "the book refused it")
            return False
        planted.append(born.id)
        from ai.recent_deeds import record_deed

        record_deed("callback_born", f'a bit of ours was born: "{born.bit}"')
        return True
    except Exception as exc:
        logger.warning("callbacks_harvest: a plant failed (%r): %s: %s",
                       op.get("bit"), type(exc).__name__, exc)
        return False


async def callbacks_harvest_llm_call(prompt: str) -> str:
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model

    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        # Room to think as well as to answer (the stake review's number):
        # this lane thinks over a whole week, and the answer is short.
        model=get_task_model("appraisal"), max_tokens=8000, temperature=0.4,
        log_context="night_cycle_callbacks_harvest",
    )

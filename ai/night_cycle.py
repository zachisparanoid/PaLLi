"""Night Cycle — Selfhood Phase 1 (2026-07-11).

A downtime engine (06:30 local since 2026-10-05; 04:00 before) that reads
the day PaLLi lived and writes one first-person "what today meant"
reflection to ai/night_cycle_store, surfaced in the next day's reply.
Ships behind companion.night_cycle (default-OFF):
flag off => this whole organ is a no-op and the prompt is byte-identical.

v1 is READ-ONLY over existing memory — it only reads the day and writes one
new reflection; nothing already stored is mutated. Gist-aging is increment 2.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ai.consolidation import _day_bounds  # the day-gist's local-midnight day window
from ai.night_cycle_store import write_daily_meaning
from ai.prompt_loader import prompt_loader
from ai.stake_appraiser import ALREADY_HERS  # a twin view: the rails working, not a failure
from core.setup_mode import lane_idle
from core.turn.companion_flags import companion_settings

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

_QUIET_DAY = "(a quiet, ordinary day — little of note)"

# Two different hours (the owner, 10-05: "move the night run off-peak").
#
# NIGHT_CYCLE_HOUR is the DAY BOUNDARY, 04:00 local: a nocturnal owner is
# often still talking until then, so the night never starts before it
# (night_run_at refuses an earlier run time), and the workbench budget's day
# turns at the same hour (companion.own_work_day_key_hour, default 4). It is
# no longer the run time. Which day a night reflects on is unchanged: the
# calendar day before the run (_reflection_date).
NIGHT_CYCLE_HOUR = 4
# The RUN TIME: when the night pass starts, local — the setting
# companion.night_cycle_run_at, default 06:30. DeepSeek bills double from
# 01:00-04:00 and 06:00-10:00 UTC on weekdays (ai/deepseek_peak.py), and
# 04:00 in New York is 08:00/09:00 UTC: peak. 06:30 there is 10:30 UTC
# under EDT and 11:30 UTC under EST, off-peak all year; 01:00 was ruled out
# because it falls in peak once the clocks change on Nov 1. The tick, the
# boot catch-up, the liveness check's due night and tonight's day key all
# read this, never NIGHT_CYCLE_HOUR.
NIGHT_RUN_AT_DEFAULT = "06:30"
_RUN_AT = re.compile(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*")
_RUN_AT_WARNED: set = set()
NIGHT_CYCLE_STATE = Path("data/night_cycle/.engine_state.json")
# The night's record (Batch 6, ruling R51): one file a night, what every
# step returned. ai/organ_liveness.py reads it to tell a step that had
# nothing to do from one that broke.
NIGHT_OUTCOMES_DIR = Path("data/night_cycle/.outcomes")
_NIGHT_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")


def _surface_errors(result: Dict[str, Any]) -> str:
    """Compose the tick's health line from EVERY failure in the pass — the
    top-level reflection error plus any nested consolidation step errors.
    Review fix (2026-07-15): reading only result['error'] actively WIPED
    last_error on nights where the reflection succeeded but consolidation
    failed, so weeks of silent failure looked healthy."""
    parts: List[str] = []
    top = str(result.get("error") or "")
    if top:
        parts.append(top)
    cons = result.get("consolidation")
    if isinstance(cons, dict):
        if cons.get("error"):
            parts.append(f"consolidation: {cons['error']}")
        # "today_retired" (R15, T12; fix round 1): a failed retire leaves
        # yesterday's rows active, so it names itself here.
        for step in ("day_gist", "aging", "reconcile", "today_retired"):
            sub = cons.get(step)
            if isinstance(sub, dict) and sub.get("error"):
                parts.append(f"{step}: {sub['error']}")
    narr = result.get("narrative")
    if isinstance(narr, dict):
        if narr.get("error"):
            parts.append(f"narrative: {narr['error']}")
        for doc, status in (narr.get("docs") or {}).items():
            if not isinstance(status, dict):
                continue
            if status.get("error"):
                parts.append(f"narrative {doc}: {status['error']}")
            elif status.get("rejected"):
                parts.append(f"narrative {doc} rejected: {status['rejected']}")
    # Organ 3 review fix (2026-07-20): passions was invisible here, and this
    # is the WORST organ to lose — "no page tonight" is its designed common
    # case, so a permanently broken one (prompt_missing, unparseable_judgment,
    # gate_error, week_marker_unwritable, a refused page, the containment
    # wrapper's own error) read exactly like a run of quiet days.
    pas = result.get("passions")
    if isinstance(pas, dict):
        if pas.get("error"):
            parts.append(f"passions: {pas['error']}")
        for step in ("journal", "interests"):
            sub = pas.get(step)
            if not isinstance(sub, dict):
                continue
            if sub.get("error"):
                parts.append(f"passions {step}: {sub['error']}")
            elif sub.get("gate_rejected"):
                parts.append(f"passions {step} rejected: {sub['gate_rejected']}")
    # Grounded Vulnerability (organ 4.9, 2026-09-01): the same class as the
    # passions fix above. The harvest's DESIGNED common case is planted=None
    # — "nothing this week" — so a permanently broken one (prompt missing,
    # unparseable candidate, the containment wrapper's own error) reads
    # exactly like a run of quiet weeks unless it is named here.
    # Its likeliest failures raise nothing, so the exception path alone does
    # not close the class: a model swap or prompt drift that stops producing
    # contract JSON, and a candidate the receipt law refuses, both write
    # nothing and look exactly like a quiet week. Those name themselves.
    # "Nothing tonight", the organ off, the cap declining and a truth she
    # already holds are healthy nights and stay silent.
    disc = result.get("disclosure_harvest")
    if isinstance(disc, dict):
        if disc.get("error"):
            parts.append(f"disclosure_harvest: {disc['error']}")
        elif disc.get("skipped") in ("unparseable", "malformed_text", "no_receipt"):
            parts.append(f"disclosure_harvest refused: {disc['skipped']}")
    # The memory sweep (Batch 5, 2026-09-28): the same class. Its designed
    # common case is a quiet night ({"moved": 0, "stamped": 0}), so a broken
    # one — the store refusing the write, the refs table unreadable — would
    # read like a run of quiet nights while MEMORY.md grew unswept.
    sweep = result.get("memory_sweep")
    if isinstance(sweep, dict) and sweep.get("error"):
        parts.append(f"memory_sweep: {sweep['error']}")
    # The night's housekeeping (Batch 8, tasks 3 and 5): after the first
    # night the prune removes a handful of copies or none and the log sweep
    # a day's worth of files, so only a failure speaks — the entry point's
    # own, or a step's.
    for step in ("housekeeping", "history_prune", "retention", "today_prune",
                 "reply_sources_prune"):
        chore = result.get(step)
        if isinstance(chore, dict) and chore.get("error"):
            parts.append(f"{step}: {chore['error']}")
    # The preferences rebuild (Batch 5, task 7, 2026-09-28): the rebuilder
    # reports its own failures as status=error / status=partial without
    # raising, so both shapes are named here beside the raised one — a
    # cache that quietly stopped following the preferences doc would read
    # exactly like a quiet night.
    rebuild = result.get("preferences_rebuild")
    if isinstance(rebuild, dict):
        if rebuild.get("error"):
            parts.append(f"preferences_rebuild: {rebuild['error']}")
        elif rebuild.get("status") == "partial":
            parts.append(f"preferences_rebuild partial: {rebuild.get('failed', '?')} rows failed")
    # The thread retirement (Batch 5, task 8): {"retired": 0} is its
    # designed common case, so only a failure speaks.
    retirement = result.get("thread_retirement")
    if isinstance(retirement, dict) and retirement.get("error"):
        parts.append(f"thread_retirement: {retirement['error']}")
    # One thing on her mind (Batch 6, task 1): the harvest's class again.
    # "No pick tonight" is the step's ordinary answer, so a broken one — an
    # answer outside the contract, a failed call — and a pick refused for
    # naming something she was not offered both name themselves. A null
    # night, the flag off and a night already picked stay silent.
    ton = result.get("tonight")
    if isinstance(ton, dict):
        if ton.get("error"):
            parts.append(f"tonight: {ton['error']}")
        elif ton.get("refused"):
            parts.append(f"tonight refused: {ton['refused']}")
    # Views at night (Batch 6, task 2): the same class. "No view formed
    # today" is the pass's ordinary answer, so a broken one — a failed
    # call, an answer outside the contract — and a view the ledger refused
    # (no evidence) both name themselves. A night with no view, a day with
    # no conversation, a full waiting list, the flag off and a night
    # already run stay silent — and so does a view she already holds (a
    # twin): the rails worked and nothing new was needed (Batch 6 review,
    # fix round 1).
    views = result.get("views")
    if isinstance(views, dict):
        if views.get("error"):
            parts.append(f"views: {views['error']}")
        elif views.get("refused") and views["refused"] != ALREADY_HERS:
            parts.append(f"views refused: {views['refused']}")
    # The weekly shared-language harvest (Batch 6, task 2): the same class
    # again. A week with no new bit is its ordinary answer, and a birth the
    # rails refused is the rails working; only a broken harvest speaks.
    bits = result.get("callbacks_harvest")
    if isinstance(bits, dict) and bits.get("error"):
        parts.append(f"callbacks_harvest: {bits['error']}")
    return "; ".join(parts)


_REFLECTION_KEYS = ("wrote", "skipped", "error")


def _night_record(date: str, result: Dict[str, Any], *, at: float, ritual_now: float) -> Dict[str, Any]:
    """The night's record (Batch 6, ruling R51): what every step returned —
    counts, a skip reason, an error — and what the night's health line
    names as a failure (or a plain error the line does not read), so "ran
    and had nothing to do" and "broke" can be told apart afterwards; until
    now a step's result reached only a log line and memory. On a night the
    weekly ritual ran, or tried to, ``weekly`` holds its week, its per-page
    results and the fears re-look's result — or ``not_reached`` with the
    ritual's error, so a ritual that stopped before the re-look reads
    differently from a re-look with nothing to plant."""
    steps: Dict[str, Any] = {"reflection": {k: result[k] for k in _REFLECTION_KEYS if k in result}}
    steps.update({k: v for k, v in result.items() if k not in _REFLECTION_KEYS})
    failed: Dict[str, str] = {}
    for step, value in steps.items():
        why = "" if step == "reflection" else _surface_errors({step: value})
        if not why and isinstance(value, dict) and value.get("error"):
            why = f"{step}: {value['error']}"
        if why:
            failed[step] = why
    record: Dict[str, Any] = {"night": date, "at": float(at), "steps": steps, "failed": failed}
    ritual = result.get("narrative")
    if isinstance(ritual, dict) and ("week" in ritual or ritual.get("error")):
        from ai.narrative_self import week_key_for

        if "fears" in ritual:
            fears = ritual["fears"]
        elif ritual.get("error"):
            fears = {"not_reached": str(ritual["error"])}
        else:
            fears = {"skipped": "flag_off"}   # the re-look returns nothing while its organ is off
        record["weekly"] = {
            "week": str(ritual.get("week") or week_key_for(ritual_now)),
            "pages": ritual.get("docs") or {},
            "fears": fears,
        }
    return record


def _save_night_record(date: str, record: Dict[str, Any]) -> Path:
    """Write the night's record in one step (a temp file renamed over). A
    ``weekly`` section an earlier run of the same night left is kept: a
    boot catch-up re-runs an unclaimed night, and the ritual — already done
    for the week — would otherwise erase the week's only record. Blocking
    I/O: the engine calls this off the event loop."""
    if not _NIGHT_NAME.fullmatch(str(date or "")):
        raise ValueError(f"night_cycle: not a night: {date!r}")
    directory = NIGHT_OUTCOMES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{date}.json"
    if "weekly" not in record:
        try:
            earlier = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            earlier = None
        if isinstance(earlier, dict) and isinstance(earlier.get("weekly"), dict):
            record = {**record, "weekly": earlier["weekly"]}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, default=str) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def night_run_at(companion: Any = None) -> Tuple[int, int]:
    """(hour, minute), local, when the night pass starts: the setting
    ``companion.night_cycle_run_at`` ("HH:MM") of ``companion`` — the live
    settings when None — else 06:30. A value that does not read as HH:MM,
    or one earlier than the day boundary (NIGHT_CYCLE_HOUR), runs at 06:30
    and says so once: a night that starts while the owner's day is still
    going would reflect on a day not yet over, and tonight's pick would
    turn over mid-conversation. Never raises; cheap enough for every turn
    (tonight's day key reads it)."""
    raw = NIGHT_RUN_AT_DEFAULT
    try:
        comp = companion if companion is not None else companion_settings()
        value = getattr(comp, "night_cycle_run_at", None) if comp is not None else None
        if value is not None:
            raw = str(value)
    except Exception:
        pass
    m = _RUN_AT.fullmatch(raw)
    if m is not None and (int(m.group(1)), int(m.group(2))) >= (NIGHT_CYCLE_HOUR, 0):
        return int(m.group(1)), int(m.group(2))
    if raw not in _RUN_AT_WARNED:
        _RUN_AT_WARNED.add(raw)
        logger.warning(
            "night_cycle: night_cycle_run_at %r is not a time from %02d:00 to 23:59 "
            "— the night runs at %s", raw[:20], NIGHT_CYCLE_HOUR, NIGHT_RUN_AT_DEFAULT,
        )
    hour, minute = NIGHT_RUN_AT_DEFAULT.split(":")
    return int(hour), int(minute)


def night_run_offset(companion: Any = None) -> timedelta:
    """The run time as an offset from local midnight (06:30 -> 6.5 hours)."""
    hour, minute = night_run_at(companion)
    return timedelta(hours=hour, minutes=minute)


def night_cycle_enabled() -> bool:
    """THE gate for this default-OFF faculty. Unbooted settings (None) or a
    missing flag => disabled — inverted from the default-ON faculties,
    mirroring ai/stake_ledger.ledger_enabled()."""
    comp = companion_settings()
    return comp is not None and bool(getattr(comp, "night_cycle", False))


async def run_night_cycle(
    *,
    owner_id: int,
    date: str,
    day_material: str,
    llm_call,
    store_write,
    now: float,
) -> Dict[str, Any]:
    """Run one daily-meaning reflection for ``date`` and persist it.

    NEVER raises (except ``CancelledError``, which must propagate so shutdown
    can cancel cleanly): every other failure path returns a status dict, since
    the engine runs this on a fire-and-forget lane that can only log-and-move-on.
    Flag OFF => no LLM call, no write, byte-identical no-op.

    ``llm_call`` and ``store_write`` are injected so the pass is testable without
    a live brain or disk; the engine (Task 4) supplies the real ones.
    """
    if not night_cycle_enabled():
        return {"skipped": "flag_off"}
    try:
        template = prompt_loader.load_prompt("night_cycle_daily_meaning")
        if not template:
            logger.warning("night_cycle: reflection prompt missing; skipping")
            return {"error": "prompt_missing"}
        material = (day_material or "").strip() or _QUIET_DAY
        prompt = template.replace("<<DAY_MATERIAL>>", material)
        text = await llm_call(prompt)
        text = (text or "").strip()
        if not text:
            return {"error": "empty_reflection"}
        store_write(
            date=date,
            text=text,
            meta={"owner_id": int(owner_id), "created_at": float(now)},
        )
        essential_logger.info(
            "night_cycle: wrote daily-meaning for %s (%d chars)", date, len(text)
        )
        return {"wrote": date}
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # never-raises: engine lane logs and moves on
        logger.warning("night_cycle: reflection pass failed for %s: %s", date, exc)
        return {"error": str(exc)}


# ── the real dependencies the engine injects into run_night_cycle ────────
#
# These reach the live brain / memory / owner refs. They are imported lazily so
# that importing this module (e.g. under unit tests) stays cheap and free of the
# heavy LLM/DB import graph, and so the pass itself remains testable with fakes.


async def _daily_meaning_llm_call(prompt: str) -> str:
    """The real reflective completion — free-text, first person. Mirrors the
    inner-monologue engine's model pick (the 'reflection' task, memory as
    fallback). Returns the raw string; ``run_night_cycle`` handles empties."""
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model

    model = get_task_model("reflection", fallback=get_task_model("memory"))
    response = await asyncio.wait_for(
        llm.chat_completion(
            [{"role": "user", "content": prompt}],
            model=model,
            temperature=0.7,
            # max_tokens is a REASONING budget here, not an output budget —
            # the same disease already documented on the gist lane in
            # ai/consolidation.py (and hand-fixed three times before on other
            # lanes). This lane never got its raise, and at 400 it cost her
            # real nights: 07-30 and 07-31 burned 400/400 on chain-of-thought
            # and returned empty (finish_reason=length, date already claimed,
            # never retried); 08-02 spent 387/400 thinking and emitted a
            # 54-char clause that was stored as a success. Output stays short
            # regardless — the prompt asks for ONE short paragraph and healthy
            # reflections measure ~750 chars — so the headroom is free.
            max_tokens=4000,
            log_context="night_cycle_daily_meaning",
        ),
        timeout=180,  # patient-lane envelope: 2x60s ceiling + headroom (2026-08-09)
    )
    return response or ""


def _surprise_markers(day: str, *, root=None) -> List[str]:
    """The day's REAL surprises as markers the reflection metabolizes
    (organ 8, spec §4). [] on any failure — the night never dies for a
    marker; slight surprises stay in the moment record only."""
    try:
        from ai.present_moment_store import read_day

        out: List[str] = []
        for e in read_day(day, root=root):
            s = (e.meta or {}).get("surprise") or {}
            if s.get("weight") == "real" and s.get("line"):
                out.append(f"[a surprise, about {s.get('about')}: {s.get('line')}]")
        return out
    except Exception:
        return []


def _fear_markers(day: str, *, root=None) -> List[str]:
    """The day's REAL fear touches (organ 6, step 24 §4) — same class as
    the surprise markers above: [] on any failure, slight touches stay in
    the moment record only."""
    try:
        from ai.present_moment_store import read_day

        out: List[str] = []
        for e in read_day(day, root=root):
            f = (e.meta or {}).get("fear") or {}
            if f.get("weight") == "real" and f.get("line") and f.get("id"):
                out.append(f"[a fear touched, {f.get('id')}: {f.get('line')}]")
        return out
    except Exception:
        return []


_DAY_CONVERSATION_CAP = 6000
_DAY_CLIP_MARK = "\n…\n"


def _render_day_conversation(
    rows: List[Tuple[float, str, str, str]], *, cap: int = _DAY_CONVERSATION_CAP
) -> str:
    """The day's conversation as a plain log, one message per line, in the
    order given: ``HH:MM  them: …`` for the owner, ``HH:MM  me: …`` for her
    (local time). Never "you:" — the night prompt calls her "you"
    throughout, so a "you:" line under a heading that calls the owner "them"
    could read as her own words (the misattribution class, DEFECT §32;
    ruling R16). Rows whose role is neither user nor assistant, and rows
    with nothing in them, are skipped. A day longer than ``cap`` keeps its
    first and last halves — peak and ending — around one ``…`` line, whole
    lines only, so no fragment reaches the reflection without its speaker.
    ``""`` for an empty day, so the caller leaves the section out entirely.
    Mechanical rendering; nothing here decides meaning."""
    lines: List[str] = []
    for ts, _platform, role, content in rows:
        who = {"user": "them", "assistant": "me"}.get(str(role or ""))
        text = " ".join(str(content or "").split())
        if who is None or not text:
            continue
        when = datetime.fromtimestamp(float(ts)).strftime("%H:%M")
        lines.append(f"{when}  {who}: {text}")
    out = "\n".join(lines)
    if len(out) <= cap:
        return out
    half = max(0, (cap - len(_DAY_CLIP_MARK)) // 2)
    # Head: snap back to the last line break, so it ends on a whole line (a
    # first line longer than half stays a fragment, but a labelled one).
    head = out[:half]
    cut = head.rfind("\n")
    if cut != -1:
        head = head[:cut]
    # Tail: start at the first line break inside the window, so every line
    # after the mark carries its "HH:MM  who:" — a bare fragment would hand
    # the reflection words with no speaker (the misattribution class behind
    # the three-doors inversion). A window that sits inside one huge message
    # keeps that line's own label in front of its end, within the same half.
    tail_start = len(out) - half
    tail = out[tail_start:]
    cut = tail.find("\n")
    if cut != -1:
        tail = tail[cut + 1 :]
    else:
        line = out[out.rfind("\n", 0, tail_start) + 1 :]
        label = line[: line.find(": ") + 2]  # this function's own prefix
        keep = max(0, half - len(label))
        tail = label + line[len(line) - keep :]
    return head.rstrip() + _DAY_CLIP_MARK + tail


async def _load_day_conversation(
    owner_id: int, date: str
) -> List[Tuple[float, str, str, str]]:
    """The owner's conversation rows for ``date`` (local midnight to
    midnight), every door, oldest first, as ``(timestamp, platform, role,
    content)``. The store's day query takes no user id and holds every
    speaker's rows, so this keeps only the owner's — her replies carry the
    owner's user id too (core/turn/persistence.py). Reminder fires are
    already excluded by the store."""
    from core.database import brain

    start_ts, end_ts = _day_bounds(date)
    rows = await brain.get_conversations_for_day(day_start_ts=start_ts, day_end_ts=end_ts)
    day: List[Tuple[float, str, str, str]] = []
    for r in rows or []:
        if int(r.get("user_id") or 0) != int(owner_id):
            continue
        day.append(
            (
                float(r.get("timestamp") or start_ts),
                str(r.get("platform") or ""),
                str(r.get("role") or ""),
                str(r.get("content") or ""),
            )
        )
    day.sort(key=lambda row: row[0])
    return day


async def _gather_day_material(*, owner_id: int, date: str) -> str:
    """Best-effort assembly of everything PaLLi has of ``date``: the day's
    real conversation with the owner, plus the thread of her own
    inner-monologue thoughts.

    Never raises — a missing source is just an omitted section; total failure
    yields ``""`` and the pass falls back to its quiet-day placeholder. v1 is
    strictly READ-ONLY over these stores."""
    parts: List[str] = []
    # Corrections of record (2026-08-17) — FIRST: today's corrections with
    # the day law, then the standing set; the reflection must never restate
    # a corrected belief (the 08-14 reflection was about "blankness" while
    # the day's correction went unwritten).
    try:
        from ai.corrections_store import render_corrections_block

        today_block = render_corrections_block(budget=800, day=date)
        standing_block = render_corrections_block(budget=700, sync=False)
        if today_block:
            parts.append(today_block)
        elif standing_block:
            parts.append(standing_block)
    except Exception as exc:  # best-effort
        logger.debug("night_cycle: corrections unavailable: %s", exc)
    try:
        # Batch 3 item 1.4 (2026-09-28): this section used to read the
        # memory-workspace day-log (get_day), whose newest file is 2026-04-30
        # — so the reflection saw an empty or ancient day every night (and,
        # before the 2026-07-15 fix of a nonexistent import here, none at
        # all). It now reads the day's real conversation from the database,
        # every door. memory_workspace_service.get_day stays in place for
        # ai/consolidation.py, which still reads it.
        rendered = _render_day_conversation(
            await _load_day_conversation(owner_id, date)
        )
        if rendered:
            parts.append("## The day with them\n" + rendered)
    except Exception as exc:  # best-effort, but LOUD: a store failure every
        # night would otherwise give her a "quiet day" forever, unseen — the
        # 2026-07-15 precedent above, where a debug-level swallow meant the
        # day never reached a single reflection.
        logger.warning(
            "night_cycle: day conversation unavailable for %s: %s", date, exc
        )
    try:
        from ai.inner_monologue_store import (
            format_entries_for_prompt,
            read_recent_for_context,
        )

        entries = read_recent_for_context(max_entries=12, max_age_hours=20.0)
        thread = format_entries_for_prompt(entries)
        if thread and thread.strip():
            parts.append("## My own quiet thoughts today\n" + thread.strip()[:4000])
    except Exception as exc:  # best-effort
        logger.debug("night_cycle: monologue thread unavailable: %s", exc)
    # Surprise markers (organ 8, spec §4): the day's REAL departures reach
    # the reflection and the day-gist as her own noticing.
    markers = _surprise_markers(date)
    if markers:
        parts.append(
            "## The day's surprises (your own noticing)\n" + "\n".join(markers)
        )
    # Fear markers (organ 6, step 24 §4) beside the surprise markers: the
    # reflection and the day-gist metabolize a REAL touch as her own
    # noticing. Most days this section simply is not there (decision 5).
    fear_marks = _fear_markers(date)
    if fear_marks:
        parts.append(
            "## Fears the day touched (your own noticing)\n" + "\n".join(fear_marks)
        )
    return "\n\n".join(p for p in parts if p.strip())


async def _resolve_owner_id() -> int:
    """The local owner's canonical app user id (memory_service's owner_user_id)."""
    from core.local_mode import get_local_owner_refs

    refs = await get_local_owner_refs()
    return int(refs.get("app_user_id") or 0)


def _thread_archive_days() -> int:
    """``memory.thread_archive_days`` from the settings snapshot; the
    schema's default (45) when settings cannot be read or the block is
    missing (the core.memory_refs.memory_core_budget pattern). Batch 5,
    task 8: the age past which a never-surfaced thread retires. 0 is kept
    (it disables the retirement, as the schema allows)."""
    try:
        from core.settings_boot import settings_service

        memory = settings_service.snapshot().memory
    except Exception:
        memory = None
    try:
        return int(getattr(memory, "thread_archive_days", 45))
    except (TypeError, ValueError):
        return 45


def _daily_meaning_text(date: str, *, root=None) -> str:
    """Her note for ``date`` as text, or "" — never the dataclass. Found
    2026-08-21: the review and the owner picture were handed
    ``str(NightCycleEntry(...))`` — a Python repr — as 'her note tonight'."""
    try:
        from ai import night_cycle_store as _ncs

        entry = _ncs.read_daily_meaning_for(date=date, root=root)
    except Exception:
        return ""
    return str(getattr(entry, "text", "") or "").strip()


class NightCycleEngine:
    """Background engine: once per local day at the run time (06:30 by
    default, night_run_at), reflect on the day just lived. Cloned from
    ``InnerMonologueEngine`` but scheduled to a wall-clock time rather than a
    rolling interval, and guarded by a date-keyed claim so a restart at 06:35
    can't re-reflect on a day already done."""

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self.is_running: bool = False
        self.last_error: str = ""
        self.run_count: int = 0
        self._task: Optional[asyncio.Task[None]] = None
        self._state_path: Path = (
            Path(state_path) if state_path is not None else NIGHT_CYCLE_STATE
        )

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._tick_loop(), name="night_cycle")
        essential_logger.info("Night cycle engine started")

    async def stop(self) -> None:
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        essential_logger.info("Night cycle engine stopped")

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "last_error": self.last_error,
            "run_count": self.run_count,
            "last_run_date": self._read_last_run_date(),
        }

    # ── scheduling ──────────────────────────────────────────────────────

    def _seconds_until_next_run(self, now: datetime) -> float:
        """Seconds from ``now`` until the next local run time (06:30 by
        default) — today's if still ahead, otherwise tomorrow's."""
        hour, minute = night_run_at()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return (target - now).total_seconds()

    def _reflection_date(self, now: datetime) -> str:
        """The day PaLLi reflects on. The engine wakes at its run time (06:30
        by default since 2026-10-05; 04:00 before), never before the 04:00
        day boundary, so the day just lived is YESTERDAY's calendar date, not the fresh post-midnight sliver
        (design 4.1: the note "written at 4 a.m." is about "tonight" — the day
        that just ended). Reflecting on ``now``'s own date would metabolize an
        almost-empty few hours; yesterday captures the whole lived day. The
        later run time does not change it: at 06:30 it is still yesterday."""
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # ── idempotency ─────────────────────────────────────────────────────

    def _read_last_run_date(self) -> str:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return str(data.get("last_run_date", ""))
        except (OSError, json.JSONDecodeError):
            return ""

    def _claim_date(self, date_str: str) -> bool:
        """Return True the first time we see ``date_str`` (recording it), False
        afterward. THE guard that stops a restart — or a second wake in the same
        minute — from re-reflecting on a day already done."""
        if self._read_last_run_date() == date_str:
            return False
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"last_run_date": date_str}), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("night_cycle: could not persist run-date: %s", exc)
        return True

    # ── loop ────────────────────────────────────────────────────────────

    def _catchup_due(self, now: datetime) -> bool:
        """Whether an unclaimed reflection date means MISSED rather than
        not-due-yet. Before the run time (06:30 by default), yesterday's date
        is always unclaimed simply because its pass hasn't happened — a boot
        then must defer to the tick, or a post-midnight restart writes the
        night early (it did, live, at 01:54 on 2026-08-06 when the arbiter
        launch restarted her). Since 2026-10-05 the guard is the run time,
        not the 04:00 day boundary: a boot between 04:00 and 06:30 waits for
        06:30 rather than starting the night at DeepSeek's peak price."""
        return (now.hour, now.minute) >= night_run_at()

    async def _tick_loop(self) -> None:
        await asyncio.sleep(30)  # let the rest of boot settle
        # Boot catch-up (R1-durable, 2026-08-04): if the day just lived is
        # still unclaimed — last night's pass failed, or the run time fell in
        # downtime — run it now instead of losing it forever. The owner's
        # frequent nightly restarts are the natural recovery lever; before
        # this, a failed pass could never retry because the date was claimed
        # BEFORE the LLM call (07-30 and 07-31 were both lost that way).
        # Guarded (2026-08-06): only when the run time (06:30 by default)
        # has already passed today — see _catchup_due.
        if self.is_running and self._catchup_due(datetime.now()):
            try:
                await self._attempt_pass()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.last_error = str(exc)
                logger.error("night_cycle: boot catch-up error: %s", exc, exc_info=True)
            await self._report_liveness()
            await self._refresh_owner_rhythm()
        while self.is_running:
            try:
                delay = self._seconds_until_next_run(datetime.now())
                await asyncio.sleep(delay)
                if not self.is_running:
                    break
                await self._attempt_pass()
                await self._report_liveness()
                await self._refresh_owner_rhythm()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = str(exc)
                logger.error("night_cycle: loop error: %s", exc, exc_info=True)
                await asyncio.sleep(600)  # back off, then re-arm for the next run time

    async def _report_liveness(self) -> None:
        """The smoke detector (2026-08-09): one loud line a day, right after
        the pass — the moment the freshest expectations exist. Only the
        organs whose flags are on are judged (Batch 6, ruling R49), against
        her database and the repo her weekly ritual commits to. Off the event
        loop (ruling R55): the check reads SQLite and asks git for a log, and
        a blocking read on the loop starves every other lane (the boot-import
        lesson, DEFECT §12). Guarded: the checker must never kill the night
        task (the presence-loop lesson, same shape)."""
        try:
            from ai import organ_liveness
            from ai.narrative_self import identity_palli_root
            from core.database import resolve_database_path
            from core.settings_boot import settings_service

            try:
                settings = settings_service.snapshot()
            except Exception:  # unreadable settings: every organ is expected
                settings = None
            report = await asyncio.to_thread(
                organ_liveness.check_liveness,
                now=datetime.now(),
                data_root=Path("data"),
                # The due night turns over at the RUN time, not the 04:00
                # day boundary (the owner, 10-05): it is the night this
                # pass just wrote.
                boundary_hour=night_run_offset().total_seconds() / 3600.0,
                flags=organ_liveness.liveness_flags(settings),
                db_path=resolve_database_path(),
                identity_root=identity_palli_root(),
            )
            if report.healthy:
                logger.info(report.summary())
            else:
                logger.warning(report.summary())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("organ_liveness: check failed (ignored): %s", exc)

    async def _refresh_owner_rhythm(self) -> None:
        """Regenerate her rhythm beliefs (ai.owner_rhythm) nightly, right
        after the pass — gated on ``companion.rhythm_refresh`` alone
        (byte-identical off) and guarded (a learner crash must never kill
        the night task). Until owner call 7 (2026-09-28) the gate was
        ``arbiter_learned_quiet``: the file went stale on 2026-08-25, the
        day quiet hours went off, and the surprise count fell from 22 (Aug)
        to 1 (Sep) — the surprise organ reads the file with no staleness
        check, so its timing line went wrong, not absent. Quiet hours stay
        off; only the refresh is decoupled."""
        try:
            c = companion_settings()
            # The fallback mirrors the schema default (on): a settings object
            # without the key still refreshes.
            if c is None or not bool(getattr(c, "rhythm_refresh", True)):
                return
            import ai.owner_rhythm as owner_rhythm

            await asyncio.to_thread(
                owner_rhythm.refresh_rhythm_file,
                now=time.time(),
                lookback_days=int(getattr(c, "rhythm_lookback_days", 56)),
                max_active_ratio=float(
                    getattr(c, "rhythm_quiet_max_active_ratio", 0.08)
                ),
                min_weekday_days=int(
                    getattr(c, "rhythm_min_observed_weekdays", 15)
                ),
                min_weekend_days=int(
                    getattr(c, "rhythm_min_observed_weekend_days", 6)
                ),
            )
            logger.info("owner_rhythm: beliefs refreshed")
        except Exception as exc:
            logger.warning("owner_rhythm: refresh failed (ignored): %s", exc)

    async def _attempt_pass(self) -> None:
        """One guarded reflection attempt: skip when the flag is off or the
        day is already claimed; otherwise run, and claim the date ONLY when
        the reflection actually wrote. A failed night stays unclaimed so the
        boot catch-up (or a hand-run) can honestly retry it — the reflection
        store overwrites per-date, so a rare double run regenerates rather
        than duplicates."""
        if not night_cycle_enabled():
            return  # flag off; re-arm for the next run time
        if lane_idle("night cycle"):
            # Setup mode (first door, 2026-09-17): no brain. Every nightly
            # organ rides this pass (reflection, consolidation, narrative,
            # stake review, passions, the picture, the harvest). The date is
            # left unclaimed, so the boot catch-up can still write the night.
            return
        reflect_date = self._reflection_date(datetime.now())
        if self._read_last_run_date() == reflect_date:
            return  # already reflected on the day just lived
        result = await self._run_once(reflect_date)
        if (result or {}).get("wrote"):
            self._claim_date(reflect_date)
        else:
            logger.error(
                "night_cycle: pass for %s did not write (%s) — date left "
                "unclaimed; it will retry at the next boot",
                reflect_date,
                _surface_errors(result or {}),
            )
        # Present Moment sense E: the night's work landed — stage
        # names only, never content.
        #
        # 2026-07-24 review fix: run_night_cycle's own result is a
        # FLAT dict — top-level "wrote"/"skipped"/"error" are
        # STRINGS, so the original isinstance(v, dict) filter never
        # saw the reflection outcome at all, only the
        # "consolidation"/"narrative"/"passions" sub-dicts
        # _run_once attaches afterward. Worse, run_passions_step's
        # own dict is always {"journal": {...}, "interests": {...}}
        # with no top-level error/skipped key even when both
        # sub-steps skipped — its real success signal
        # ("wrote": True) lives one level deeper than
        # consolidation/narrative's does — so "passions" passed the
        # old filter on every single night regardless of whether
        # either sub-step wrote anything. Measured effect: a
        # typical night reported stages == ["passions"], naming the
        # organ that did nothing and omitting the reflection that
        # ran. Each stage below now checks its own real success
        # signal; anything unexpected just fails to match (never
        # raises past the outer except) — the degrade-to-empty
        # contract still holds.
        try:
            from ai.present_moment import notice as _pm_notice
            _r = result or {}
            _stages: list = []
            if _r.get("wrote"):
                _stages.append("reflection")
            for _stage_key in ("consolidation", "narrative"):
                _sub = _r.get(_stage_key)
                if isinstance(_sub, dict) and "error" not in _sub and "skipped" not in _sub:
                    _stages.append(_stage_key)
            _pas = _r.get("passions")
            if isinstance(_pas, dict):
                _wrote_journal = bool((_pas.get("journal") or {}).get("wrote"))
                _wrote_interests = bool((_pas.get("interests") or {}).get("wrote"))
                if _wrote_journal or _wrote_interests:
                    _stages.append("passions")
            _pm_notice("organ_wrote", organ="night_cycle", stages=_stages)
        except Exception:
            pass
        self.run_count += 1
        self.last_error = _surface_errors(result)

    async def _run_once(self, date: str) -> Dict[str, Any]:
        owner_id = await _resolve_owner_id()
        material = await _gather_day_material(owner_id=owner_id, date=date)
        result = await run_night_cycle(
            owner_id=owner_id,
            date=date,
            day_material=material,
            llm_call=_daily_meaning_llm_call,
            store_write=write_daily_meaning,
            now=time.time(),
        )
        # Wave 1a (2026-07-15): consolidation rides the same tick, AFTER the
        # daily-meaning pass. Gated inside run_nightly_consolidation
        # (companion.consolidation, default OFF => {"skipped": "flag_off"});
        # its failure must never cost the night's reflection.
        try:
            import ai.consolidation as consolidation

            result["consolidation"] = await consolidation.run_nightly_consolidation(
                owner_id=owner_id, day=date
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: consolidation step failed: %s", exc)
            result["consolidation"] = {"error": str(exc)}
        # Wave 1b (2026-07-16): the Narrative Self weekly ritual rides the
        # same tick, AFTER consolidation. Gated inside run_weekly_ritual
        # (companion.narrative_self, default OFF ⇒ {"skipped": "flag_off"};
        # ISO-week marker ⇒ at most one pass per week). Its failure must
        # never cost the night's reflection or consolidation. Its clock is
        # kept: the night's record names the week the ritual ran for.
        ritual_now = time.time()
        try:
            import ai.narrative_self as narrative_self

            result["narrative"] = await narrative_self.run_weekly_ritual(
                owner_id=owner_id,
                llm_call=narrative_self.narrative_author_llm_call,
                gate_llm_call=narrative_self.narrative_gate_llm_call,
                now=ritual_now,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: narrative step failed: %s", exc)
            result["narrative"] = {"error": str(exc)}
        # The shared language, weekly (Batch 6, task 2, 2026-09-29): right
        # after the weekly ritual — after the pages — she looks over seven
        # days of the two of them talking for a bit that became theirs. At
        # most two births a week; none is an honest week. NOT inside the
        # ritual: that one returns early in any week with no page revised,
        # and sits behind the story pages' flag and their first writing.
        # Gated inside (companion.idiolect alone) and run once per ISO week
        # by its own record beside her book. Its failure must never cost
        # the earlier steps.
        try:
            from ai import callbacks_harvest

            result["callbacks_harvest"] = await callbacks_harvest.run_weekly_callbacks_harvest(
                owner_id, llm_call=callbacks_harvest.callbacks_harvest_llm_call,
                now=time.time(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: the shared-language harvest failed: %s",
                           exc or type(exc).__name__)
            result["callbacks_harvest"] = {"error": str(exc) or type(exc).__name__}
        # Correction-triggered targeted revision (2026-08-17): if any of her
        # self-pages still carries a belief the corrections ledger has since
        # corrected, revise THOSE pages off-cycle — same author, same gate,
        # never touching the ISO-week marker. The W34 incident's cadence
        # half: a Tuesday correction used to sit in her pages until Monday.
        # Skipped entirely when the weekly just ran this tick (it already
        # rewrote from evidence that leads with the corrections block).
        try:
            import ai.narrative_self as narrative_self

            weekly = result.get("narrative") or {}
            weekly_ran = isinstance(weekly, dict) and bool(weekly.get("docs"))
            if weekly_ran:
                result["narrative_targeted"] = {"skipped": "weekly_ran_this_tick"}
            else:
                result["narrative_targeted"] = await narrative_self.run_targeted_correction_pass(
                    owner_id=owner_id,
                    llm_call=narrative_self.narrative_author_llm_call,
                    gate_llm_call=narrative_self.narrative_gate_llm_call,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: targeted correction pass failed: %s", exc)
            result["narrative_targeted"] = {"error": str(exc)}
        # The stake review (2026-08-18): after the reflection is written, she
        # looks over what she holds for him and lets go of what has gone quiet
        # (resolve/fade/update, never open), under a deterministic age
        # backstop. Gated inside (companion.stake_ledger). The ledger froze at
        # 12/12 for 34 days when the per-exchange appraiser was its only
        # writer. Its failure must never cost the earlier steps.
        try:
            import ai.stake_review as stake_review

            result["stake_review"] = await stake_review.run_nightly_stake_review(
                owner_id=owner_id,
                day=date,
                day_reflection=_daily_meaning_text(date),
                llm_call=stake_review.stake_review_llm_call,
                now=time.time(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: stake review failed: %s", exc)
            result["stake_review"] = {"error": str(exc)}
        # Views at night (Batch 6, task 2, 2026-09-29): right after the
        # review, so the pass sees the board as the review left it. She
        # looks over the WHOLE day for a stance of hers that showed up more
        # than once; at most one view a night, none most nights. Judged
        # seconds after each message, from a few lines, not one view ever
        # formed. The gate is read here, before the day is loaded, so a
        # dark organ reads nothing; the pass checks it again, and runs once
        # per night by its own record. The date is the day looked back on.
        try:
            from ai import stake_appraiser, stake_ledger

            if stake_ledger.ledger_enabled() and stake_ledger.opinions_enabled():
                result["views"] = await stake_appraiser.appraise_views(
                    date,
                    llm_call=stake_appraiser.night_views_llm_call,
                    now=time.time(),
                    day_conversation=_render_day_conversation(
                        await _load_day_conversation(owner_id, date)
                    ),
                )
            else:
                result["views"] = {"skipped": "flag_off"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: the views pass failed: %s", exc or type(exc).__name__)
            result["views"] = {"error": str(exc) or type(exc).__name__}
        # Memory that picks (Batch 5, 2026-09-28, spec 2.2): the nightly
        # sweep of her memory of them by age and use. A Durable Context or
        # Useful References line unused for memory.durable_archive_days, an
        # Active Thread untouched for memory.thread_archive_days, MOVES to the
        # same section of MEMORY_ARCHIVE.md (never deleted; `palli_ctl memory
        # restore` brings it back); a line with no stamp and no reference is
        # stamped with today instead. Deterministic, sidecar-backed. It took
        # the slot of the 08-18 pass that dropped old threads outright.
        try:
            from ai.memory_sweep import sweep_memory

            result["memory_sweep"] = await sweep_memory(owner_id=owner_id, now=time.time())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: memory sweep failed: %s", exc)
            result["memory_sweep"] = {"error": str(exc)}
        # The night's housekeeping (Batch 8, task 3, ruling T3-3): right
        # after the sweep, so tonight's copies count, old copies of her
        # identity documents go — the newest few of each (10 by the owner's
        # rule), one a day for a month, one a week before that and the
        # first ever stay (memory.history_keep_last; 0 = off). Then (task 5)
        # logs and throwaway folders past their limits go, aged by the date
        # in each name; her records never (retention.enabled; off = none).
        # One entry point, one import here, for every housekeeping step; each
        # step is its own key in the night's record. Its failure must never
        # cost the earlier steps.
        try:
            from core.retention import run_nightly_housekeeping

            result.update(await run_nightly_housekeeping(now=time.time()))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: housekeeping failed: %s", exc or type(exc).__name__)
            result["housekeeping"] = {"error": str(exc) or type(exc).__name__}
        # The preferences cache (Batch 5, task 7, 2026-09-28, rulings R22 and
        # R47): the user_memory rows derived from the preferences doc's
        # pref_ entries — what recall's fact retrieval reads — are rebuilt
        # from the doc, right after the sweep. The rebuilder scrubs and
        # rewrites only its OWN rows (a cache) and has had no caller since
        # May, so the cache drifted from what reflect wrote. Deterministic,
        # no LLM. Its failure must never cost the earlier steps.
        try:
            import core.memory_service as memory_service

            result["preferences_rebuild"] = await memory_service.rebuild_user_memory_from_documents(
                owner_user_id=owner_id
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: preferences rebuild failed: %s", exc)
            result["preferences_rebuild"] = {"error": str(exc)}
        # The thread retirement (Batch 5, task 8, rulings R14/R47/R50): an
        # active thread her judgment NEVER surfaced, held longer than
        # memory.thread_archive_days, is set down — status 'dismissed' with
        # a 'retired_unsurfaced' reason, reversible with `palli_ctl threads
        # unretire`; recurring rows never. The arbiter's daily backstop
        # (ai/arbiter.py maybe_run_daily_sweep → expire_stale at 45 days)
        # already dismisses every old topic thread, surfaced or not, so
        # live this finds about none: it is the never-surfaced rule under
        # the memory knob, not a second backstop. Deterministic, no LLM.
        try:
            from core.database import brain

            days = _thread_archive_days()
            retired = await brain.retire_unsurfaced_open_threads(
                owner_user_id=owner_id, older_than_days=days,
            )
            result["thread_retirement"] = {"retired": int(retired or 0), "older_than_days": days}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: thread retirement failed: %s", exc)
            result["thread_retirement"] = {"error": str(exc)}
        # The picture of him (2026-08-19): MEMORY_SUMMARY + RELATIONSHIP_STATE
        # redrawn in her voice from the swept memory + tonight's note — the
        # two owner docs that rendered placeholders into every reply.
        try:
            import ai.owner_picture as owner_picture

            result["owner_picture"] = await owner_picture.run_nightly_owner_picture(
                owner_id=owner_id, day_reflection=_daily_meaning_text(date),
                llm_call=owner_picture.owner_picture_llm_call, now=time.time(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: owner picture failed: %s", exc)
            result["owner_picture"] = {"error": str(exc)}
        # Organ 3 (2026-07-19): Passions rides the same tick, AFTER the
        # narrative ritual. Gated inside (companion.passions, default OFF ⇒
        # {"skipped": "flag_off"}); journal nightly-when-earned, interests
        # ISO-weekly. Its failure must never cost the earlier steps.
        try:
            import ai.passions as passions

            result["passions"] = await passions.run_passions_step(
                owner_id=owner_id, date=date, now=time.time()
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: passions step failed: %s", exc)
            result["passions"] = {"error": str(exc)}
        # Grounded Vulnerability (organ 4.9, spec §2): the disclosure
        # harvest — zero most nights, at most one small truth, receipted.
        try:
            import ai.disclosure_harvest as disclosure_harvest

            result["disclosure_harvest"] = await disclosure_harvest.run_nightly_disclosure_harvest(
                day=date, llm_call=disclosure_harvest.disclosure_harvest_llm_call,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: disclosure harvest failed: %s", exc)
            result["disclosure_harvest"] = {"error": str(exc)}
        # One thing on her mind (Batch 6, task 1, 2026-09-29): the night's
        # LAST step — after the review has let go of what went quiet and
        # after passions wrote tonight's journal page, so both are in front
        # of her when she picks. At most one pick; none is an honest night.
        # Gated inside (companion.tonight) and written once per night there,
        # so a boot catch-up that re-runs this pass never replaces a pick.
        # The clock is the wall clock, not ``date``: the pick belongs to the
        # day that is starting, the day she will say it in.
        try:
            from ai import tonight

            result["tonight"] = await tonight.run_nightly_pick(
                owner_id=owner_id, llm_call=tonight.tonight_pick_llm_call, now=time.time(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: tonight's pick failed: %s", exc)
            result["tonight"] = {"error": str(exc)}
        # The night's record (Batch 6, ruling R51): what every step above
        # returned, kept on disk, so the health check can tell a step that
        # had nothing to do from one that broke. Written off the event
        # loop; a record that cannot be written never costs the night.
        try:
            record = _night_record(date, result, at=time.time(), ritual_now=ritual_now)
            await asyncio.to_thread(_save_night_record, date, record)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("night_cycle: could not save the record of the night of %s: %s",
                           date, exc or type(exc).__name__)
        return result


# Module singleton — boot wiring (Task 5) starts/stops this.
night_cycle_engine = NightCycleEngine()

"""One thing on her mind — the nightly pick and its record (Batch 6, task 1;
items 3.1 and 3.2 of docs/2026-09-25-fix-plan.md).

She has hopes and worries, a journal and daydreams, and almost none of it
ever reached the owner: 30+ stakes on the ledger, not one recorded as said.
Each night, as the last step of the night cycle, she picks AT MOST ONE
thing she would bring up — from her open hopes and worries, her last seven
journal pages and her recent daydreams — and it is written to
``data/tonight/<day>.json``. Her reply prompt shows it until she has said
it; a small yes/no check after each reply, and after a say the arbiter
sent, records that she did.

Who decides what (owner rule 07-16):
- the model picks, and the model says whether she said it;
- code keeps the two budgets: one pick a night, written once; said once.

The day a pick belongs to is the local date of the moment it was picked,
moved back by the night cycle's run time (06:30 by default since
2026-10-05; 04:00 before): the hours after midnight still belong to the pick
of the night before, so a conversation that runs past midnight keeps the
same pick, until the next night picks. An unsaid pick is not carried into
the next night.

A night with nothing worth saying still writes its file, with a null pick,
so "nothing to say" and "never ran" look different to whoever reads the
folder. An answer outside the contract writes nothing and raises.

Ships behind ``companion.tonight`` (off by default). The CALLERS check the
gate — the night step here, the reply band, reflect, the moment — and the
functions that read and write the one file are plain. The folder is
resolved at call time, so tests redirect it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

TONIGHT_DIR = Path("data/tonight")
TEXT_CAP = 300
WHY_CAP = 240
SOURCES = ("stake", "journal", "daydream")
# A regret and a view are never offered: a regret is said once, ever, by
# the arbiter's own stamp, and a view has its own law. Each keeps its door.
PICKABLE_STAKE_KINDS = ("hope", "worry", "taste", "wonder")
# The pick's fixed id on the moment's thread board (ai/present_moment_gather.py).
THREAD_ID = "tonight"

BAND_HEADING = "## One thing on my mind"
BAND_LAW = (
    "Say it once, when it fits, in your own words — never force it, "
    "never repeat it once it's said."
)
# The same law on the moment's thread board, where the line stays until the
# reply's said-check (strict on purpose) says yes: a false no must not read
# as leave to say it again. Whether it was already brought up is the
# model's to read, from the talk it is shown.
BOARD_LAW = ("bring it up only if you have not already brought it up with them; "
             "never repeat it once it's said")

_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# The night cycle's default run time (ai.night_cycle.NIGHT_RUN_AT_DEFAULT),
# for when that module cannot be read.
_DEFAULT_BOUNDARY = timedelta(hours=6, minutes=30)
# The ledger's open rows are every kind together (the four here, regrets,
# views), so the read asks for more than the open cap of 12 — the stake
# review's own number.
_STAKE_READ_LIMIT = 60
_STAKE_TEXT_CAP = 400
_JOURNAL_PAGES = 7
_JOURNAL_PAGE_CAP = 900
_DAYDREAM_DAYS = 7
_DAYDREAM_LIMIT = 10
_DAYDREAM_TEXT_CAP = 300
_CONVERSATION_CAP = 4000
_REPLY_CAP = 6000
_ID_CAP = 80
# Clears the provider's whole DEEP ladder (4 x 240 s + backoff = 970 s), the
# harvest's and the stake review's envelope; pinned by
# test_the_picks_envelope_clears_the_whole_deep_ladder.
_PICK_TIMEOUT_S = 1000.0
_NOTHING = "(nothing here)"


@dataclass
class Pick:
    day: str
    source: Literal["stake", "journal", "daydream"]
    source_id: str
    text: str                       # her words, at most TEXT_CAP characters
    why: str
    said_at: Optional[float] = None


class PickRefused(ValueError):
    """The model answered outside the contract. Nothing is written, so the
    night reads as "never ran" and the health line names the reason."""


def tonight_enabled() -> bool:
    try:
        from core.turn.companion_flags import companion_settings
        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "tonight", False))
    except Exception:
        return False


# Alias for gate-logic tests to restore the real implementation when the
# autouse conftest guard patches tonight_enabled off.
tonight_enabled_impl = tonight_enabled


def is_conversation(surface: Any) -> bool:
    """True on a surface where the two of them talk — the callbacks organ's
    own list of doors (ai/callbacks_store.py), so there is one list for the
    house. The band shows only there and the said-check runs only there:
    after a reminder or a reach-out nothing she said could be recorded.

    Fails closed, and says nothing. It is asked on every turn while a pick
    is unsaid; a line here would be noise, and the callbacks organ's own
    gate in reflect logs under the callbacks name, which is why the
    said-check does not use that one (fix round 1, ruling R61b)."""
    try:
        from ai.callbacks_store import CONVERSATIONAL_SURFACES

        return isinstance(surface, str) and surface in CONVERSATIONAL_SURFACES
    except Exception:
        return False


# ── the file ─────────────────────────────────────────────────────────────

def _root() -> Path:
    """Call-time resolution, so a redirect of TONIGHT_DIR takes effect."""
    return TONIGHT_DIR


def _boundary() -> timedelta:
    """The night cycle's RUN time, not its 04:00 day boundary (the owner,
    10-05: the night moved to 06:30, off DeepSeek's peak). A pick stands
    from the night that writes it to the next one; keyed at 04:00 it would
    vanish at 04:00 and the next would not exist until 06:30 — two and a
    half hours of no band and no said-check every day, in the hours a
    nocturnal owner is still talking."""
    try:
        from ai.night_cycle import night_run_offset

        return night_run_offset()
    except Exception:
        return _DEFAULT_BOUNDARY


def day_for(now: float) -> str:
    """The night ``now`` belongs to: its local date, moved back by the night
    cycle's run time. With the default 06:30, 06:31 on the 29th and 05:00 on
    the 30th are both the 29th."""
    moment = datetime.fromtimestamp(float(now)) - _boundary()
    return moment.strftime("%Y-%m-%d")


def _path(day: str) -> Optional[Path]:
    """The day's file, or None for anything that is not a date: the day
    becomes a file name, so nothing else may reach the path."""
    day = str(day or "")
    if not _DAY_RE.fullmatch(day):
        return None
    return _root() / f"{day}.json"


def _read_night(day: str) -> Optional[Dict[str, Any]]:
    """The night's whole record, or None when there is no readable one."""
    path = _path(day)
    if path is None:
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _write_night(day: str, record: Dict[str, Any]) -> None:
    path = _path(day)
    if path is None:
        raise ValueError(f"tonight: not a day: {day!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _clean(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def _pick_from(day: str, record: Optional[Dict[str, Any]]) -> Optional[Pick]:
    chosen = (record or {}).get("pick")
    if not isinstance(chosen, dict):
        return None
    source = chosen.get("source")
    source_id = chosen.get("source_id")
    text = chosen.get("text")
    if source not in SOURCES or not isinstance(source_id, str) or not source_id:
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    said_at = (record or {}).get("said_at")
    if isinstance(said_at, bool) or not isinstance(said_at, (int, float)):
        said_at = None
    why = chosen.get("why")
    return Pick(day=day, source=source, source_id=source_id, text=text,
                why=why if isinstance(why, str) else "",
                said_at=float(said_at) if said_at is not None else None)


def load_pick(day: str) -> Optional[Pick]:
    """The pick of that night, said or not. None when the night picked
    nothing, when it never ran, or when its file cannot be read."""
    return _pick_from(str(day or ""), _read_night(day))


def load_current(now: Optional[float] = None) -> Optional[Pick]:
    """The pick of the night ``now`` belongs to — that night's file only."""
    return load_pick(day_for(time.time() if now is None else now))


def mark_said(day: str, *, at: float) -> bool:
    """Said once: only an unsaid pick takes the stamp, and the first time
    is the record. True when the file changed. Never raises."""
    try:
        record = _read_night(day)
        pick = _pick_from(str(day or ""), record)
        if record is None or pick is None or pick.said_at is not None:
            return False
        record["said_at"] = float(at)
        _write_night(day, record)
        return True
    except Exception as exc:
        logger.warning("tonight: could not mark %s said (%s)", day, exc or type(exc).__name__)
        return False


# ── what she is shown ────────────────────────────────────────────────────

def render_band(pick: Optional[Pick]) -> str:
    """The reply prompt's band, whole, heading included; "" once the pick
    is said or when there is none. Her words only: the reason and the id
    stay in the file."""
    if pick is None or pick.said_at is not None:
        return ""
    text = str(pick.text or "").strip()
    if not text:
        return ""
    return f"{BAND_HEADING}\n{BAND_LAW}\n{text}"


def render_board_line(pick: Optional[Pick]) -> str:
    """The pick as one line of the moment's thread board, under its fixed
    id; "" once it is said or when there is none."""
    if pick is None or pick.said_at is not None:
        return ""
    text = _clean(pick.text, TEXT_CAP)
    if not text:
        return ""
    return (f"- [{THREAD_ID}] [one thing on your mind | when-the-moment-fits "
            f"| {BOARD_LAW}] {text}")


# ── what she may pick from ───────────────────────────────────────────────

@dataclass(frozen=True)
class _Candidate:
    source: str
    id: str
    text: str
    kind: str = ""
    note: str = ""      # a stake she already said: "said MM-DD"


def _field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _said_tag(voiced_at: Any) -> str:
    """"said MM-DD" for a stake she already brought up, from the ledger's
    ``voiced_at`` in local time; "" when it was never said or the stamp is
    not a time. The stake stays on the list: the prompt says what the tag
    means, and whether it is worth bringing up again is hers to decide
    (fix round 1, ruling R61a)."""
    if isinstance(voiced_at, bool) or not isinstance(voiced_at, (int, float)):
        return ""
    try:
        return "said " + datetime.fromtimestamp(float(voiced_at)).strftime("%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""


def _candidates(stakes: List[Any], journal_pages: List[Any],
                daydreams: List[Any]) -> List[_Candidate]:
    """Every row with an id becomes a candidate; an id is offered once."""
    out: List[_Candidate] = []
    seen: set = set()

    def add(source: str, raw_id: Any, text: Any, kind: str = "", note: str = "") -> None:
        cid = _clean(raw_id, _ID_CAP)
        if raw_id is None or not cid or (source, cid) in seen:
            return
        seen.add((source, cid))
        out.append(_Candidate(source=source, id=cid, text=str(text or ""), kind=kind,
                              note=note))

    for s in stakes or []:
        add("stake", _field(s, "id"), _clean(_field(s, "stake"), _STAKE_TEXT_CAP),
            _clean(_field(s, "kind"), 20), _said_tag(_field(s, "voiced_at")))
    for p in journal_pages or []:
        add("journal", _field(p, "id"), str(_field(p, "text") or "").strip()[:_JOURNAL_PAGE_CAP])
    for d in daydreams or []:
        raw_id = _field(d, "id")
        written_at = _field(d, "written_at")
        if raw_id is None and isinstance(written_at, (int, float)):
            raw_id = f"{written_at:.0f}"
        add("daydream", raw_id, _clean(_field(d, "text"), _DAYDREAM_TEXT_CAP))
    return out


async def _read_stakes() -> List[Any]:
    """Her open hopes, worries, tastes and wonders — and only while the
    ledger is on. One she already said is still here; its line carries the
    day (_said_tag). [] on any failure: one unreadable store never costs
    the other two."""
    try:
        from ai import stake_ledger

        if not stake_ledger.ledger_enabled():
            return []
        rows = await stake_ledger.open_stakes(limit=_STAKE_READ_LIMIT)
        return [s for s in rows if getattr(s, "kind", "") in PICKABLE_STAKE_KINDS]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("tonight: stakes unreadable: %s", exc)
        return []


def _read_journal_pages() -> List[Dict[str, str]]:
    """Her last seven journal pages, oldest first; a page's id is its date."""
    try:
        from ai.narrative_self import identity_palli_root

        pages = sorted((identity_palli_root() / "journal").glob("????-??-??.md"))
    except Exception as exc:
        logger.warning("tonight: journal unreadable: %s", exc)
        return []
    out: List[Dict[str, str]] = []
    for page in pages[-_JOURNAL_PAGES:]:
        try:
            out.append({"id": page.stem, "text": page.read_text(encoding="utf-8")})
        except (OSError, UnicodeDecodeError):
            continue    # one unreadable page is a skipped page
    return out


def _read_daydreams() -> List[Any]:
    """Her recent daydreams, newest first. A daydream has no id of its own,
    so the second it was written stands in for one."""
    try:
        from ai import daydream_store

        return list(daydream_store.read_recent(days_back=_DAYDREAM_DAYS,
                                               limit=_DAYDREAM_LIMIT))
    except Exception as exc:
        logger.warning("tonight: daydreams unreadable: %s", exc)
        return []


async def _read_recent_conversation(owner_id: int, now: float) -> str:
    """What the two of them said lately: the day just lived and the hours
    since midnight, as the night's own plain log. "" on any failure."""
    try:
        from ai.night_cycle import _load_day_conversation, _render_day_conversation

        today = datetime.fromtimestamp(float(now))
        rows: list = []
        for day in (today - timedelta(days=1), today):
            rows.extend(await _load_day_conversation(int(owner_id), day.strftime("%Y-%m-%d")))
        return _render_day_conversation(rows, cap=_CONVERSATION_CAP)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("tonight: recent conversation unreadable: %s", exc)
        return ""


def _render(candidates: List[_Candidate], source: str) -> str:
    rows = [c for c in candidates if c.source == source]
    if not rows:
        return _NOTHING
    if source == "journal":
        return "\n\n".join(f"[{c.id}]\n{c.text}".rstrip() for c in rows)
    return "\n".join(
        (f"- [{c.id}] {f'({c.kind}) ' if c.kind else ''}{c.text}".rstrip()
         + (f" ({c.note})" if c.note else ""))
        for c in rows
    )


def _parse_pick(raw: Any) -> Optional[Dict[str, str]]:
    """Her answer → the pick, or None for an honest "nothing tonight".
    Anything outside the contract raises PickRefused with its reason."""
    from core.llm_json import safe_parse_json_object

    obj = safe_parse_json_object(raw if isinstance(raw, str) else "")
    if not isinstance(obj, dict) or "pick" not in obj:
        raise PickRefused("unparseable")
    chosen = obj["pick"]
    if chosen is None:
        return None
    if not isinstance(chosen, dict):
        raise PickRefused("unparseable")
    source = chosen.get("source")
    if not isinstance(source, str) or source.strip().lower() not in SOURCES:
        raise PickRefused("unknown_source")
    raw_id = chosen.get("source_id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)) or not _clean(raw_id, _ID_CAP):
        raise PickRefused("no_id")
    text = chosen.get("text")
    if not isinstance(text, str) or not text.strip():
        # Never str()-coerce her words: {"text": {"a": 1}} would put the
        # literal "{'a': 1}" in her reply prompt as the thing on her mind.
        raise PickRefused("malformed_text")
    why = chosen.get("why")
    return {
        "source": source.strip().lower(),
        "source_id": _clean(raw_id, _ID_CAP),
        "text": _clean(text, TEXT_CAP),
        "why": _clean(why, WHY_CAP) if isinstance(why, str) else "",
    }


# ── the pick ─────────────────────────────────────────────────────────────

async def pick_tonight(
    owner_id: int,
    *,
    llm_call: Callable[[str], Awaitable[str]],
    now: float,
    stakes: Optional[List[Any]] = None,
    journal_pages: Optional[List[Any]] = None,
    daydreams: Optional[List[Any]] = None,
    recent_conversation: Optional[str] = None,
) -> Optional[Pick]:
    """Ask her for tonight's one thing and write the night's file.

    Each of the four inputs is read from its store when it is None. The
    model is asked only when there is something to choose from. Returns the
    pick, or None for a night with none — and that night's file is written
    too, with a null pick. A pick naming something that was not offered is
    refused: a null night, noted in the file, one log line.

    Raises PickRefused when the answer is outside the contract, and lets a
    failed model call through; in both cases nothing is written. The night
    step (run_nightly_pick) is what holds the flag, the one-a-night rule and
    the errors.
    """
    day = day_for(now)
    if stakes is None:
        stakes = await _read_stakes()
    if journal_pages is None:
        journal_pages = _read_journal_pages()
    if daydreams is None:
        daydreams = _read_daydreams()
    offered = _candidates(stakes, journal_pages, daydreams)
    record: Dict[str, Any] = {
        "day": day,
        "picked_at": float(now),
        "owner_id": int(owner_id),
        "offered": {s: sum(1 for c in offered if c.source == s) for s in SOURCES},
        "pick": None,
        "said_at": None,
    }
    if not offered:
        _write_night(day, record)
        return None

    if recent_conversation is None:
        recent_conversation = await _read_recent_conversation(owner_id, now)
    from ai.prompt_loader import prompt_loader

    template = prompt_loader.load_prompt("tonight_pick")
    if not template:
        raise RuntimeError("tonight_pick prompt missing")
    prompt = template.format(
        open_stakes=_render(offered, "stake"),
        journal_pages=_render(offered, "journal"),
        daydreams=_render(offered, "daydream"),
        recent_conversation=str(recent_conversation or "").strip() or _NOTHING,
    )
    raw = await asyncio.wait_for(llm_call(prompt), timeout=_PICK_TIMEOUT_S)
    chosen = _parse_pick(raw)
    if chosen is not None and not any(
        c.source == chosen["source"] and c.id == chosen["source_id"] for c in offered
    ):
        logger.warning(
            "tonight: the pick for %s named %s %r, which was not offered — no pick tonight",
            day, chosen["source"], chosen["source_id"][:40],
        )
        record["refused"] = "not_offered"
        chosen = None
    record["pick"] = chosen
    _write_night(day, record)
    return _pick_from(day, record)


async def run_nightly_pick(
    *, owner_id: int, llm_call: Callable[[str], Awaitable[str]], now: float,
) -> Dict[str, Any]:
    """The night cycle's last step. Never raises (a cancel goes through).

    One pick a night, written once: when the night's file is already there
    — a pick or a null night — nothing is asked and nothing changes, so the
    boot catch-up re-running the night can never replace a pick she may
    already have said."""
    if not tonight_enabled():
        return {"skipped": "flag_off"}
    day = day_for(now)
    path = _path(day)
    if path is not None and path.exists():
        return {"skipped": "already_picked", "day": day}
    result: Dict[str, Any] = {"day": day}
    try:
        pick = await pick_tonight(owner_id, llm_call=llm_call, now=now)
        result["picked"] = f"{pick.source}:{pick.source_id}" if pick else None
        refused = (_read_night(day) or {}).get("refused")
        if refused:
            result["refused"] = str(refused)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # The type name is the fallback: a timeout's str() is "", and an
        # empty error reads as a healthy night on the health line.
        logger.warning("tonight: the pick failed for %s: %s", day, exc or type(exc).__name__)
        result["error"] = (" ".join(str(exc).split()) or type(exc).__name__)[:160]
    essential_logger.info(
        "tonight: %s — picked=%s%s%s", day, result.get("picked"),
        f" refused={result['refused']}" if result.get("refused") else "",
        f" error={result['error']}" if result.get("error") else "")
    return result


async def tonight_pick_llm_call(prompt: str) -> str:
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model

    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        # The budget is room to think as well as to answer (the stake
        # review's number): this lane thinks, and the answer is short.
        model=get_task_model("appraisal"), max_tokens=8000, temperature=0.6,
        log_context="night_cycle_tonight_pick",
    )


# ── the record that she said it ──────────────────────────────────────────

async def said_check(
    pick: Optional[Pick], text: str, *, llm_call: Callable[[str], Awaitable[str]],
) -> bool:
    """Did she bring the pick up, as her own thing, in ``text``? One small
    model call. True only for an answer that is the JSON object and nothing
    else (a code fence is allowed) with ``"said": true``; anything else —
    prose around it, a string "true", an empty answer, an error — is a no,
    and the band stays. Never raises (a cancel goes through)."""
    try:
        if pick is None or pick.said_at is not None or not str(pick.text or "").strip():
            return False
        said_text = str(text or "").strip()
        if not said_text:
            return False
        from ai.prompt_loader import prompt_loader
        from core.llm_json import strip_markdown_fences

        template = prompt_loader.load_prompt("tonight_said")
        if not template:
            logger.warning("tonight: the said-check prompt is missing — the pick stays unsaid")
            return False
        raw = await llm_call(template.format(pick_text=pick.text,
                                             reply_text=said_text[:_REPLY_CAP]))
        try:
            answer = json.loads(strip_markdown_fences(raw if isinstance(raw, str) else ""))
        except ValueError:
            return False
        return isinstance(answer, dict) and answer.get("said") is True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("tonight: the said-check failed (%s) — the pick stays unsaid",
                       exc or type(exc).__name__)
        return False


async def tonight_said_llm_call(prompt: str) -> str:
    """The said-check's call for the moment's leg; reflect makes the same
    call through its own client. One lane for both."""
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model

    return await llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=get_task_model("response"), temperature=0.1, max_tokens=300,
        log_context="reflect_turn_tonight_said",
    )


async def stamp_said(pick: Pick, *, at: float, brain_client: Any = None) -> bool:
    """The whole record of a yes, in this order: the pick file; then, for a
    stake, the ledger's ``voiced_at``; then its ``last_surfaced_at``. For a
    journal page or a daydream the pick file is the record.

    False when the pick was already said (or its file is gone): nothing
    else is stamped then. A ledger stamp that fails is logged and never
    takes the file's stamp back. Never raises (a cancel goes through)."""
    if not mark_said(pick.day, at=at):
        return False
    if pick.source == "stake":
        try:
            from ai import stake_ledger

            stake_id = int(pick.source_id)
            if not await stake_ledger.mark_stake_said(
                stake_id, brain_client=brain_client, now=at
            ):
                logger.info("tonight: stake %s took no stamp (not open, or said before)",
                            stake_id)
            await stake_ledger.record_surfaced(stake_id, brain_client=brain_client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("tonight: the ledger stamp failed for stake %r (%s)",
                           pick.source_id, exc or type(exc).__name__)
    essential_logger.info("tonight: %s — said (%s:%s)", pick.day, pick.source, pick.source_id)
    return True

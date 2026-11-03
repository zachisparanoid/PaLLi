"""The second look at the record, inside a reply (R15, memory that reaches).

When ``search_what_we_said`` comes back thin (``memory_reach_search.is_thin``:
zero or weak hits, a resource fact), or PaLLi asks for a wider look, she
takes a second, harder one (``second_look``). A widen call plans a wider
search (``prompts/memory_reach_widen.txt``): other wordings, the dates,
which lines to read around (``neighbours``) and whose thread to follow
(``thread``). The plan runs, the lines merge by id, and while what was
gathered is still thin a second round plans again. A keep call then sifts
what was gathered (``prompts/memory_reach_keep.txt``). Both are her private
looks, in her own voice, and both run while the owner waits on the reply,
so their lanes never think and keep the 30 s request ceiling
(ai/llm_openai_compatible.py; tests/ai/test_request_timeout_lanes.py's
HUMAN_FACING table).

What to look for and what to keep is the model's; the code decides
resources only. At most ``MAX_ROUNDS`` widen rounds, ``MAX_CALLS`` model
calls in all and ``BUDGET_SECONDS`` of wall time: when the wall comes, what
was gathered is the answer, never an exception into the turn. At most
``KEEP_CHARS`` of text comes back, and each page carries at most
``PAGE_CHARS`` of the gathered lines. The scope is the caller's: the one
``Scope`` reaches every search and every read around a line, and is never
widened. The plan's ids are the model's, so only whole ids of lines shown
on its page are read around, and the scope still holds for those. One log
line per look, counts only (the replay, evals/memory_reach_replay, reads
it): ``memory reach: second look rounds=… calls=… seconds=… thin=…
timed_out=…``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from ai import memory_reach_search as mrs
from ai.memory_reach_index import Embed
from ai.memory_reach_search import Hit, Scope

logger = logging.getLogger(__name__)

WIDEN_LANE = "memory_reach_widen"
KEEP_LANE = "memory_reach_keep"

# Each call's fixed shape: one user message, no tools, no thinking (the lane),
# room for the plan's JSON or the kept lines. Spelled as literals so the lane
# scanner sees each lane where its call is made.
CALL_ARGS = {
    WIDEN_LANE: {"temperature": 0.2, "max_tokens": 600, "log_context": "memory_reach_widen"},
    KEEP_LANE: {"temperature": 0.2, "max_tokens": 900, "log_context": "memory_reach_keep"},
}


def widen_prompt(question: str, found_so_far: str) -> str:
    """The widen look's page: the question, then the dated lines found so far
    (``render_lines(..., ids=True)``, so the plan can name lines by id).
    Raises when the template is missing: the caller turns that into no
    widening, never an error in the turn."""
    from ai.prompt_loader import prompt_loader

    # a literal name: the prompt canary (tests/ai/test_prompt_loader.py) walks these calls
    template = prompt_loader.load_prompt("memory_reach_widen")
    if not template:
        raise RuntimeError("prompts/memory_reach_widen.txt is missing or empty")
    return template.format(
        question=str(question or "").strip(),
        found_so_far=str(found_so_far or "").strip(),
    )


def keep_prompt(question: str, gathered: str, max_chars: int) -> str:
    """The keep look's page: the question, the dated lines the search
    gathered, and the length the kept lines must stay under. Raises when the
    template is missing, as ``widen_prompt`` does."""
    from ai.prompt_loader import prompt_loader

    template = prompt_loader.load_prompt("memory_reach_keep")   # literal, as above
    if not template:
        raise RuntimeError("prompts/memory_reach_keep.txt is missing or empty")
    return template.format(
        question=str(question or "").strip(),
        gathered=str(gathered or "").strip(),
        max_chars=int(max_chars),
    )


# --- the caps (resource facts) -------------------------------------------------

MAX_ROUNDS = 2          # widen rounds
MAX_CALLS = 3           # model calls in all: the widens and the keep
BUDGET_SECONDS = 10.0   # the whole look's wall time
KEEP_CHARS = 1200       # the most text the look gives back
SHOWN_LINES = 12        # with nothing kept, the first gathered lines shown, at most
PAGE_CHARS = 12_000     # the most of the gathered lines one page carries (~3k tokens)
WIDEN_DAYS = 30         # the first search's dates, widened each way, when the plan names none
_MAX_REPHRASINGS = 2
_MAX_OPEN_UP = 3
_MAX_FOLLOW = 2
_REPHRASING_CHARS = 200
_RAW_CHARS = 8_000      # the most of a widen answer read for its JSON
_MAX_DECODES = 50       # the most places in it a JSON object is tried from
_MAX_ID = 2 ** 63 - 1   # SQLite's largest rowid
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_LINE_ID = re.compile(r"#?([0-9]{1,19})")
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

LookLLM = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class LookResult:
    """What the second look gives back. ``text``: the kept lines (the keep
    call's answer, at most ``KEEP_CHARS``), or, when nothing was kept (no
    keep call, an empty answer, the wall), the first ``SHOWN_LINES``
    gathered lines as dated lines in ``KEEP_CHARS``; "" when nothing was
    gathered. ``hits``: every line gathered, the first lines included,
    merged by id. ``rounds``: widen rounds; ``calls``: model calls made
    (one the wall cut short counts); ``seconds``: the look's own time;
    ``timed_out``: the wall came first; ``thin``: ``is_thin`` of ``hits``."""

    text: str
    hits: list[Hit]
    rounds: int
    calls: int
    seconds: float
    timed_out: bool
    thin: bool


# --- the lanes' callable -------------------------------------------------------

def build_llm(lane: str) -> LookLLM:
    """One lane's callable (page -> answer): one user message, no tools, no
    thinking (the lane), ``CALL_ARGS[lane]``, on the quick reads' model
    (``CADENCE_MODEL``, as core/agent/first_look.build_llm resolves it; unset
    = the provider's default). Only this module's two lanes."""
    if lane not in CALL_ARGS:
        raise ValueError(f"the second look has no lane {lane!r}")
    from ai.llm_client import llm as llm_client_singleton
    from core.config import config

    model = (getattr(config, "CADENCE_MODEL", "")
             or os.environ.get("PALLI_CADENCE_MODEL", "")
             or os.environ.get("CADENCE_MODEL", ""))
    args = CALL_ARGS[lane]

    async def look_llm(page: str) -> str:
        kwargs: dict[str, Any] = {"messages": [{"role": "user", "content": page}]}
        if model:
            kwargs["model"] = model
        result = await llm_client_singleton.chat_completion(**kwargs, **args)
        return result or ""

    return look_llm


# --- the plan --------------------------------------------------------------------

def _json_object(raw: Any) -> dict | None:
    """The answer's JSON object: the first ``{`` to the last ``}`` (fences
    and prose around it are fine), else the first ``{`` an object decodes
    from (prose with braces of its own after it); None when there is none."""
    if not isinstance(raw, str):
        return None
    text = raw[:_RAW_CHARS]
    match = _OBJECT.search(text)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, RecursionError):
        data = None
    if isinstance(data, dict):
        return data
    decoder = json.JSONDecoder()
    for n, brace in enumerate(re.finditer(r"\{", text)):
        if n >= _MAX_DECODES:
            break
        try:
            data, _ = decoder.raw_decode(text, brace.start())
        except (ValueError, RecursionError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _plan_day(value: Any) -> str | None:
    """A real calendar day written ``YYYY-MM-DD``; None for anything else."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _DAY.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _rephrasings(value: Any) -> list[str]:
    items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    picked: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())[:_REPHRASING_CHARS].strip()
        if text and text.casefold() not in {p.casefold() for p in picked}:
            picked.append(text)
            if len(picked) >= _MAX_REPHRASINGS:
                break
    return picked


def _line_id(item: Any) -> int | None:
    """A line id as the page wrote it ("#41"), as its digits ("41") or as a
    number (41); None for anything else (a bool, a fraction, a word, an id
    no record can hold)."""
    if isinstance(item, bool):
        return None
    if isinstance(item, int):
        rid = item
    elif isinstance(item, str):
        match = _LINE_ID.fullmatch(item.strip())
        if match is None:
            return None
        rid = int(match.group(1))
    else:
        return None
    return rid if 0 < rid <= _MAX_ID else None


def _line_ids(value: Any, cap: int) -> list[int]:
    picked: list[int] = []
    for item in value if isinstance(value, list) else [value]:
        rid = _line_id(item)
        if rid is not None and rid not in picked:
            picked.append(rid)
            if len(picked) >= cap:
                break
    return picked


def parse_plan(raw: Any) -> dict:
    """The widen call's plan: ``rephrasings`` (at most 2 distinct wordings),
    ``since``/``until`` (a real day ``YYYY-MM-DD`` or None: a bad date is
    dropped, never passed on), ``open_up`` (at most 3 line ids) and
    ``follow`` (at most 2). Fences and prose around the object are fine;
    {} when there is no JSON object."""
    data = _json_object(raw)
    if data is None:
        return {}
    return {
        "rephrasings": _rephrasings(data.get("rephrasings")),
        "since": _plan_day(data.get("since")),
        "until": _plan_day(data.get("until")),
        "open_up": _line_ids(data.get("open_up"), _MAX_OPEN_UP),
        "follow": _line_ids(data.get("follow"), _MAX_FOLLOW),
    }


# --- the dates ---------------------------------------------------------------------

def _as_day(value: Any) -> str | None:
    """The first search's bound as ``YYYY-MM-DD``, read by the search's own
    parser (``memory_reach_search._day_bound``), so whatever the search took
    as a day the look widens; None for none, or for a bound no search could
    read. (The plan's dates are the model's and stay strict: ``_plan_day``.)"""
    try:
        return mrs._day_bound(value, "since/until")
    except (ValueError, OverflowError, OSError):
        return None


def _shifted(day: str | None, days: int) -> str | None:
    if day is None:
        return None
    try:
        return (date.fromisoformat(day) + timedelta(days=days)).isoformat()
    except OverflowError:
        return None                           # past the calendar's end: no bound


# --- the pages and the text -----------------------------------------------------------

def _cut(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _clip(text: str, cap: int) -> str:
    """At most ``cap`` characters: whole lines while they fit, else the
    first line cut."""
    if len(text) <= cap:
        return text
    end = text.rfind("\n", 0, cap + 1)
    return text[:end].rstrip() if end > 0 else _cut(text, cap)


def _page(hits: list[Hit], cap: int, *, ids: bool = False) -> tuple[str, list[Hit]]:
    """The most of ``hits``, in order, whose dated lines fit in ``cap``
    characters, and those hits; a first line longer than ``cap`` is cut."""
    if not hits:
        return "", []
    text = mrs.render_lines(hits, ids=ids)
    if len(text) <= cap:
        return text, list(hits)
    fits, over = 0, len(hits)                 # render_lines grows with each hit
    while over - fits > 1:
        mid = (fits + over) // 2
        if len(mrs.render_lines(hits[:mid], ids=ids)) <= cap:
            fits = mid
        else:
            over = mid
    if fits == 0:
        return _cut(mrs.render_lines(hits[:1], ids=ids), cap), hits[:1]
    return mrs.render_lines(hits[:fits], ids=ids), hits[:fits]


def _unfenced(text: str) -> str:
    body = str(text or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
    return body.strip()


def _said(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


# --- the look ---------------------------------------------------------------------------

class _Look:
    """The look so far, kept whole when the wall cuts it short."""

    def __init__(self, first: list[Hit]) -> None:
        self.gathered: list[Hit] = []
        self._at: dict[int, int] = {}
        self.rounds = 0
        self.calls = 0
        self.kept = ""
        self.searched: set[tuple[str, str | None, str | None]] = set()
        self.opened: set[int] = set()
        self.followed: set[int] = set()
        self.merge(first)

    def merge(self, hits: list[Hit]) -> None:
        """Each line once, where it was first gathered; a stronger reading
        of the same line (exact, or a higher score) takes its place."""
        for hit in hits:
            at = self._at.get(hit.id)
            if at is None:
                self._at[hit.id] = len(self.gathered)
                self.gathered.append(hit)
            elif (hit.exact, hit.score) > (self.gathered[at].exact, self.gathered[at].score):
                self.gathered[at] = hit


def _time_left(remaining: Callable[[], float]) -> float:
    """The seconds the look has left; none is ``TimeoutError``, before a
    call is counted: the look is over."""
    left = remaining()
    if left <= 0:
        raise TimeoutError
    return left


async def _ask(llm: LookLLM | None, lane: str, page: str, left: float) -> str:
    """One model call, given only the time that is ``left``; past it,
    ``TimeoutError``. A failed call is an empty answer."""
    call = llm if llm is not None else build_llm(lane)
    try:
        answer = await asyncio.wait_for(call(page), timeout=left)
    except TimeoutError:
        raise
    except Exception as exc:
        logger.info("memory_reach_look: the %s call failed (%s)", lane, type(exc).__name__)
        return ""
    return answer if isinstance(answer, str) else ""


def _around(path: Path | None, scope: Scope, opens: list[int], follows: list[int]) -> list[Hit]:
    """The lines around each ``opens`` id and after each ``follows`` id, on
    one read-only connection opened and closed in this thread."""
    conn = mrs.open_record(path)
    if conn is None:
        return []
    try:
        found: list[Hit] = []
        for hit_id in opens:
            found.extend(mrs.neighbours(conn, hit_id, scope=scope))
        for hit_id in follows:
            found.extend(mrs.thread(conn, hit_id, scope=scope))
        return found
    finally:
        conn.close()


async def _widen(look: _Look, question: str, plan: dict, shown: set[int], *, scope: Scope,
                 embed: Embed | None, path: Path | None,
                 first_days: tuple[str | None, str | None]) -> None:
    """One round's plan, run: the question and each rephrasing searched
    over the plan's dates (else the first search's, widened), none twice;
    the lines around each shown ``open_up`` id and after each shown
    ``follow`` id, none twice. A step that fails is skipped."""
    since = plan.get("since") or _shifted(first_days[0], -WIDEN_DAYS)
    until = plan.get("until") or _shifted(first_days[1], WIDEN_DAYS)
    if since and until and since > until:
        since, until = until, since
    for query in (question, *plan.get("rephrasings", ())):
        key = (_said(query), since, until)
        if not key[0] or key in look.searched:
            continue
        look.searched.add(key)
        try:
            look.merge(await mrs.search(query, scope=scope, since=since, until=until,
                                        embed=embed, path=path))
        except Exception as exc:
            logger.info("memory_reach_look: a wider search failed (%s)", type(exc).__name__)
    opens = [i for i in plan.get("open_up", ()) if i in shown and i not in look.opened]
    follows = [i for i in plan.get("follow", ()) if i in shown and i not in look.followed]
    look.opened.update(opens)
    look.followed.update(follows)
    if not (opens or follows):
        return
    try:
        look.merge(await asyncio.to_thread(_around, path, scope, opens, follows))
    except Exception as exc:
        logger.info("memory_reach_look: reading around a line failed (%s)", type(exc).__name__)


async def _run(look: _Look, question: str, *, scope: Scope, llm: LookLLM | None,
               embed: Embed | None, path: Path | None,
               first_days: tuple[str | None, str | None], look_harder: bool,
               remaining: Callable[[], float]) -> None:
    while (look.rounds < MAX_ROUNDS and look.calls < MAX_CALLS - 1
           and (mrs.is_thin(look.gathered) or (look_harder and look.rounds == 0))):
        found_so_far, shown = _page(look.gathered, PAGE_CHARS, ids=True)
        try:
            page = widen_prompt(question, found_so_far)
        except Exception as exc:
            logger.info("memory_reach_look: no widening (%s)", type(exc).__name__)
            break
        left = _time_left(remaining)
        look.calls += 1
        look.rounds += 1
        plan = parse_plan(await _ask(llm, WIDEN_LANE, page, left))
        await _widen(look, question, plan, {h.id for h in shown}, scope=scope, embed=embed,
                     path=path, first_days=first_days)
    if not look.gathered or look.calls >= MAX_CALLS:
        return
    gathered, _ = _page(look.gathered, PAGE_CHARS)
    try:
        page = keep_prompt(question, gathered, KEEP_CHARS)
    except Exception as exc:
        logger.info("memory_reach_look: no sifting (%s)", type(exc).__name__)
        return
    left = _time_left(remaining)
    look.calls += 1
    look.kept = _clip(_unfenced(await _ask(llm, KEEP_LANE, page, left)), KEEP_CHARS)


async def second_look(question: str, first: list[Hit], *, scope: Scope,
                      llm: LookLLM | None = None, budget_s: float = BUDGET_SECONDS,
                      embed: Embed | None = None, path: Path | None = None,
                      now: Callable[[], float] = time.monotonic, since: Any = None,
                      until: Any = None, look_harder: bool = False) -> LookResult:
    """The second, harder look for ``question``, from the quick look's lines
    (``first``), inside ``scope`` (never widened: a first line outside it is
    dropped before anything, by ``Scope.reaches``).

    While the gathered lines are thin (``is_thin``), and at most
    ``MAX_ROUNDS`` times, a widen call plans and the plan runs: the question
    and the plan's rephrasings searched over the plan's dates or the first
    search's (``since``/``until``) widened by ``WIDEN_DAYS`` each way, the
    lines around the plan's ``open_up`` ids, the thread after its ``follow``
    ids. ``look_harder`` (PaLLi asked for the wider look) widens once even
    when the first lines are not thin. Then, with anything gathered, one
    keep call sifts it. At most ``MAX_CALLS`` model calls (``llm``: one
    callable for both pages, else each lane's ``build_llm``), each given
    only the time that remains of ``budget_s``; the whole look is cut at
    ``budget_s`` and keeps what it had. ``embed``/``path`` go to the
    record's reads; ``now`` is the look's clock. Never raises into the
    turn, except a ``TypeError`` for a scope that is not a ``Scope``."""
    if not isinstance(scope, Scope):
        raise TypeError(f"a second look needs a Scope, not {scope!r}")
    start = now()
    inside = [hit for hit in first or [] if scope.reaches(hit.room)]
    if len(inside) < len(first or []):
        # A caller's bug, never the look's: lines outside the scope don't go on.
        logger.warning("memory_reach_look: %d first line(s) outside the look's scope dropped",
                       len(first) - len(inside))
    look = _Look(inside)
    question = str(question or "").strip()
    first_days = (_as_day(since), _as_day(until))
    look.searched.add((_said(question), *first_days))     # the quick look's own search
    timed_out = False
    try:
        await asyncio.wait_for(
            _run(look, question, scope=scope, llm=llm, embed=embed, path=path,
                 first_days=first_days, look_harder=bool(look_harder),
                 remaining=lambda: budget_s - (now() - start)),
            timeout=budget_s)
    except TimeoutError:
        timed_out = True
    except Exception as exc:
        logger.warning("memory_reach_look: the look stopped early (%s)", type(exc).__name__)
    seconds = now() - start
    text = look.kept or _page(look.gathered[:SHOWN_LINES], KEEP_CHARS)[0]
    thin = mrs.is_thin(look.gathered)
    logger.info("memory reach: second look rounds=%d calls=%d seconds=%.2f thin=%s timed_out=%s",
                look.rounds, look.calls, seconds, thin, timed_out)
    return LookResult(text=text, hits=list(look.gathered), rounds=look.rounds, calls=look.calls,
                      seconds=seconds, timed_out=timed_out, thin=thin)

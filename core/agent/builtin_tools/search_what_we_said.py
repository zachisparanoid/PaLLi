"""`search_what_we_said`: PaLLi's search of the record of everything said
(R15, memory that reaches; plan docs/2026-10-13-memory-that-reaches-plan.md, T8).

On the owner's turns only (``owner_only``, and the guard in ``execute``),
and registered only while companion.memory_reach is on (``enabled``, read
through ``ai.memory_reach_index`` at call time, so the suite's pin holds).

One call: the record catches up (``memory_reach_index.catch_up``, 1.5 s at
most), the quick look searches it (``memory_reach_search.search``) inside a
scope that comes from the turn, never from the model: every chat, or with
``room="here"`` the turn's own room, read by the search module's own room
rule (``_room_of``, the one ``scope_for_turn`` uses); no real room is
``NO_ROOM``, where nothing is. A thin quick look (``is_thin``, a resource
fact), or ``look_harder``, takes the second look
(``memory_reach_look.second_look``). Every sentence PaLLi reads is one of
Fable's (the brief's items 1–17, with 7a, 9a, 14a and 16a: Draft 1, the
owner's yes of 10-13), verbatim; the code fills only counts, dates, where,
the seconds and a bad date's own text. The result is at most
``RESULT_CHARS``.

One log line per call, counts only, never the words:
``memory reach: query_chars=… scope=all|room hits=… thin=… rounds=… calls=…
seconds=…`` (evals/memory_reach_replay reads it by ``query_chars=``).

What a search gave the reply (its dated lines, its query and those counts)
goes onto the turn's context (``ToolContext.record_lines``,
``record_queries``, ``record_looks``) once its result stands, and the runner
writes it to the owner's record of what each reply used
(core/reply_sources.py; T13/W3).
"""
from __future__ import annotations

import logging
import re
import time
from types import SimpleNamespace
from typing import Any

from ai import memory_reach_index as mri
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext

logger = logging.getLogger(__name__)

RESULT_CHARS = 4000       # the most a result carries
CATCH_UP_S = 1.5          # the record's catch-up before a search
QUERY_ECHO_CHARS = 200    # the most of the query a "nothing found" echoes back

# Fable's words (the brief's "Words for the owner's yes"), verbatim.
_DESCRIPTION = (  # items 1–6
    "Search the record of everything we've said: every message since the start, both sides, "
    "in every chat, by exact words or by meaning. "
    "Reach for it when a question turns on what was actually said: what they told you, what "
    "you sent, when, in which words. "
    "Give the words you expect to find, or a phrase in quotes for an exact match; add dates "
    "when you know roughly when. "
    "The quick look returns the lines, dated, with who said them and where. "
    "If the lines don't settle it, call again with look_harder=true: a wider look that reads "
    "around each hit, tries other wordings and widens the dates, about ten seconds. "
    "When nothing comes back you haven't found it; that isn't proof it wasn't said."
)
_QUERY = "The words you expect to find, or a phrase in quotes for the exact words."    # item 7
_EXACT = "Only lines with this exact phrase (default false)."                          # item 8
_DATE = "A date, YYYY-MM-DD, to narrow when (optional)."                               # item 9
_WHO = 'Whose lines: "them", "me", a name, or "anyone" (default anyone).'              # item 10
_ROOM = '"here" for this chat; leave it out for every chat.'                           # item 11
_LOOK_HARDER = ("A wider look: nearby lines, other wordings, wider dates; about ten seconds "
                "(default false).")                                                    # item 12
_FOUND = "Found {n} lines ({where}, from {since} to {until}{words_only}):"            # item 13
_FOUND_ONE = "Found 1 line ({where}, from {since} to {until}{words_only}):"          # item 13, one
_WIDER = ("(The quick look was thin, so this is the wider look; it took {seconds} "
          "seconds.)")                                                                # item 14
_WIDER_ASKED = ("(This is the wider look you asked for; it took {seconds} "
                "seconds.)")                                                          # item 14a
_QUICK = ("(This was a quick look. If these lines don't settle it, call again with "
          "look_harder=true for a wider look: nearby lines, other wordings, wider dates; "
          "about ten seconds.)")                                                       # item 15
_NOTHING = ('Nothing found for "{query}": searched {where}, from {since} to {until}'
            "{words_only}{wider}. Not finding it doesn't mean it wasn't said.")       # item 16
_NOTHING_WIDER = ", and the wider look too"                                            # item 16
_WORDS_ONLY = ", by words only"                                                        # 13, 16
_HERE_NO_ROOM = ('"here" means a chat room, and this isn\'t one, so nothing was searched. '
            "Leave room out to search every chat.")                                    # item 16a
_RAN_OUT = "(The wider look ran out of its ten seconds; this is what it had gathered.)"  # 17
# A bad input is an error, so no search runs (items 7a and 9a).
_BAD_QUERY = "No search ran: the query was empty. Give the words you expect to find."  # 7a
_BAD_DATE = ('No search ran: "{value}" isn\'t a date in the form YYYY-MM-DD. '
             "Fix it or leave it out.")                                                # 9a
BAD_VALUE_CHARS = 40      # the most of a bad date a 9a error echoes back

# The fills for item 13's and 16's {where}, {since} and {until} (item 1's
# "since the start", item 11's "this chat" and "every chat"). "this chat" is
# filled only where the turn has a real room (16a answers where it has none).
_EVERY_CHAT = "every chat"
_THIS_CHAT = "this chat"
_NO_SINCE = "the start"
_NO_UNTIL = "now"


def enabled() -> bool:
    """companion.memory_reach, read through ``ai.memory_reach_index`` at
    call time (the suite's pin patches it there); fail-closed. The
    registry's gate reads it here, so the registry imports one module for
    the tool and its switch (R15, T7/T8: the coupling budget)."""
    try:
        return bool(mri.enabled())
    except Exception:
        return False


def _flag(value: Any) -> bool:
    """A yes/no input, given as a boolean or as a word ("true")."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _cut(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _turn_view(ctx: ToolContext) -> SimpleNamespace:
    """The turn as the search module's room rule reads it. The runner builds
    the context from the turn's own ``owner_id``, ``surface`` and
    ``metadata.channel_id`` (core/agent/runner.py), so these are the turn's."""
    return SimpleNamespace(surface=getattr(ctx, "surface", ""),
                           owner_id=getattr(ctx, "turn_owner_id", None),
                           metadata=SimpleNamespace(channel_id=getattr(ctx, "channel_id", 0)))


class SearchWhatWeSaidTool:
    name = "search_what_we_said"
    description = _DESCRIPTION
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": _QUERY},
            "exact": {"type": "boolean", "description": _EXACT},
            "since": {"type": "string", "description": _DATE},
            "until": {"type": "string", "description": _DATE},
            "who": {"type": "string", "description": _WHO},
            "room": {"type": "string", "description": _ROOM},
            "look_harder": {"type": "boolean", "description": _LOOK_HARDER},
        },
        "required": ["query"],
    }
    requires_approval = False
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        if not bool(getattr(ctx, "is_owner", False)):
            # The registry refuses a non-owner first (owner_only); this is the
            # same refusal, in its words, for a call that comes around it.
            return ToolFailure(f"Tool {self.name!r} is owner-only; caller is not the owner.")
        started = time.monotonic()
        query = str(input.get("query") or "").strip()
        here = str(input.get("room") or "").strip().lower() == "here"
        counts = {"hits": 0, "thin": False, "rounds": 0, "calls": 0,
                  "searched": False, "shown": ""}
        try:
            result = _capped(await self._answer(input, ctx, query, here, counts))
        finally:
            seconds = time.monotonic() - started
            logger.info("memory reach: query_chars=%d scope=%s hits=%d thin=%s rounds=%d "
                        "calls=%d seconds=%.2f", len(query), "room" if here else "all",
                        counts["hits"], counts["thin"], counts["rounds"], counts["calls"],
                        seconds)
        if counts["searched"] and not isinstance(result, ToolFailure):
            _hand_over(ctx, query, counts, seconds)
        return result

    async def _answer(self, input: dict[str, Any], ctx: ToolContext, query: str, here: bool,
                      counts: dict) -> str | ToolFailure:
        from ai import memory_reach_look as mrl
        from ai import memory_reach_search as mrs

        if not query:
            return ToolFailure(_BAD_QUERY)
        bounds = []
        for label in ("since", "until"):
            try:
                bounds.append(mrs._day_bound(input.get(label), label))
            except (ValueError, TypeError, OverflowError):
                return ToolFailure(_BAD_DATE.format(value=_bad_value(input.get(label))))
        since, until = bounds
        # The scope is the turn's, never the model's: every chat on the
        # owner's turn, or this one room by the search module's own rule.
        scope = mrs.Scope(mrs._room_of(_turn_view(ctx))) if here else mrs.Scope(None)
        if scope.room == mrs.NO_ROOM:
            return _HERE_NO_ROOM                                 # "here", and no chat room
        fills = {"query": _cut(query, QUERY_ECHO_CHARS),
                 "where": _THIS_CHAT if here else _EVERY_CHAT,
                 "since": since or _NO_SINCE, "until": until or _NO_UNTIL, "words_only": ""}
        try:
            await mri.catch_up(budget_s=CATCH_UP_S)
        except Exception as exc:                            # the search goes on regardless
            logger.warning("memory reach: the catch-up failed (%s)", type(exc).__name__)
        look_harder = _flag(input.get("look_harder"))
        exact = _flag(input.get("exact"))
        hits = await mrs.search(query, scope=scope, exact=exact, since=since, until=until,
                                who=input.get("who"))
        counts["searched"] = True
        counts["hits"], counts["thin"] = len(hits), mrs.is_thin(hits)
        # ", by words only" describes the quick look: an exact-phrase search,
        # or one the search says ran without its meaning leg. When neither is
        # known, nothing is added and the line is still true.
        if exact or getattr(hits, "words_only", False):
            fills["words_only"] = _WORDS_ONLY
        if not (look_harder or counts["thin"]):
            return _quick(hits, fills, counts)
        # The harder look searches every speaker in the scope, and by meaning
        # as well as words, on purpose: the first search's ``who`` and
        # ``exact`` do not carry into it (the line may be someone else's, or
        # put another way). The dates do: it widens them.
        look = await mrl.second_look(query, hits, scope=scope, since=since, until=until,
                                     look_harder=look_harder)
        counts["rounds"], counts["calls"] = look.rounds, look.calls
        # Its own seconds only: the rounds are in the log line, not in the
        # words (nothing PaLLi does changes with them).
        head = (_RAN_OUT if look.timed_out
                else (_WIDER_ASKED if look_harder else _WIDER).format(
                    seconds=f"{look.seconds:.1f}"))
        if not look.text.strip():
            nothing = _NOTHING.format(**fills, wider=_NOTHING_WIDER)
            return f"{_RAN_OUT}\n{nothing}" if look.timed_out else nothing
        counts["shown"] = look.text
        return f"{head}\n{look.text}"


def _bad_value(value: Any) -> str:
    """A bad date as the caller gave it, on one line and at most
    ``BAD_VALUE_CHARS`` characters, for item 9a."""
    return _cut(" ".join(str(value).split()), BAD_VALUE_CHARS)


def _quick(hits: list, fills: dict, counts: dict) -> str:
    """Item 13 ("Found 1 line" for one), the quick look's dated lines (the
    best that fit in ``RESULT_CHARS``, whole: the second look's own
    page-fitter), and item 15."""
    from ai import memory_reach_look as mrl

    head_room = len(_FOUND.format(n=len(hits), **fills)) + len(_QUICK) + 3
    body, shown = mrl._page(hits, RESULT_CHARS - head_room)
    found = (_FOUND_ONE if len(shown) == 1 else _FOUND).format(n=len(shown), **fills)
    counts["shown"] = body
    return f"{found}\n{body}\n\n{_QUICK}"


# A room's heading in the rendered lines (``memory_reach_search.render_lines``).
_ROOM_HEADING = re.compile(r"^In .+:$")


def _hand_over(ctx: ToolContext, query: str, counts: dict, seconds: float) -> None:
    """What this search gave the reply, onto the turn's context for the
    runner's line of what the reply drew on (R15, T13/W3; core/reply_sources.py):
    the dated lines the result carried (room headings and blank lines left
    out), the query, and the counts the log line carries. Called only once
    a search ran and its result stands (the ``deliveries`` contract)."""
    lines = getattr(ctx, "record_lines", None)
    if isinstance(lines, list):
        lines.extend(line for line in (raw.strip() for raw in str(counts["shown"]).splitlines())
                     if line and not _ROOM_HEADING.match(line))
    queries = getattr(ctx, "record_queries", None)
    if isinstance(queries, list):
        queries.append(query)
    looks = getattr(ctx, "record_looks", None)
    if isinstance(looks, list):
        looks.append({"hits": counts["hits"], "thin": bool(counts["thin"]),
                      "rounds": counts["rounds"], "calls": counts["calls"],
                      "seconds": round(seconds, 2)})


def _capped(result: str | ToolFailure) -> str | ToolFailure:
    if isinstance(result, ToolFailure):
        return result
    return _cut(result, RESULT_CHARS)

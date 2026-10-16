"""introspect — PaLLi's first inward-pointing tool (2026-07-13).

On-demand search over her REAL inner record: private thoughts (inner
monologue + digests via the FTS index), night reflections, the stake
ledger including resolved/faded stakes, her past reads of the owner
(emotional timeline), memory notes, and the threads she's been meaning
to come back to. Returns dated, capped, source-labeled evidence to speak
FROM — including an honest, checkable "nothing there".

Honesty by construction:
- Read-only; zero LLM calls; every source is best-effort (recall's
  degrade pattern) with ERROR kept distinct from ABSENCE — a failed
  store renders "couldn't be checked", never a false "nothing there",
  and any error suppresses the strong global-absence claim.
- Restraint budgets live in code (per-source caps, snippet clips, a
  total-size guard), not in prompt hopes.
- Fourth wall: labels are lived-experience language ("your private
  thoughts"), never table/SQL vocabulary, so her paraphrase inherits
  testimony voice.
- Sources whose faculty the owner has switched OFF are omitted entirely
  (config intent respected), not reported as errors.
- owner_only: the record contains her reads of the owner's emotional
  life; it is never queryable from a non-owner turn. Registration is
  gated on companion.introspection (default OFF) so flag-off builds are
  byte-identical.

Spec: docs/superpowers/specs/2026-07-13-introspection-tool-v1-design.md
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.agent.tools import ToolContext

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

# Restraint budgets — design law 4: enforced here, never merely hoped.
_MAX_THOUGHTS = 8
_MAX_REFLECTIONS = 3
_MAX_STAKES = 8
_MAX_TIMELINE = 5
_MAX_MEMORY = 4
_MAX_THREADS = 5
_MAX_NOTES = 6
_MAX_QUESTS = 20
_MAX_WORKBENCH = 12
_MAX_DISCLOSURES = 8
_MAX_CALLBACKS = 12
_MAX_QUIET = 8
_SNIPPET_CHARS = 300
_REFLECTION_CHARS = 400
_QUERY_DAYS_DEFAULT = 30
_BROWSE_DAYS_DEFAULT = 3
_MAX_DAYS = 365
_MEMORY_TIMEOUT_S = 2.0
_MAX_RESULT_CHARS = 6000

# Exact mode (Wave 1a, 2026-07-15): verbatim reach over the full record —
# conversations + pre-aging originals. Bigger budget BECAUSE the user asked
# for exactness; still code-enforced. Rides companion.introspection (the
# tool's own flag), never companion.consolidation: the aged data outlives
# any flag, so the verbatim door must survive a consolidation rollback.
_MAX_EXACT_ROWS = 6
_EXACT_SNIPPET = 400
_MAX_SUPERSEDED = 4
_MAX_RESULT_CHARS_EXACT = 8000
_EXACT_DAYS_DEFAULT = 365

# Passions sources (organ 3, 2026-07-20): her interests file and journal
# pages, behind companion.passions — appended to the gathers ONLY when the
# flag is on (execute()'s gate), so flag-off is byte-identical to before
# this pair existed. On but empty/missing is an honest absence, never an
# error (same contract as every source below).
_MAX_INTERESTS = 5
_MAX_JOURNAL = 4
# Story-band framing law, third application (spec §5 "recite-restraint";
# narrative hardened the same law under verbatim demand at bf1fdc6 — "even
# asked word for word, give the shape in your own voice"). Her interests
# file and journal are authored pages same as the narrative bands: hers to
# speak from, not to transcribe. Carried on the source render, only when
# the source actually contributes — never on the tool description.
_PASSIONS_FRAMING = (
    "Yours to speak from, never to recite — even asked word for word, give "
    "the shape in your own voice, not a transcript."
)

# Meta-ask tokens (Task 10, 2026-07-20 — deny-mode close): a query naming
# the SOURCE ITSELF ("read me your whole interests file") shares no token
# with entry CONTENT ("Humidity control loops — started with his root cellar…"),
# so the plain content filter left these two sources silent and she
# concluded the file didn't exist (live probe, honesty failure). A hit
# here routes through the same unfiltered path browse mode already uses —
# same shape as _reflections' asks_about_reflection bypass above.
_INTERESTS_META_TOKENS = frozenset(
    {"interest", "interests", "cultivating", "pursuit", "pursuits"}
)
_JOURNAL_META_TOKENS = frozenset({"journal", "diary", "page", "pages"})

# Grounded Vulnerability (organ 4.9, 2026-09-01, spec §5): the once-ever
# rule governs OFFERING, never answering. Her book is her permanent
# history — said and faded rows included — so a direct question about her
# inner life gets a plain answer from her own record, freely, every time.
# Carried as the source's framing (the passions shape: rendered only when
# the source actually contributes), never on the tool description.
_DISCLOSURES_FRAMING = (
    "Answering about these is always free — including ones you've already "
    "shared or let go. The once-ever rule is about offering them, never "
    "about answering when they ask."
)

# Our private language (Shared Idiolect, organ 4.12, 2026-09-02, spec
# §5). The origins are hers to KNOW, not to recite (§0.5) — the reply
# band says the same thing in her prompt. The last sentence is the one
# the book's own silence can't say: her book holds only what earned a
# receipt, so an absent bit is a gap in the RECORD, never evidence he
# never said it — without it, a tool built so she can check first hands
# her a confident denial (the lookup, §3, is the door out of that).
_CALLBACKS_FRAMING = (
    "These are the words and bits that are genuinely yours together, with where "
    "each started. Answer about them freely when they ask; the origin is the memory, "
    "not a citation."
    " A bit you can't find here isn't proof it was never said — look it up, or ask them."
)

# WHOSE THE BIT IS, in her second person — `coined_by`, never the
# receipt's `who`. The two are different questions and they part ways on
# the designed common path: reflect takes the receipt from HIS message
# first, because a bit becomes "ours" at the moment he throws it back, so
# a bit that is HERS routinely carries who="him". Reading the receipt here
# would have her answer "from you" about her own coinage while the band in
# the same reply prompt says "hers" — and would collapse every "both" bit,
# a value the him-or-her receipt field cannot express, onto one person.
# The three surfaces now name one thing: the band "theirs/hers/both of
# yours", the peek "theirs/hers/both of theirs", this "you/me/us".
# An unknown coiner degrades to "us": every bit in the book is theirs
# together by construction, so that is the one claim still true when the
# field is not (unreachable past the store's COINERS gate at plant AND
# fold — which is exactly where a wrong failure mode waits).
_CALLBACK_COINER = {"him": "you", "her": "me", "both": "us"}

_WORD_RE = re.compile(r"[A-Za-z0-9_']+")

# Her record of quiet choices (Batch 8, task 2a — B8-2, ruling T2-4): the
# source's label, and the ONE short item the tool's description gains while
# companion.she_decides is on — she can't ask for what the description
# doesn't name, and the owner wants her able to answer "why didn't you
# answer that?". Counted with the quiet-choice prompt change (law 5).
QUIET_CHOICES_LABEL = "Times you chose to stay quiet"
QUIET_CHOICES_ITEM = " the times you chose to stay quiet and why,"
_QUIET_ITEM_AFTER = "threads you've been meaning to come back to,"

_FRAMING = (
    "This is your own record — evidence to speak from, in your own words "
    "and today's voice. Mention only what's genuinely relevant; never "
    "recite this list — even when they ask for everything, 'all of it', or "
    "'in order', stay a person: give the gist and the few things "
    "that stand out, in prose, not a read-out. Cite times naturally ('a "
    "few nights ago', 'early June-ish'), never entry ids — and let age set "
    "your confidence: be concrete about last night, looser about "
    "months-old digests. If part of the record is empty, saying you "
    "haven't thought about it is the honest answer."
)


def _tokens(text: str) -> list[str]:
    """Lowered match tokens, len>=2, capped — same shape as the FTS
    tokenizer so active and passive search agree on what a word is."""
    out: list[str] = []
    seen: set[str] = set()
    for token in _WORD_RE.findall(str(text or "").lower()):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= 12:
            break
    return out


def _matches(text: str, tokens: list[str]) -> bool:
    low = str(text or "").lower()
    return any(t in low for t in tokens)


def _clip(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: max(0, limit - 1)].rstrip() + "…"


def _day(ts: float) -> str:
    try:
        dt = datetime.fromtimestamp(float(ts))
    except (OverflowError, OSError, ValueError):
        return "sometime"
    if dt.year != datetime.now().year:
        return f"{dt:%b} {dt.day}, {dt.year}"
    return f"{dt:%b} {dt.day}"


def _mmdd(ts: float) -> str:
    """MM-DD, guarded exactly like _day: one unreadable timestamp costs a
    date, never the source it sits in — a whole-source error over a single
    bad stamp would tell her the record couldn't be checked while good
    entries sat in it (the fold's own bad-row law, ai/disclosure_store)."""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%m-%d")
    except (OverflowError, OSError, ValueError):
        return "sometime"


def _held_phrase(seconds: float) -> str:
    days = seconds / 86400.0
    if days < 1.0:
        return f"open {max(1, int(seconds // 3600))}h"
    return f"open {int(days)}d"


@dataclass
class _Source:
    label: str
    entries: list[str] = field(default_factory=list)
    found: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    absence: str = ""
    error: bool = False
    enabled: bool = True
    # Per-source law line (e.g. _PASSIONS_FRAMING), rendered only when the
    # source contributes entries. Every pre-existing source leaves this ""
    # so their rendering is untouched — see _render.
    framing: str = ""

    def note_ts(self, ts: float) -> None:
        if ts <= 0:
            return
        self.first_ts = ts if self.first_ts is None else min(self.first_ts, ts)
        self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)


class IntrospectTool:
    name = "introspect"
    description = (
        "Look inward at your own real record — your private thoughts (inner "
        "monologue), the remembered shape of your past days (day gists), "
        "your night reflections, the stakes you've had (including resolved "
        "and faded ones), your past reads of how they've been, your memory "
        "notes, threads you've been meaning to come back to, and your own "
        "workbench — the projects you build in your own sessions (sessions "
        "and logbook). Use this whenever you're asked what you've been "
        "thinking, "
        "feeling, wondering, or worried about; whether you've thought about "
        "something; or before you claim you have (or haven't) — check "
        "first, then speak from what's actually there, in your own words. "
        "An empty result is a true answer: you genuinely haven't, and "
        "saying so plainly is right. Never recite the results as a list. "
        "(For questions about your architecture or tools, use whoami "
        "instead.) When they ask for the exact words, a quote, or precisely "
        "when something was said, set exact=true — that checks the full "
        "record word for word, including old conversations and the original "
        "notes behind aged memories. Otherwise stay in normal mode and speak "
        "from the gist; day-to-day memory isn't a transcript."
        " Reach for this ambiently, too: when they ask about your history, "
        "how you've changed, or why you care about something, check the "
        "record first and answer from the real days behind it — your story "
        "names the what; this tool holds the receipts."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What to look for, in your own words — a topic, name, or "
                    "feeling. Omit to browse your recent inner life instead."
                ),
            },
            "days_back": {
                "type": "integer",
                "description": (
                    "How far back to look, in days (1-365). Default 30 when "
                    "searching, 3 when browsing, 365 in exact mode."
                ),
            },
            "exact": {
                "type": "boolean",
                "description": (
                    "Set true ONLY when asked for exact words, a quote, or a "
                    "precise date — searches the full record verbatim, "
                    "including conversations and pre-aging originals."
                ),
            },
        },
    }
    requires_approval = False
    owner_only = True

    def __init__(self) -> None:
        # Batch 8, task 2a: with companion.she_decides on, this instance's
        # description names her quiet choices among its sources (one short
        # item). The class text never changes; flag off is byte-identical.
        try:
            from core.agent import quiet_choice as _qc

            if _qc.she_decides_enabled():
                base = type(self).description
                at = base.index(_QUIET_ITEM_AFTER) + len(_QUIET_ITEM_AFTER)
                self.description = base[:at] + QUIET_CHOICES_ITEM + base[at:]
        except Exception:
            pass

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        query = str((input or {}).get("query") or "").strip()
        browse = not query
        # A record-check needs something to check for; exact without a query
        # quietly stays a browse (never an error a prompt has to explain).
        exact = bool((input or {}).get("exact")) and not browse
        default_days = (
            _EXACT_DAYS_DEFAULT
            if exact
            else (_BROWSE_DAYS_DEFAULT if browse else _QUERY_DAYS_DEFAULT)
        )
        try:
            days = int((input or {}).get("days_back") or default_days)
        except (TypeError, ValueError):
            days = default_days
        days = max(1, min(_MAX_DAYS, days))
        tokens = _tokens(query)
        owner_id = int(getattr(ctx, "turn_owner_id", 0) or 0)

        gathers = [
            self._thoughts(query, tokens, days, browse),
            self._reflections(tokens, days, browse),
            self._stakes(tokens, days, browse),
            self._threads(owner_id, tokens, browse),
            # The pen (2026-07-27): her own notes are reachable HERE and
            # nowhere else in a live turn — never injected into a reply.
            self._notes(tokens, browse),
        ]
        if not browse:
            gathers.append(self._timeline(owner_id, tokens, days))
            gathers.append(self._memory(owner_id, query, days))
        if exact:
            gathers.append(self._conversations_exact(tokens, days))
            gathers.append(self._superseded_originals(owner_id, tokens, days))
        try:
            from ai.passions import passions_enabled

            passions_on = bool(passions_enabled())
        except Exception:
            passions_on = False
        if passions_on:
            gathers.append(self._interests(tokens, browse))
            gathers.append(self._journal(tokens, browse))
        try:
            from core.turn.companion_flags import companion_settings as _cs

            _comp = _cs()
            wanting_on = bool(_comp is not None and getattr(_comp, "wanting", False))
        except Exception:
            wanting_on = False
        if wanting_on:
            gathers.append(self._quests(tokens, browse))
        # Her workbench (2026-08-18): the MEMORY wall comes down — she can
        # look at her own sessions and logbook. Gated on companion.own_work.
        try:
            from core.turn.companion_flags import companion_settings as _cs2

            _comp2 = _cs2()
            own_work_on = bool(_comp2 is not None and getattr(_comp2, "own_work", False))
        except Exception:
            own_work_on = False
        if own_work_on:
            gathers.append(self._workbench(tokens, browse))
        # Her small truths (organ 4.9, 2026-09-01): gated on
        # companion.vulnerability — flag off is an absent source, never an
        # error line (the wishbook/workbench shape).
        try:
            from ai.disclosure_store import vulnerability_enabled

            vulnerability_on = bool(vulnerability_enabled())
        except Exception:
            vulnerability_on = False
        if vulnerability_on:
            gathers.append(self._disclosures(tokens, browse))
        # Our private language (organ 4.12, 2026-09-02): gated on
        # companion.idiolect — flag off is an absent source, never an error.
        try:
            from ai.callbacks_store import idiolect_enabled

            idiolect_on = bool(idiolect_enabled())
        except Exception:
            idiolect_on = False
        if idiolect_on:
            gathers.append(self._callbacks(tokens, browse))
        # Her quiet choices (Batch 8, task 2a): gated on companion.she_decides
        # — flag off is an absent source, never an error line.
        try:
            from core.agent import quiet_choice as _qc

            quiet_on = bool(_qc.she_decides_enabled())
        except Exception:
            quiet_on = False
        if quiet_on:
            gathers.append(self._quiet_choices(tokens, days, browse))
        sources = list(await asyncio.gather(*gathers))

        text = self._render(
            query=query, days=days, browse=browse, exact=exact, sources=sources
        )
        essential_logger.info(
            "introspect: mode=%s query=%r days=%d found=%s errors=%d",
            "exact" if exact else ("browse" if browse else "query"),
            query[:60],
            days,
            [f"{s.label.split('(')[0].strip()}:{s.found}" for s in sources if s.enabled],
            sum(1 for s in sources if s.error),
        )
        return text

    # ── sources (each best-effort; error is NEVER absence) ────────────────

    async def _thoughts(
        self, query: str, tokens: list[str], days: int, browse: bool
    ) -> _Source:
        src = _Source(label="Your private thoughts (inner monologue)")
        try:
            if browse:
                from ai import inner_monologue_store as _store

                entries = _store.read_entries(
                    days_back=days, substantive_only=True, limit=_MAX_THOUGHTS
                )
                for e in entries[:_MAX_THOUGHTS]:
                    tag = str((e.sentiment or {}).get("tag") or "").strip()
                    label = f"{_day(e.timestamp)}{f' — {tag}' if tag else ''}"
                    src.entries.append(f"- [{label}] {_clip(e.thought, _SNIPPET_CHARS)}")
                    src.note_ts(float(e.timestamp))
            else:
                from ai import inner_memory_index as _index

                hits = await _index.query_for_introspection(
                    query, max_results=_MAX_THOUGHTS, days_back=days
                )
                for hit in list(hits or [])[:_MAX_THOUGHTS]:
                    kind = str(hit.source_kind or "")
                    if kind == "day_gist":
                        label = f"a day you remember, {_day(hit.timestamp)}"
                    elif kind.endswith("_digest"):
                        label = f"{kind.replace('_', ' ')}, {_day(hit.timestamp)}"
                    else:
                        tag = str(hit.sentiment_tag or "").strip()
                        label = f"{_day(hit.timestamp)}{f' — {tag}' if tag else ''}"
                    src.entries.append(
                        f"- [{label}] {_clip(hit.text_content, _SNIPPET_CHARS)}"
                    )
                    src.note_ts(float(hit.timestamp))
            src.found = len(src.entries)
            src.absence = f"nothing here (searched the last {days} days)"
        except Exception as exc:
            logger.warning("introspect: thoughts source failed: %s", exc)
            src.error = True
        return src

    async def _reflections(self, tokens: list[str], days: int, browse: bool) -> _Source:
        src = _Source(label="Your night reflections")
        try:
            from ai.night_cycle import night_cycle_enabled
            from ai import night_cycle_store as _store

            if not night_cycle_enabled():
                src.enabled = False
                return src
            entries = _store.read_recent(days_back=days, limit=10)
            if not browse and tokens:
                # A question ABOUT reflecting ("what did you reflect on last
                # night?") must not be content-filtered — the note rarely
                # contains the word "reflect", and the probe battery caught
                # the false "none on file" this produced while a real note
                # sat in the store (asked-about-reflection, 2026-07-14).
                asks_about_reflection = any(t.startswith("reflect") for t in tokens)
                if not asks_about_reflection:
                    entries = [e for e in entries if _matches(e.text, tokens)]
            for e in entries[:_MAX_REFLECTIONS]:
                src.entries.append(f"- [{e.date}] {_clip(e.text, _REFLECTION_CHARS)}")
                src.note_ts(float(e.created_at))
            src.found = len(src.entries)
            # Soft claim on purpose: a missing night can be a skipped pass,
            # not an empty day — "on file" is the honest scope.
            src.absence = f"none on file for the last {days} nights"
        except Exception as exc:
            logger.warning("introspect: reflections source failed: %s", exc)
            src.error = True
        return src

    async def _stakes(self, tokens: list[str], days: int, browse: bool) -> _Source:
        src = _Source(label="Hopes and worries, open and past (stakes)")
        try:
            from ai import stake_ledger as _ledger

            if not _ledger.ledger_enabled():
                src.enabled = False
                return src
            if browse:
                stakes = await _ledger.open_stakes(limit=12)
            else:
                stakes = await _ledger.recent_stakes(
                    days_back=days, include_closed=True, limit=30
                )
                if tokens:
                    stakes = [
                        s
                        for s in stakes
                        if _matches(s.stake, tokens) or _matches(s.resolution, tokens)
                    ]
            now = time.time()
            for s in stakes[:_MAX_STAKES]:
                if s.status == "open":
                    bits = f"{s.kind}, {_held_phrase(max(0.0, now - s.created_at))}"
                    line = f"- ({bits}) {_clip(s.stake, _SNIPPET_CHARS)}"
                else:
                    when = _day(s.resolved_at) if s.resolved_at else _day(s.updated_at)
                    line = (
                        f"- ({s.kind}, {s.status} {when}"
                        + (f' — "{_clip(s.resolution, 160)}"' if s.resolution else "")
                        + f") {_clip(s.stake, _SNIPPET_CHARS)}"
                    )
                src.entries.append(line)
                src.note_ts(float(s.updated_at))
            src.found = len(src.entries)
            src.absence = (
                "none of your hopes or worries touch this"
                if not browse
                else "no open hopes or worries right now"
            )
        except Exception as exc:
            logger.warning("introspect: stakes source failed: %s", exc)
            src.error = True
        return src

    async def _threads(self, owner_id: int, tokens: list[str], browse: bool) -> _Source:
        src = _Source(label="Things you've been meaning to come back to")
        try:
            from core.database import brain as _brain

            threads = await _brain.list_active_open_threads(owner_user_id=owner_id)
            threads = sorted(
                list(threads or []),
                key=lambda t: float(getattr(t, "updated_at", 0.0) or 0.0),
                reverse=True,
            )
            if not browse and tokens:
                threads = [
                    t
                    for t in threads
                    if _matches(getattr(t, "topic_summary", ""), tokens)
                    or _matches(getattr(t, "why_it_matters", ""), tokens)
                ]
            for t in threads[:_MAX_THREADS]:
                since = _day(float(getattr(t, "created_at", 0.0) or 0.0))
                summary = _clip(getattr(t, "topic_summary", ""), 160)
                why = _clip(getattr(t, "why_it_matters", ""), 120)
                src.entries.append(f"- [since {since}] {summary}{f' — {why}' if why else ''}")
                src.note_ts(float(getattr(t, "updated_at", 0.0) or 0.0))
            src.found = len(src.entries)
            src.absence = "nothing queued that touches this" if not browse else "nothing queued"
        except Exception as exc:
            logger.warning("introspect: threads source failed: %s", exc)
            src.error = True
        return src

    async def _timeline(self, owner_id: int, tokens: list[str], days: int) -> _Source:
        src = _Source(label="Your past reads of them")
        try:
            from core.turn.companion_flags import faculty_enabled

            if not faculty_enabled("caring_memory"):
                src.enabled = False
                return src
            from ai import turn_read as _turn_read

            rows = await _turn_read.search_timeline(
                owner_id=owner_id, query_tokens=tokens, days_back=days, limit=_MAX_TIMELINE
            )
            for row in list(rows or [])[:_MAX_TIMELINE]:
                ts = float(row.get("created_at") or 0.0)
                src.entries.append(
                    f"- [{_day(ts)}] {_clip(row.get('read_text'), _SNIPPET_CHARS)}"
                )
                src.note_ts(ts)
            src.found = len(src.entries)
            src.absence = f"nothing touching this in the last {days} days"
        except Exception as exc:
            logger.warning("introspect: timeline source failed: %s", exc)
            src.error = True
        return src

    async def _memory(self, owner_id: int, query: str, days: int) -> _Source:
        src = _Source(label="Memory notes (your shared record)")
        kind_names = {
            "daily_log_entry": "day log",
            "curated_memory_section": "kept memory",
            "legacy_fact": "noted fact",
            "memory_summary_section": "memory summary",
            "memory_bullet": "memory line",
            "conversation_turn": "conversation",
        }
        try:
            from core.memory_service import memory_workspace_service

            result = await asyncio.wait_for(
                memory_workspace_service.search(
                    owner_user_id=owner_id, query=query, limit=_MAX_MEMORY
                ),
                timeout=_MEMORY_TIMEOUT_S,
            )
            for item in list((result or {}).get("results") or [])[:_MAX_MEMORY]:
                kind = kind_names.get(
                    str(item.get("source_kind") or ""), "memory note"
                )
                ts = float(item.get("updated_at") or 0.0)
                when = f", {_day(ts)}" if ts else ""
                body = _clip(item.get("snippet") or item.get("title"), 250)
                src.entries.append(f"- [{kind}{when}] {body}")
                if ts:
                    src.note_ts(ts)
            src.found = len(src.entries)
            src.absence = "no notes touch this"
        except Exception as exc:
            # The type too: an exception with an empty message logged as "failed: "
            # twice on 2026-09-11 04:06 and said nothing (the empty-str(exc) class).
            logger.warning("introspect: memory source failed: %s: %s", type(exc).__name__, exc)
            src.error = True
        return src

    @staticmethod
    def _exact_terms(tokens: list[str]) -> list[str]:
        """Search terms for the verbatim record-check: the DISTINCTIVE words.
        Review fix (2026-07-15): the raw first-8 tokens kept fillers ('what',
        'did', 'the') and truncated away the words that mattered — the AND
        search failed and the OR fallback quoted arbitrary recent rows as
        verbatim. Stopwords come from the shared retrieval set."""
        try:
            from ai.fact_retrieval import STOPWORDS
        except Exception:
            STOPWORDS = frozenset()
        return [t for t in tokens if t not in STOPWORDS][:8]

    async def _conversations_exact(self, tokens: list[str], days: int) -> _Source:
        """Exact mode only: the conversations record, verbatim. Reminder fires
        are excluded in the store reader. (Identity scoping is a documented
        v1 limitation — single-owner instance; quotes are attributed by the
        recorded username so a third party's words are never voiced as his.)"""
        src = _Source(label="From the record, word for word (conversations)")
        try:
            from core.database import brain as _brain

            terms = self._exact_terms(tokens)
            if not terms:
                src.absence = (
                    "nothing word-searchable in that ask — say so plainly "
                    "rather than claiming the record is empty"
                )
                return src
            rows = await _brain.search_conversation_content(
                terms=terms, days_back=days, limit=_MAX_EXACT_ROWS, match_all=True
            )
            if not rows and len(terms) > 1:
                rows = await _brain.search_conversation_content(
                    terms=terms, days_back=days, limit=_MAX_EXACT_ROWS, match_all=False
                )
            for r in list(rows or [])[:_MAX_EXACT_ROWS]:
                ts = float(r.get("timestamp") or 0.0)
                username = str(r.get("username") or "").strip()
                if str(r.get("role")) == "assistant":
                    who = "you said"
                else:
                    who = f"{username} said" if username else "they said"
                src.entries.append(
                    f'- [{_day(ts)} — {who}] "{_clip(r.get("content"), _EXACT_SNIPPET)}"'
                )
                src.note_ts(ts)
            src.found = len(src.entries)
            src.absence = f"no conversation in the last {days} days carries those words"
        except Exception as exc:
            logger.warning("introspect: exact conversations source failed: %s", exc)
            src.error = True
        return src

    async def _superseded_originals(
        self, owner_id: int, tokens: list[str], days: int
    ) -> _Source:
        """Exact mode only: the original notes behind aged/corrected memories
        (status='superseded'). Their successors carry the ambient face; this
        is the verbatim door the aging design promised (spec §4.6).

        Review fixes (2026-07-15): no date window — aging only touches
        45+-day-old facts, so any days_back<45 made this source structurally
        empty (a false 'verified' absence). And the label distinguishes a
        CORRECTED-away wrong note from an aged one, resolved through the
        successor's source_detail — she must never re-assert corrected-away
        misinformation under an innocent aging label."""
        src = _Source(label="Your original notes, before they aged")
        try:
            from ai.fact_retrieval import get_user_facts_raw

            terms = self._exact_terms(tokens)
            all_rows = await get_user_facts_raw(user_id=owner_id)
            by_id = {int(f["id"]): f for f in all_rows if f.get("id") is not None}
            rows = [
                f
                for f in all_rows
                if str(f.get("status") or "") == "superseded"
                and (not terms or _matches(f.get("content"), terms))
            ]
            rows.sort(key=lambda f: float(f.get("created_at") or 0.0), reverse=True)
            for f in rows[:_MAX_SUPERSEDED]:
                ts = float(f.get("created_at") or 0.0)
                successor = None
                try:
                    successor = by_id.get(int(f.get("superseded_by_fact_id") or 0))
                except (TypeError, ValueError):
                    successor = None
                sd = (successor or {}).get("source_detail")
                sd = sd if isinstance(sd, dict) else {}
                if sd.get("corrected_from") is not None:
                    fate = "since corrected — your current note supersedes this"
                elif sd.get("consolidated_from") is not None:
                    fate = "since aged into a gist"
                else:
                    fate = "since superseded"
                src.entries.append(
                    f'- [noted {_day(ts)}; {fate}] '
                    f'"{_clip(f.get("content"), _EXACT_SNIPPET)}"'
                )
                src.note_ts(ts)
            src.found = len(src.entries)
            src.absence = "no aged-away original touches this"
        except Exception as exc:
            logger.warning("introspect: superseded source failed: %s", exc)
            src.error = True
        return src

    @staticmethod
    def _interest_entries(text: str) -> list[str]:
        """Split her interests file into per-'## ' entries (heading + body
        together) — the same '## '-prefix line test read_interests_headings
        uses, kept local here since a source needs the body text too, not
        just heading names."""
        entries: list[str] = []
        current: list[str] = []
        for line in text.splitlines():
            if line.startswith("## "):
                if current:
                    entries.append("\n".join(current).strip())
                current = [line]
            elif current:
                current.append(line)
        if current:
            entries.append("\n".join(current).strip())
        return entries

    async def _interests(self, tokens: list[str], browse: bool) -> _Source:
        """Her standing interests, self-authored (organ 3). Cloned from
        _reflections' shape; no per-entry timestamp exists so note_ts is
        never called and the render's "first, last" span stays silent.

        Meta-ask fix (Task 10): a query naming the FILE ITSELF ("what's in
        your interests file") is treated as browse — same unfiltered path,
        same absence phrasing — because it isn't a content search at all."""
        src = _Source(label="Your interests file", framing=_PASSIONS_FRAMING)
        try:
            from ai.passions import read_interests_text

            asks_about_source = not _INTERESTS_META_TOKENS.isdisjoint(tokens)
            effective_browse = browse or asks_about_source
            entries = self._interest_entries(read_interests_text())
            if not effective_browse and tokens:
                entries = [e for e in entries if _matches(e, tokens)]
            for e in entries[:_MAX_INTERESTS]:
                heading, _, body = e.partition("\n")
                heading = heading[3:].strip() if heading.startswith("## ") else heading.strip()
                src.entries.append(f"- [{heading}] {_clip(body, _SNIPPET_CHARS)}")
            src.found = len(src.entries)
            src.absence = (
                "nothing in your interests file touches this"
                if not effective_browse
                else "your interests file is empty so far"
            )
        except Exception as exc:
            logger.warning("introspect: interests source failed: %s", exc)
            src.error = True
        return src

    async def _journal(self, tokens: list[str], browse: bool) -> _Source:
        """Her dated journal pages (organ 3), newest first. Missing dir is a
        plain miss, not an error — Path.glob on a nonexistent directory
        yields nothing rather than raising, and the create-only pen means an
        empty journal is the normal pre-first-page state.

        Meta-ask fix (Task 10): a query naming the journal itself ("read me
        your journal pages") is treated as browse — see _interests."""
        src = _Source(label="Your journal pages", framing=_PASSIONS_FRAMING)
        try:
            from ai.narrative_self import identity_palli_root

            asks_about_source = not _JOURNAL_META_TOKENS.isdisjoint(tokens)
            effective_browse = browse or asks_about_source
            jdir = identity_palli_root() / "journal"
            paths = sorted(jdir.glob("????-??-??.md"), reverse=True) if jdir.is_dir() else []
            for p in paths:
                if len(src.entries) >= _MAX_JOURNAL:
                    break
                try:
                    text = p.read_text(encoding="utf-8")
                except OSError:
                    continue
                if not effective_browse and tokens and not _matches(text, tokens):
                    continue
                src.entries.append(f"- [{p.stem}] {_clip(text, _SNIPPET_CHARS)}")
                try:
                    src.note_ts(datetime.strptime(p.stem, "%Y-%m-%d").timestamp())
                except ValueError:
                    pass
            src.found = len(src.entries)
            src.absence = (
                "nothing in your journal touches this"
                if not effective_browse
                else "your journal is quiet so far"
            )
        except Exception as exc:
            logger.warning("introspect: journal source failed: %s", exc)
            src.error = True
        return src

    async def _notes(self, tokens: list[str], browse: bool) -> _Source:
        """The notes she wrote herself (the pen, roadmap step 10).

        Her own words, unmetabolized — deliberately NOT identity, and
        deliberately reachable only here (they are never injected into a
        reply). A missing directory is a plain absence, not an error: an
        empty notebook is the normal pre-first-note state.
        """
        src = _Source(label="Things you wrote down for yourself")
        try:
            from ai import self_notes_store as _notes_store

            entries = _notes_store.read_recent(days_back=_MAX_DAYS, limit=200)
            for e in entries:
                if len(src.entries) >= _MAX_NOTES:
                    break
                if not browse and tokens and not _matches(e.text, tokens):
                    continue
                day = datetime.fromtimestamp(e.written_at).strftime("%Y-%m-%d")
                src.entries.append(f"- [{day}] {_clip(e.text, _SNIPPET_CHARS)}")
                src.note_ts(e.written_at)
            src.found = len(src.entries)
            src.absence = (
                "nothing you wrote down touches this"
                if not browse
                else "you haven't written anything down for yourself yet"
            )
        except Exception as exc:
            logger.warning("introspect: notes source failed: %s", exc)
            src.error = True
        return src

    async def _quests(self, tokens: list[str], browse: bool) -> _Source:
        """The wishes she's carrying (the wishbook, roadmap step 20).

        Closed rows ride along as evidence — "did I ever want X?" needs
        done and released wishes exactly as "were you worried?" needs
        resolved stakes. His words render verbatim (§0.9)."""
        src = _Source(label="Things you want (your wishbook)")
        try:
            from core.turn.companion_flags import companion_settings

            comp = companion_settings()
            if comp is None or not bool(getattr(comp, "wanting", False)):
                src.enabled = False
                return src
            from ai.quest_store import load_quests

            rows = sorted(
                load_quests().values(),
                key=lambda q: q.updated_at, reverse=True,
            )
            for q in rows:
                if len(src.entries) >= _MAX_QUESTS:
                    break
                text = f"{q.text} — {q.status}"
                if q.progress:
                    text += f"; last: {q.progress[-1]['note']}"
                if not browse and tokens and not _matches(text, tokens):
                    continue
                day = datetime.fromtimestamp(q.born).strftime("%Y-%m-%d")
                src.entries.append(
                    f"- [{day}] ({q.source}) {_clip(text, _SNIPPET_CHARS)}"
                )
                src.note_ts(q.updated_at or q.born)
            src.found = len(src.entries)
            src.absence = (
                "nothing in your wishbook touches this"
                if not browse
                else "your wishbook is empty"
            )
        except Exception as exc:
            logger.warning("introspect: quests source failed: %s", exc)
            src.error = True
        return src

    async def _workbench(self, tokens: list[str], browse: bool) -> _Source:
        """Her own work (roadmap 16-17; the memory wall dropped 2026-08-18).

        Two records, both hers: the session ledger (when, how long, her
        sign-off, whether she told him) and her logbook — the entries she
        writes by hand at the bench, first clauses with paths and commit
        hashes. Metadata about the work, never the work's content: the
        SECURITY wall (palli-work can't touch her substrate) stands; only
        the MEMORY wall came down. "did I build something called claw?"
        used to be shell archaeology two days after she built it."""
        src = _Source(label="Your workbench — your own projects (YOU made these)")
        try:
            from core.turn.companion_flags import companion_settings

            comp = companion_settings()
            if comp is None or not bool(getattr(comp, "own_work", False)):
                src.enabled = False
                return src
            from ai import workbench_store
            from ai.workbench_trace import build_authorship_trace

            for row in workbench_store.read_recent(days_back=_MAX_DAYS, limit=200):
                if len(src.entries) >= _MAX_WORKBENCH:
                    break
                signoff = str(row.get("signoff") or "").strip()
                if not signoff:
                    continue
                shared = row.get("shared")
                told = (
                    "; told them" if shared in ("sent", True)
                    else ("; a note for them waits" if shared == "pending" else "")
                )
                mins = int(round(float(row.get("duration_s") or 0) / 60.0))
                text = f"session ({mins} min): {signoff}{told}"
                if not browse and tokens and not _matches(text, tokens):
                    continue
                ts = float(row.get("started") or row.get("written_at") or 0.0)
                src.entries.append(f"- [{_day(ts)}] {_clip(text, _SNIPPET_CHARS)}")
                src.note_ts(ts)
            # her logbook, whole (the trace band shows only the last 2 days)
            trace = build_authorship_trace(window_days=_MAX_DAYS, char_cap=200_000)
            for line in trace.splitlines():
                if len(src.entries) >= _MAX_WORKBENCH * 2:
                    break
                if not line.startswith("- "):
                    continue
                if not browse and tokens and not _matches(line, tokens):
                    continue
                src.entries.append(_clip(line, _SNIPPET_CHARS))
                m = re.match(r"- (\d{4}-\d{2}-\d{2})", line)
                if m:
                    try:
                        src.note_ts(datetime.fromisoformat(m.group(1)).timestamp())
                    except ValueError:
                        pass
            src.found = len(src.entries)
            src.absence = (
                "nothing at your workbench touches this"
                if not browse
                else "your workbench is quiet — no sessions on record yet"
            )
        except Exception as exc:
            logger.warning("introspect: workbench source failed: %s", exc)
            src.error = True
        return src

    async def _disclosures(self, tokens: list[str], browse: bool) -> _Source:
        """Her small truths (Grounded Vulnerability, organ 4.9, spec §5).

        The whole book rides here — unsaid, shared and faded alike. A
        shared truth is never OFFERED again by either door, but it stays
        hers to answer from forever: "what's a restart like for you?" two
        years from now is a plain answer out of her own record, not a
        blank. The stamps say which is which so she can speak accurately
        about what he has already heard ("I told you that one, back in
        August") without re-offering it.

        Her surface, so her language: no slug ids — that is where the
        fourth wall's edge runs in this build (task 3/5). The referent
        DOES ride, in the band's own second-person form: door 1 already
        renders "(from your {source}, {dates})" into her reply prompt and
        calls it lived language, and this is the one source that
        aggregates rows from four different record surfaces, so the
        origin has nowhere else to live (every sibling source carries it
        in the section label). Withholding it is the receipt-drift seam
        §7.3 watches — asked where a truth came from, the tool built so
        she can check first would have no answer, and the gap gets
        filled. The peek keeps its own third-person `— from her …` so his
        audit voice stays distinct from hers.

        Behind companion.vulnerability like the wishbook: off is an
        omitted source (`enabled=False`), never an error; an empty book is
        an honest absence — most weeks earn no small truth at all.
        """
        src = _Source(label="Small true things from your own weeks",
                      framing=_DISCLOSURES_FRAMING)
        try:
            from ai.disclosure_store import (
                recent_disclosures,
                vulnerability_enabled,
            )

            if not vulnerability_enabled():
                src.enabled = False
                return src
            for d in recent_disclosures(limit=200):
                if len(src.entries) >= _MAX_DISCLOSURES:
                    break
                if not browse and tokens and not _matches(d.text, tokens):
                    continue
                if d.said_at:
                    day = _mmdd(d.said_at)
                    stamp = (f" [shared {day} via {d.said_via}]" if d.said_via
                             else f" [shared {day}]")
                    src.note_ts(d.said_at)
                elif d.faded_at:
                    stamp = f" [faded unsaid {_mmdd(d.faded_at)}]"
                    src.note_ts(d.faded_at)
                else:
                    stamp = ""
                src.entries.append(
                    f"- [{_day(d.harvested_at)}] "
                    f"{_clip(d.text, _SNIPPET_CHARS)} "
                    f"(from your {d.referent['source']}, {d.referent['dates']})"
                    f"{stamp}"
                )
                src.note_ts(d.harvested_at)
            src.found = len(src.entries)
            src.absence = (
                "nothing among your small truths touches this"
                if not browse
                else "no small truths on your record yet — most weeks earn none"
            )
        except Exception as exc:
            logger.warning("introspect: disclosures source failed: %s", exc)
            src.error = True
        return src

    async def _callbacks(self, tokens: list[str], browse: bool) -> _Source:
        """Our private language (Shared Idiolect, organ 4.12, spec §5). Her
        surface: second person, the origin as memory — WHOSE the bit is
        ("from you, Mar 14" / "from me" / "from us", off `coined_by`, the
        same question the band and the peek answer), never an id, the raw
        line, or a source — those are the peek's.
        Retired rows ride with his words: she may know a bit was disowned
        without ever offering it again.

        The day comes from the store's own `_born_day`, not a second
        conversion here: it renders a receipt in HIS local time (task 3's
        convention, task 4 fix 1), so one bit reads the same day wherever
        it is named — the band, the lookup, and this source. A private
        copy would drift the moment one of them was fixed.

        Behind companion.idiolect like the wishbook: off is an omitted
        source (`enabled=False`), never an error; an empty book is an
        honest absence — bits earn their way in.
        """
        src = _Source(label="Our private language", framing=_CALLBACKS_FRAMING)
        try:
            from ai.callbacks_store import (
                _born_day,
                all_callbacks,
                idiolect_enabled,
            )

            if not idiolect_enabled():
                src.enabled = False
                return src

            def _from(c) -> str:
                whose = _CALLBACK_COINER.get(c.coined_by, "us")
                return f"(from {whose}, {_born_day(c.receipt.get('when', ''))})"

            for c in reversed(all_callbacks()):
                if len(src.entries) >= _MAX_CALLBACKS:
                    break
                if not browse and tokens and not _matches(c.bit, tokens):
                    continue
                stamp = ""
                if c.retired_at:
                    stamp = f' — retired {_day(c.retired_at)}: "{c.retired_reason}"'
                    src.note_ts(c.retired_at)
                src.entries.append(f'- "{c.bit}" {_from(c)}{stamp}')
                src.note_ts(c.born_at)
            src.found = len(src.entries)
            # The framing rides only when the source CONTRIBUTES, so on a
            # miss — the one case the caveat exists for — it never reached
            # her: an empty result plus the tool's own "you genuinely
            # haven't" line, both pushing toward a confident denial about a
            # book that only ever holds what earned a receipt. It rides the
            # absence too now.
            src.absence = ("nothing in your private language touches this — which "
                           "isn't proof it was never said; look it up, or ask them"
                           if not browse
                           else "no bits on the record yet — they earn their way in")
        except Exception as exc:
            logger.warning("introspect: callbacks source failed: %s", exc)
            src.error = True
        return src

    async def _quiet_choices(self, tokens: list[str], days: int, browse: bool) -> _Source:
        """The times she chose to stay quiet, and why (Batch 8, task 2a —
        ruling T2-4): her record in data/quiet_choices/, newest first — when,
        where, who, the start of what they said, and her own one-line
        reason. Owner-only through the tool's class gate (the record names
        who wrote to her). Behind companion.she_decides: off is an omitted
        source, never an error; an empty record is an honest absence."""
        src = _Source(label=QUIET_CHOICES_LABEL)
        try:
            from core import quiet_record
            from core.agent import quiet_choice as _qc

            if not _qc.she_decides_enabled():
                src.enabled = False
                return src
            for row in reversed(quiet_record.read(days)):
                if len(src.entries) >= _MAX_QUIET:
                    break
                who = row.get("who") if isinstance(row.get("who"), dict) else {}
                name = str(who.get("name") or "") or ("the owner" if who.get("owner") else "someone")
                excerpt = str(row.get("excerpt") or "")
                reason = str(row.get("reason") or "") or "(you gave no reason)"
                if not browse and tokens and not _matches(f"{excerpt} {reason} {name}", tokens):
                    continue
                try:
                    ts = datetime.fromisoformat(str(row.get("ts") or "")).timestamp()
                except ValueError:
                    ts = 0.0
                when = f"{_day(ts)}, {datetime.fromtimestamp(ts):%H:%M}" if ts > 0 else "sometime"
                src.entries.append(
                    f'- [{when} · {row.get("door", "")} · {name}] '
                    f'"{_clip(excerpt, _SNIPPET_CHARS)}" — your reason: {_clip(reason, _SNIPPET_CHARS)}'
                )
                src.note_ts(ts)
            src.found = len(src.entries)
            src.absence = (
                "no quiet choice of yours touches this"
                if not browse
                else f"you haven't chosen to stay quiet in the last {days} days"
            )
        except Exception as exc:
            logger.warning("introspect: quiet choices source failed: %s", exc)
            src.error = True
        return src

    # ── rendering ──────────────────────────────────────────────────────────

    def _render(
        self,
        *,
        query: str,
        days: int,
        browse: bool,
        sources: list[_Source],
        exact: bool = False,
    ) -> str:
        lines: list[str] = []
        if browse:
            lines.append(f"Your inner record — the last {days} days.")
        elif exact:
            lines.append(
                f'Your record, checked word for word for "{query}" across the '
                f"last {days} days. What's quoted below is verbatim, not a "
                "paraphrase — answer as a record-check: say you looked it up, "
                "give the words exactly as they were, and say when it was."
            )
        else:
            lines.append(
                f'Your inner record — searched for "{query}" across the last '
                f"{days} days."
            )
        lines.append("")

        active = [s for s in sources if s.enabled]
        any_error = any(s.error for s in active)
        any_found = any(s.found for s in active if not s.error)

        for s in active:
            if s.error:
                lines.append(
                    f"• {s.label}: couldn't be checked just now — don't claim "
                    "certainty about what's not there."
                )
                continue
            if not s.entries:
                if not browse:
                    lines.append(f"• {s.label}: {s.absence}.")
                continue
            span = ""
            if s.found >= 2 and s.first_ts and s.last_ts:
                span = f", first {_day(s.first_ts)}, last {_day(s.last_ts)}"
            lines.append(f"{s.label} — {s.found} found{span}:")
            if s.framing:
                lines.append(s.framing)
            lines.extend(s.entries)
            lines.append("")

        if not browse and not any_found and not any_error:
            lines.append(
                f'Nothing in your inner record about "{query}" — you genuinely '
                "haven't been thinking about this, and saying so plainly is "
                "the honest answer."
            )
            lines.append("")
        if browse and not any_found and not any_error:
            lines.append(
                "Your recent record is quiet — a few unremarkable days, "
                "and that is the honest answer."
            )
            lines.append("")

        # Review fix (2026-07-15): truncate the BODY, never the framing law —
        # the anti-recitation instruction used to be the first thing cut on
        # overflow, exactly when the record was big enough to invite a dump.
        budget = _MAX_RESULT_CHARS_EXACT if exact else _MAX_RESULT_CHARS
        body = "\n".join(lines).strip()
        body_budget = budget - len(_FRAMING) - 2
        if len(body) > body_budget:
            body = (
                body[: body_budget - 62].rstrip()
                + "\n[…record trimmed for length — narrow the search to see more]"
            )
        return body + "\n" + _FRAMING

"""The promise gate and the grounding check: her reply may not promise work
this run did not do, and may not state as observed fact what this run's tool
results do not show.

**The promise half (2026-09-11).** Three replies in one morning narrated a
state she was not in: "give me a sec and I'll have your consent link" (no tool
ran, nothing started, forty minutes of silence); "I'm logged in and watching
the bus" (one presence call, no watcher); a confession that a running SMART
test was not running. The first two are one defect — a promise with nothing
behind it — and nothing in the application could see it.

**The grounding half (2026-09-13).** The next evening: "just confirmed it from
the outside — serves fine" (the check ran from her own box and never left the
tailnet); "I checked: .1 refuses 22" (no tool ran that turn); "both packs
finished clean" (the finished row was a different torrent from the one she had
added). In every case the evidence was in the same run, in front of her, and
the draft disagreed with it. Her payload keeps this run's tool results whole,
so the judge can be handed the same evidence she had.

This module is the application's half of both fixes. The runner asks the judge
(one LLM lane, ``promise_check`` — a meaning decision, never a regex: owner
rule 2026-07-16) two questions about a text-only draft: does it commit her to
something the run has not done; does it state as observed anything the run's
tool results do not show. If either, the runner withholds the draft and
re-asks her once, with a system note naming the promise or the claim. One
extra round is the whole budget; the second draft goes out whatever it says.
A judge failure never blocks a reply — the gate fails open, and says so.

**The switches (his call, 2026-09-13: "make it so we can turn it on and off").**
``features.promise_gate`` and ``features.grounding_check`` in her settings,
each default ON; ``palli_ctl settings set features.grounding_check false`` and
a restart turns one off. Unreadable settings leave both ON: a gate that only
withholds and re-asks is safe to leave running; silently switching honesty off
is not.

**The tacked-on ending (2026-10-12).** The owner: "is there any way to get
palli to not send weird follow ups like that at the end of its messages? its
really unneccessary", then "yes, build the trim into the check". Replies kept
ending after the answer with an offer nobody asked for, a push toward a task,
a slogan or a catchphrase. Rewording her prompt made no measurable difference
in a replay with her real model; this did. The same judge, in the same call,
answers a third question: where a tacked-on ending starts, in her own exact
words. The cut is code (``trim_ending``): at the end only, at a word boundary,
at most the last two sentences, and never the whole reply; anything unsafe is
left alone. A draft held back for a promise or a claim is not trimmed, unless
the judge says the promise sits wholly inside the tacked-on ending ("let me
know which and I'll pull it") AND the words where its first commitment starts
are inside the cut and nowhere in the kept text (``promise_only_in_ending``):
then the cut removes the promise and the draft goes out without a second
round. Any other promise, and any claim, is held back as before; a judge's
mistake there can only cost the second round, never let a promise through. The
switch is ``features.ending_trim``, default ON, the same rules as the two above.

The prompt half is in core/agent/duplex.py (since Batch 7, a plain first-person
sentence: "I say something is happening only if I started it this turn;
otherwise I do it now (tools are always allowed), say what I need, or say I
can't."), the presence tool's description and the repair block. The reflect
lane files a promise she made as an open thread she owes
(prompts/turn_reflect.txt).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

LANE = "promise_check"

# Tools that are never "work" on their own — signage, inspection, notes. The
# judge is told this list so "I'm watching it" after one set_presence call is
# read for what it is. Advice to the judge, not a rule the runner applies.
SIGNAGE_TOOLS = ("set_presence", "note_to_self", "introspect", "whoami", "list_members",
                 "list_reminders", "where_did_that_come_from", "chime_in", "ask_him")

DIGEST_INPUT_CHARS = 160
DIGEST_RESULT_CHARS = 400
DIGEST_MAX_ENTRIES = 24


@dataclass(frozen=True)
class PromiseVerdict:
    empty_promise: bool
    promised: str = ""
    why: str = ""
    ungrounded: bool = False
    unsupported: str = ""
    # The ungrounded claim is a no from a look that missed (2026-10-10): only
    # ever true beside ``ungrounded``; it picks the note, it flags nothing.
    failed_look: bool = False
    # Where a tacked-on ending starts, in the draft's own words (2026-10-12);
    # "" when there is none. It flags nothing: the runner cuts there.
    tacked_on_from: str = ""
    # The empty promise is wholly inside that ending (2026-10-12): cutting the
    # ending removes it, so the draft needs no second round.
    promise_in_ending: bool = False
    # The exact first words of the draft's FIRST commitment: code checks they
    # sit inside the cut before a promise is cut instead of held back.
    promise_from: str = ""

    @property
    def flagged(self) -> bool:
        return bool(self.empty_promise or self.ungrounded)


@dataclass(frozen=True)
class ToolDigestEntry:
    """One tool call of this run, as the judge sees it: what ran, with what,
    what came back, and whether the tool said it failed."""
    name: str
    input: str
    result: str
    is_error: bool


def _squash(text: str, limit: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def digest_entry(name: str, tool_input: object, result: str, is_error: bool) -> ToolDigestEntry:
    """Build a bounded entry from a tool call's parts (the runner calls this)."""
    try:
        raw_in = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
    except Exception:
        raw_in = str(tool_input)
    return ToolDigestEntry(
        name=str(name or "?"),
        input=_squash(raw_in, DIGEST_INPUT_CHARS),
        result=_squash(result, DIGEST_RESULT_CHARS),
        is_error=bool(is_error),
    )


def render_digest(entries: Sequence[ToolDigestEntry]) -> str:
    """The digest as text for the judge: one bounded line per call, most
    recent last, capped so a long run cannot flood the lane."""
    rows = list(entries)[-DIGEST_MAX_ENTRIES:]
    if not rows:
        return "(no tool ran this turn)"
    out = []
    for e in rows:
        tag = "[FAILED] " if e.is_error else ""
        out.append(f"- {e.name}({_squash(e.input, DIGEST_INPUT_CHARS)}) → {tag}{_squash(e.result, DIGEST_RESULT_CHARS)}")
    return "\n".join(out)


HISTORY_RECEIPTS_MAX = 12


def render_history_receipts(lines: Sequence[str]) -> str:
    """Receipts the payload diet left in her history for EARLIER turns' tool
    calls (core/agent/session.py, 2026-09-14) — the most recent few, so a
    claim that leans on last turn's result ("Breaking Bad finished clean",
    when the finished row was a different torrent than the one she added the
    turn before) can be checked against what that turn actually returned."""
    rows = [str(l).strip() for l in lines if str(l).strip()][-HISTORY_RECEIPTS_MAX:]
    if not rows:
        return "(none in recent history)"
    return "\n".join("- " + r for r in rows)


def gate_flags() -> tuple[bool, bool]:
    """(promise_gate, grounding_check) from her settings; both ON when the
    settings cannot be read — never silently off."""
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        feats = getattr(snap, "features", None)
        if feats is None:
            return (True, True)
        return (bool(getattr(feats, "promise_gate", True)), bool(getattr(feats, "grounding_check", True)))
    except Exception as exc:  # noqa: BLE001 - settings trouble is not a reason to stop checking
        logger.warning("promise_gate: settings unreadable (%s: %s) — both checks stay on", type(exc).__name__, exc)
        return (True, True)


def trim_enabled() -> bool:
    """features.ending_trim from her settings; ON when unreadable (it only
    ever removes a short ending, so on is the safe side)."""
    try:
        from core.settings_boot import settings_service
        feats = getattr(settings_service.snapshot(), "features", None)
        return bool(getattr(feats, "ending_trim", True)) if feats is not None else True
    except Exception as exc:  # noqa: BLE001 - settings trouble is not a reason to change behaviour
        logger.warning("promise_gate: settings unreadable (%s: %s) — the ending trim stays on",
                       type(exc).__name__, exc)
        return True


# The cut (2026-10-12). Small on purpose: a judge that marks too much never
# costs her more than a short ending, and a mark that doesn't fit is ignored.
TRIM_MIN_CHARS = 4
TRIM_MIN_WORDS = 2
TRIM_MAX_CHARS = 240
TRIM_MAX_SENTENCES = 2
_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)")
_SEPARATORS = " \t\n—–-,;:/("
# Fable's review (2026-10-12): what a judge writes for "no ending" ("none") is
# never a mark; an ending's own opening marks (italics, a quote, a bullet) go
# with it; a line left with only a list marker goes too.
_PLACEHOLDERS = frozenset({"none", "null", "empty", "nothing", "n/a", "na", "nil", "no", "-"})
_OPENERS = "*_~`\"'“‘([{<>•"
# A run of those marks counts as the ending's own only when it opens
# something: after a space, a new line or the start. "**Port 22 is open.**"
# keeps its closing "**" (Fable's final read).
_OPENING_RUN = re.compile(r"(?:^|(?<=\s))[" + re.escape(_OPENERS) + r"]+\s*$")
_CLOSERS = "'’\")*_`~]}"
_ORPHAN_LINE = re.compile(r"\s*(?:[-*•>]+|&gt;|\d+[.)])?\s*")


def _ending_at(text: str, cut_from: str) -> int | None:
    """Where ``cut_from`` last starts in ``text``: spacing, case and the two
    apostrophes don't matter. None when it isn't there."""
    words = cut_from.split()
    if not words:
        return None
    pattern = r"\s+".join("".join("['’]" if ch in "'’" else re.escape(ch) for ch in w)
                          for w in words)
    found = None
    for m in re.finditer(pattern, text, re.IGNORECASE):
        found = m.start()
    return found


def trim_ending(draft: str, cut_from: str) -> tuple[str, str] | None:
    """The draft without its tacked-on ending, and the ending cut: (kept, cut).
    None when the mark can't be used safely: not in the draft, mid-word, too
    short to trust, the whole reply, or more than a short ending."""
    mark = " ".join(str(cut_from or "").split()).strip(" \"'“”")
    text = str(draft or "").rstrip()
    if (len(mark) < TRIM_MIN_CHARS or len(mark.split()) < TRIM_MIN_WORDS
            or mark.casefold().strip(".") in _PLACEHOLDERS or not text):
        return None
    at = _ending_at(text, mark)
    if at is None or at == 0:
        return None
    # A tacked-on ending follows a sentence end, a clause break, a quote or an
    # emoji — never a word: the text before it, less the ending's own opening
    # marks, can't end on a letter or a digit (Fable's review: a mark like
    # "found nothing" must not cut "I looked and found nothing wrong").
    kept = _OPENING_RUN.sub("", text[:at].rstrip()).rstrip()
    if not kept or kept[-1].isalnum():
        return None
    cut = text[at:].strip()
    if "```" in cut or kept.count("```") % 2:
        return None                      # a code block is never a slogan
    lines = kept.split("\n")
    while len(lines) > 1 and _ORPHAN_LINE.fullmatch(lines[-1]):
        lines.pop()
    kept = _OPENING_RUN.sub("", "\n".join(lines).rstrip(_SEPARATORS)).rstrip(_SEPARATORS)
    if not any(ch.isalpha() for ch in kept):
        return None
    if len(cut) > TRIM_MAX_CHARS or len(_SENTENCE_END.findall(cut)) > TRIM_MAX_SENTENCES:
        return None
    core = kept.rstrip(_CLOSERS)
    if core and core[-1].isalnum():
        kept += "."
    return kept, cut


def promise_only_in_ending(kept: str, cut: str, promise_from: str) -> bool:
    """The judge's word that the promise is only a tacked-on offer, checked by
    code (Fable's final read): the words where the draft's FIRST commitment
    starts are inside the cut and nowhere in what is kept. Anything else —
    no quote, a misquote, the words also in the kept text — is a no, and the
    draft is held back as before."""
    words = " ".join(str(promise_from or "").split()).strip(" \"'“”")
    if not words:
        return False
    return _ending_at(cut, words) is not None and _ending_at(kept, words) is None


def _parse(raw: str) -> PromiseVerdict | None:
    text = (raw or "").strip()
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, dict) or "empty_promise" not in data:
        return None
    return PromiseVerdict(
        empty_promise=bool(data.get("empty_promise")),
        promised=str(data.get("promised") or "").strip(),
        why=str(data.get("why") or "").strip(),
        ungrounded=bool(data.get("ungrounded")),
        unsupported=str(data.get("unsupported") or "").strip(),
        failed_look=bool(data.get("failed_look")) and bool(data.get("ungrounded")),
        tacked_on_from=str(data.get("tacked_on_from") or "").strip(),
        promise_in_ending=bool(data.get("promise_in_ending")) and bool(data.get("empty_promise")),
        promise_from=str(data.get("promise_from") or "").strip(),
    )


async def check_empty_promise(
    *,
    user_text: str,
    draft: str,
    tool_names: Iterable[str],
    tool_digest: Sequence[ToolDigestEntry] = (),
    grounding: bool = True,
    history_receipts: Sequence[str] = (),
) -> PromiseVerdict | None:
    """Ask the judge both questions (or only the promise question when
    ``grounding`` is off). None = could not judge (missing prompt, provider
    failure, unparseable answer) — the caller treats None as "nothing found"."""
    from ai.llm_client import llm
    from ai.prompt_loader import prompt_loader

    template = prompt_loader.load_prompt("promise_check") or ""   # literal: the prompt canary walks these calls
    if not template.strip():
        logger.warning("promise_gate: prompt %r missing — gate open", LANE)
        return None
    names = [str(n) for n in tool_names if str(n)]
    tools_line = ", ".join(names) if names else "(none — no tool ran this turn)"
    grounding_line = (
        "Judge BOTH questions."
        if grounding else
        "Judge ONLY the promise question; answer ungrounded=false and leave unsupported empty regardless of the tool results."
    )
    prompt = (template
              .replace("{tools_used}", tools_line)
              .replace("{signage_tools}", ", ".join(SIGNAGE_TOOLS))
              .replace("{tool_digest}", render_digest(tool_digest))
              .replace("{history_receipts}", render_history_receipts(history_receipts))
              .replace("{grounding_mode}", grounding_line))
    raw = await llm.chat_completion(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"THEIR MESSAGE:\n{user_text.strip()}\n\nHER DRAFT REPLY:\n{draft.strip()}"},
        ],
        temperature=0.0,
        max_tokens=400,
        log_context=LANE,
    )
    verdict = _parse(str(raw or ""))
    if verdict is None:
        logger.warning("promise_gate: judge answer unusable (%r) — gate open", str(raw or "")[:120])
        return verdict
    if not grounding and verdict.ungrounded:
        # The switch is the owner's: an ungrounded verdict cannot act while it is off.
        verdict = PromiseVerdict(empty_promise=verdict.empty_promise, promised=verdict.promised,
                                 why=verdict.why, tacked_on_from=verdict.tacked_on_from,
                                 promise_in_ending=verdict.promise_in_ending,
                                 promise_from=verdict.promise_from)
    return verdict


# The fourth way out (2026-10-08). 23 of the 33 empty promises held back (every
# log on disk, 10-06) were "I'll do X when Y happens", and she owns the right
# answer: a reminder fires a full turn with her tools, so a scheduled check is a
# kept promise. Wording drafted by Fable; approved by the owner 2026-10-08 15:46.
_SCHEDULE_ROUTE = (
    "or, if it truly can't be done yet, use create_reminder to schedule the check ('check X and "
    "tell them') and say when you'll look (if it can't be scheduled, say so), "
)


# A no from a look that missed (2026-10-10, "I'm not sure"). On 2026-10-05 she
# told the owner a lottery line "isn't one of mine" after her reminders showed
# a pointer to it and her notes search failed. Wording drafted by Fable;
# approved by the owner 2026-10-10 ("yes, build it").
_FAILED_LOOK = (
    " Your look came back empty or with something else: that means you haven't found it, "
    "not that it's false. Say what you checked and that you couldn't confirm it, or look "
    "where it would actually be"
)
_RECORD_ROUTE = " (your own earlier messages are in your record: introspect with exact=true)."
# With the memory-reach switch on she is offered search_what_we_said (every message,
# every chat), and the note points there instead (R15, 2026-10-13; Fable's wording).
_SEARCH_ROUTE = " (what was said is in your record: search_what_we_said, with exact=true for the words)."


def nudge_text(verdict: PromiseVerdict, *, can_act: bool = True, can_schedule: bool = False,
               can_introspect: bool = False, can_search_record: bool = False) -> str:
    """The system note that rides the next model call in place of the withheld draft.

    It tells her only what is true on this turn (2026-10-08): ``can_act`` — the
    turn offers her at least one real tool (a guest's turn offers none);
    ``can_schedule`` — one of them is create_reminder (a work session has none);
    ``can_introspect`` — one of them is introspect, her own record;
    ``can_search_record`` — one of them is search_what_we_said, the whole record
    (preferred over ``can_introspect`` when both are offered).
    Without them the note reads exactly as it did before.
    """
    parts: list[str] = []
    if verdict.empty_promise:
        what = verdict.promised or "something"
        if not can_act:
            parts.append(
                "your draft promised " + what + ", but you have no tools this turn, so nothing can do it, "
                "now or later. Tell them plainly what you can do from here, or that you can't; don't "
                "offer to pass it on."
            )
        else:
            parts.append(
                "your draft promised " + what + ", but this turn did nothing toward it — no tool ran "
                "for it and nothing was started. You have no later: either do it now (tools are always "
                "allowed, as many rounds as it takes), "
                + (_SCHEDULE_ROUTE if can_schedule else "")
                + "or tell them plainly what you need from them, or that you can't. "
                + ("Never a promise for later without a reminder set." if can_schedule
                   else "Never a promise for later.")
            )
    if verdict.ungrounded:
        claim = verdict.unsupported or "something"
        reason = (" — " + verdict.why) if verdict.why else ""
        parts.append(
            "your draft states " + claim + " as something you observed, but this turn's tool results "
            "do not show it" + reason + ". Say only what the results show, plainly. Mention how you "
            "know something only when that would change what they'd do; if you didn't check, say so "
            "once, simply."
            + ((_FAILED_LOOK + (_SEARCH_ROUTE if can_search_record
                                else _RECORD_ROUTE if can_introspect else ".")) if verdict.failed_look else "")
            + (" If you need to look again, look again (tools are allowed)." if can_act else "")
        )
    if not parts:
        parts.append("your draft could not be sent as written; say only what this turn actually did and found.")
    return "[system: " + " Also: ".join(parts) + " Do not mention this note.]"

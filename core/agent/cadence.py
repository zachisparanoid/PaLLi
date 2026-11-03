from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol


logger = logging.getLogger(__name__)


CadenceLLM = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class CadenceConfig:
    should_respond: bool
    register: str
    length: str
    phase: str
    target_beats: int
    observation_text: str
    directive: str  # rendered system-prompt block
    # Momentum tag (2026-07-08): relationship-relevant emotional register in
    # ai/relationship_momentum's vocabulary. Threaded into the synthesized
    # TurnDecision so reflect's momentum update finally has a signal on the
    # ReAct path. Defaults keep every existing constructor call working.
    emotional_context: str = "neutral"
    # She decides (Batch 8, task 2a — ruling T2a-1): what the quick vote
    # says she does with the message — "answer" | "wait" | "stay_quiet" —
    # and why, in one line. ``should_respond`` stays, as decision ==
    # "answer". A vote that names no decision (today's text never asks for
    # one, and every older fixture feeds only should_respond) derives it:
    # true is "answer", false is "stay_quiet".
    decision: str = "answer"
    reason: str = ""


DECISIONS = ("answer", "wait", "stay_quiet")


SHAPE_RESPONSE_PROMPT = """\
You are PaLLi's response-shaping pass. Read the user's most recent
turn (and recent conversation context if any) and decide:

1. should_respond: for anything the USER actually said to PaLLi — a
   question, a request, an action ask, a comment, even a short remark
   or a "thanks" — this is ALWAYS true. If the user spoke, they get a
   reply. Return false for a PaLLi-initiated proactive/monologue impulse
   that, on reflection, isn't worth sending right now — or when the user
   explicitly asks for no response ("don't reply", "don't respond to
   this", "no need to answer"): honoring that is the reply. Never return
   false merely because a message is an action_request or seems casual.
2. register: which conversational register fits.
   Options: casual_greeting, depth_inquiry, support, action_request,
   correction, settled_conversation, opening, neutral.
3. length: how long the reply should be. short is the DEFAULT — casual
   conversation, reactions, check-ins, quick answers, banter are all
   short (one or two sentences). medium (a few sentences) only for a
   genuine question that needs real explanation or an emotional moment
   that deserves room. long (multiple paragraphs) ONLY when the user
   explicitly asked for depth — a plan, a breakdown, a detailed
   explanation — or the task itself demands it (code, step-by-step
   instructions they requested). When the user asks for CONTENT ITSELF —
   a transcript, a quote, a list, a file's text, a table, code — the
   answer is that content, verbatim: vote long, because compressing
   verbatim material destroys it. When torn between two, choose the shorter.
4. phase: where we are in the conversation arc
   (opening, settled_conversation, returning, closing)
5. target_beats: 1 for single-flow reply; 2-3 for cadenced multi-beat
   delivery on emotional registers
6. observation_text: 2-5 sentences of qualitative read on what the
   user is signaling
7. emotional_context: the relationship-relevant emotional register of the
   user's turn, STRICTLY one of: deep_intimacy, vulnerable_close, venting,
   playful_banter, frustrated, casual_greeting, neutral. Default to
   neutral unless the signal is clear — this feeds a slow, compounding
   relationship state, so precision beats sensitivity.

Return ONLY a single JSON object with these exact keys, no other text:
{
  "should_respond": <bool>,
  "register": "<string>",
  "length": "<string>",
  "phase": "<string>",
  "target_beats": <int 1..3>,
  "observation_text": "<string>",
  "emotional_context": "<string>"
}

Conversation context:
<<RECENT_HISTORY>>

User's latest turn:
<<USER_TEXT>>
"""


def _swap(text: str, old: str, new: str) -> str:
    """Replace exactly one occurrence; a text that drifted fails at import."""
    if text.count(old) != 1:
        raise RuntimeError(f"cadence: the vote text no longer has exactly one {old!r}")
    return text.replace(old, new)


# She decides whether to answer (Batch 8, task 2a — B8-2, ruling T2a-1): the
# participant-framed vote, used on a human conversation turn when
# companion.she_decides is on. Only the opening (who she is, item 1: the
# decision) and two lines of the JSON differ from SHAPE_RESPONSE_PROMPT; the
# shaping half (register, length, phase, beats, observation, emotional
# context) is today's text, unchanged. Today's "ALWAYS true for a user" rule
# is gone: she is an AI that knows it, taking part like a participant, and
# whose "don't reply" counts is part of what she weighs. With the switch off
# SHAPE_RESPONSE_PROMPT is sent verbatim.
_PARTICIPANT_OPENING = """\
You are PaLLi's quick first read of a message. PaLLi is an AI and knows it:
she takes part in conversations like a participant who knows she is an AI,
not as a helper that must answer every message. Read the latest message
(and the conversation before it, if any) and decide:

1. decision: what PaLLi does with this message. One of:
   - "answer": someone is talking to her or asking her something, or she
     has something worth adding. A real question or request to her is
     "answer", unless a silence that counts (below) covers it.
   - "wait": the message looks unfinished and more is clearly coming
     ("you won't believe this", "bro..."), so she reads the rest first.
   - "stay_quiet": nothing from her is wanted here. They asked her not to
     reply ("don't reply", "no need to answer"), or the message isn't hers
     to answer (people talking to each other).
   Whose "don't reply" counts: every message shows who sent it; only the
   owner's account mark identifies the owner, a name or nickname proves
   nothing; a sender the app hides stays hidden to her too. A guest's "don't reply"
   covers only that guest's own messages; it never silences her toward the
   owner or anyone else. A standing silence ("stay quiet in here until
   tomorrow") counts only when the owner asked for it. When the owner gives
   her a standing instruction about how to behave here ("stay quiet in here
   until tomorrow"), the decision is "answer", even when it asks for quiet:
   her full turn saves it as a room note and can stay quiet in that same
   step. The only exception to a silence is a real safety reason: someone
   says they are in danger.
   reason: one short line saying why.
"""
PARTICIPANT_SHAPE_PROMPT = _swap(
    _swap(
        _swap(
            _PARTICIPANT_OPENING
            + SHAPE_RESPONSE_PROMPT[SHAPE_RESPONSE_PROMPT.index("2. register:"):],
            '  "should_respond": <bool>,\n',
            '  "decision": "<answer|wait|stay_quiet>",\n  "reason": "<one short line>",\n',
        ),
        "User's latest turn:\n",
        "Latest message:\n",
    ),
    # Step 2, part 6 (the plan, A5): the read reaches her as her own notes, so
    # it describes and never guesses (10-06: a guess about who a message was
    # from reached her as a fact). The switch-off vote keeps today's item 6.
    "6. observation_text: 2-5 sentences of qualitative read on what the\n"
    "   user is signaling\n",
    "6. observation_text: 2-5 sentences on how the people here seem and\n"
    "   what they're signaling. Describe only: never guess facts, answers,\n"
    "   or who someone is.\n",
)


# The room-reading fix (2026-10-12, sentence C; Fable's words, approved by the
# owner word for word): in a shared room the vote says what a message does
# instead of guessing what it means (10-11: "a nickname or a slip" reached her
# as a fact, and she answered a message meant for a friend), and an unclear
# message is not hers. Shared rooms only: a DM's vote is
# PARTICIPANT_SHAPE_PROMPT, byte for byte.
ROOM_READING_VOTE = (
    "   Who a message is for: say what the message does (\"calls someone named\n"
    "   Casey\"), never what it might mean (\"a nickname or a slip\"). A name that\n"
    "   isn't hers and isn't a known alias of hers belongs to someone else. When\n"
    "   it's unclear whether she's meant, the decision is \"stay_quiet\".\n"
)
PARTICIPANT_ROOM_PROMPT = _swap(
    PARTICIPANT_SHAPE_PROMPT,
    "   reason: one short line saying why.\n",
    ROOM_READING_VOTE + "   reason: one short line saying why.\n",
)


_DEFAULT_CONFIG = CadenceConfig(
    should_respond=True, register="neutral", length="medium",
    phase="settled_conversation", target_beats=1,
    observation_text="(default — cadence shape failed; using neutral fallback)",
    directive="",
)


# Batch 7, her voice (audit 4.9): the directive is plain sentences, never a
# form of field labels. Short and medium are said once, in the trailing
# shape note (reply_governor.render_shape_note, which names the cap the
# governor enforces in code). The directive keeps only what nothing else
# says: a "long" vote (no shape note, no cap: the one permission she has to
# run long, DEFECT §14), several beats (the chunker splits on blank lines),
# and the vote's observation of them. Every CadenceConfig field stays on the
# dataclass; downstream code reads the attributes, never this text.
# Consequence, accepted: a workbench or late-digest turn (govern_replies
# off, so no shape note) no longer gets a short/medium hint; both are exempt
# from the length limit by the owner's calls.
# Fix round 1 (ruling C15): the long sentence claims no reason (the vote
# also runs on workbench, monologue and proactive turns, where nobody
# asked); the beats sentence names no length (a long vote with beats said
# "long … short messages"); and a plain heading keeps a bare observation
# from reading as part of the band before it.
_DIRECTIVE_HEADING = "## This turn"
_LONG_SENTENCE = "This one can run long."
_BEATS_SENTENCE = (
    "This one reads better as two or three messages, with a blank line "
    "between them."
)


def _observation_sentences(cfg: CadenceConfig) -> str:
    """The vote's observation, whole and unlabelled — or "" when it says
    nothing: empty, the failed vote's stand-in, or wholly in parentheses
    (a log line, not something she noticed)."""
    obs = str(cfg.observation_text or "").strip()
    if not obs or obs == _DEFAULT_CONFIG.observation_text:
        return ""
    if obs.startswith("(") and obs.endswith(")"):
        return ""
    return obs


def render_cadence_directive(cfg: CadenceConfig) -> str:
    parts: list[str] = []
    if str(cfg.length or "").strip().lower() == "long":
        parts.append(_LONG_SENTENCE)
    if cfg.target_beats >= 2:
        parts.append(_BEATS_SENTENCE)
    observation = _observation_sentences(cfg)
    if observation:
        parts.append(observation)
    if not parts:
        return ""
    return f"{_DIRECTIVE_HEADING}\n" + " ".join(parts)


async def shape_response(ctx: Any, *, llm: CadenceLLM, participant: bool = False,
                         signals: str = "", note: str = "", room: bool = False,
                         names: str = "") -> CadenceConfig:
    """Pre-loop decide-lite pass. Returns CadenceConfig with rendered directive.

    ``participant`` (Batch 8, task 2a): send the participant-framed vote,
    PARTICIPANT_SHAPE_PROMPT, instead of today's text. The runner asks for it
    only on a human conversation turn with companion.she_decides on; every
    other call sends SHAPE_RESPONSE_PROMPT exactly as before.

    ``signals`` / ``note`` (Batch 8, task 2b — rulings T2b-3, T2b-5): one
    line each, after the latest message — the signals line
    (``render_signals``) and, in a room's first read, the owner's room note.
    Read only with ``participant``; "" adds nothing, so every other prompt is
    byte-identical.

    ``room`` / ``names`` (the room-reading fix, 2026-10-12): in a shared room,
    the vote with sentence C (PARTICIPANT_ROOM_PROMPT) and the line naming who
    the message calls (``core.room_people``), between the signals and the
    note. Read only with ``participant``; off, today's prompt.

    ``ctx`` is the flat agent-loop context (an
    :class:`core.agent.context_adapter.AgentLoopContext` in production,
    or a hand-wired test fake). Pre-D.2 wave 1 the getattr lookups
    below returned empty strings because the upstream ``TurnContext``
    didn't carry these named fields; the cadence prompt landed at the
    LLM with empty user/history slots and produced near-random
    classifications. The adapter renders real strings now.

    Falls back to a neutral default on LLM error or unparseable response.
    """
    user_text = getattr(ctx, "user_text", "")
    recent = getattr(ctx, "recent_history_text", "")
    template = ((PARTICIPANT_ROOM_PROMPT if room else PARTICIPANT_SHAPE_PROMPT)
                if participant else SHAPE_RESPONSE_PROMPT)
    prompt = template.replace("<<USER_TEXT>>", user_text).replace(
        "<<RECENT_HISTORY>>", recent or "(no prior context)"
    )
    if participant:
        extra_lines = [s for s in (str(signals or "").strip(), str(names or "").strip(),
                                   str(note or "").strip()) if s]
        if extra_lines:
            prompt += "\n" + "\n".join(extra_lines) + "\n"
    # F4 (companion.subtext_read): an over-explain read in observation_text.
    # The reply consumes observation_text, so this is how "doesn't over-explain
    # to an expert" reaches the actual response. Its first half ("name the
    # UNSPOKEN thing ... deflection, a bid for reassurance, testing") went on
    # 10-13 (the owner, the presence slice): it made her tense with a new
    # friend, and the first look reads how people seem in her own voice.
    try:
        from core.turn.companion_flags import faculty_enabled
        if faculty_enabled("subtext_read"):
            prompt += (
                "\n\nAlso, in observation_text specifically, "
                "gauge their expertise on the topic: if they clearly "
                "already know the fundamentals, the reply should still fully "
                "ANSWER the question but pitch it to their level — skip the "
                "101 preamble and get to the substantive part. Calibrating "
                "depth means answering more sharply, NEVER withholding the "
                "answer or telling them they already know it."
            )
    except Exception:
        pass
    try:
        raw = await llm(prompt)
    except Exception as exc:
        logger.warning("cadence.shape_response: LLM call failed: %s", exc)
        return _with_directive(_DEFAULT_CONFIG)

    try:
        body = raw.strip()
        if body.startswith("```"):
            body = body.strip("`")
            if body.startswith("json\n"):
                body = body[5:]
        first = body.find("{")
        last = body.rfind("}")
        if first == -1 or last == -1:
            raise ValueError("no JSON object in LLM response")
        data = json.loads(body[first:last + 1])
    except Exception as exc:
        logger.warning("cadence.shape_response: invalid JSON from LLM: %s; raw=%r", exc, raw[:200])
        return _with_directive(_DEFAULT_CONFIG)

    should_respond = bool(data.get("should_respond", True))
    decision = str(data.get("decision") or "").strip().lower()
    if decision in DECISIONS:
        should_respond = decision == "answer"
    else:
        decision = "answer" if should_respond else "stay_quiet"
    cfg = CadenceConfig(
        should_respond=should_respond,
        register=str(data.get("register", "neutral")),
        length=str(data.get("length", "medium")),
        phase=str(data.get("phase", "settled_conversation")),
        target_beats=int(data.get("target_beats", 1)) or 1,
        observation_text=str(data.get("observation_text", "")),
        directive="",
        emotional_context=str(data.get("emotional_context", "neutral") or "neutral"),
        decision=decision,
        reason=" ".join(str(data.get("reason") or "").split()),
    )
    return _with_directive(cfg)


def _with_directive(cfg: CadenceConfig) -> CadenceConfig:
    return CadenceConfig(
        should_respond=cfg.should_respond,
        register=cfg.register,
        length=cfg.length,
        phase=cfg.phase,
        target_beats=cfg.target_beats,
        observation_text=cfg.observation_text,
        directive=render_cadence_directive(cfg),
        emotional_context=cfg.emotional_context,
        decision=cfg.decision,
        reason=cfg.reason,
    )


# ── the room's first read, carried into the full turn (Batch 8, task 2b) ──
# Ruling T2b-1: when a room's first read says "answer", its vote rides on the
# turn (metadata.extra["first_pass"]) so the runner does not ask again — the
# read's shape and length budget are the turn's.
_FIRST_PASS_FIELDS = ("decision", "reason", "register", "length", "phase",
                      "target_beats", "observation_text", "emotional_context")


def first_pass_fields(cfg: CadenceConfig) -> dict[str, Any]:
    """The vote as plain values, to ride on a turn's metadata."""
    return {name: getattr(cfg, name) for name in _FIRST_PASS_FIELDS}


def config_from_first_pass(fields: Any) -> CadenceConfig | None:
    """The vote back from a turn's metadata, with its directive; None when
    there is nothing usable (the runner then asks as usual)."""
    if not isinstance(fields, dict):
        return None
    try:
        decision = str(fields.get("decision") or "answer").strip().lower()
        if decision not in DECISIONS:
            decision = "answer"
        cfg = CadenceConfig(
            should_respond=decision == "answer",
            register=str(fields.get("register") or "neutral"),
            length=str(fields.get("length") or "medium"),
            phase=str(fields.get("phase") or "settled_conversation"),
            target_beats=int(fields.get("target_beats") or 1) or 1,
            observation_text=str(fields.get("observation_text") or ""),
            directive="",
            emotional_context=str(fields.get("emotional_context") or "neutral"),
            decision=decision,
            reason=" ".join(str(fields.get("reason") or "").split()),
        )
    except Exception:
        return None
    return _with_directive(cfg)


# ── the signals she weighs, never requirements (Batch 8, task 2b) ─────────
# Ruling T2b-3: a DM, an @mention, a reply to one of her messages (with that
# message's words and time) and her name. The doors' intakes note them (with
# companion.she_decides on); her decision calls — a room's first read and the
# full turn's quick vote — see them as ONE line. Never a condition in code:
# nothing here decides whether she answers.
_HER_NAME = re.compile(r"(?<![\w@])(?:palli|polly)(?![\w])", re.IGNORECASE)
_SIGNAL_DOORS = ("discord_text", "telegram_text")
_QUOTE_CHARS = 300


def names_her(text: Any) -> bool:
    """"PaLLi" or "Polly" as a whole word, either case — not inside another
    word, and not her own @handle."""
    return bool(_HER_NAME.search(str(text or "")))


def signals_of(turn: Any) -> dict[str, Any]:
    """The signals a chat door noted on this turn; {} for any other door
    (the dashboard, voice) — they are not rooms and carry no line."""
    if str(getattr(turn, "surface", "") or "") not in _SIGNAL_DOORS:
        return {}
    md = getattr(turn, "metadata", None)
    if md is None:
        return {}
    extra = getattr(md, "extra", None)
    extra = extra if isinstance(extra, dict) else {}
    reply = extra.get("reply_to")
    out = {
        "is_dm": bool(getattr(md, "is_dm", False)),
        "is_mention": bool(getattr(md, "is_mention", False)),
        "is_reply_to_her": bool(getattr(md, "is_reply_to_bot", False)),
        "reply_to": dict(reply) if isinstance(reply, dict) else None,
        "names_her": bool(extra.get("names_her", False)),
    }
    # Step 2, part 7 (A6): who else it @mentions; the key only when someone
    # is, so every other turn's signals stay as they were.
    others = _names(extra.get("mentions_others"))
    if others:
        out["mentions_others"] = others
    return out


def _names(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    return [n for n in dict.fromkeys(" ".join(str(x or "").split()) for x in raw) if n]


def merge_signals(many: Any) -> dict[str, Any]:
    """Several messages read as one (a burst, or what waited): any one of
    them can carry a signal; the first reply found is the one named."""
    items = [s for s in (many or ()) if s]
    if not items:
        return {}
    reply = next((s.get("reply_to") for s in items if s.get("is_reply_to_her")), None) \
        or next((s.get("reply_to") for s in items if s.get("reply_to")), None)
    merged = {
        "is_dm": all(bool(s.get("is_dm")) for s in items),
        "is_mention": any(bool(s.get("is_mention")) for s in items),
        "is_reply_to_her": any(bool(s.get("is_reply_to_her")) for s in items),
        "reply_to": reply,
        "names_her": any(bool(s.get("names_her")) for s in items),
    }
    others = _names([n for s in items for n in (s.get("mentions_others") or [])])
    if others:
        merged["mentions_others"] = others
    return merged


def _when(iso: Any) -> str:
    try:
        from datetime import datetime

        moment = datetime.fromisoformat(str(iso))
        if moment.tzinfo is not None:
            moment = moment.astimezone()
        return f"{moment:%b} {moment.day}, {moment:%H:%M}"
    except Exception:
        return ""


# The person words of the line (R14, Draft 5): "her" for the quick vote, a
# judge reading about her (today's approved line, byte for byte), and "me" for
# her own first look, which reads the same facts in her first person.
_PERSON = {
    "her": {"obj": "her", "poss": "her", "subj": "she"},
    "me": {"obj": "me", "poss": "my", "subj": "I"},
}


def render_signals(signals: Any, *, voice: str = "her") -> str:
    """The one context line for the decision calls; "" when there is none.
    ``voice="me"`` is the same line in her own first person (the first look)."""
    if not isinstance(signals, dict) or not signals:
        return ""
    p = _PERSON["me" if voice == "me" else "her"]
    parts = [f"a direct message to {p['obj']}" if signals.get("is_dm") else "said in a shared room"]
    if signals.get("is_mention"):
        parts.append(f"it @mentions {p['obj']}")
    others = ", ".join(_names(signals.get("mentions_others"))[:5])
    if others:
        parts.append(f"it also @mentions {others}" if signals.get("is_mention")
                     else f"it @mentions someone else ({others}), not {p['obj']}")
    reply = signals.get("reply_to") if isinstance(signals.get("reply_to"), dict) else None
    if signals.get("is_reply_to_her"):
        words = " ".join(str((reply or {}).get("text") or "").split())[:_QUOTE_CHARS]
        when = _when((reply or {}).get("at"))
        if words:
            parts.append(f"it replies to {p['poss']} message{' from ' + when if when else ''}: \"{words}\"")
        else:
            parts.append(f"it replies to one of {p['poss']} messages")
    elif reply is not None and reply.get("to_her") is None:
        parts.append(f"it replies to an earlier message {p['subj']} can't see")
    elif reply is not None:
        parts.append("it replies to someone else's message")
    if signals.get("names_her"):
        parts.append(f"it says {p['poss']} name")
    return ("About the latest message (signals to weigh, never rules): "
            + "; ".join(parts) + ".")


JudgmentMode = Literal["cancel", "continue", "sidebar", "ack_silent"]


@dataclass(frozen=True)
class JudgmentDecision:
    mode: JudgmentMode
    acknowledgment: str  # may be empty


JUDGMENT_PROMPT = """\
PaLLi is mid-task. New user message just arrived. Decide what to do.

In-flight task summary:
<<IN_FLIGHT>>

New user message (or grouped batch):
<<NEW_INPUT>>

Modes:
- cancel: new input changes the goal (e.g., "stop", "actually do X
  instead", "nevermind"). Abort the task and start fresh.
- continue: in-flight task should keep running. Optionally emit a
  brief acknowledgment ("still on it, give me a sec").
- sidebar: new input is unrelated to the task — a sidebar question
  or chitchat. Emit a quick chat reply; task continues undisturbed.
- ack_silent: heard but no response warranted (a casual "ok" or
  "thanks" while the task runs).

Return ONLY a JSON object:
{ "decision": "<one of: cancel | continue | sidebar | ack_silent>",
  "acknowledgment": "<short text PaLLi should say, or empty>" }
"""


_VALID_MODES = {"cancel", "continue", "sidebar", "ack_silent"}
_DEFAULT_JUDGMENT = JudgmentDecision(mode="ack_silent", acknowledgment="")


async def judgment_pass(
    *,
    in_flight_summary: str,
    new_input: str,
    llm: CadenceLLM,
) -> JudgmentDecision:
    """During-flight judgment. Returns mode + optional acknowledgment.

    Falls back to ack_silent on any error / unparseable response —
    that's the safest default (don't disturb the task).
    """
    prompt = JUDGMENT_PROMPT.replace("<<IN_FLIGHT>>", in_flight_summary).replace(
        "<<NEW_INPUT>>", new_input
    )
    try:
        raw = await llm(prompt)
    except Exception as exc:
        logger.warning("cadence.judgment_pass: LLM error: %s", exc)
        return _DEFAULT_JUDGMENT

    try:
        body = raw.strip()
        if body.startswith("```"):
            body = body.strip("`")
            if body.startswith("json\n"):
                body = body[5:]
        first = body.find("{")
        last = body.rfind("}")
        if first == -1 or last == -1:
            raise ValueError("no JSON object")
        data = json.loads(body[first:last + 1])
    except Exception as exc:
        logger.warning("cadence.judgment_pass: invalid JSON: %s; raw=%r", exc, raw[:200])
        return _DEFAULT_JUDGMENT

    mode = data.get("decision", "")
    if mode not in _VALID_MODES:
        logger.warning("cadence.judgment_pass: invalid mode %r; defaulting to ack_silent", mode)
        return _DEFAULT_JUDGMENT

    return JudgmentDecision(
        mode=mode,
        acknowledgment=str(data.get("acknowledgment", "")),
    )


import asyncio as _asyncio
from typing import Awaitable as _Awaitable


_OnChunk = Callable[[str], _Awaitable[None]]


class _BaseChunker:
    def __init__(self, on_chunk: _OnChunk) -> None:
        self._on_chunk = on_chunk
        self._buffer: list[str] = []
        self._all_text: list[str] = []

    async def feed(self, text: str) -> None:
        raise NotImplementedError

    async def flush(self) -> None:
        if self._buffer:
            await self._emit("".join(self._buffer))
            self._buffer = []

    async def _emit(self, text: str) -> None:
        if not text:
            return
        self._all_text.append(text)
        await self._on_chunk(text)

    async def iteration_boundary(self) -> None:
        """A tool round-trip sits between the text before and the text after.

        The runner reuses ONE chunker for the whole ReAct turn, so narration
        from model call N and the opening words of model call N+1 used to
        land back-to-back in the same buffer and in ``total_text``'s bare
        join: "...instead of guessing.Source is right there — let me read
        it.It's a puzzle..." (2026-08-18: 15% of her Discord messages carried
        the seam; every worst case was a multi-tool turn). Guarantee a
        paragraph break instead. Idempotent; a no-op when nothing has been
        said yet or the last text already ends a paragraph. Presentation
        seam — deterministic on purpose.
        """
        if self._buffer:
            joined = "".join(self._buffer)
            sep = _paragraph_gap_after(joined)
            if sep:
                self._buffer.append(sep)
        elif self._all_text:
            # Already emitted (delivered as its own chunk); only the persisted
            # reply text needs the gap — nothing new to send.
            sep = _paragraph_gap_after(self._all_text[-1])
            if sep:
                self._all_text.append(sep)

    @property
    def total_text(self) -> str:
        return "".join(self._all_text)


def _paragraph_gap_after(text: str) -> str:
    """The whitespace needed after ``text`` to close a paragraph: "" if it
    already ends with a blank line, "\n" if it ends with a single newline,
    else "\n\n"."""
    if not text or text.endswith("\n\n"):
        return ""
    if text.endswith("\n"):
        return "\n"
    return "\n\n"


class SingleFlowChunker(_BaseChunker):
    """Emit on paragraph or long-sentence boundaries (Discord typing-feel)."""
    def __init__(self, on_chunk: _OnChunk, *, min_chars: int = 80, max_chars: int = 1500) -> None:
        super().__init__(on_chunk)
        self._min = min_chars
        self._max = max_chars

    async def feed(self, text: str) -> None:
        self._buffer.append(text)
        joined = "".join(self._buffer)
        if "\n\n" in joined and len(joined) >= self._min:
            head, tail = joined.split("\n\n", 1)
            await self._emit(head + "\n\n")
            self._buffer = [tail]
            return
        if len(joined) >= self._max:
            for sep in [". ", "? ", "! ", "\n"]:
                idx = joined.rfind(sep, 0, self._max)
                if idx > self._min:
                    await self._emit(joined[: idx + len(sep)])
                    self._buffer = [joined[idx + len(sep) :]]
                    return


class CadencedBeatChunker(_BaseChunker):
    """Emit one Discord message per beat (target_beats), with brief pauses."""
    def __init__(
        self,
        on_chunk: _OnChunk,
        *,
        target_beats: int,
        beat_pause_ms: int = 1500,
        min_beat_chars: int = 60,
    ) -> None:
        super().__init__(on_chunk)
        self._target_beats = max(2, target_beats)
        self._beat_pause = beat_pause_ms / 1000.0
        self._min_beat = min_beat_chars
        self._beats_emitted = 0

    async def feed(self, text: str) -> None:
        self._buffer.append(text)
        joined = "".join(self._buffer)
        if "\n\n" in joined and len(joined) >= self._min_beat:
            head, tail = joined.split("\n\n", 1)
            await self._emit_beat(head)
            # Beats reach Discord as separate messages, but the persisted
            # reply is their join — keep the paragraph gap there (2026-08-18).
            gap = _paragraph_gap_after(head)
            if gap:
                self._all_text.append(gap)
            self._buffer = [tail]

    async def _emit_beat(self, text: str) -> None:
        await self._emit(text)
        self._beats_emitted += 1
        if self._beats_emitted < self._target_beats:
            await _asyncio.sleep(self._beat_pause)

    async def flush(self) -> None:
        if self._buffer:
            await self._emit_beat("".join(self._buffer))
            self._buffer = []


class VoiceFlowChunker(_BaseChunker):
    """Voice flow: emit text as it arrives. Voice TTS handles pauses naturally."""
    async def feed(self, text: str) -> None:
        await self._emit(text)


def build_chunker(
    *,
    target_beats: int,
    surface: str,
    on_chunk: _OnChunk,
) -> _BaseChunker:
    """Factory: pick the right chunker for surface + target_beats."""
    if surface in ("voice_local", "discord_voice"):
        return VoiceFlowChunker(on_chunk)
    if target_beats >= 2:
        return CadencedBeatChunker(on_chunk, target_beats=target_beats)
    return SingleFlowChunker(on_chunk)

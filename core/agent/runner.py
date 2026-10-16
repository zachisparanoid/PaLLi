from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from ai import call_ledger as _call_ledger
from ai import memory_reach_index as _memory_reach_index
from ai import memory_reach_search as _memory_reach_search
from ai import turn_read as _turn_read
from core.agent import cadence as _cadence
from core.agent import first_look as _first_look
from core.agent import reply_governor as _reply_governor
from core.agent import observability as _obs
from core.agent import steering as _steering
from core.agent import duplex as _dx
from core.agent import quiet_choice as _qc
from core.agent import message_times as _mt
from core.agent import room_card as _room_card
from core.agent import room_tail as _room_tail
from core.agent import voice_note_final as _voice_note_final
from core.turn import history_prefetch as _history_prefetch
from core.agent.speakers import (
    OWNER_MARK, is_shared_room, label_names, label_speakers_for_llm, names_shown,
    normalize_speaker, speaker_of,
)
from core import reply_sources as _reply_sources
from core import room_notes as _room_notes
from core import voice_replies as _voice_replies
from core.agent.session import (
    AgentMessage, Session, TextBlock, diet_for_llm, ensure_user_tail, fold_interims_for_thinking,
    mark_undelivered_for_llm, mark_working_notes_for_llm,
    normalize_for_llm, strip_working_note_mark, window_for_llm,
    DIET_AUDIENCES, RECEIPT_MARK, ShownThisTurn, shown_this_turn,
)
from core.owner_gate import is_guest_line, is_guest_turn
from core.session_keys import guest_run_key, session_key_for


logger = logging.getLogger(__name__)


async def _open_session_with_user_message(
    session_key: str, root: Path, user_text: str, *, speaker: Any = None,
) -> Session:
    """Open the session and record the user's message as one shielded unit.

    Burst-drop fix (2026-07-07): called via ``asyncio.shield`` from run_turn
    so a barge-in cancel (bots/discord/client.py on_message/on_typing) landing
    mid-open/append still lets the transcript write complete — the turn dies,
    the user's words don't. Module-level on purpose: run_turn's deferred
    ``from core.agent.session import AgentMessage, TextBlock, ...`` makes
    those names function-local there, so constructing the AgentMessage inside
    run_turn before that import line would raise UnboundLocalError.
    """
    session = await Session.open_or_create(session_key, root=root)
    await session.append(
        AgentMessage(role="user", content=[TextBlock(text=user_text)],
                     speaker=normalize_speaker(speaker))
    )
    return session


async def _open_session_with_exchange(
    session_key: str, root: Path, user_text: str, assistant_text: str, *, speaker: Any = None,
) -> Session:
    """Transcribe one steer exchange post-hoc: his coalesced fragment(s),
    then the judged interim reply that actually reached him — the same order
    and grammar the boundary drain writes mid-run (see the drain in
    run_turn). Used by the post-run sweep and the deadline fallback for
    REPLIED bundles whose run ended before any model boundary could drain
    them (the 2026-07-28 api-key incident: such bundles were dropped, so
    Discord and the DB carried the exchange while her transcript never did).
    Post-run only — the run is over, so no open tool round-trip exists for
    this append to violate."""
    session = await Session.open_or_create(session_key, root=root)
    text = str(user_text or "").strip()
    if text:
        await session.append(
            AgentMessage(role="user", content=[TextBlock(text=text)],
                         speaker=normalize_speaker(speaker))
        )
    reply = str(assistant_text or "").strip()
    if reply:
        await session.append(
            AgentMessage(role="assistant", content=[TextBlock(text=reply)])
        )
    return session


def _resolve_max_iterations() -> int:
    raw = os.environ.get("MAX_REACT_ITERATIONS")
    try:
        n = int(raw) if raw else 50
        return n if n > 0 else 50
    except ValueError:
        return 50


def _resolve_history_max_messages(surface: str | None = None) -> int:
    """LLM-payload history window, in messages. 0 or negative disables.

    Default 120: the live Discord channel that motivated this averaged
    ~180 tokens/message, so 120 ≈ ~20k prompt tokens — plenty of in-channel
    continuity without replaying the entire channel archive on every turn
    (long-term memory lives in recall/reflect, not in this transcript).

    ``surface="reminder_fire"`` has its own cap,
    ``PALLI_AGENT_HISTORY_MAX_MESSAGES_REMINDER`` (default 40; Batch 4 task 1,
    2026-09-28): a fire's session holds nothing but her earlier fires, and
    under the chat cap every reminder replayed hundreds of them. The chat it
    goes into reaches the fire through the turn content instead
    (core/reminders/scheduler.py ``_recent_conversation_lines``).
    """
    if surface == "reminder_fire":
        name, default = "PALLI_AGENT_HISTORY_MAX_MESSAGES_REMINDER", 40
    else:
        name, default = "PALLI_AGENT_HISTORY_MAX_MESSAGES", 120
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _resolve_history_step() -> int:
    """How many messages the history window's START moves at a time.

    Default 50 (Batch 2, 2026-09-27): a start that slides one message per
    turn changes the payload's history prefix every turn, so the provider's
    prompt cache never hits it; a start that moves every 50 messages keeps
    the prefix byte-identical between steps. 1 or less = the old slide.
    """
    raw = os.environ.get("PALLI_AGENT_HISTORY_STEP")
    try:
        n = int(raw) if raw else 50
    except ValueError:
        return 50
    return n if n > 1 else 1


# DEFECT_RECORD §18: appended to the trailing note when cadence voted silence
# on a user-facing turn. Meaning stays hers; the code only stops forcing.
SILENCE_NOTE = ("[They may have asked you not to respond. If that is what they meant, "
                "the correct reply is nothing at all — no words, no emoji, no "
                "acknowledgment; silence IS the reply. If they actually asked a "
                "question, answer it.]")

# Second read F3 (2026-10-04) — DEFECT_RECORD §57: a per-run brake for tool
# rounds that are not moving. When the same round (the same tools, each with
# the same answer) comes back SAME_ANSWER_BRAKE times in a row, the tool
# rounds are over for this reply: the next step is a reply. Before this, a
# model that ignored the taught finish (the 10-01 voice-note loop, 51 calls)
# ran to the 50-step safety limit — about fifty full reply calls at ~60k
# prompt tokens each — and ended with nothing; the loop detector's circuit
# breaker needs 30 identical calls and only adds a line of text.
# Why three: two identical answers in a row are often real work — a retry
# after a hiccup, or a second look to confirm a status — so the brake must
# not fire on the second. A third identical answer in a row means two
# retries taught her nothing new; each round past that costs a full reply
# call and cannot change the answer. Different tools, or the same tool with
# a different answer, reset the count: ordinary multi-tool work never meets
# it. A cost floor, not a messaging decision — the reply step keeps her
# quiet choice where it is offered, and what she says is hers.
SAME_ANSWER_BRAKE = 3
BRAKE_NOTE = ("[No more tools in this reply: {tools} gave the same answer {n} times in a "
              "row, so another call won't change it. Write your reply now with what you "
              "have{quiet}.]")
BRAKE_QUIET_TAIL = " — or stay quiet, if that is what fits"
# A call made anyway on the reply step is not run; the run ends there.
BRAKE_NOT_RUN = ("Not run — the tool rounds are over for this reply (the same answer kept "
                 "coming back).")


@dataclass(frozen=True)
class RunResult:
    responded: bool
    response_text: str
    iterations: int = 0
    error: str | None = None
    terminated_by: str = "natural"     # Step 27: "stopped" (cooperative stop) / "paused" ("hold on") ended the loop
    # 2026-05 (D.2 wave 1): attachments produced by tool dispatch within
    # the ReAct loop — the paths deliver_artifact handed over on the turn's
    # ToolContext (week 2 task 2). Mirrors the equivalent slot on
    # CadencedReply / TurnResult so dispatch can pass them through to
    # surface adapters (Discord, dashboard) without losing them.
    attachments: tuple[str, ...] = field(default_factory=tuple)
    # Bug 5 (2026-07-04): recall's attachment analysis, surfaced so dispatch
    # can persist the RICH user row (image/text analysis baked into content)
    # instead of only the "[image attached]" placeholder. Empty on non-
    # attachment turns. Lets future turns remember what an image showed.
    image_descriptions: tuple[str, ...] = field(default_factory=tuple)
    text_attachment_contents: tuple[str, ...] = field(default_factory=tuple)
    # Batch 8, task 2a (B8-2, ruling T2-2): her reply chose to stay quiet (the
    # stay_quiet control call). Binding: response_text is "" and nothing is
    # sent; ``responded`` is True only for files she delivered earlier in
    # the run. ``quiet_reason`` is her one line, for her record.
    quiet: bool = False
    quiet_reason: str = ""
    # Batch 8, task 10 (B8-5, ruling T10-3): the voice messages her voice_note
    # tool made this run (OGG/Opus bytes on ``ToolContext.voice_notes``), for
    # the door to send with her reply. A quiet run keeps them: a note she made
    # is a delivery, like a file (final fix wave, ruling FW-3).
    voice_notes: tuple[bytes, ...] = ()


@dataclass
class RunnerDeps:
    """All external dependencies the runner needs.

    Pulled out for testability — tests pass fakes; production wiring
    in core.agent.dispatch builds the real deps.
    """
    build_turn_context: Callable[[Any], Awaitable[Any]]
    cadence_llm: _cadence.CadenceLLM
    sessions_root: Path
    provider: str
    model: str
    client: Any
    tool_registry: Any | None
    polling_tools: set[str]
    apply_reflection: Callable[..., Awaitable[Any]] | None = None
    # 2026-09-02: extra keyword args for llm_client.call, filled by dispatch per
    # active provider (e.g. extra_body={"reasoning_effort": "none"} for a local
    # Ollama brain through its OpenAI-style endpoint). Empty = byte-identical.
    call_kwargs: dict[str, Any] = field(default_factory=dict)
    # The reply-length governor (2026-08-22): conversation turns hand the
    # cadence vote to the adapter (context var) and the trailing shape note
    # to the LLM call. Dispatch sets False for her workbench/forge sessions.
    govern_replies: bool = True
    # D.3 wave 2 step 2: explicit is_owner flag resolved by dispatch.py
    # against ``local_mode.get_local_owner_refs()``. Pre-Wave-2 the runner
    # read ``getattr(ctx, "is_owner", True)`` which defaulted to True for
    # every surface — combined with ``local_shell``'s ``owner_only=True``
    # gate, that meant any non-owner surface (Discord guild channel,
    # voice with non-owner speaker) could invoke local_shell in ReAct
    # mode. Security fix: dispatch resolves once, runner uses what
    # dispatch told it. None means "fall back to ctx.is_owner" so
    # legacy test wiring that constructs RunnerDeps without this slot
    # keeps working.
    is_owner: bool | None = None
    # Fix 6 (the owner, 10-12): the recent-conversation fetch, run early so
    # the quick length vote goes beside recall. None (tests' fakes) = the
    # old order, vote after recall.
    fetch_recent_history: Callable[[Any], Awaitable[Any]] | None = None
    # One first look (R14, T5): the look's call (prompt -> answer), built by
    # dispatch (first_look.build_llm); tests hand a fake. None = the look
    # builds its own when it runs.
    first_look_llm: Callable[[str], Awaitable[str]] | None = None
    # Step 2, part 5 (add-on b, 2026-10-08): the owner's account ids
    # (local_mode.get_local_owner_refs), read by dispatch beside the owner
    # check on the owner's turns, for the owner's-accounts section. None: no
    # section (and the run never looks them up itself).
    owner_refs: dict[str, Any] | None = None
    # Own-work spec §4e (2026-07-28): a PER-RUN round ceiling. The global
    # MAX_REACT_ITERATIONS env var could not carry her per-session budget:
    # a workbench session and his ordinary turns run concurrently by design
    # (spec §4b), so flipping the process-wide value would cap HIS turn too.
    # None keeps the env-resolved default for every other caller.
    max_iterations: int | None = None
    # Batch 8, task 1 (B8-1, ruling R6): who reads the room's past turns —
    # "owner" | "collaborator" | "stranger" (core.agent.session.DIET_AUDIENCES),
    # resolved by dispatch beside is_owner. None (legacy wiring) means the
    # owner for an owner turn and a stranger for a guest turn; a guest turn
    # is never read as the owner, whatever this says.
    audience: str | None = None


# Post-turn work that must not block the reply and must survive a barge-in
# cancelling run_turn itself: reflection and prerecorded-attachment enrich run
# as detached, strongly-referenced tasks (same retention pattern as dispatch's
# _BACKGROUND_PERSIST_TASKS). Detached tasks outlive their spawner, so a
# cancel landing mid-turn no longer discards them.
_DETACHED_POST_TASKS: set[asyncio.Task] = set()

# TurnDecision.emotional_context Literal vocabulary (core/turn/types.py) —
# also exactly the nudge vocabulary ai/relationship_momentum.py matches on.
_MOMENTUM_TAGS = {
    "deep_intimacy", "vulnerable_close", "venting", "playful_banter",
    "frustrated", "casual_greeting", "neutral",
}


def _strip_imitated_stamps(response: Any) -> Any:
    """DEFECT_RECORD §20 (2026-09-02): an imitated working-note stamp never
    enters her record or the chunker. The payload stamp is the system's;
    if the model wrote one, it is stripped here at the model boundary. A
    block that was nothing but the stamp is dropped. Identity otherwise."""
    blocks = list(getattr(response, "text_blocks", None) or [])
    cleaned = [strip_working_note_mark(t) for t in blocks]
    if cleaned == blocks:
        return response
    logger.info("runner: stripped an imitated working-note stamp from the model's text (DEFECT_RECORD §20)")
    return replace(response, text_blocks=[t for t in cleaned if t.strip()])


def _look_said_answer(look: Any, first_pass: Any, md: Any) -> bool:
    """Her first look said answer: the runner's own, or the room pass's carry
    when a look really ran there (``first_look.carry_fields`` marks it)."""
    if look is not None:
        return str(getattr(look, "decision", "")) == "answer"
    if first_pass is None or not _first_look.enabled():
        return False
    extra = getattr(md, "extra", None)
    carry = extra.get("first_pass") if isinstance(extra, dict) else None
    return (isinstance(carry, dict) and carry.get("look") is True
            and str(getattr(first_pass, "decision", "")) == "answer")


def _times_for_filter(messages: list[AgentMessage], turn: Any) -> frozenset[str]:
    """The time labels this call shows her: the lines' (when times are on) and
    the turn's own, the minute her "Current Time" names."""
    times = _mt.times_shown(messages)
    try:
        times |= {_mt.stamp_label(turn.received_at)}
    except Exception:                       # no time on the turn: the lines' only
        pass
    return times


def _spawn_detached(coro: Awaitable[Any], what: str) -> None:
    task = asyncio.create_task(coro)
    _DETACHED_POST_TASKS.add(task)

    def _done(t: asyncio.Task) -> None:
        _DETACHED_POST_TASKS.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.warning("runner: detached %s failed: %s", what, t.exception())

    task.add_done_callback(_done)


async def flush_detached_tasks() -> None:
    """Await all in-flight detached post-turn tasks (test helper)."""
    pending = [t for t in _DETACHED_POST_TASKS if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# Phase B (correction 4): the payload tail after a boundary drained an
# exchange she already answered (a sidecar's reply, a judged interim) —
# deterministic presentation, never persisted.
_CONTINUATION_NOTE = ("[their words above arrived while you worked and you already "
                      "answered them — carry on with the task]")


def _derive_session_key(turn: Any) -> str:
    # Step 27 Phase B (spec §1): a sidecar turn runs on a CHILD session key
    # carried in metadata.extra — the parent transcript is never its writer.
    md = getattr(turn, "metadata", None)
    _extra = getattr(md, "extra", None) if md is not None else None
    if isinstance(_extra, dict) and str(_extra.get("session_key_override") or "").strip():
        return str(_extra["session_key_override"]).strip()
    surface = getattr(turn, "surface", "unknown")
    channel_id = getattr(md, "channel_id", None) if md else None
    # Batch 8, task 1 (ruling R7): the mapping lives in core.session_keys —
    # one transcript per chat room (Discord channel or DM, Telegram chat),
    # keyed on its id; the other surfaces on the owner id.
    # ai/callbacks_lookup.session_key_for delegates there too, so the two
    # stay byte-equal by construction (pinned by tests/ai/test_callbacks_lookup.py).
    return session_key_for(surface, channel_id, getattr(turn, "owner_id", 0))


def _run_state_key(turn: Any, transcript_key: str) -> str:
    """Batch 8, task 1 (B8-1, ruling R3): the key this run's STATE lives
    under — phase, tool count, now-doing, stop and pause requests, queued
    steers, task slot, presence. The owner's run uses the transcript key, as
    always. A guest's run gets ``{room_key}:guest:{their id}``: in a shared
    room the transcript is one conversation, but a guest's run must never
    drain, clear or take the owner's run state. Its words and her reply
    still go to the room's file (``transcript_key``)."""
    if is_guest_turn(turn):
        return guest_run_key(transcript_key, getattr(turn, "owner_id", 0))
    return transcript_key


def _payload_audience(deps: Any, turn: Any) -> str:
    """Who reads the room's past turns (ruling R2): what dispatch said, but
    a guest turn is never read as the owner and an unknown value is a
    stranger — fail closed."""
    guest = is_guest_turn(turn)
    raw = str(getattr(deps, "audience", None) or "").strip().lower()
    if raw in DIET_AUDIENCES and not (guest and raw == "owner"):
        return raw
    if raw:
        return "stranger"
    return "stranger" if guest else "owner"


# Batch 8, final fix wave (ruling FW-1 — the whole-branch review's C1). A turn
# recorded on arrival — the owner's burst, or a guest's line in a room read
# with companion.she_decides on — opens the room file seconds later, and the
# room file is shared: by then other writers' rows can sit AFTER the turn's
# own (the owner's tool call and its result, her quiet choice for the owner,
# another guest's line). The run must not take its last loaded row for its
# own. The helpers below find the turn's own rows by their words and build
# the payload's view of the loaded rows from them. Payload-only: the file and
# the session's own list are never touched.
_ATTACHMENT_PLACEHOLDERS = ("[image attached]", "[file attached]")


def _row_words(m: AgentMessage) -> str:
    return "".join(b.text for b in m.content if isinstance(b, TextBlock))


def _match_turn_rows(messages: list[AgentMessage], want: str) -> list[int]:
    rest = want
    found: list[int] = []
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role != "user":
            continue
        text = _row_words(m)
        if not text.strip():
            continue
        forms = [text]
        if text.startswith(("[image attached", "[file attached")) and text not in _ATTACHMENT_PLACEHOLDERS:
            # A burst's bare image or file line, already enriched with what
            # recall saw in it (the detached enrich, above).
            forms += list(_ATTACHMENT_PLACEHOLDERS)
        for form in forms:
            if rest == form:
                found.append(i)
                return sorted(found)
            if rest.endswith("\n" + form):
                found.append(i)
                rest = rest[: -(len(form) + 1)]
                break
    return []


def _turn_rows(messages: list[AgentMessage], text: str) -> list[int]:
    """The indices of this turn's own user rows in ``messages``, oldest
    first; [] when they are not all there.

    A turn's words are one row per line the door coalesced (a burst's
    fragments, the lines a room read took together), joined with "\\n" in
    the turn. Searching back from the end, each user row whose words are the
    next line back is the turn's, and any other row is someone else's and is
    skipped. The stored words can differ from the turn's only by the
    transcript's redaction, so the redacted form is tried too."""
    want = str(text or "")
    if not want.strip():
        return []
    forms = [want]
    try:
        from core.secrets_redaction import redact

        redacted = redact(want)
        if redacted != want:
            forms.append(redacted)
    except Exception:
        pass
    for form in forms:
        found = _match_turn_rows(messages, form)
        if found:
            return found
    return []


def _another_speaker_after(messages: list[AgentMessage], own: list[int], loaded: int) -> bool:
    """Is there a line from another speaker after the first of the turn's own
    rows (ruling FW-17)? Read from the row's speaker record when it has one
    (2026-10-08), by the guest tag on rows written before."""
    mine = set(own)

    def _other(m: AgentMessage) -> bool:
        record = getattr(m, "speaker", None)
        if record:
            return record.get("kind") in ("guest", "hidden")
        return is_guest_line(_row_words(m))

    return any(
        i not in mine and messages[i].role == "user" and _other(messages[i])
        for i in range(own[0] + 1, loaded)
    )


def _payload_rows(
    messages: list[AgentMessage], *, loaded: int, own: list[int], audience: str,
    own_text: str,
) -> tuple[list[AgentMessage], int]:
    """The rows the payload is built from, and where the run's verbatim
    region starts (the diet's ``keep_from``).

    ``messages[:loaded]`` is what the run found in the room file (with its
    own row, when the runner appended it); ``messages[loaded:]`` is what the
    run itself appended since, kept verbatim for every audience. ``own`` is
    the turn's own rows among the loaded ones.

    - The owner: today's boundary (the last loaded row on). Only when a
      guest's line sits after the turn's own rows are they presented last,
      so that line is never the owner run's final user message. Anything
      else is the identity (ruling FW-17): his own exchanges after his words
      — steering's recovery writes his words, then her holding reply, and
      re-dispatches them already recorded — stay exactly as before.
    - A guest (a stranger or a collaborator): every loaded row goes through
      the audience diet — none of another writer's rows reaches the payload
      verbatim — and the turn's own rows come last. When they cannot be
      found, the turn's own words are presented last instead (fail closed:
      never someone else's message as the one to answer)."""
    loaded = max(0, min(int(loaded), len(messages)))
    own = [i for i in own if 0 <= i < loaded]
    in_place = bool(own) and own == list(range(loaded - len(own), loaded))
    if audience == "owner":
        if not own or in_place or not _another_speaker_after(messages, own, loaded):
            return messages, max(0, loaded - 1)
        mine = set(own)
        head = [m for i, m in enumerate(messages[:loaded]) if i not in mine]
        return head + [messages[i] for i in own] + list(messages[loaded:]), max(0, loaded - 1)
    if own:
        if in_place:
            return messages, loaded
        mine = set(own)
        head = [m for i, m in enumerate(messages[:loaded]) if i not in mine]
        return head + [messages[i] for i in own] + list(messages[loaded:]), loaded
    if not str(own_text or "").strip():
        return messages, loaded
    words = AgentMessage(role="user", content=[TextBlock(text=str(own_text))])
    return list(messages[:loaded]) + [words] + list(messages[loaded:]), loaded + 1


def _shown_rows(rows: list[AgentMessage], *, audience: str, surface: str) -> list[AgentMessage]:
    """The rows this reply's history will show (R15, W2): through the
    payload's own diet and window, at the cap and step the run uses."""
    if not rows:
        return []
    step = 1 if surface == "reminder_fire" else _resolve_history_step()
    return window_for_llm(diet_for_llm(list(rows), keep_from=len(rows), audience=audience),
                          _resolve_history_max_messages(surface=surface), step=step)


def _oldest_shown_ts(shown: list[AgentMessage]) -> float | None:
    """When the oldest of the rows shown (``_shown_rows``) was said (epoch
    seconds), or None when there are none, so a guest's section holds only
    lines older than the conversation above."""
    stamps = [(m.timestamp if m.timestamp.tzinfo is not None
               else m.timestamp.replace(tzinfo=timezone.utc)).timestamp()
              for m in shown if isinstance(getattr(m, "timestamp", None), datetime)]
    return min(stamps) if stamps else None


def _owner_name_shown(shown: list[AgentMessage]) -> str:
    """The name the room's own labels show for the owner among the rows
    shown (``core.agent.speakers.label_names``: the latest label ending in
    the owner's mark that has a name), or "" when they show none: a guest's
    section names the owner the same way (Fable's ruling W1), never by the
    record's name, which for a database line is the account handle."""
    for label in reversed(label_names(shown)):
        if label and label.endswith(OWNER_MARK):
            name = label[:-len(OWNER_MARK)].strip()
            if name:
                return name
    return ""


async def run_turn(
    turn: Any,
    *,
    deps: RunnerDeps,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> RunResult:
    """Entry point. Pre-loop -> ReAct loop -> post-loop."""
    started = time.monotonic()
    # Batch 8, task 1 (B8-1, ruling R3): two keys. The TRANSCRIPT key names
    # the session file (a shared room's one conversation); the STATE key
    # names everything this run keeps in core.agent.steering and
    # core.agent.task_slots. They are the same for every run but a guest's.
    transcript_key = _derive_session_key(turn)
    state_key = _run_state_key(turn, transcript_key)
    # Phase-owned routing (2026-08-04): everything from here to the first
    # model call is PREP — the ~15s recall/cadence window where both 08-04
    # incidents lived. The Discord fork reads this to pick barge-in vs steer.
    _steering.set_run_phase(state_key, "prep")

    # ── Cancellation-safe transcript record (2026-07-07 burst-drop fix) ──
    # A barge-in cancel (bots/discord/client.py) can kill this task at any
    # await. Pre-fix, the user append lived AFTER the recall + cadence
    # pre-reads below (~6s of sequential Haiku round-trips), so any message
    # whose follow-up arrived inside that window was cancelled out of the
    # transcript entirely — persisted to the conversations DB by dispatch's
    # Bug-1 early persist, yet invisible to every future LLM payload, which
    # reads THIS session store (window_for_llm below). 2026-07-07 morning:
    # "sorry. after that call last night" / "i just called it
    # quits" vanished exactly this way (live burst repro: 4 of 5 dropped).
    # The append now happens FIRST, before any cancellable await, shielded
    # so a cancel landing mid-write still completes it. Scope mirrors the
    # Bug-1 DB gate: genuine typed user turns only — attachment turns keep
    # the late append (their transcript text is finalized by recall's
    # vision analysis), synthesized origins keep theirs (their prompt is
    # built from ctx, and they are never barge-in cancelled).
    session: Session | None = None
    user_appended_early = False
    _early_md = getattr(turn, "metadata", None)
    _early_origin = (
        getattr(_early_md, "origin", "user") if _early_md is not None else "user"
    )
    _early_text = str(getattr(turn, "content", "") or "")
    _early_has_attachments = bool(getattr(turn, "images", None)) or bool(
        getattr(_early_md, "text_attachments", None)
    )
    _early_prerecorded = bool(getattr(_early_md, "user_side_prerecorded", False))
    if (
        _early_text.strip()
        and _early_origin in ("user", "dashboard_user")
        and not _early_has_attachments
        and not _early_prerecorded
    ):
        try:
            session = await asyncio.shield(
                _open_session_with_user_message(
                    transcript_key, deps.sessions_root, _early_text,
                    speaker=speaker_of(turn),
                )
            )
            user_appended_early = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "runner: early transcript append failed; deferring to the "
                "late append: %s", exc,
            )
            session = None

    # Step 2, part 4 (2026-10-08; the who-is-talking plan, A1): in a shared
    # room with group chat on, her quick reads read the room itself, guests'
    # lines included, each saying who said it — the conversations database
    # they read before never holds a guest's words. Read once, before recall:
    # the mood read there gets it through current_room_tail, the quick vote
    # below directly. This turn's own rows are left out (the vote shows them
    # as the latest message). Anywhere else: None, and today's reads.
    _room_read: str | None = None
    _room_rows: list[AgentMessage] | None = None
    _own_row_ids: list[int] = []
    if _qc.offered(turn) and is_shared_room(turn):
        # The early append's session already holds the room (Fable's
        # checkpoint, finding 3): no second read of the file.
        _room_rows = (list(session.messages) if session is not None
                      else await _room_tail.load(transcript_key, root=deps.sessions_root))
        if _room_rows is not None:
            _own_recorded = user_appended_early or _early_prerecorded
            _own_row_ids = _turn_rows(_room_rows, _early_text) if _own_recorded else []
            _room_read = _room_tail.render(_room_rows, drop=_own_row_ids)

    # The room-reading fix (2026-10-12; the owner's design): in a shared room,
    # the names this message calls, as facts — her first read in recall reads
    # them in her own words (the replay: without them it still wrote "he's
    # checking if I'm around" on a friend's name), the quick vote in the third
    # person with sentence C, and her reply call in her own. The vote can't
    # stop a reply on the owner's turns, so her reply call needs them too
    # (10-11: the vote's guess reached her as her own note). Elsewhere "".
    _in_room = _qc.offered(turn) and is_shared_room(turn)
    _names_vote, _names_reply = (
        _room_card.names_lines(turn, _room_rows or [], transcript_key) if _in_room else ("", ""))
    if _names_reply:
        logger.info("runner: room reading — %s (session=%s)", _names_reply, state_key)

    # --- Pre-loop ---
    # Fix 6 (the owner, 10-12): the quick length vote reads only the turn's
    # words and the recent conversation, so it starts here, beside recall,
    # not after it (~2 s off every reply). The history is fetched once and
    # recall reuses it (core/turn/history_prefetch). No early fetch wired =
    # the old order.
    _quiet_offered = _qc.offered(turn)
    _turn_md0 = getattr(turn, "metadata", None)
    # Batch 8, task 2b (rulings T2b-1, T2b-3, T2b-5): a room's first read
    # already voted "answer" — its vote rides on the turn and is not asked
    # again. Otherwise the participant vote sees the signals line.
    _first_pass = None
    if _quiet_offered and isinstance(getattr(_turn_md0, "extra", None), dict):
        _first_pass = _cadence.config_from_first_pass(_turn_md0.extra.get("first_pass"))

    async def _vote(vote_ctx: Any, hist_task: Any = None) -> Any:
        if _room_read is not None:
            vote_ctx = SimpleNamespace(user_text=_room_tail.latest([turn]), recent_history_text=_room_read)
        elif hist_task is not None:
            from core.agent.context_adapter import _render_recent_history
            vote_ctx = SimpleNamespace(
                user_text=str(getattr(turn, "content", "") or ""),
                recent_history_text=_render_recent_history(
                    SimpleNamespace(recent_history=tuple(await hist_task), turn=turn)))
        return await _cadence.shape_response(
            vote_ctx, llm=deps.cadence_llm, participant=_quiet_offered,
            signals=(_cadence.render_signals(_cadence.signals_of(turn))
                     if _quiet_offered else ""),
            room=_in_room, names=_names_vote)

    # One first look (R14, T5; plan §2.3; Q7: everywhere "she decides"
    # applies, DMs included): with companion.first_look on, a human turn with
    # no carry from the room pass takes her first look here, in the vote's
    # slot beside recall, in place of the vote AND of recall's first read
    # (ai.turn_read.first_look_in_charge, set around recall below) — one
    # call, not two. It reads what the vote read before recall: the room and
    # its rows, the names (set above, before recall: keep that order), the
    # signals, the owner's note with its words, the early history in a DM.
    _look_task = None
    _look_owner = deps.is_owner is not False and not is_guest_turn(turn)
    _hist_task = _vote_task = _hist_token = None
    if deps.fetch_recent_history is not None and _first_pass is None:
        _hist_task = asyncio.ensure_future(deps.fetch_recent_history(turn))
        _hist_token = _history_prefetch.prefetched_history.set(_hist_task)
    if _quiet_offered and _first_pass is None and _first_look.enabled():
        _look_task = asyncio.ensure_future(_first_look.take_for_turn(
            turn, llm=deps.first_look_llm, key=transcript_key, in_room=_in_room,
            room_read=_room_read, room_rows=_room_rows, own_rows=_own_row_ids,
            history=_hist_task, owner=_look_owner))
    elif _hist_task is not None:
        _vote_task = asyncio.ensure_future(_vote(None, _hist_task))

    def _drop_early() -> None:
        for _t in (_vote_task, _look_task, _hist_task):
            if _t is not None and not _t.done():
                _t.cancel()

    _room_token = _room_tail.current_room_tail.set(_room_read)
    _names_token = _room_tail.current_room_names.set(_names_reply)
    _in_charge_token = (_turn_read.first_look_in_charge.set(True)
                        if _look_task is not None else None)
    try:
        raw_ctx = await deps.build_turn_context(turn)
    except asyncio.CancelledError:
        _drop_early()
        raise
    except Exception as exc:
        _drop_early()
        logger.exception("runner: build_turn_context failed")
        # The phase set above is cleared by the loop's finally, which this
        # return never reaches (Fable's review, 2026-10-10): clear it here.
        _steering.clear_run_state(state_key)
        return RunResult(responded=False, response_text="", error=f"recall_failed: {exc}")
    finally:
        _room_tail.current_room_tail.reset(_room_token)
        _room_tail.current_room_names.reset(_names_token)
        if _in_charge_token is not None:
            _turn_read.first_look_in_charge.reset(_in_charge_token)
        if _hist_token is not None:
            _history_prefetch.prefetched_history.reset(_hist_token)

    # The look's read goes where recall's would have (R14, T5): the
    # reflection's mood read and the arc on recall's context; Draft 2's body
    # into the reply's read section below. A look that failed (None): no
    # read and the default shape, as when the first read timed out (Q8's
    # twin for the owner, plan risk 11).
    _look = None
    if _look_task is not None:
        _look_arc = ""
        try:
            _look, _look_arc = await _look_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a look that breaks costs the look
            logger.warning("runner: the first look broke (session=%s): %s", state_key, exc)
            _look = None
        raw_ctx = _first_look.into_turn_context(raw_ctx, _look, arc=_look_arc)
        if _look is None:
            logger.info(
                "runner: no first look on a human turn (origin=%s); today's defaults — "
                "no read, the default shape (session=%s)", _early_origin, state_key)

    # D.2 wave 1 (2026-05): when recall produced a real TurnContext,
    # flatten it through the adapter so build_system_prompt / cadence /
    # the session writer see real strings instead of getattr-empty
    # fields. When recall produced a test fake (already exposes the
    # named fields directly), use it as-is so the existing test
    # contract holds without rewriting every test's FakeCtx.
    #
    # D.3 wave 2 step 2: prefer deps.is_owner (resolved by dispatch.py
    # against local_mode.get_local_owner_refs()) when set. Falls back
    # to whatever the ctx already carries — that's the legacy path tests
    # exercise when they hand-wire FakeCtx with is_owner=True.
    from core.agent.context_adapter import build_agent_loop_context
    from core.turn.types import TurnContext

    if deps.is_owner is not None:
        resolved_is_owner = bool(deps.is_owner)
    else:
        resolved_is_owner = bool(getattr(raw_ctx, "is_owner", True))

    if isinstance(raw_ctx, TurnContext):
        ctx: Any = build_agent_loop_context(
            turn,
            raw_ctx,
            is_owner=resolved_is_owner,
        )
    else:
        ctx = raw_ctx
    # The reply's read section (R14, T5; Draft 2 under today's heading and
    # framing, Q4): her look's body. A friend's turn reads the room pass's
    # carry (blank while the owner's room note stands, the carry rule), or
    # its own look here, blank too while a note stands (FW-2 generalised).
    # companion.caring_memory off: no read here either, as the reflection
    # gets none (first_look.caring_on; Fable's review, 10-13) — recall gave
    # the reply no read with it off, and the look changes nothing there.
    _read_body = ""
    _has_look = _look is not None or (_first_pass is not None and _first_look.enabled())
    if _has_look and _first_look.caring_on():
        if _look is not None:
            _note_hides = is_guest_turn(turn) and bool(_room_notes.note_line(transcript_key))
            _read_body = "" if _note_hides else _first_look.render_body(_look)
        else:
            _read_body = _first_look.render_body(
                _first_look.from_carry(_turn_md0.extra.get("first_pass")))
    ctx = _first_look.with_read_section(ctx, _read_body)

    # Bug 5 (2026-07-04): capture recall's attachment analysis now (from raw_ctx,
    # the TurnContext or a test fake) so it can ride out on RunResult and dispatch
    # can persist the rich user row. Empty tuples on non-attachment turns.
    _img_descs = tuple(getattr(raw_ctx, "image_descriptions", ()) or ())
    _txt_attachs = tuple(getattr(raw_ctx, "text_attachment_contents", ()) or ())

    turn_md = getattr(turn, "metadata", None)
    turn_origin = getattr(turn_md, "origin", "user") if turn_md is not None else "user"

    # Burst coalescing: a prerecorded image/file turn stored a
    # "[image attached]" / "[file attached]" placeholder on arrival; recall
    # just produced the vision/file analysis. Enrich the transcript entry and
    # DB row NOW, as a detached task (2026-07-08). This used to run post-loop,
    # where a barge-in cancelling run_turn mid-loop skipped it and the rich
    # form was lost from both stores (live-bugs Bug-5/6 residual). Detached
    # and spawned pre-loop, it survives the cancel; enrich re-reads the file
    # fresh under its own lock, so racing a concurrent fragment append is safe.
    # A guest's line recorded on arrival (Batch 8, task 2b: a room read with
    # companion.she_decides on) is never "enriched": its tagged text matches
    # no placeholder, and the enrich's DB half would write a guest row into
    # the conversations table, which guest turns never do.
    if (bool(getattr(turn_md, "user_side_prerecorded", False)) and (_img_descs or _txt_attachs)
            and not is_guest_turn(turn)):

        async def _enrich_prerecorded(
            sk: str, root: Any, t: Any, imgs: tuple, txts: tuple
        ) -> None:
            from core.turn.persistence import (
                _expand_content_with_attachments,
                _persist_user_side_with_content,
            )

            rich = _expand_content_with_attachments("[image attached]", imgs, txts)
            s = await Session.open_or_create(sk, root=root)
            await s.enrich_last_user_placeholder(rich)
            await _persist_user_side_with_content(t, rich)

        _spawn_detached(
            _enrich_prerecorded(
                transcript_key, deps.sessions_root, turn, _img_descs, _txt_attachs
            ),
            "prerecorded attachment enrich",
        )

    # Batch 8, task 2a (B8-2, rulings T2-2, T2a-1, T2a-2): she decides whether
    # to answer. On a human conversation turn with companion.she_decides on,
    # the quick vote is participant-framed, it no longer decides silence, and
    # the reply call is offered the stay_quiet control call. Off (and on
    # every reminder, workbench, forge, monologue or outreach run): today's.
    # _quiet_offered and _first_pass are set before recall (fix 6). The
    # owner's room note (INTERIM) shows in her reply call below.
    # The note's words are the owner's (ruling T2b-5b): a guest's reply call
    # sees only that the owner has a note here, until when (ruling FW-8).
    _room_note = (_room_notes.note_line(transcript_key, words=not is_guest_turn(turn))
                  if _quiet_offered else "")
    try:
        if _first_pass is not None:
            cadence_cfg = _first_pass
        elif _look_task is not None:
            # The shape from her look (no observation: its words are the read).
            cadence_cfg = (_first_look.as_cadence_config(_look) if _look is not None
                           else _cadence._with_directive(_cadence._DEFAULT_CONFIG))
            if _look is not None:
                # Draft 6's line on every look the runner took, not only one
                # that held back (Fable's review, 10-13): a week's `first
                # look` lines then count them all, the room pass's and these.
                # T2a-2 stands either way: her reply decides.
                logger.info(
                    "runner: first look said %s on a human turn (origin=%s); "
                    "her reply decides (session=%s) — %s",
                    _look.decision, turn_origin, state_key,
                    _look.reason or "no reason given",
                )
        elif _vote_task is not None:
            cadence_cfg = await _vote_task                  # started beside recall (fix 6)
        else:
            cadence_cfg = await _vote(ctx)
    except Exception as exc:
        logger.warning("runner: cadence shape failed, using default: %s", exc)
        cadence_cfg = _cadence._with_directive(_cadence._DEFAULT_CONFIG)

    _silence_allowed = False
    if not cadence_cfg.should_respond:
        # A should_respond=false verdict means "don't reach out" — which only
        # makes sense for PaLLi-INITIATED turns (proactive/monologue outreach
        # that, on reflection, isn't worth sending). A turn a HUMAN actually
        # sent — user, dashboard_user, or a reminder they set — must always get
        # a reply. Honoring cadence's silence there drops the message entirely:
        # no reply, and (because we return before the loop) it never enters the
        # session transcript, so PaLLi has no record it happened. That is
        # exactly how the owner's "check the git commits" request vanished on
        # 2026-07-06: the cadence model had moved to Haiku (Bug 3), which —
        # unlike DeepSeek — mislabels even action_requests as should_respond=
        # false, and this gate honored it. Cadence's register/length/beats
        # still shape the reply; only the silence gate is scoped.
        if turn_origin in ("monologue", "proactive_action"):
            logger.info(
                "runner: cadence chose silence for PaLLi-initiated turn "
                "(origin=%s); not reaching out (session=%s)",
                turn_origin, state_key,
            )
            _steering.clear_run_state(state_key)   # the loop's finally is never reached
            return RunResult(
                responded=False,
                response_text="",
                iterations=0,
                image_descriptions=_img_descs,
                text_attachment_contents=_txt_attachs,
            )
        # DEFECT_RECORD §18 (2026-09-01): "dont respond once you see this
        # message" → cadence voted false → this branch FORCED a reply ("🤐").
        # The guarantee is now a forced RUN, not a forced reply: his words
        # enter the transcript and the model reads them; the reply may be
        # empty (an empty reply is silence — nothing is sent). A mis-vote on
        # a real question still gets answered, because the model — not the
        # vote — decides what to say.
        if _quiet_offered:
            # Batch 8, task 2a (ruling T2a-2): the vote no longer gates
            # silence on a human turn — no silence note. Her reply decides,
            # and staying quiet is a call she can make (stay_quiet). The same
            # for her first look (R14), advisory on a turn the runner reads;
            # its line was logged above, with every other look's.
            if _look is None:
                logger.info(
                    "runner: the quick vote said %s on a human turn (origin=%s); "
                    "her reply decides — she may choose quiet (session=%s)",
                    cadence_cfg.decision, turn_origin, state_key,
                )
        else:
            _silence_allowed = True
            logger.info(
                "runner: cadence voted should_respond=false on a user-facing turn "
                "(origin=%s); running with silence allowed — the message is never "
                "dropped, the reply may be nothing (session=%s)",
                turn_origin, state_key,
            )

    _obs.log_loop_start(
        session_key=state_key,
        surface=getattr(turn, "surface", "unknown"),
        turn_origin=turn_origin,
        cadence_summary=f"register={cadence_cfg.register} length={cadence_cfg.length} beats={cadence_cfg.target_beats}",
        tool_names=deps.tool_registry.all_names() if deps.tool_registry else [],
    )

    from core.agent import llm_client as _llm
    from core.agent.detection import ToolLoopDetector
    from core.agent.session import AgentMessage, TextBlock, ToolResultBlock
    from core.agent.system_prompt import build_system_prompt_parts
    from core.agent.tools import ToolContext

    # Burst-drop fix: the early transcript block above usually opened the
    # session already; reuse it (per-key turns are strictly sequential — the
    # barge-in cancels any sibling before its own dispatch starts, so no
    # other task can have appended in between). Only turns the early gate
    # excluded (attachments / synthesized origins / empty) open here.
    if session is None:
        session = await Session.open_or_create(transcript_key, root=deps.sessions_root)
    chunker = _cadence.build_chunker(
        target_beats=cadence_cfg.target_beats,
        surface=getattr(turn, "surface", ""),
        on_chunk=on_chunk or _noop_on_chunk,
    )
    # Memory that reaches (R15, W2): a guest's turn has no tools, so one code
    # step searches that room's own history before the reply, and her prompt
    # carries what it found (items 18–19). Its scope is the turn's
    # (scope_for_turn; a guest's transcript key can carry ":guest:<id>"),
    # it keeps only lines older than what this reply's history shows, and
    # it names the owner as those rows' labels do (W1).
    # Never on the owner's turn (the search tool is theirs); off, nothing.
    # A failed step costs the section, never the turn.
    _room_lines: list[str] = []
    if _memory_reach_index.enabled() and is_guest_turn(turn):
        try:
            _shown = list(session.messages)
            if not user_appended_early and not _early_prerecorded:
                # This turn's own row, appended below: the history shows it.
                _shown.append(AgentMessage(role="user", content=[TextBlock(text=_early_text)]))
            _shown = _shown_rows(_shown, audience=_payload_audience(deps, turn),
                                 surface=str(getattr(turn, "surface", "") or ""))
            _room_body = await _memory_reach_search.room_section_for(
                turn, text=_early_text, shown_since_ts=_oldest_shown_ts(_shown),
                owner_name=_owner_name_shown(_shown))
            ctx = _memory_reach_search.with_room_memory(ctx, _room_body)
            if getattr(ctx, "room_memory_text", ""):
                _room_lines = _memory_reach_search.dated_lines(_room_body)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed step costs the section, never the turn
            logger.warning("runner: the guest's room step failed (%s, session=%s)",
                           type(exc).__name__, state_key)
    # Batch 2, task 3 (2026-09-27): the prompt travels in two parts. The
    # stable prefix goes as `system`; the volatile tail (clock, bands,
    # directive) goes as `system_tail`, which llm_client rides in the one
    # trailing system message after the history — so the provider's prefix
    # cache covers the prefix AND the history instead of breaking at the
    # clock.
    # Step 2, add-on (a): a turn with no tool registry (a guest's) is never
    # told tools are allowed, in the working law or the length note.
    _tools_offered = deps.tool_registry is not None
    stable_prompt, volatile_prompt = build_system_prompt_parts(
        ctx, cadence_directive=cadence_cfg.directive, quiet_choice=_quiet_offered,
        tools=_tools_offered,
    )
    # Step 2, part 5 (2026-10-08; the plan's A3 and add-on b): the room card in
    # a shared room with group chat on, on every turn (a guest's too), and the
    # owner's accounts on the owner's turns, on every app. Both change from
    # turn to turn, so they open the volatile tail, never the cached prefix.
    _owner_turn = resolved_is_owner and not is_guest_turn(turn)
    _card_parts: list[str] = []
    try:            # a card that can't be built costs the card, never the turn
        if _owner_turn:
            _room_card.remember_owner(turn)
        if _qc.she_decides_enabled() and is_shared_room(turn):
            if _room_rows is None:
                _room_rows = await _room_tail.load(transcript_key, root=deps.sessions_root)
            _card_parts.append(_room_card.room_section(turn, _room_rows or [], transcript_key))
        if _owner_turn:
            _card_parts.append(_room_card.owner_accounts(deps.owner_refs))
    except Exception as exc:  # noqa: BLE001
        logger.warning("runner: the room card could not be built (session=%s): %s",
                       state_key, exc)
        _card_parts = []
    if any(_card_parts):
        volatile_prompt = "\n\n".join(p for p in (*_card_parts, volatile_prompt) if p.strip())
    # The register fix (2026-08-22): the vote travels two ways for a
    # conversation turn — by context var to the adapter's governor, and as a
    # trailing note on the LLM call. Never for her workbench (deps.govern_replies).
    # The R14 rule (Draft 3): when the look the runner took let it pass or
    # waited, the length note says what it limits only if she answers, and
    # her look's own line closes the trailing note (below, after the names
    # and voice lines). On "answer", today's note, byte for byte. Every human
    # turn whose look is the runner's — a friend's DM too (the coordinator's
    # ruling after T5: Q7 put the look in DMs, and a friend's DM has no room
    # pass); a friend's room turn carries the room pass's look, whose quiet
    # was final there, so it never holds back here. The tools clause is the
    # length note's own (_tools_offered: a friend's belt is web_search only
    # with features.guest_web_search on, T7; else none).
    _look_holds = (_look.decision if _look is not None
                   and _look.decision in _reply_governor.LOOK_HOLDS_BACK else None)
    _shape_token = None
    _shape_note: str | None = None
    if deps.govern_replies and _reply_governor.cap_for(cadence_cfg.length) is not None:
        _shape_token = _reply_governor.current_reply_shape.set(str(cadence_cfg.length).lower())
        _shape_note = _reply_governor.render_shape_note(
            cadence_cfg.length, tools=_tools_offered, look=_look_holds) or None
    if _silence_allowed:
        _shape_note = ((_shape_note or "") + "\n" + SILENCE_NOTE).strip()
    if _room_note:
        # The owner's room note (Batch 8, task 2b — INTERIM): one line, only
        # while it stands and only on a turn offered the quiet choice.
        _shape_note = ((_shape_note or "") + "\n" + _room_note).strip()
    if _names_reply:
        _shape_note = ((_shape_note or "") + "\n" + _names_reply).strip()
    # Voice replies (Batch 8, task 10 — owner O2, ruling T10-4 — INTERIM, the
    # owner revisits it after the batches): while the switch is on in this
    # conversation, the owner's reply call there carries one status line, so
    # the text line beside a voice reply is her call (core/voice_replies.py);
    # since 10-12 a guest's does too — their reply is spoken there as well,
    # and the seam reads a guest's turn by its origin. Off, or any other
    # turn: "" and the payload is untouched.
    _voice_line = _voice_replies.status_line_for(
        turn, owner=resolved_is_owner and not is_guest_turn(turn))
    if _voice_line:
        _shape_note = ((_shape_note or "") + "\n" + _voice_line).strip()
    # Step 27 Phase B (spec §3, build-session correction 2): a turn that
    # RESUMES a paused task, or arrives while one is paused, says so to the
    # model in a trailing note — deterministic presentation of a structural
    # fact, so "go on" picks the work back up and a side remark does not.
    if _dx.duplex_enabled():
        _pb_extra = getattr(turn_md, "extra", None) if turn_md is not None else None
        if isinstance(_pb_extra, dict):
            _pb_note = None
            if str(_pb_extra.get("resume_of") or "").strip():
                _pb_note = ("[resumed — pick up exactly where you left off: "
                            f"{str(_pb_extra['resume_of']).strip()}]")
            elif str(_pb_extra.get("paused_task") or "").strip():
                _pb_note = ("[PAUSED — you set this down: "
                            f"{str(_pb_extra['paused_task']).strip()}; do not continue it "
                            "unless they say so]")
            if _pb_note:
                _shape_note = ((_shape_note or "") + "\n" + _pb_note).strip()
    if _look_holds:
        # The very last line: her own first look (Draft 3, variant A), so the
        # last thing she reads before writing is her look, not an order to
        # answer (10-12 17:56). T2a-2 stands: only stay_quiet is quiet.
        # A friend's turn while the owner's room note stands: the line without
        # its reason, which could say the note's words back — as the read body
        # is blanked then (the carry rule; 10-13).
        _look_reason = "" if (_room_note and is_guest_turn(turn)) else _look.reason
        _shape_note = ((_shape_note or "") + "\n"
                       + _reply_governor.render_look_line(_look_holds, _look_reason)).strip()
    # The owner, 10-13: a friend's reply call, while the owner's room note
    # stands and her first look said answer, ends with the line saying so —
    # the runner's own look (a friend's DM) or the room pass's carry, only
    # when a look really ran there (a failed or timed-out look answers by
    # default, and that is code's answer, not hers). Exclusive with the look
    # line above (that look held back), so it is last whenever it shows.
    if _room_note and is_guest_turn(turn) and _look_said_answer(_look, _first_pass, _turn_md0):
        _shape_note = ((_shape_note or "") + "\n" + _reply_governor.LOOK_READ_THE_NOTE).strip()
    # D.3 wave 2 step 2: ToolContext.is_owner gates owner-only tools at
    # ToolRegistry.dispatch (core/agent/tools.py:134). Use the same
    # resolved_is_owner the AgentLoopContext carries so the schema-filter
    # at ToolRegistry.schemas_for and the per-call gate stay in lockstep.
    # Second read F9 (2026-10-04): the owner's own words this run, for a tool
    # that saves something as the owner's (room_note). Only an owner's
    # conversation turn has any: a guest's, a reminder fire's or her own
    # runs' content is not the owner asking.
    _owner_words = ([str(getattr(turn, "content", "") or "")]
                    if (resolved_is_owner and not is_guest_turn(turn)
                        and turn_origin in ("user", "dashboard_user")) else [])
    tool_ctx = ToolContext(turn_owner_id=getattr(turn, "owner_id", 0),
                           is_owner=resolved_is_owner,
                           surface=str(getattr(turn, "surface", "") or ""),
                           channel_id=int(getattr(
                               getattr(turn, "metadata", None), "channel_id", 0,
                           ) or 0),
                           owner_words=_owner_words)
    # Week 2 task 2 (2026-09-10): ONE context for the whole turn, so
    # ``tool_ctx.deliveries`` IS this turn's delivery hand-off — the paths
    # tools put there (deliver_artifact is the canonical producer) become
    # RunResult.attachments at the bottom of this function. There is no text
    # scan any more: a tool that merely PRINTS a path delivers nothing.
    tools_schemas = (deps.tool_registry.schemas_for(tool_ctx)
                     if deps.tool_registry else [])
    if _quiet_offered:
        # Batch 8, task 2a (ruling T2-2): the quiet choice is the runner's own
        # control call, never a registry tool — a guest, who has no registry,
        # is offered it too and still reaches no tool.
        tools_schemas = [*tools_schemas, dict(_qc.STAY_QUIET_SCHEMA)]

    # Burst-drop fix: skip when the cancellation-safe early block already
    # recorded this message (ctx.user_text is str(turn.content or "") —
    # context_adapter.py:106 — so early/late write identical text). The
    # late path remains for attachment turns (recall-enriched user_text)
    # and synthesized origins.
    if not user_appended_early and not bool(getattr(turn_md, "user_side_prerecorded", False)):
        await session.append(AgentMessage(role="user", content=[TextBlock(text=getattr(ctx, "user_text", ""))],
                                          speaker=speaker_of(turn)))
    # Boundary for the response_text fallback below: only messages appended
    # AFTER this point belong to this run. Reaching past it resurrects the
    # previous turn's reply (2026-06-12 "looping" incident).
    turn_history_start = len(session.messages)

    # Batch 8, task 1 (B8-1, ruling R2): who reads the room's past turns.
    _audience = _payload_audience(deps, turn)
    # Batch 8, final fix wave (ruling FW-1): this turn's own rows. The runner
    # just appended an ordinary turn's row, so it is the last one; a turn
    # recorded on arrival is found by its words (other writers may have
    # appended after it — see _payload_rows).
    _own_text = str(getattr(turn, "content", "") or "")
    if bool(getattr(turn_md, "user_side_prerecorded", False)):
        _own_rows = _turn_rows(session.messages[:turn_history_start], _own_text)
    else:
        _own_rows = [turn_history_start - 1] if turn_history_start else []

    tracker = _obs.TokenTracker()
    iterations = 0
    tool_calls_total = 0
    terminated_by = "natural"
    _err_streak: dict[str, int] = {}        # Step 27: consecutive errors per tool name
    _tools_called: list[str] = []           # promise gate (2026-09-11): what this run has used
    _tool_digest: list = []                 # grounding check (2026-09-13): what each call returned
    # The owner, 10-12: what PaLLi sees when it talks mid-task. Facts, never
    # words; published at each tool round below, cleared with the run state.
    from core.agent import work_progress as _wp
    _wp_asked = str(getattr(ctx, "user_text", "") or "")
    try:
        _wp_recent = _wp.recent_exchange(session.messages[:turn_history_start])
    except Exception:  # noqa: BLE001 - context for a mid-task reply, never a reason to fail a turn
        _wp_recent = ""
    _wp_progress = None
    _promise_gate_used = False              # one extra round per run, never more
    _promise_nudge = ""                     # rides the next call's trailing system note
    # Second read F3: the last round's answers, how many rounds in a row gave
    # exactly them, and — once the brake is on — the tools that kept answering
    # the same (the next step is a reply).
    _last_round_answers: tuple = ()
    _same_rounds = 0
    _brake_tools: list[str] | None = None
    # Second read F2: consecutive "not yet"s per failing tool beside her quiet
    # choice; once the budget is spent, her next stay_quiet ends the run.
    _not_yet_streak: dict[str, int] = {}
    _quiet_must_stand = False
    _overrun_task: asyncio.Task | None = None
    known_names = set(deps.tool_registry.all_names()) if deps.tool_registry else set()
    detector = ToolLoopDetector(known_tool_names=known_names, polling_tools=deps.polling_tools)

    max_iter = deps.max_iterations or _resolve_max_iterations()
    _surface = str(getattr(turn, "surface", "") or "")
    history_cap = _resolve_history_max_messages(surface=_surface)
    # A reminder fire's window is exactly its cap, never stepped: fires are
    # hours apart, so a stepped start (Batch 2) earns no cache reuse there and
    # would only add up to step-1 older fires on top of the cap.
    history_step = 1 if _surface == "reminder_fire" else _resolve_history_step()

    _unanswered: list[Any] = []
    _run_cancelled = False
    _final_reply_text = ""            # the natural reply, if one came after the drains
    _quiet_reason = ""                # Batch 8: her one line when she chose quiet
    # Step 27 Phase B (spec §4, correction 1): the run itself is one of the
    # tasks his cap allows — hold a slot for a chat-door run (Discord or,
    # since 2026-09-14, Telegram) that is not a sidecar (the launcher owns a
    # sidecar's slot). One cap across the doors, because the cap is about
    # HER, not about a channel. Best-effort: a run always
    # runs; a full house is logged, never a refusal of his message.
    _run_slot_held = False
    if _dx.duplex_enabled() and str(getattr(turn, "surface", "") or "") in (
        "discord_text", "telegram_text", "mobile_text",
    ):
        _pb_md = getattr(turn, "metadata", None)
        _pb_x = getattr(_pb_md, "extra", None) if _pb_md is not None else None
        if not (isinstance(_pb_x, dict) and _pb_x.get("sidecar_of")):
            try:
                from core.agent import task_slots as _slots
                _run_slot_held = _slots.acquire(state_key, "the run")
                if not _run_slot_held:
                    logger.info("runner: task slots full on %s — the run proceeds unslotted", state_key)
            except Exception:
                _run_slot_held = False
    _shown_token = shown_this_turn.set(ShownThisTurn())
    try:
        while True:
            iterations += 1
            if iterations > max_iter:
                _obs.log_detection_trigger(
                    session_key=state_key, detector="max_iterations_safety",
                    level="critical", count=iterations, paired_tool_name=None,
                )
                await session.append(AgentMessage(
                    role="tool",
                    content=[ToolResultBlock(
                        tool_use_id=f"max-iter-{iterations}",
                        content=f"CRITICAL: max iterations ({max_iter}) reached. Stop and report.",
                        is_error=True,
                    )],
                ))
                terminated_by = "max_iterations"
                # Force termination by emitting one more LLM call (it'll see the CRITICAL and stop)
                # but cap at +1 so we don't loop forever.
                if iterations > max_iter + 1:
                    break
            _obs.log_iteration_start(session_key=state_key, iteration_n=iterations)

            if _dx.duplex_enabled() and _steering.stop_requested(state_key):
                # Step 27 (spec §3): "forget it" lands HERE — the previous
                # tool round-trip is closed on disk, the next model call has
                # not begun. Never mid-write. The queued steers are the
                # client's to drop (it clears the queue on a stop, as today).
                terminated_by = "stopped"
                break

            if _dx.duplex_enabled() and _steering.pause_requested(state_key):
                # Step 27 Phase B (spec §3): "hold on" — the round-trip is
                # closed on disk; remember what she was doing and step away.
                # "go on" is his next message on this same session: the loop
                # continues from its own transcript.
                # Fable's review (10-12): a short, time-free note, so the pause
                # slots never report a frozen duration or carry the full note.
                _steering.set_paused(state_key, _wp_progress.paused_note() if _wp_progress is not None
                                     else _steering.get_now_doing(state_key))
                terminated_by = "paused"
                break

            # Steering drain (spec §4e): mid-task input joins the run HERE —
            # after the previous tool round-trip closed, before the next model
            # call. This is the same boundary openclaw drains at, and it keeps
            # the on-disk transcript free of user messages inside an open tool
            # round-trip (validity-boundary spec §6 constraint 1) by
            # construction.
            _drained_replied = False
            _still_running: list[Any] = []
            for _bundle in _steering.drain(state_key):
                if getattr(_bundle, "sidecar_running", False):
                    # Phase B (correction 3): a sidecar is answering this —
                    # its reply is not here yet. Not this hand's to write.
                    _still_running.append(_bundle)
                    continue
                _bundle.drained = True
                if _bundle.replied and _bundle.reply_text:
                    _drained_replied = True
                _text = str(getattr(_bundle.turn, "content", "") or "").strip()
                if _text:
                    await session.append(AgentMessage(
                        role="user", content=[TextBlock(text=_text)],
                        speaker=speaker_of(_bundle.turn)))
                    if _owner_words and not is_guest_turn(_bundle.turn):
                        # Second read F9: the owner's words, arrived mid-run.
                        tool_ctx.owner_words.append(_text)
                if _bundle.replied and _bundle.reply_text:
                    # Her interim reply is appended ONLY if it actually reached
                    # him, so the resumed run never re-answers what she already
                    # said and never claims words he never saw.
                    await session.append(AgentMessage(
                        role="assistant",
                        content=[TextBlock(text=_bundle.reply_text)]))
                elif getattr(_bundle, "quiet", False):
                    # Batch 8, task 2a (ruling T2-3): she chose quiet for
                    # these words (a quiet talk sidecar) — answered, with
                    # nothing sent; the run owes them nothing.
                    pass
                else:
                    # Never answered (judged hold, failed send, or unjudged):
                    # the run now has his words in context. If the run ends
                    # WITHOUT a reply he is still owed one — the finally
                    # re-queues it for the client's post-run sweep (spec
                    # §4f); a natural reply composed after this drain IS the
                    # answer (2026-08-28).
                    _unanswered.append(_bundle)

            for _bundle in _still_running:
                _steering.enqueue(state_key, _bundle)

            # Recomputed every iteration — the session grows as the tool loop
            # appends assistant/tool messages, and the window must always keep
            # the in-flight round-trip intact at the tail. Three payload
            # passes, in order: the diet (2026-08-18 — earlier turns' tool
            # round-trips leave; this run from its user message on is kept
            # verbatim), the window, then the grammar law.
            # Batch 8 (ruling FW-1): the rows in the order the payload shows
            # them, and where the run's verbatim region starts — for a guest,
            # only what this run appended; the owner's is unchanged.
            _rows, _keep_from = _payload_rows(
                session.messages, loaded=turn_history_start, own=_own_rows,
                audience=_audience, own_text=_own_text)
            windowed_messages = normalize_for_llm(
                window_for_llm(
                    diet_for_llm(
                        # §17: stamp working notes BEFORE the diet strips the tool
                        # blocks that identify them; flag off = identity.
                        # 5.10: a reply the door could not deliver is stamped
                        # too, in every mode (a failed send is not a duplex
                        # thing); identity when nothing is flagged.
                        mark_undelivered_for_llm(
                            mark_working_notes_for_llm(_rows)
                            if _dx.duplex_enabled() else _rows),
                        keep_from=_keep_from,
                        # Batch 8 (B8-1): a stranger reads only what was said
                        # in the room; the owner's payload is unchanged.
                        audience=_audience,
                    ),
                    history_cap,
                    step=history_step,
                ),
                log_context=state_key,
            )
            # The payload ends on him (2026-08-28): a turn re-dispatched for
            # an exchange already on the record — the deadline fallback, the
            # sweep, a full_turn's real answer — would otherwise end on her
            # interim reply, which a thinking-mode provider refuses to
            # continue. Payload-only; identity on every ordinary payload.
            # A drained interim never sits bare inside a tool round (§13,
            # 2026-08-29): folded into the user message it answered, payload-only.
            windowed_messages = fold_interims_for_thinking(windowed_messages)
            windowed_messages = ensure_user_tail(
                windowed_messages,
                fallback_text=str(getattr(turn, "content", "") or ""),
                continuation=(_CONTINUATION_NOTE if _drained_replied else ""),
            )
            # Times on every line (2026-10-08, the owner's call): on the copy
            # that goes to the model, last, after every pass that reads the start
            # of a row's text; windowed_messages stays unstamped for the receipt
            # check below. Off = the payload as before, byte-identical.
            _llm_messages = windowed_messages
            # Who said each line (2026-10-08): in a shared room with
            # she_decides on, every user line shows its speaker — the owner's
            # with the owner's mark; switch off or a DM = as before.
            _shown_speakers: frozenset[str] = frozenset()
            if _qc.she_decides_enabled() and is_shared_room(turn):
                _llm_messages = label_speakers_for_llm(_llm_messages)
                _shown_speakers = names_shown(_llm_messages)
            if _mt.message_times_enabled():
                _llm_messages = _mt.stamp_times_for_llm(_llm_messages)
            # What this call shows her, for the reply filter (Fable's
            # checkpoint, 6 and 10): a copied label of someone here, or a
            # copied time she was shown (a line's, or this turn's own), leaves
            # her reply; any other bracket or time is her own words.
            shown_this_turn.set(ShownThisTurn(speakers=_shown_speakers,
                                              times=_times_for_filter(_llm_messages, turn)))
            if iterations == 1 and len(windowed_messages) < len(session.messages):
                logger.info(
                    "runner: history windowed %d -> %d messages for LLM payload "
                    "(cap=%d step=%d) session=%s",
                    len(session.messages), len(windowed_messages), history_cap,
                    history_step, state_key,
                )

            # Phase B starts at model INVOCATION (spec §4) — a message
            # arriving while this call streams is already mid-generation.
            # Idempotent on later iterations.
            _steering.set_run_phase(state_key, "generating")
            # Second read F3: after the brake, this step is a reply (below).
            # Known first, so the voice line can say what the step can do.
            _braked = _brake_tools is not None
            # R15, Group V: while a voice message waits, every later round
            # shows what it will say (item 36), after the look line; it is
            # made when the reply is finished. On the braked step voice_note
            # can't be called: 36's first sentence alone (W3, M1). No words
            # waiting: "" and the note is as before, byte for byte.
            _voice_pending = _voice_note_final.pending_line(tool_ctx, can_call=not _braked)
            _round_note = ((str(_shape_note or "") + "\n" + _voice_pending).strip()
                           if _voice_pending else _shape_note)
            _step_note = (
                (str(_round_note or "") + "\n" + _promise_nudge).strip()
                if _promise_nudge else _round_note
            )
            # Second read F3: after the brake, this step is a reply. With her
            # quiet choice offered it is the only tool left (the choice stays
            # hers); without it the tool list stays as it was — the cached
            # prefix with it — and the call asks for no tool. Not braked: the
            # request is byte-identical to before.
            _step_tools = tools_schemas
            _step_extra: dict[str, Any] = {}
            if _braked:
                _step_note = (str(_step_note or "") + "\n" + BRAKE_NOTE.format(
                    tools=" and ".join(_brake_tools or ()) or "the tools",
                    n=SAME_ANSWER_BRAKE,
                    quiet=BRAKE_QUIET_TAIL if _quiet_offered else "")).strip()
                if _quiet_offered:
                    _step_tools = [dict(_qc.STAY_QUIET_SCHEMA)]
                elif tools_schemas:
                    _step_extra = {"tool_choice": "none"}
            llm_started = time.monotonic()
            response = await _llm.call(
                provider=deps.provider, client=deps.client, model=deps.model,
                system=stable_prompt, system_tail=volatile_prompt,
                messages=_llm_messages,
                tools=_step_tools,
                # Step 27 (spec §2): with duplex on, text is NOT delivered as
                # it streams — an iteration that calls tools is her working
                # note (kept below, never sent); only the final reply is fed
                # to the chunker, after the response is in hand. Flag off:
                # today's live delivery, byte-identical.
                on_chunk=(None if _dx.duplex_enabled() else chunker.feed),
                trailing_system_note=_step_note,
                **_step_extra,
                **(getattr(deps, "call_kwargs", None) or {}),
            )
            _promise_nudge = ""
            response = _strip_imitated_stamps(response)
            latency_ms = int((time.monotonic() - llm_started) * 1000)
            tracker.add(prompt=response.prompt_tokens, completion=response.completion_tokens)
            _obs.log_llm_call(
                session_key=state_key, iteration_n=iterations,
                provider=deps.provider, model=deps.model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens, latency_ms=latency_ms,
                cache_hit_tokens=response.cache_hit_tokens,
                cache_miss_tokens=response.cache_miss_tokens,
            )
            _call_ledger.record_call(
                lane=_call_ledger.loop_lane(turn_origin), provider=deps.provider, model=deps.model,
                prompt=response.prompt_tokens, hit=response.cache_hit_tokens,
                out=response.completion_tokens, session=state_key,
            )
            # The promise gate (2026-09-11, core/agent/promise_gate.py): a
            # text-only draft that commits her to work this run has not done is
            # withheld — never appended, never delivered — and she gets ONE more
            # round with a system note saying what she promised. Only where the
            # draft is not already streaming (duplex on), only once per run, and
            # only while there is room for another round. A judge failure fails
            # open: the draft goes out and the log says the gate was skipped.
            if (_dx.duplex_enabled() and not response.tool_uses and not _promise_gate_used
                    and iterations < max_iter):
                _draft = "".join(response.text_blocks).strip()
                from core.agent import promise_gate as _pg
                _promise_on, _grounding_on = _pg.gate_flags()
                if _draft and _promise_on:
                    _promise_gate_used = True
                    try:
                        _history_receipts = [
                            b.text for _m in windowed_messages for b in _m.content
                            if isinstance(b, TextBlock) and str(getattr(b, "text", "")).startswith(RECEIPT_MARK)
                        ]
                        _verdict = await _pg.check_empty_promise(
                            user_text=str(getattr(ctx, "user_text", "") or ""),
                            draft=_draft, tool_names=list(_tools_called),
                            tool_digest=list(_tool_digest), grounding=_grounding_on,
                            history_receipts=_history_receipts,
                        )
                    except Exception as exc:  # noqa: BLE001 - never block a reply on the judge
                        logger.warning("promise_gate: judge failed (%s: %s) — draft goes out",
                                       type(exc).__name__, exc)
                        _verdict = None
                    # A promise that is only a tacked-on offer ("let me know which
                    # and I'll pull it", 2026-10-12): the cut removes it, so the
                    # draft goes out without a second round. Any other promise,
                    # and any claim, is held back as before.
                    if (_verdict is not None and _verdict.empty_promise
                            and not getattr(_verdict, "ungrounded", False)
                            and getattr(_verdict, "promise_in_ending", False)
                            and _pg.trim_enabled()):
                        _cut_first = _pg.trim_ending(_draft, _verdict.tacked_on_from)
                        if _cut_first is not None and _pg.promise_only_in_ending(
                                _cut_first[0], _cut_first[1], getattr(_verdict, "promise_from", "")):
                            response.text_blocks = [_cut_first[0]]
                            logger.info("promise_gate: the promise was only a tacked-on offer — cut %r, "
                                        "kept …%r (session=%s)", _cut_first[1][:160],
                                        _cut_first[0][-80:], state_key)
                            _verdict = None
                    if _verdict is not None and getattr(_verdict, "flagged", False):
                        if _verdict.empty_promise:
                            logger.info("promise_gate: held back an empty promise — %r (%s) session=%s",
                                        _verdict.promised, _verdict.why, state_key)
                        if getattr(_verdict, "ungrounded", False):
                            logger.info("promise_gate: held back an unsupported claim%s — %r (%s) session=%s",
                                        " (a no from a look that missed)" if getattr(_verdict, "failed_look", False) else "",
                                        _verdict.unsupported, _verdict.why, state_key)
                        # Tell her only what is true on this turn (2026-10-08): a
                        # guest's run offers no tools, a work session no reminders.
                        _offered = {str(s.get("name") or "") for s in tools_schemas} - {_qc.STAY_QUIET}
                        _can_schedule = "create_reminder" in _offered
                        if _can_schedule:
                            # ...and only when a reminder could reach a chat: with
                            # none (a web-page-only install) the tool refuses.
                            from core.agent.builtin_tools.create_reminder import can_deliver_from
                            _can_schedule = await can_deliver_from(tool_ctx)
                        _promise_nudge = _pg.nudge_text(
                            _verdict,
                            can_act=bool(_offered),
                            can_schedule=_can_schedule,
                            can_introspect="introspect" in _offered,
                            can_search_record="search_what_we_said" in _offered,
                        )
                        continue
                    # The tacked-on ending (2026-10-12; the owner: "yes, build the
                    # trim into the check"): the same judge marked where an ending
                    # nobody asked for starts; the reply goes out, and into her
                    # record, without it. A mark that can't be used safely is
                    # ignored (promise_gate.trim_ending).
                    _mark = getattr(_verdict, "tacked_on_from", "") if _verdict is not None else ""
                    if _mark and _pg.trim_enabled():
                        _trimmed = _pg.trim_ending(_draft, _mark)
                        if _trimmed is not None:
                            response.text_blocks = [_trimmed[0]]
                            logger.info("promise_gate: trimmed a tacked-on ending — cut %r, kept …%r "
                                        "(session=%s)", _trimmed[1][:160], _trimmed[0][-80:], state_key)

            # Append assistant message (text + tool_use blocks).
            # Skip empty messages — a reasoning-prone model can burn its full
            # token budget on chain-of-thought and return text_blocks=[] +
            # tool_uses=[]. Saving an empty assistant message to session
            # history breaks the NEXT turn: when session history is replayed,
            # OpenAI/DeepSeek/etc. reject the request with
            #   400 "Invalid assistant message: content or tool_calls must be set"
            # The empty message represents a failed iteration anyway; nothing
            # downstream needs it.
            # Batch 8, task 2a (ruling T2-2): a step that chooses quiet keeps
            # no words — any text beside the stay_quiet call was never sent,
            # so it never enters her record as something she said.
            _quiet_call = _qc.find_call(response.tool_uses) if _quiet_offered else None
            assistant_blocks: list[Any] = []
            if _quiet_call is None:
                for t in response.text_blocks:
                    assistant_blocks.append(TextBlock(text=t))
            for tu in response.tool_uses:
                assistant_blocks.append(tu)
                _tools_called.append(str(getattr(tu, "name", "") or ""))
            if assistant_blocks:
                await session.append(AgentMessage(
                    role="assistant",
                    content=assistant_blocks,
                    reasoning=response.reasoning,
                ))
            else:
                logger.warning(
                    "runner: skipping empty assistant message (LLM returned 0 text + 0 tool calls; "
                    "likely token-budget exhaustion on reasoning model). session=%s iter=%d "
                    "prompt_tokens=%d completion_tokens=%d",
                    state_key, iterations,
                    response.prompt_tokens, response.completion_tokens,
                )

            if _dx.duplex_enabled() and not response.tool_uses:
                # The final reply — deliver it now (governed text: llm_client
                # already applied the register governor to text_blocks).
                for _t in response.text_blocks:
                    if _t:
                        await chunker.feed(_t)

            if not response.tool_uses:
                _final_reply_text = "".join(response.text_blocks).strip()
                # Don't overwrite max_iterations / no_registry / detection_critical
                # signals that may have been stamped earlier in this iteration.
                # `terminated_by` defaults to "natural" at init, so a clean
                # text-only stop already reads correctly without re-assignment.
                break

            # Batch 8, task 2a (rulings T2-2, T2-2a): the calls this step
            # dispatches — every call but stay_quiet, which is the runner's.
            # By name (final fix wave, ruling FW-5): a second stay_quiet in the
            # same step is the same choice, never a tool for the registry.
            _dispatch_uses = ([tu for tu in response.tool_uses
                               if str(getattr(tu, "name", "") or "") != _qc.STAY_QUIET]
                              if _quiet_call is not None else list(response.tool_uses))
            if _quiet_call is not None and not _dispatch_uses:
                # She chose quiet, on its own. Recognised here, before any
                # registry: the round closes on disk, the run ends with no
                # further model call, and nothing is sent.
                _quiet_reason = _qc.reason_of(_quiet_call)
                await asyncio.shield(session.append(AgentMessage(
                    role="tool",
                    content=_qc.round_results(response.tool_uses, _quiet_call,
                                              spoke=_voice_note_final.will_speak(tool_ctx)),
                )))
                terminated_by = "quiet"
                logger.info(
                    "runner: she chose quiet (origin=%s, session=%s): %s",
                    turn_origin, state_key, _quiet_reason or "(no reason given)",
                )
                break

            if _braked:
                # Second read F3: the reply step called a tool anyway. Nothing
                # more is dispatched — each call is answered "not run" so the
                # round closes on disk — and the run ends here. A quiet choice
                # among them stands (it is hers; what is beside it did not run).
                _not_run = [ToolResultBlock(tool_use_id=tu.id, content=BRAKE_NOT_RUN,
                                            is_error=True) for tu in _dispatch_uses]
                if _quiet_call is not None:
                    _quiet_reason = _qc.reason_of(_quiet_call)
                    _round = _qc.round_results(response.tool_uses, _quiet_call, _not_run,
                                               spoke=_voice_note_final.will_speak(tool_ctx))
                    terminated_by = "quiet"
                else:
                    _round = _not_run
                    terminated_by = "tool_brake"
                await asyncio.shield(session.append(AgentMessage(role="tool", content=_round)))
                logger.info(
                    "runner: tool rounds were over (%s kept giving the same answer) and the "
                    "reply step called %s anyway — not run; the run ends (session=%s)",
                    ", ".join(_brake_tools or ()),
                    ", ".join(tu.name for tu in _dispatch_uses) or "nothing else", state_key,
                )
                break

            # Tool dispatch (Task 20 adds detection + size truncation hookup)
            if deps.tool_registry is None and _quiet_call is None:
                terminated_by = "no_registry"
                break
            # A tool round-trip is about to sit between this call's text and
            # the next call's — keep the two from welding in the delivered
            # reply (2026-08-18; see _BaseChunker.iteration_boundary).
            await chunker.iteration_boundary()
            _doing_names = ", ".join(tu.name for tu in _dispatch_uses)   # the quiet-choice log line below
            # The work as it stands (spec §4d; the owner, 10-12): what PaLLi
            # reads when someone talks to it mid-task. Facts only; cleared
            # with the run state in the finally below.
            try:
                from core.agent import promise_gate as _pg_wp
                _wp_progress = _wp.WorkProgress(
                    asked=_wp_asked, started_at=started, step=iterations,
                    done=_pg_wp.render_digest(_tool_digest) if _tool_digest else "",
                    running=_wp.running_lines(_dispatch_uses),
                    note="".join(b.text for b in assistant_blocks if isinstance(b, TextBlock)),
                    recent=_wp_recent,
                )
                _steering.set_work_progress(state_key, _wp_progress)
            except Exception as exc:  # noqa: BLE001 - the note is context, never a reason to fail a turn
                logger.debug("runner: work note skipped (%s: %s)", type(exc).__name__, exc)
            _steering.note_tool_calls(state_key, len(_dispatch_uses))
            if _dx.duplex_enabled() and _overrun_task is None:
                # Step 27 (spec §2): the time sense starts with the first tool
                # call — a plain reply has nothing to overrun.
                _overrun_task = asyncio.create_task(_dx.overrun_watch(
                    state_key,
                    started_at=started,
                    factor=float(_dx._knob("duplex_overrun_factor", 1.5)),
                    default_minutes=float(_dx._knob("duplex_overrun_minutes_default", 10)),
                ))
            try:
                if deps.tool_registry is None:
                    # (Batch 8) a turn with no registry — a guest's — named a
                    # tool beside its quiet choice: it does not exist here.
                    results = [ToolResultBlock(tool_use_id=tu.id, content=_qc.NO_TOOLS_RESULT,
                                               is_error=True) for tu in _dispatch_uses]
                else:
                    results = await asyncio.gather(*[
                        deps.tool_registry.dispatch(tu, tool_ctx) for tu in _dispatch_uses
                    ])
            except asyncio.CancelledError:
                # Barge-in landed mid-dispatch — the 2026-07-25 poison window:
                # the assistant tool_use is already on disk (append above), its
                # results are not. Close the round-trip under a shield so the
                # cancel can't strand an orphan, then let the cancel proceed.
                # Mirrors the 2026-07-07 burst-drop shield (top of this file);
                # read-side normalize_for_llm remains the guarantee — this keeps
                # the FILE honest immediately, so her record says what happened
                # without waiting for the next turn's read. Spec §7.
                await asyncio.shield(session.append(AgentMessage(
                    role="tool",
                    content=[ToolResultBlock(
                        tool_use_id=tu.id,
                        content="[interrupted: a new message arrived before this command finished]",
                        is_error=True,
                    ) for tu in response.tool_uses],
                )))
                raise
            tool_calls_total += len(results)
            try:
                from core.agent import promise_gate as _pg_d
                for _tu, _res in zip(_dispatch_uses, results):
                    _tool_digest.append(_pg_d.digest_entry(
                        getattr(_tu, "name", ""), getattr(_tu, "input", None),
                        str(getattr(_res, "content", "") or ""), bool(getattr(_res, "is_error", False)),
                    ))
            except Exception as exc:  # noqa: BLE001 - the digest is advice to a judge, never a reason to fail a turn
                logger.debug("promise_gate: digest entry skipped (%s: %s)", type(exc).__name__, exc)
            if _wp_progress is not None:
                try:
                    from dataclasses import replace as _replace_wp
                    from core.agent import promise_gate as _pg_wp
                    _wp_progress = _replace_wp(
                        _wp_progress, running="",
                        done=_pg_wp.render_digest(_tool_digest) if _tool_digest else "")
                    _steering.set_work_progress(state_key, _wp_progress)
                except Exception as exc:  # noqa: BLE001 - context, never a reason to fail a turn
                    logger.debug("runner: work note skipped (%s: %s)", type(exc).__name__, exc)
            if _dx.duplex_enabled():
                # Step 27 (spec §2): a tool erroring TWICE in a row on the
                # same step is a snag she may feel — a wake, structure only.
                for _tu, _res in zip(_dispatch_uses, results):
                    if getattr(_res, "is_error", False):
                        _err_streak[_tu.name] = _err_streak.get(_tu.name, 0) + 1
                        if _err_streak[_tu.name] == 2:
                            try:
                                from ai import present_moment as _pm
                                _pm.notice("task_problem", tool=str(_tu.name),
                                           what="repeated_error")
                            except Exception:
                                pass
                    else:
                        _err_streak.pop(_tu.name, None)
            # Batch 8, task 2a (ruling T2-2a): in a quiet step the round
            # carries the quiet call's result beside the others' — "you stayed
            # quiet" when everything went through, "not yet" when it did not.
            _quiet_failed: list[str] = []
            _keeps_failing = False
            _round = list(results)
            if _quiet_call is not None:
                _quiet_failed = _qc.failed_names(_dispatch_uses, results)
                if _quiet_failed and _quiet_must_stand:
                    # Second read F2: she was told it keeps failing; this
                    # quiet choice ends the run, whatever failed beside it.
                    logger.info(
                        "runner: %s failed again beside her quiet choice after "
                        "\"keeps failing\" — the choice stands (session=%s)",
                        ", ".join(_quiet_failed), state_key,
                    )
                    _quiet_failed = []
                elif _quiet_failed:
                    # Consecutive per tool: a tool that went through (or was
                    # not called) this step starts again from zero.
                    _not_yet_streak = {n: _not_yet_streak.get(n, 0) + 1 for n in _quiet_failed}
                    if max(_not_yet_streak.values()) > _qc.NOT_YET_BUDGET:
                        _keeps_failing = True
                        _quiet_must_stand = True
                else:
                    _not_yet_streak = {}
                _round = _qc.round_results(response.tool_uses, _quiet_call, results,
                                           failed=_quiet_failed, keeps_failing=_keeps_failing,
                                           spoke=_voice_note_final.will_speak(tool_ctx))
            await session.append(AgentMessage(role="tool", content=_round))

            # Deeds sense (impulse-freshness spec §3): record deed-shaped
            # tool calls so her present can see what she DID, not just what
            # she said. Errored results are skipped — a failed delivery
            # recorded as a done deed would recreate the exact
            # self-mis-knowledge this sense exists to fix. Best-effort;
            # never touches the run.
            try:
                from ai.recent_deeds import record_tool_deed
                for _tu, _res in zip(_dispatch_uses, results):
                    if getattr(_res, "is_error", False):
                        continue
                    record_tool_deed(_tu.name, dict(_tu.input or {}))
            except Exception:
                pass

            if _quiet_call is not None:
                if not _quiet_failed:
                    # Everything beside her quiet choice went through (the
                    # reminder is set, the file is on its way): her choice
                    # stands — no further model call, no words sent.
                    _quiet_reason = _qc.reason_of(_quiet_call)
                    terminated_by = "quiet"
                    logger.info(
                        "runner: she chose quiet after %s (origin=%s, session=%s): %s",
                        _doing_names, turn_origin, state_key,
                        _quiet_reason or "(no reason given)",
                    )
                    break
                # Something did not go through: she decides again with that
                # in view — she may tell them, or choose quiet once more.
                logger.info(
                    "runner: quiet choice deferred — %s didn't go through "
                    "(session=%s); she decides again%s",
                    ", ".join(_quiet_failed), state_key,
                    " (told it keeps failing; her next quiet choice ends the run)"
                    if _keeps_failing else "",
                )

            # Second read F3: the brake. This round's answers — each tool with
            # what it said, order-free (parallel calls) — against the last
            # round's; the count resets on any difference. (R15, Group V: a
            # voice_note answer is compared without the words it quotes back —
            # a reworded call that doesn't finish is the same answer, DEFECT §56.)
            _answers = tuple(sorted(
                (str(getattr(tu, "name", "") or ""),
                 _voice_note_final.brake_answer(str(getattr(tu, "name", "") or ""),
                                                str(getattr(res, "content", "") or "")),
                 bool(getattr(res, "is_error", False)))
                for tu, res in zip(_dispatch_uses, results)))
            _same_rounds = _same_rounds + 1 if (_answers and _answers == _last_round_answers) else 1
            _last_round_answers = _answers
            if _same_rounds >= SAME_ANSWER_BRAKE and _brake_tools is None:
                _brake_tools = sorted({a[0] for a in _answers})
                logger.info(
                    "runner: %s gave the same answer %d rounds in a row — the next "
                    "step is a reply (session=%s)",
                    ", ".join(_brake_tools), _same_rounds, state_key,
                )

            # Check detection on each tool call
            critical_hit = False
            for tu, res in zip(_dispatch_uses, results):
                decision = detector.check(
                    tu.name, tu.input,
                    result_hash=str(hash(res.content)) if res.content else None,
                )
                if decision.stuck:
                    _obs.log_detection_trigger(
                        session_key=state_key, detector=decision.detector or "unknown",
                        level=decision.level or "warning", count=decision.count,
                        paired_tool_name=tu.name,
                    )
                    if decision.level == "critical":
                        # Inject synthetic CRITICAL tool_result; loop will see it next iteration
                        await session.append(AgentMessage(
                            role="tool",
                            content=[ToolResultBlock(
                                tool_use_id=f"loop-detection-{iterations}",
                                content=decision.message,
                                is_error=True,
                            )],
                        ))
                        critical_hit = True
                        break
            if critical_hit:
                # Don't break the outer loop — let next LLM iteration see the CRITICAL text
                continue
    except asyncio.CancelledError:
        # A cancelled run is dead work (command/judged stop, or a legacy
        # barge-in). Drained-but-unanswered steers die with it as record
        # (spec §5.3) instead of being re-queued below: the client already
        # tore the queue down at cancel time, and re-queueing here — which
        # runs strictly AFTER that synchronous teardown — would resurrect
        # messages about work the owner just killed and hand them to the
        # next sweep as fresh turns.
        _run_cancelled = True
        raise
    finally:
        shown_this_turn.reset(_shown_token)
        if _shape_token is not None:
            _reply_governor.current_reply_shape.reset(_shape_token)
        if _overrun_task is not None:
            _overrun_task.cancel()
            try:
                await _overrun_task
            except (asyncio.CancelledError, Exception):
                pass
        if _run_slot_held:
            try:
                from core.agent import task_slots as _slots
                _slots.release(state_key, "the run")
            except Exception:
                pass
        if _dx.duplex_enabled():
            # Step 27 (spec §5): her signage was for the work; the work is
            # over. Best-effort, never into the run's outcome.
            _act = _steering.pop_presence_activity(state_key)
            if _act:
                try:
                    from core.agent import hands_runtime as _hr
                    _client = _hr.get_client()
                    if _client is not None:
                        await _client.change_presence(activity=None)
                except Exception:
                    pass
        _steering.clear_run_state(state_key)
        # Step 27: a cooperatively STOPPED run drops them exactly as the
        # hard cancel does — "forget it" killed the work they were about.
        # 2026-08-28: a run that ended naturally WITH a reply answered them —
        # his words were in her context before that reply was composed (the
        # drain seam above); re-queuing produced a redundant turn on an
        # already-answered exchange (13:23, and 08-24 12:59).
        # Phase B: a PAUSED run answered them by the act of pausing — re-queuing
        # its own "hold on" would sweep it into a new turn and un-pause it.
        # Batch 8 (ruling T2-3): a quiet choice answered them too.
        _answered_by_run = (terminated_by == "natural" and bool(_final_reply_text)) \
            or terminated_by == "quiet"
        if (not _run_cancelled and terminated_by not in ("stopped", "paused")
                and not _answered_by_run):
            for _b in _unanswered:
                _steering.enqueue(state_key, _b)

    if terminated_by != "quiet":
        # (Batch 8: a quiet run's buffered words are never released.)
        await chunker.flush()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    _obs.log_loop_end(
        session_key=state_key, iterations=iterations, tracker=tracker,
        tool_calls=tool_calls_total, terminated_by=terminated_by, latency_ms=elapsed_ms,
    )

    # Batch 8, task 2a (ruling T2-3): a quiet choice is binding — no words go
    # out, whatever the chunker gathered on the way.
    response_text = "" if terminated_by == "quiet" else chunker.total_text
    if not response_text and terminated_by not in ("stopped", "paused", "quiet"):
        # (Step 27: a cooperatively STOPPED or PAUSED run must not re-send
        # its last working note as a reply — the fallback is skipped for it.)
        # Fallback: streaming may have been bypassed (e.g., non-streaming
        # provider or test mock that returns LLMResponse directly). In that
        # case, derive response_text from THIS RUN's final assistant message.
        # Scope matters: scanning the whole session here re-sent the PREVIOUS
        # turn's reply whenever the LLM returned 0 text + 0 tool calls — the
        # 2026-06-12 "looping" incident where every model-chose-silence turn
        # made PaLLi repeat its last message verbatim (conversation rows
        # #5425/#5427 were byte-identical).
        for msg in reversed(session.messages[turn_history_start:]):
            if msg.role == "assistant":
                response_text = "".join(
                    b.text for b in msg.content if isinstance(b, TextBlock)
                )
                break

    # R15, Group V: the voice message is made now, once the reply is
    # finished, from the words she last gave voice_note — so the voice and
    # the text come from one finished answer. Beside a quiet choice too
    # (FW-3, below). Shielded, as the transcript writes are: a cancel landing
    # during the speech never cuts it half-way, and the cancel still
    # proceeds (a cancelled run is dead work).
    await asyncio.shield(_voice_note_final.finish(tool_ctx))
    # V fix round 1 (the owner's "never a lost message"): words still waiting
    # here mean the speech failed — pending_words is "" once a note exists.
    # When no text carries the answer, her words go as her text reply, whole
    # (not the 240-character quote); beside a quiet choice too — she chose to
    # say them, so the run is no longer quiet and no quiet row is written. A
    # stopped or paused run sends nothing, as before.
    _unspoken = _voice_note_final.pending_words(tool_ctx)
    if _unspoken and not response_text.strip() and terminated_by not in ("stopped", "paused"):
        if terminated_by == "quiet":
            terminated_by = "natural"
            _quiet_reason = ""
        if chunker.total_text.strip():
            # Only after a quiet choice: text an earlier round streamed was
            # already handed to the door's capture, which sends its chunks
            # rather than response_text — the words go the same way.
            await (on_chunk or _noop_on_chunk)(_unspoken)
        response_text = _unspoken
        logger.warning(
            "runner: the voice message couldn't be made and the reply had no text; "
            "its words (%d chars) go as the text reply (session=%s)",
            len(_unspoken), state_key,
        )
        try:
            await asyncio.shield(session.append(AgentMessage(
                role="assistant", content=[TextBlock(text=_unspoken)])))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the words still go; say why the record lacks them
            logger.warning("runner: could not write the voice message's words to the "
                           "transcript (session=%s): %s", state_key, type(exc).__name__)

    # Second read F8 (2026-10-04): her quiet choice stood, but a voice message
    # she made this run still goes out (ruling FW-3, confirmed by the owner
    # 10-04) — she answered aloud. The record says so: the spoken words are
    # her reply in her transcript (dispatch writes no quiet row when the run
    # carries a voice note, and the quiet call's result already said the
    # voice message goes). The door still sends the note and no text.
    _spoken_words = ""
    if terminated_by == "quiet" and tool_ctx.voice_notes:
        _spoken_words = "\n\n".join(
            w for w in (str(x or "").strip() for x in tool_ctx.voice_note_words) if w)
        if _spoken_words:
            try:
                await asyncio.shield(session.append(AgentMessage(
                    role="assistant", content=[TextBlock(text=_spoken_words)])))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the note still goes; say why the record lacks it
                logger.warning("runner: could not write the voice message's words to the "
                               "transcript (session=%s): %s", state_key, exc)
        logger.info(
            "runner: her quiet choice stood beside a voice message — she answered aloud; "
            "the words are her reply in the transcript (session=%s)", state_key,
        )

    if deps.apply_reflection is not None:
        # Synthesize a TurnDecision-shaped object from cadence_cfg so reflect's
        # decision-keyed routing fires in the same mode as the existing
        # pipeline. Without this, reflect runs in degraded mode (decision=None
        # path). ReAct mode bypasses tier routing, so delegation_tier=0 is
        # the safe analog. The cadence-derived length_bucket maps the cadence
        # length onto the tier-0 length-bucket vocabulary.
        from core.turn.types import BehaviorFrame, TurnDecision
        length_bucket = _cadence_length_to_bucket(getattr(cadence_cfg, "length", "medium"))
        # Momentum wire fix (2026-07-08): the ReAct path never set
        # emotional_context, so ai/relationship_momentum's nudge branches never
        # fired — closeness/warmth only decayed (audit 2026-07-06). Cadence now
        # emits a momentum-vocabulary tag; sanitize to the Literal set here.
        _ec = str(getattr(cadence_cfg, "emotional_context", "") or "").strip().lower()
        decision = TurnDecision(
            should_respond=True,
            length_bucket=length_bucket,
            delegation_tier=0,
            delegation_task_hint=None,
            emotional_context=_ec if _ec in _MOMENTUM_TAGS else None,  # type: ignore[arg-type]
            behavior_frame=BehaviorFrame(
                observation_text=getattr(cadence_cfg, "observation_text", "")
                or "react_loop_completed",
            ),
        )
        # apply_reflection expects a real TurnContext (reads ctx.turn.owner_id,
        # ctx.recent_history, etc.) — pass the raw underlying ctx through the
        # adapter wrapper rather than the flat AgentLoopContext.
        ctx_for_reflect = getattr(ctx, "ctx", ctx)
        # Latency fix (2026-07-08): reflection was the last serialized LLM call
        # on the reply path — every reply waited a full reflection round-trip
        # (audit 2026-07-06; reflect.py's docstring long promised otherwise).
        # Detach it like dispatch's stake appraiser: the reply returns now,
        # reflection lands in the background, and a barge-in cancelling this
        # run_turn no longer kills it. Tests await flush_detached_tasks().
        _spawn_detached(
            deps.apply_reflection(ctx_for_reflect, response_text, decision=decision),
            "apply_reflection",
        )

    # (Prerecorded-attachment enrich moved PRE-loop, 2026-07-08 — spawned as a
    # detached task right after recall so a mid-loop barge-in can't skip it.)

    # She answered in a room whose owner's note stands (Batch 8, task 2b —
    # INTERIM): her record of quiet choices notes it, with her reply's start.
    # (Second read F8: a voice message she sent beside a quiet choice is a
    # reply too — the note records its words.)
    _said_here = _spoken_words if terminated_by == "quiet" else response_text
    if _room_note and _said_here.strip():
        _md = turn_md
        _x = getattr(_md, "extra", None) if _md is not None else None
        _room_notes.after_reply(
            door=str(getattr(turn, "surface", "") or ""), conversation=transcript_key,
            who_id=getattr(turn, "owner_id", None),
            who_name=(getattr(_md, "discord_username", None) if _md is not None else None)
            or (_x.get("telegram_username") if isinstance(_x, dict) else None) or "",
            owner=not is_guest_turn(turn), reply_text=_said_here,
        )

    # Batch 8, task 10 (ruling T10-3): her voice notes go out with the reply.
    # A quiet choice sends no words and no spoken reply (T10-5), but a voice
    # note she made is a delivery, like a file: when her quiet choice stands,
    # it still goes, with no text (final fix wave, ruling FW-3).
    _voice_notes = tuple(tool_ctx.voice_notes)

    # What this reply drew on (R15, memory that reaches, T13/W3): one line in
    # the owner's record (core/reply_sources.py; his dashboards page and
    # `palli_ctl replies`, never a chat) — the dated lines her search
    # returned, the notes, facts and today's lines her prompt carried, a
    # guest's room section, her queries and the look's counts. The owner's
    # and guests' conversation turns alike; never a quiet choice, never her
    # own runs. A failure costs the line, never the reply.
    if (response_text.strip() and terminated_by != "quiet"
            and turn_origin in _qc.QUIET_ORIGINS and _reply_sources.enabled()):
        try:
            _sources = _reply_sources.context_sources(
                raw_ctx, shown=resolved_is_owner and not is_guest_turn(turn))
            _reply_sources.record(
                door=str(getattr(turn, "surface", "") or ""), conversation=transcript_key,
                who_name=_reply_sources.who_name(turn), owner=not is_guest_turn(turn),
                reply_excerpt=response_text, record_lines=list(tool_ctx.record_lines),
                notes=_sources["notes"], facts=_sources["facts"], today=_sources["today"],
                room_lines=_room_lines,
                queries=list(tool_ctx.record_queries),
                look=_reply_sources.look_of(tool_ctx.record_looks),
            )
        except Exception as exc:  # noqa: BLE001 - the owner's record, never the reply's
            logger.warning("runner: the reply-sources line was not written (%s, session=%s)",
                           type(exc).__name__, state_key)

    # An empty response with no deliverables is SILENCE, not a degenerate
    # reply — surfaces must not send anything (and dispatch must not persist
    # an assistant side). A reasoning model occasionally emits only
    # chain-of-thought; treating that as a respond-turn is what produced the
    # stale-resend loop. A voice note is a deliverable (T10-3).
    return RunResult(
        responded=bool(response_text) or bool(tool_ctx.deliveries) or bool(_voice_notes),
        response_text=response_text,
        iterations=iterations,
        terminated_by=terminated_by,
        attachments=tuple(tool_ctx.deliveries),
        image_descriptions=_img_descs,
        text_attachment_contents=_txt_attachs,
        quiet=terminated_by == "quiet",
        quiet_reason=_quiet_reason,
        voice_notes=_voice_notes,
    )


async def _noop_on_chunk(_text: str) -> None:
    pass


def _cadence_length_to_bucket(length: str) -> str:
    """Map cadence's length vocabulary ("short"/"medium"/"long") onto the
    TurnDecision.length_bucket Literal ("tiny"/"short"/"medium"/"long")
    so the synthesized decision passes the dataclass's type contract.
    Cadence doesn't currently emit "tiny", so the mapping is identity for
    everything cadence might produce; unknown values default to "medium".
    """
    valid = {"tiny", "short", "medium", "long"}
    return length if length in valid else "medium"

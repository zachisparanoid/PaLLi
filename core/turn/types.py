from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


SurfaceName = Literal[
    "discord_text",
    "discord_voice",
    # Telegram door (2026-09-14, task 2): a second chat surface on the same
    # spine. core.turn.ingest validates against this tuple, so a Telegram
    # turn built through normalize_turn would be refused without it.
    "telegram_text",
    # The mobile door (2026-10-13): the owner's phone app, a third chat
    # surface on the same spine. Its turns are built directly at intake.
    "mobile_text",
    "voice_local",
    "dashboard",
    "proactive",
    "reminder_fire",
    # Her own work (roadmap 16-17, 2026-07-28): a self-initiated work
    # session. Not a surface anyone speaks on — the dispatch/delivery seam
    # reads it and sends nowhere (own-work spec §4b, null delivery).
    "workbench",
]


@dataclass(frozen=True, slots=True)
class ImageRef:
    """A reference to an image attached to a turn (URL or local path)."""

    url: str | None
    local_path: str | None
    mime: str | None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class TextAttachmentRef:
    """A reference to a text/code attachment the user dropped into a message."""

    url: str | None
    filename: str | None
    mime: str | None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class TurnMetadata:
    """Surface- and origin-specific metadata that doesn't fit on Turn directly."""

    channel_id: int | None = None
    channel_name: str | None = None
    guild_id: int | None = None
    guild_name: str | None = None
    is_dm: bool = False
    is_mention: bool = False
    is_reply_to_bot: bool = False
    is_voice: bool = False
    batch_message_ids: tuple[int, ...] = ()
    # "workbench" (2026-07-28) is why this field matters more than it looks:
    # every persistence and appraisal gate on the dispatch spine keys on
    # origin in ("user", "dashboard_user"). A work session left at the "user"
    # default would write her workbench prompt into conversations as a row
    # HE supposedly sent, and appraise her stakes against an exchange that
    # never happened. Her own work is not a conversation with him.
    origin: Literal[
        "user", "monologue", "reminder", "dashboard_user", "workbench", "guest"
    ] = "user"
    candidate_tick: datetime | None = None
    reminder_id: int | None = None
    discord_username: str | None = None
    text_attachments: tuple[TextAttachmentRef, ...] = ()
    # DEFECT_RECORD §19 (2026-09-02): every attachment that is not an image,
    # a voice note, or a text file — a spreadsheet, a PDF, an archive. Recall
    # downloads each into data/inbox/ and hands her the path.
    file_attachments: tuple[TextAttachmentRef, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)
    # Burst coalescing (2026-07-07): set True on the single turn a settled
    # burst dispatches. The fragments were already recorded on arrival, so the
    # engine must not re-append/re-persist the user side. See
    # docs/superpowers/specs/2026-07-07-burst-coalescing-design.md.
    user_side_prerecorded: bool = False
    # Step 2 (2026-10-08): who sent this message, built at intake from the
    # account it arrived from (core/agent/speakers.py). None = not recorded
    # (every synthesized turn, and any caller that doesn't say).
    speaker: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Turn:
    """A canonical turn input. One shape for every surface."""

    surface: SurfaceName
    owner_id: int
    content: str
    images: list[ImageRef] = field(default_factory=list)
    metadata: TurnMetadata = field(default_factory=TurnMetadata)
    received_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    role: Literal["user", "assistant"]
    content: str
    at: datetime


@dataclass(frozen=True, slots=True)
class BehaviorFrame:
    """Per-turn cognitive judgment summary.

    Direction C (2026-05-02): collapsed from a bag-of-structs into a
    single per-turn `observation_text` field. The LLM produces 2-5
    sentences of natural prose describing what the user is signaling
    on this turn (sarcasm, mood, brevity intent, depth intent, fatigue,
    address confidence, theory-of-mind, calibration cues — all of it).
    The response model reads that prose directly as cognitive context
    instead of consuming a dozen translated booleans and floats.

    Why this is a single-field dataclass rather than a bare string:
    keeps the carrier class symmetric with TurnDecision (decide.py
    builds and attaches a BehaviorFrame; downstream consumers read it
    via decision.behavior_frame.*) so existing wiring stays intact
    without churn. If/when the dataclass earns more fields (e.g., a
    confidence score on the observation), they'll land here.
    """

    observation_text: str = ""


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Everything the rest of the loop needs to think well. Built by recall."""

    turn: Turn
    recent_history: tuple[HistoryEntry, ...] = ()
    compiled_identity: Any = None  # CompiledIdentity from core.identity (defined in Wave 2.1)
    register_directive: str = ""
    recent_memory_hits: tuple[str, ...] = ()
    relevant_facts: tuple[dict, ...] = ()
    # Batch 5, task 4 (memory that picks, 2026-09-28, ruling R6): the
    # ``mcb_`` id of every MEMORY.md bullet recall's search returned for
    # this turn that cleared the hits' relevance floor (R40), in result
    # order, BEFORE the dedup against the memory core — a bullet the core
    # already shows was still surfaced. Task 5 bumps each one's reference
    # stamp after the reply. () when the search returned no such bullet. A
    # declared field: the context is frozen+slots, and recall fills it in
    # its single constructor call.
    memory_lookup_ids: tuple[str, ...] = ()
    monologue_digest: str = ""
    unfulfilled_first_contact_targets: tuple[str, ...] = ()
    # Direction C (2026-05-02): the entire structured-cognitive bag
    # (interaction_mode_read, pragmatic_reading, emotional_state_projection,
    # topic_boundaries, relationship_calibration, sentiment_baseline) was
    # collapsed into a single per-turn observation prose field. Recall now
    # produces this by calling the LLM once with an open-ended "describe
    # what the user is signaling" prompt; downstream consumers read the
    # prose directly. See docs/specs/2026-05-02-direction-c-cognitive-prose-spec.md.
    observation_text: str = ""
    # Vision analysis output for any images attached to this turn. One
    # string per image, in the same order as turn.images. Populated by
    # recall._safe_analyze_images via ai.vision_analyzer.
    image_descriptions: tuple[str, ...] = ()
    # Decoded contents of any text/code attachments on this turn, keyed
    # in the same order as turn.metadata.text_attachments. Populated by
    # recall._safe_load_text_attachments.
    text_attachment_contents: tuple[str, ...] = ()
    # Tier-1 tool results from this turn, populated by respond_tier1
    # before delegating to respond_tier0. Each entry is a labeled
    # result line like "[web_search] <result text>" or
    # "[schedule_reminder] reminder #42 scheduled for 2026-05-05 09:00".
    # Empty when no Tier-1 dispatch fired or when the tool returned no
    # usable result. respond_tier0's capability-honesty section consults
    # this slot to decide whether action-claims are evidence-backed.
    tool_results: tuple[str, ...] = ()
    # Temporal-awareness plan, Workstream E: count of inner monologue
    # ticks that fired between the user's previous message and this
    # one. Strong session-boundary signal — N >= 4 typically means
    # 4+ hours have passed (monologue ticks default to ~hourly cadence)
    # which is highly suggestive of a session break. Decide reads this
    # to disambiguate continuation vs new session.
    monologue_ticks_since_last_user_message: int = 0
    # Companion timestamp of the user's most recent prior message,
    # exposed at the TurnContext level so decide and respond don't
    # have to re-scan history to find it. None on first-ever turn.
    last_user_message_at: Any = None  # datetime | None — Any to keep slots-friendly
    # F6 (companion.situational_awareness): a brief note on what PaLLi
    # notices about the user's current context (active app, presence,
    # time-of-day) — surfaced into the reply so PaLLi can reference it
    # naturally. The full version feeds the monologue; this is a short
    # slice for the live turn. Empty when the faculty is off or no
    # situational signal is available (e.g. headless host).
    situational_note: str = ""
    # F7 (companion.relationship_calibration): a short prose directive
    # describing the accumulated relationship read (how close/warm/playful/
    # direct to be), derived from the compounding momentum state. Empty on
    # cold start or when the faculty is off. Set by recall, consumed by the
    # reply via the agent-loop system prompt.
    relationship_posture: str = ""
    # Caring memory (2026-06-11, companion.caring_memory): the unified
    # per-turn read — an ai/turn_read.TurnRead instance (or None on skip/
    # failure). Set by recall pre-reply; consumed by the system prompt
    # (awareness), and by reflect post-reply (timeline write, salience
    # stamp, circle-back thread). Typed Any to keep types.py import-light.
    turn_read: Any = None
    # Compact prose render of the recent emotional_timeline entries
    # (oldest→newest), for the prompt's "recent arc" section. Empty when
    # no meaningful history or faculty off.
    emotional_arc_text: str = ""
    # Stake ledger (2026-07-06, companion.stake_ledger): compact first-person
    # render of PaLLi's open stakes (ai/stake_ledger.py — held hopes/worries/
    # tastes/wonders), read pre-reply so the responder itself carries them.
    # Set by recall; consumed by the agent-loop system prompt and the turn
    # read's input. Empty when the faculty is off (its default) or on any
    # ledger-read failure.
    held_stakes_text: str = ""
    # Night Cycle daily-meaning (ai/night_cycle.py — the private first-person
    # "what today meant" note). Read by recall when companion.night_cycle is on;
    # surfaced in the next day's reply. Empty when the faculty is off (default)
    # or on any read failure.
    night_cycle_reflection: str = ""
    # Present Moment Slice 2 (2026-07-27): the rendered reply-band inner text
    # (age line + her latest moment prose) — "" when either flag is off, the
    # store is empty, or any read fails. Set by recall (sync, best-effort);
    # consumed by the agent-loop system prompt as the "## Your present
    # moment" band. The cycle stays the moment's only WRITER; this is a read.
    present_moment_text: str = ""
    # The authorship trace (2026-08-16): her recent workbench work as plain
    # authorship facts, read at recall when companion.own_work is on — ""
    # otherwise. Rendered by the agent-loop system prompt as the "## Your
    # own recent work" band so she can speak about what her hands built
    # without live archaeology (the three-doors/claw conversations).
    own_work_trace: str = ""
    # The grounding rule (2026-08-17): her recent DEEDS — actions, not words
    # — in the reply prompt. The arbiter and her moment could already see
    # them; the self that ANSWERS HIM could not, so "what have you been up
    # to?" had nothing but improvisation to reach for (08-17 18:17: "poking
    # at the claw" — untouched since 08-14). Read at recall; "" on failure.
    recent_deeds_text: str = ""
    # Grounded Vulnerability door 1 (organ 4.9, 2026-09-01, spec §3): at
    # most two unsaid small truths from her own week, each with its age,
    # under the law that governs OFFERING them. Read at recall from her
    # book (ai/disclosure_store.py); "" when companion.vulnerability is
    # off, when nothing is unsaid, or on any failure — and "" means the
    # reply prompt is byte-identical to a build without the organ.
    disclosures_text: str = ""
    # Shared Idiolect (organ 4.12, 2026-09-02, spec §4): the most recent
    # open bits of theirs as light memory under CALLBACKS_LAW. "" when
    # companion.idiolect is off, when the book is empty, or on failure.
    callbacks_text: str = ""
    # One thing on her mind (Batch 6, task 1, 2026-09-29): the night's pick
    # as the whole band, heading included (ai/tonight.py render_band). ""
    # when companion.tonight is off, when the night picked nothing, once she
    # has said it, on a turn that is not a conversation, or on failure.
    tonight_text: str = ""
    # The presence slice (10-13): the stake the first look's "my_own_from"
    # named, for the said check after the reply (core/turn/reflect.py
    # _record_stake_said); None when the look drew on no stake.
    own_stake_drawn: Any = None
    # Her own life lately (D2's lines without the look's tags), as her first
    # look saw it, for the reply's own section (D9). "" without a look.
    own_life_text: str = ""
    # Narrative Self (Wave 1b): her three self-authored documents, read in
    # recall when companion.narrative_self is on — empty strings otherwise.
    # Injected as system-prompt bands: hers to speak from, never to recite.
    narrative_care_spine: str = ""
    narrative_my_story: str = ""
    narrative_story_of_us: str = ""
    # The sidecar's read-only view of its parent (step 27 Phase B). Carried on
    # a real field since 2026-09-09: TurnContext is frozen+slots, so the old
    # setattr fallback in the sidecar wrapper could never work, and her first
    # live task sidecar died before its prompt was built. "" outside a sidecar.
    sidecar_snapshot_text: str = ""


@dataclass(frozen=True, slots=True)
class TurnDecision:
    """One LLM call's worth of judgment about this turn.

    Companion-100 autonomous session (2026-05-04): added the
    emotional_context field, which tags the conversational/emotional
    register so respond_tier0 can adjust without regex-matching the user's
    text. (Batch 7, 2026-10-04: the old Tier-1 routing field went with the
    decide LLM that filled it; nothing read it.)
    """

    should_respond: bool
    length_bucket: Literal["tiny", "short", "medium", "long"]
    delegation_tier: Literal[0, 1, 2]
    delegation_task_hint: str | None
    # Emotional/conversational register tag from decide. Used by
    # respond_tier0 to adjust posture (e.g., "vulnerable_close" → hold
    # the moment; "playful_banter" → match the energy). The decide LLM
    # picks one; respond reads it. No regex. None when register is
    # neutral / no special handling needed.
    emotional_context: Literal[
        "vulnerable_close",
        "venting",
        "playful_banter",
        "deep_intimacy",
        "frustrated",
        "casual_greeting",
        "neutral",
    ] | None = None
    # Temporal-awareness plan, Workstream B: conversation_phase tags
    # whether this turn is mid-flow, a topic shift, or a fresh
    # re-engagement after a gap. respond_tier0 reads it to know whether
    # to treat prior conversation as a live thread or background
    # memory. None means decide didn't emit one; safe default behavior
    # is "same_session_continuation."
    conversation_phase: Literal[
        "same_session_continuation",
        "same_session_topic_shift",
        "new_session_short_break",
        "new_session_after_long_gap",
        "proactive_outreach",
    ] | None = None
    # Cadenced-delivery plan: cadence_appropriate gates whether the
    # respond stage should generate multiple beats vs one block. The
    # decide LLM picks this per-turn based on whether multi-message
    # delivery would feel natural for THIS specific reply. Default
    # False — single-beat replies are the safe default and what the
    # vast majority of turns get.
    cadence_appropriate: bool = False
    # Optional target beat count from decide. None means "let respond
    # decide within range." Capped at 6 by validation. Used as a soft
    # hint; respond can produce fewer if the content doesn't warrant
    # more.
    cadence_target_beats: int | None = None
    # Direction C (2026-05-02): TurnDecision shrunk to four control-flow
    # signals (should_respond, length_bucket, delegation_tier,
    # delegation_task_hint) plus the carrier for cognitive prose
    # (behavior_frame.observation_text). The LLM's qualitative reading
    # of the user lives in the BehaviorFrame; everything that used to
    # be a structured field on this dataclass (intent, sentiment_refinement,
    # register_override, needs_search, needs_screenshot, notable_for_reflection,
    # first_contact_question, reasoning) now lives in observation prose
    # OR has been dropped because the new contract doesn't need it.
    behavior_frame: BehaviorFrame = field(default_factory=BehaviorFrame)


@dataclass(frozen=True, slots=True)
class ReflectionMemoryUpdates:
    durable_context_add: list[str] = field(default_factory=list)
    # 2026-08-18 (friend-shaped memory): exact text of an existing Durable
    # Context line this turn superseded — a correction ("Miso" → "Mochi"),
    # a change ("shelved the cabin dream"). The writer decides; code retires.
    durable_context_retire: list[str] = field(default_factory=list)
    active_threads_add: list[str] = field(default_factory=list)
    active_threads_resolve: list[str] = field(default_factory=list)
    important_decisions_add: list[str] = field(default_factory=list)
    useful_references_add: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReflectionRelationshipDelta:
    trust_shift: Literal["+1", "0", "-1"] = "0"
    posture_shift: str | None = None
    summary_revision: str | None = None


@dataclass(frozen=True, slots=True)
class ReflectionFactAdd:
    key: str
    value: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ReflectionPlan:
    memory_updates: ReflectionMemoryUpdates
    personality_observations: list[str]
    relationship_delta: ReflectionRelationshipDelta | None
    user_facts_add: list[ReflectionFactAdd]
    monologue_note: str | None
    first_contact_resolutions: dict[str, str]
    skip_reason: str | None  # "small_talk", "already_known", "low_confidence", or None
    # Temporal-awareness plan, Workstream D: True when reflect detects
    # the conversation reached a natural close ("goodnight", "talk
    # later", "I gotta run", "let's pick this up later"). When True,
    # reflect writes a `last_session_closed_at` timestamp to durable
    # context so decide can use it as a strong session-boundary signal
    # on the next user message.
    session_close_signal: bool = False
    session_close_reason: str = ""  # "goodbye", "going_to_bed", "explicit_close", "topic_resolved", ""
    # 2026-05-10 — Open-Threads Agenda. Capture and resolve flow through
    # these two fields, dispatched in ``apply_reflection`` via
    # ``brain.add_open_thread`` / ``brain.close_open_thread``.
    open_threads_to_add: list[dict[str, str]] = field(default_factory=list)
    open_threads_to_close: list[dict[str, str]] = field(default_factory=list)
    # Wanting (step 20): durably-directive turns file into her wishbook.
    # Parsed in reflect._parse_quests_to_add; dispatched in
    # apply_reflection via ai.quest_store.plant (dormant-at-cap).
    quests_to_add: list[dict[str, str]] = field(default_factory=list)
    # Corrections of record (2026-08-17): a turn that establishes something
    # she believed or said was WRONG — his correction with evidence, or her
    # own verified retraction — files into the corrections ledger, which
    # outranks every older gist/reflection/journal line. Parsed in
    # reflect._parse_corrections_to_add; dispatched in apply_reflection via
    # ai.corrections_store.record_correction. Always on: truth machinery.
    corrections_to_add: list[dict[str, str]] = field(default_factory=list)
    # Fears (organ 6, step 24 §6): on RARE turns a fear of HERS crystallizes
    # — text + her one-line watch-condition. Parsed in
    # reflect._parse_fears_to_add; dispatched in apply_reflection via
    # ai.fear_store.plant (decline-at-cap; ≤1/night in code; most turns
    # file nothing — decision 5).
    fears_to_add: list[dict[str, str]] = field(default_factory=list)
    # Grounded Vulnerability (organ 4.9, spec §3/§5): the id of the one
    # small truth of HERS this reply genuinely shared — door 1's other
    # half. Her reply prompt carries the truths in lived language with no
    # ids (the fourth wall); the machine-facing reflect block carries the
    # same rows WITH ids, so this is the only place "said once, forever"
    # can actually be stamped. Parsed in reflect._parse_disclosure_said;
    # dispatched via ai.disclosure_store.mark_said(via="reply"), which
    # refuses an unknown, already-said, or faded id.
    disclosure_said: str = ""
    # Shared Idiolect (organ 4.12, spec §2): a bit of THEIRS born this turn
    # — the LLM names it and quotes the line; the receipt (who/when) is
    # stamped by CODE at dispatch, where the turn's own text lives, after
    # proving the quote is really in it. UNVERIFIED here by design.
    callbacks_born: list[dict[str, str]] = field(default_factory=list)
    # His "that's not ours" — an open id + his words. Retires, never deletes.
    callbacks_retired: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Beat:
    """One beat of a cadenced reply.

    `text` is what gets sent. `delay_after_previous_seconds` is the
    pause to wait BEFORE this beat fires, relative to the previous
    beat's send time (or 0 for the first beat).

    Cadenced-delivery plan: a single LLM call generates the full
    `beats: list[Beat]` structure when cadence_appropriate=True.
    Surface adapters (Discord, dashboard, voice) interpret beats per
    surface — Discord stagger-sends, dashboard concatenates with
    metadata, voice (Phase 1) treats as single utterance.
    """

    text: str
    delay_after_previous_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class CadencedReply:
    """Reply structure that may be a single beat or multiple beats.

    `beats` is always at least 1 entry. Single-beat replies are the
    common case; multi-beat replies fire only when decide+respond
    judged cadence appropriate AND the content actually warrants it.

    `total_text` is the concatenated content for storage in
    conversation_history (single row, separator-joined) and for
    surfaces that don't support staggered delivery.
    """

    beats: tuple[Beat, ...]
    total_text: str
    # 2026-05-06: file paths produced by tier-1 tools that should be
    # delivered as attachments alongside the reply (e.g. screenshot
    # captures, webcam snaps, command output files). Populated from the
    # paths deliver_artifact handed over on the turn's ToolContext
    # (week 2 task 2). Surface adapters (Discord, dashboard) read this
    # and attach the files to the outbound message.
    attachments: tuple[str, ...] = ()

    def is_single_beat(self) -> bool:
        return len(self.beats) <= 1

    @classmethod
    def from_single(cls, text: str) -> CadencedReply:
        return cls(beats=(Beat(text=text),), total_text=text)


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Output of process_turn(). Used by callers and observability."""

    responded: bool
    response_text: str
    decision: TurnDecision | None
    reflection_applied: bool
    delegation_tier_used: int
    error: str | None = None
    # Step 27 Phase B (spec §3): how the ReAct loop ended ("natural" /
    # "stopped" / "paused" / …) — the client arms the pause-expiry clock and
    # skips the post-run sweep on "paused". "" for non-runner turns.
    terminated_by: str = ""
    # Cadenced-delivery plan: when respond produced multi-beat output,
    # the full structure travels here so surface adapters can fan out.
    # response_text remains the concatenated total for backward compat.
    cadenced_reply: CadencedReply | None = None
    # 2026-05-06: file paths to attach to the outbound message. Mirrors
    # cadenced_reply.attachments when present; explicit field on
    # TurnResult so callers that don't unpack the cadenced reply can
    # still see the attachments.
    attachments: tuple[str, ...] = ()
    # Batch 8, task 2a (B8-2, ruling T2-3): she chose to stay quiet. Every
    # door sends nothing (``responded`` is False unless she delivered a file
    # earlier in the run, which goes out with no words); the dashboard draws
    # no bubble at all. ``quiet_reason`` is her one line.
    quiet: bool = False
    quiet_reason: str = ""
    # Batch 8, task 10 (B8-5, ruling T10-3): her voice messages (OGG/Opus
    # bytes from the voice_note tool) for the door to send with the reply —
    # a Discord audio attachment, a Telegram voice note. A delivery part like
    # the words; counted in ``responded``; empty when she chose quiet.
    voice_notes: tuple[bytes, ...] = ()

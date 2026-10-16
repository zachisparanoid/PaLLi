"""Flatten TurnContext into the named string fields the agent loop expects.

D.2 Wave 1 (2026-05): ``core/agent/system_prompt.build_system_prompt`` and
``core/agent/cadence.shape_response`` and ``core/agent/runner.run_turn``
all read fields like ``identity_core_text``, ``user_text``,
``recent_history_text``, ``memory_text``, ``facts_text``, ``runtime_text``,
``skills_text`` via ``getattr(ctx, "X", "")`` — but none of those fields
exist on :class:`core.turn.types.TurnContext` (which stores structured
data: ``compiled_identity``, ``recent_history``, ``recent_memory_hits``,
``relevant_facts``, etc.). Pre-fix, every reader silently saw empty
strings; the agent loop ran with an empty system prompt and an empty
user message, and the LLM produced nonsense without crashing. Tests
didn't catch this because they mock the LLM and feed it scripted
``FakeCtx`` objects with the renamed fields hand-wired.

This adapter does the rendering once at dispatch time so the agent loop
sees real strings. The :class:`TurnContext` itself stays pure — recall is
an unchanged context-gatherer; the text-rendering needs are an agent-loop
concern.

The render helpers cross-reference :mod:`core.turn.respond_tier0`'s
``_build_system_prompt`` so ReAct sees the same identity / memory / facts /
history content the pipeline path does. Where the agent loop only has
slots for fewer named strings than respond_tier0's rich section layout,
the helpers fold related sections together (e.g. ``runtime_text``
carries both the current-time block and the capability-disclosure block).

Subsequent work in Phase D can fan these slots out further if the audit
shows the agent loop deserves a richer section layout — for now, the
priority is "stop running with an empty prompt".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.turn.types import HistoryEntry, Turn, TurnContext


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentLoopContext:
    """Flat view over a :class:`TurnContext` for the agent loop.

    The seven string fields below are what
    :func:`core.agent.system_prompt.build_system_prompt`,
    :func:`core.agent.cadence.shape_response`, and
    :func:`core.agent.runner.run_turn` read via ``getattr`` lookups.
    Pre-D.2 every lookup returned ``""`` because the TurnContext shape
    didn't carry these fields; the adapter renders them once so the loop
    runs on real content.

    The wrapped :attr:`turn` and :attr:`ctx` stay accessible for downstream
    tool dispatch / observability code that wants the structured data.
    """

    turn: Turn
    ctx: TurnContext
    identity_core_text: str
    skills_text: str
    runtime_text: str
    memory_text: str
    facts_text: str
    user_text: str
    recent_history_text: str
    register_directive: str = ""
    situational_text: str = ""
    relationship_text: str = ""
    images_text: str = ""
    turn_read_text: str = ""
    emotional_arc_text: str = ""
    # Stake ledger (2026-07-06): first-person render of PaLLi's open stakes.
    # The system prompt reads ONLY these flat fields — a TurnContext field
    # that isn't flattened here never reaches the responder.
    held_stakes_text: str = ""
    # Her own life lately, for the reply (the presence slice, D9); the
    # owner's turns only, and in a group only what the room check passed.
    own_life_text: str = ""
    # Night Cycle (2026-07-11): the latest daily-meaning reflection, surfaced in
    # the next day's reply. Empty when companion.night_cycle is off.
    night_cycle_reflection_text: str = ""
    # Present Moment Slice 2 (2026-07-27): the rendered moment band inner
    # text (recall's _read_present_moment_band) — "" when dark.
    present_moment_text: str = ""
    # Authorship trace (2026-08-16): her recent workbench work — "" when dark.
    own_work_trace_text: str = ""
    # The grounding rule (2026-08-17): her recent deeds — "" when the ring
    # is empty or unreadable.
    recent_deeds_text: str = ""
    # Grounded Vulnerability door 1 (organ 4.9, 2026-09-01): her unsaid
    # small truths + their law — "" when the organ is dark, and "" for a
    # guest (they are hers to offer HIM).
    disclosures_text: str = ""
    # Shared Idiolect (organ 4.12): "" when dark, and "" for a guest —
    # theirs, not a stranger's.
    callbacks_text: str = ""
    # One thing on her mind (Batch 6, task 1): the night's pick, the whole
    # band — "" when dark, said or none, and "" for a guest (it is for the
    # owner to hear).
    tonight_text: str = ""
    # Narrative Self (Wave 1b) — flattened self-document bands.
    narrative_care_spine_text: str = ""
    narrative_my_story_text: str = ""
    narrative_story_of_us_text: str = ""
    # The spoken-register band (step 31, 2026-08-20, spec §4): non-empty
    # exactly when this turn's reply will be said aloud.
    spoken_register_text: str = ""
    # Her person (week 1, task 4): "Your person: {name} ({pronouns})." from
    # the owner's profile once learned, else the two knobs. Static per
    # owner — it sits in the cache-stable prefix. "" for a guest turn (the
    # gate below) or a ctx built by hand without it.
    person_line: str = ""
    # The memory doc (batch 2, task 3, 2026-09-27): MEMORY.md rendered on
    # its own — since batch 5, task 3 (2026-09-28) as its CORE under a
    # budget (compiler.plan_memory_core; the same label and preamble as
    # before, sections least-changing first) — so the system prompt can put
    # it at the END of the cache-stable prefix (followed only by the
    # corrections band since batch 5, task 7) instead of inside
    # identity_core_text, where an edit (~7x a day) moved the first
    # differing byte to the top of the prompt. Not P()-wrapped, like
    # identity_core_text was with the doc inside it: the bundle is compiled
    # for the SPEAKER, so a guest's is their own doc, exactly as before.
    # Since batch 5, task 7 (ruling R46) the preferences doc's pref_ rows
    # follow the core in this same slot (compiler.render_preferences_block).
    owner_memory_text: str = ""
    # Corrections of record (batch 5, task 7, 2026-09-28, rulings R20/R21):
    # ai.corrections_store.render_corrections_block at the budget the
    # setting ``memory.corrections_budget_chars`` names (default 1,500),
    # without syncing the owner's file, with the reply-only footer (R61) —
    # the block every night-time gather puts first, now in the reply too,
    # right after the memory band in the stable prefix. "" when nothing is
    # standing, "" for a guest (the corrections are the owner's), "" when
    # the store fails.
    corrections_text: str = ""
    # Memory that reaches (R15, W2): on a guest's turn, the lines of that
    # room's own history the runner's step found (ai.memory_reach_search
    # .room_section_for), set by the runner after this is built; "" on every
    # other turn, and then the prompt is byte-identical to a build without it.
    room_memory_text: str = ""
    needs_support: float = 0.0
    is_owner: bool = True


def build_agent_loop_context(
    turn: Turn,
    ctx: TurnContext,
    *,
    is_owner: bool = True,
) -> AgentLoopContext:
    """Render :class:`TurnContext` fields into the named strings the agent
    loop expects.

    Each render helper's output format mirrors what
    :mod:`core.turn.respond_tier0` produces for the equivalent prompt
    section in pipeline mode; the rendering is shared in spirit (same
    underlying ``ctx`` fields, same labels) without coupling the agent
    loop to ``respond_tier0``'s exact section ordering.
    """
    # The owner gate (item 7a, 2026-08-19): a GUEST turn (a non-owner
    # Discord author / voice speaker) renders her identity core, the room's
    # conversation and the message — and none of the owner's private bands:
    # memory hits, facts, relationship, turn read, emotional arc, stakes,
    # the night's reflection, the moment, her workbench trace, deeds, her
    # small truths, their private language, the night's pick, the story of
    # us, the care spine, the operator skills catalog, her life story, and
    # (Batch 8, task 1, ruling R1) the ambient-awareness band: the owner's
    # presence and the house right now. The room's conversation is rendered
    # by audience in the runner (diet_for_llm): a stranger reads what was
    # said out loud, a collaborator also the work she did in that room; the
    # bands here stay blank for both.
    # Several of those stores are global (not keyed by owner id) — stakes,
    # the moment, deeds, the trace, her book of small truths, their book of
    # callbacks, the night's pick — so keying the turn to the guest is not
    # enough; the render itself must stay quiet. is_owner False ⇒ private
    # "" here.
    #
    # runtime_text is deliberately NOT P()-wrapped (fix round 1, 2026-09-25
    # review): it carries the clock and the time-gap framing, which are not
    # private — a guest hears her speak the time too — alongside the §32
    # "Where I Am" block (process/pid/session/doors) and the sidecar's
    # parent snapshot, which ARE private (hers to know, not a stranger's map
    # of where she lives). _render_runtime takes is_owner itself and gates
    # only those last two pieces in place, so a guest still gets "## Current
    # Time" but never "## Where I Am". (Round 0 wrapped the whole field in
    # P(), which silently took the clock away from every guest turn too.)
    P = (lambda v: v) if is_owner else (lambda v: "")
    # The presence slice (the owner, 10-13: "Only its own life + that room"):
    # on the owner's own turn in a group, friends read the reply, so what she
    # would volunteer about him (her hopes and worries, tonight's thought,
    # her small truths) stays for the private chat. Her memory, for
    # answering him, is unchanged. The owner, 10-14 ("Yes, keep them private
    # too", on Fable's final review): her present moment, their private
    # callbacks, last night's reflection and the story of us stay there too.
    from core.agent.speakers import is_shared_room

    V = (lambda v: "") if is_owner and is_shared_room(turn) else P
    return AgentLoopContext(
        turn=turn,
        ctx=ctx,
        identity_core_text=_render_identity_core(ctx),
        owner_memory_text=_render_owner_memory(ctx),
        corrections_text=_render_corrections(ctx, is_owner=is_owner),
        skills_text=P(_render_skills(ctx)),
        runtime_text=_render_runtime(ctx, is_owner=is_owner),
        memory_text=P(_render_memory(ctx)),
        facts_text=P(_render_facts(ctx)),
        user_text=str(turn.content or ""),
        recent_history_text=_render_recent_history(ctx),
        register_directive=_render_register(ctx),
        # Batch 8, task 1 (ruling R1): P()-wrapped. It carries the owner's
        # Discord presence and the house right now (who is home, since
        # when) — the house is the owner's, never a guest's to read.
        situational_text=P(_render_situational(ctx)),
        images_text=_render_images(ctx),
        relationship_text=P(str(getattr(ctx, "relationship_posture", "") or "").strip()),
        turn_read_text=P(str(getattr(getattr(ctx, "turn_read", None), "read", "") or "").strip()),
        emotional_arc_text=P(str(getattr(ctx, "emotional_arc_text", "") or "").strip()),
        held_stakes_text=V(str(getattr(ctx, "held_stakes_text", "") or "").strip()),
        own_life_text=P(str(getattr(ctx, "own_life_text", "") or "").strip()),
        night_cycle_reflection_text=V(str(getattr(ctx, "night_cycle_reflection", "") or "").strip()),
        present_moment_text=V(str(getattr(ctx, "present_moment_text", "") or "").strip()),
        own_work_trace_text=P(str(getattr(ctx, "own_work_trace", "") or "").strip()),
        recent_deeds_text=P(str(getattr(ctx, "recent_deeds_text", "") or "").strip()),
        disclosures_text=V(str(getattr(ctx, "disclosures_text", "") or "").strip()),
        callbacks_text=V(str(getattr(ctx, "callbacks_text", "") or "").strip()),
        tonight_text=V(str(getattr(ctx, "tonight_text", "") or "").strip()),
        narrative_care_spine_text=P(str(getattr(ctx, "narrative_care_spine", "") or "").strip()),
        narrative_my_story_text=P(str(getattr(ctx, "narrative_my_story", "") or "").strip()),
        narrative_story_of_us_text=V(str(getattr(ctx, "narrative_story_of_us", "") or "").strip()),
        # Step 31 (spec §4): the turn already knows it will be spoken
        # (voice/runtime.py tags metadata.is_voice); the prompt now knows
        # too. Not P()-wrapped: register is not private — a guest hears
        # her speak as well. Text turns: "" ⇒ no section ⇒ byte-identical.
        spoken_register_text=_render_spoken_register(turn),
        # Her person (week 1, task 4). P()-wrapped like every owner-derived
        # band: a guest turn's bundle is compiled for the SPEAKER's id
        # (recall: compile_for_owner(owner_id=turn.owner_id); the guest
        # surface builds the turn with owner_id=author_id), so the owner's
        # profile is not in it — the KNOBS would fill the line instead, and
        # LOCAL_OWNER_DISPLAY_NAME can carry the owner's real name. A
        # stranger never learns it here. Guest ⇒ "" ⇒ no line.
        person_line=P(owner_person_line(getattr(ctx, "compiled_identity", None))),
        # R15, W2: empty here; the runner's guest step fills it.
        room_memory_text="",
        needs_support=float(getattr(getattr(ctx, "turn_read", None), "needs_support", 0.0) or 0.0) if is_owner else 0.0,
        is_owner=is_owner,
    )


def _render_spoken_register(turn: Turn) -> str:
    """The spoken-register band (step 31, spec §4). Standing, like the law
    of reaching — register truth, not a feature; _plan_voice_reply's cap
    stays underneath as the safety net, no longer the author."""
    if not bool(getattr(getattr(turn, "metadata", None), "is_voice", False)):
        return ""
    # A TEXT door never speaks (telegram door, 2026-09-14, final review I3).
    # The phone app is a text door too (the mobile door, 2026-10-13).
    # Telegram marks a VOICE NOTE with metadata.is_voice — she reads it (the
    # transcript is resolved before the turn is recorded) and answers in
    # text; `/voice on` may send a voice note ALONGSIDE those words, never
    # instead of them. Telling her the reply "will be said aloud in the
    # voice channel" was therefore an untruth on every Telegram voice note,
    # and an expensive one: it costs him the long form, the lists and the
    # links in a chat that renders all three. The Discord half of the same
    # question is the owner's DEFECT entry, not this wave's to change.
    if str(getattr(turn, "surface", "") or "") in ("telegram_text", "mobile_text"):
        return ""
    return (
        "SPOKEN, NOT WRITTEN: this reply will be said aloud in the voice "
        "channel. One to three spoken sentences, your spoken rhythm — no "
        "lists, no markdown, no links, no headings. If something genuinely "
        "needs the long form, say the one live sentence and put the rest in "
        "text. Your VOICE_IDENTITY registers apply — where you actually are "
        "decides which."
    )


# The first-contact rule for "is this a real answer": not empty, not a
# `{placeholder}` the template left behind, and — for the name — not one of
# the stand-in labels (identity/bootstrap/FIRST_CONTACT.md,
# target_completion_rules.user_name).
_STAND_IN_NAMES = frozenset({"local operator", "operator", "owner", "user"})


def _learned(value: Any, *, stand_ins: frozenset[str] = frozenset()) -> str:
    """The value as a real answer, or "" while it is still a default — or
    is not a string at all: a list or a number in the frontmatter is not an
    answer, and its repr must never reach the cache-stable prefix."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or (text.startswith("{") and text.endswith("}")):
        return ""
    if text.lower() in stand_ins:
        return ""
    return text


def resolve_owner_person(compiled: Any) -> tuple[str, str]:
    """(name, pronouns) for her person, the way ``owner_person_line`` finds
    them: the profile's real answers first, else the two knobs. The name is
    "" while it is still a stand-in ("Local Operator", "user"), so callers
    can tell "known" from "default"."""
    from core.config import config

    frontmatter: dict[str, Any] = {}
    try:
        doc = (getattr(compiled, "owner", None) or {}).get("personality_profile")
        frontmatter = dict(getattr(doc, "frontmatter", None) or {})
    except Exception as exc:
        logger.debug("context_adapter: owner profile unreadable: %s", exc)
    name = _learned(frontmatter.get("display_name"), stand_ins=_STAND_IN_NAMES) or _learned(
        str(getattr(config, "LOCAL_OWNER_DISPLAY_NAME", "") or ""), stand_ins=_STAND_IN_NAMES
    )
    pronouns = _learned(frontmatter.get("pronouns")) or (
        str(getattr(config, "LOCAL_OWNER_PRONOUNS", "") or "").strip() or "they/them"
    )
    return name, pronouns


def owner_person_line(compiled: Any) -> str:
    """The one line that names her person: ``Your person: {name} ({pronouns}).``

    Week 1, task 4. Name and pronouns come from the owner's
    ``personality_profile`` frontmatter (``display_name`` / ``pronouns``)
    when they are real answers — the rule first contact applies — else from
    ``LOCAL_OWNER_DISPLAY_NAME`` / ``LOCAL_OWNER_PRONOUNS``. ``compiled`` is
    the bundle recall already loaded (no second identity load); a profile
    written before the field existed simply lacks it and falls back the same
    way. Nothing volatile goes in: the line sits in the cache-stable prefix.
    """
    from core.config import config

    frontmatter: dict[str, Any] = {}
    try:
        doc = (getattr(compiled, "owner", None) or {}).get("personality_profile")
        frontmatter = dict(getattr(doc, "frontmatter", None) or {})
    except Exception as exc:
        logger.debug("context_adapter: owner profile unreadable: %s", exc)
    name = _learned(frontmatter.get("display_name"), stand_ins=_STAND_IN_NAMES) or (
        str(getattr(config, "LOCAL_OWNER_DISPLAY_NAME", "") or "").strip() or "Local Operator"
    )
    pronouns = _learned(frontmatter.get("pronouns")) or (
        str(getattr(config, "LOCAL_OWNER_PRONOUNS", "") or "").strip() or "they/them"
    )
    return f"Your person: {name} ({pronouns})."


def _render_register(ctx: TurnContext) -> str:
    """F3 (companion.mood_tone), de-mirrored 2026-05-23: wrap recall's CARE
    note for the agent-loop system prompt. This is no longer a "match their
    energy" register — recall only emits a directive when the user's state
    warrants *care* (vulnerable/stressed/tired/etc.), and the note is about
    showing up with care from PaLLi's own voice. Empty for upbeat/neutral
    states (PaLLi just uses its own voice) or when the faculty is off.

    Co-celebration carve-out (2026-07-06): the old blanket "don't adopt their
    mood or energy" quietly banned celebrating WITH him — the one kind of
    energy-matching the owner explicitly wants (de-mirroring is about not
    becoming a personality/mood chameleon, not about staying flat while he's
    excited). Genuine shared excitement is PaLLi's own feeling, not mimicry.
    """
    directive = str(getattr(ctx, "register_directive", "") or "").strip()
    if not directive:
        return ""
    return (
        "## Reading the room (for care, not for mimicry)\n"
        "You sense where they're at. Let it shape how gently or plainly you "
        "show up — but stay YOU. Don't chameleon their mood, slang, or "
        "personality; don't absorb their lows as your own. The one exception "
        "that isn't mimicry: when they're genuinely excited, be excited WITH "
        "them — co-celebration is your own real feeling, never flatten it.\n"
        f"{directive}"
    )


def _render_images(ctx: TurnContext) -> str:
    """Render vision analysis of attached images for the agent-loop prompt.

    The pipeline path (respond_tier0) rendered ``ctx.image_descriptions`` into
    the prompt, but the ReAct path never carried them — so attached-image
    analysis, even when it worked, was dropped before it reached the live
    brain. This closes that gap. When vision failed, recall populates
    image_descriptions with an explicit "could not be analyzed" note (the
    honesty guard), which flows through here unchanged so PaLLi is told it
    can't see rather than confabulating."""
    descriptions = tuple(getattr(ctx, "image_descriptions", ()) or ())
    lines: list[str] = []
    for idx, desc in enumerate(descriptions, start=1):
        text = str(desc or "").strip()
        if not text:
            continue
        prefix = f"Image {idx}" if len(descriptions) > 1 else "Attached image"
        lines.append(f"**{prefix}:** {text}")
    if not lines:
        return ""
    return (
        "## Attached image(s) — what you can actually see\n"
        "The user attached an image this turn; this is your own visual "
        "analysis of it. Respond to what is genuinely here — do not invent "
        "details beyond this.\n\n" + "\n\n".join(lines)
    )


def _render_situational(ctx: TurnContext) -> str:
    """F6 (companion.situational_awareness): wrap recall's situational note
    (what PaLLi notices about the user's context) for the system prompt.
    Empty when no signal (faculty off / headless host)."""
    note = str(getattr(ctx, "situational_note", "") or "").strip()
    if not note:
        return ""
    # Batch 7, her voice (audit 4.4): the time's rule lives once, in the
    # Current Time block; this band says the rest plainly, in her words.
    return (
        "## Ambient awareness (private to me)\n"
        "A background sense of their context, like the app they're in. It's "
        "private, not material for the reply: I bring it up only when they "
        "do, or when it bears on what they just asked. Most turns it goes "
        "unmentioned, and it isn't how I open a reply.\n"
        f"{note}"
    )


def _render_identity_core(ctx: TurnContext) -> str:
    """Mirror :func:`core.turn.respond_tier0._identity_section`.

    ``compiled_identity`` is a :class:`core.identity.models.CompiledIdentity`
    when recall succeeded; ``None`` on identity-service failure. The
    canonical rendering is ``compiled_identity.text_prompt()`` — same call
    the pipeline path makes — minus the memory doc (batch 2, task 3,
    2026-09-27), which :func:`_render_owner_memory` renders on its own so
    the system prompt can place it last in the cache-stable prefix.
    """
    compiled = getattr(ctx, "compiled_identity", None)
    if compiled is None:
        return ""
    try:
        text = compiled.text_prompt(exclude=("memory",)) or ""
    except Exception as exc:
        logger.debug("context_adapter: identity.text_prompt() failed: %s", exc)
        return ""
    return text.strip()


def _memory_core_budget() -> int:
    """The memory core's budget — :func:`core.memory_refs.memory_core_budget`
    (Batch 5, task 4, ruling R38: ONE helper, so recall's dedup of its
    look-up against the core and this render plan the same core). Kept
    under this name: a test monkeypatches it here."""
    from core import memory_refs

    return memory_refs.memory_core_budget()


def _render_owner_memory(ctx: TurnContext) -> str:
    """The memory CORE (compiler.plan_memory_core) for the
    ``owner_memory_text`` slot — batch 5, task 3 (2026-09-28): the doc under
    ``memory.core_budget_chars`` instead of whole, ranked with the owner's
    reference stamps as core.memory_refs last loaded them. That snapshot
    changes once a day on purpose, so this text — the last band of the
    stable prefix before the corrections of record — stays byte-identical
    across the day's turns; until recall loads it the refs are ``{}`` and
    the core ranks by stamps alone.
    Degrades to "" exactly like :func:`_render_identity_core`: no bundle, a
    bundle that raises, or a duck-typed / mocked bundle that renders
    something other than text.

    Batch 5, task 7 (ruling R46): the ``preferences`` doc's ``pref_`` rows
    (compiler.render_preferences_block, capped at 2,000 chars) follow the
    core after a blank line, so this slot is core + preferences — and the
    reflection's system message, which reads this slot, inherits them. The
    preferences render has its own try (fix round 1, M2): its failure
    drops the preferences, never her memory core."""
    compiled = getattr(ctx, "compiled_identity", None)
    if compiled is None:
        return ""
    try:
        # Imported here, like models.text_prompt does, so this adapter
        # never pulls the identity compiler in at import time.
        from core import memory_refs
        from core.identity.compiler import plan_memory_core

        owner_id = getattr(getattr(ctx, "turn", None), "owner_id", None)
        refs = memory_refs.snapshot(owner_id) if owner_id is not None else {}
        text = plan_memory_core(compiled, budget_chars=_memory_core_budget(), refs=refs).text
    except Exception as exc:
        logger.debug("context_adapter: plan_memory_core failed: %s", exc)
        return ""
    if not isinstance(text, str):
        return ""
    text = text.strip()
    preferences = ""
    try:
        from core.identity.compiler import render_preferences_block

        rendered = render_preferences_block(compiled)
        if isinstance(rendered, str):
            preferences = rendered.strip()
    except Exception as exc:
        logger.debug("context_adapter: render_preferences_block failed: %s", exc)
    if preferences:
        text = f"{text}\n\n{preferences}" if text else preferences
    return text


def _render_corrections(ctx: TurnContext, *, is_owner: bool = True) -> str:
    """Corrections of record for the ``corrections_text`` slot — batch 5,
    task 7 (2026-09-28, rulings R20/R21). The block the night-time gathers
    put first (ai.corrections_store.render_corrections_block) reaches the
    REPLY now: the budget for the correction lines (the label and the law
    are its fixed cost) is the setting ``memory.corrections_budget_chars``
    (core.memory_refs.corrections_budget; default 1,500 — fix round 1,
    rulings R54/R56: at 700 one correction of the 19 standing reached her,
    at 1,500 two, the whole set is ~9,000; the owner turns it without a
    build; 0 turns the band off before the store is read), ``sync=False``
    because the reply path must never append to the owner's CORRECTIONS.md
    — that is the night jobs' work, and ``footer=True`` (fix wave, ruling
    R61): when the budget cut lines, the band alone closes with "(N more
    corrections stand.)" — the night gathers keep the default and their
    text does not change by a byte. A guest gets "" before the file is
    touched: the corrections are the owner's. Anything the store raises
    gives "", like every render here. Deterministic for a given store: each
    line carries the correction's own date, never the clock, so the stable
    prefix it ends stays byte-identical across turns."""
    if not is_owner:
        return ""
    try:
        from ai.corrections_store import render_corrections_block
        from core import memory_refs

        budget = memory_refs.corrections_budget()
        if budget <= 0:
            return ""
        text = render_corrections_block(budget=budget, sync=False, footer=True)
    except Exception as exc:
        logger.debug("context_adapter: corrections block failed: %s", exc)
        return ""
    if not isinstance(text, str):
        return ""
    return text.strip()


def _render_skills(ctx: TurnContext) -> str:
    """Render the skill catalog as a capabilities block.

    The agent loop already has the tool *schemas* injected via the tool
    registry. This block describes the higher-level *skills* (the shell
    skills at ``prompts/skills/`` this host can run) that she reads to know
    which command to compose through local_shell. It is
    :func:`core.turn.skills_loader.render_capabilities_block`'s default
    catalog, in her stable prefix, owner only.

    Returns empty string when the prompts/skills/ directory is empty or
    cannot be read.
    """
    try:
        from core.turn.skills_loader import render_capabilities_block
        return render_capabilities_block() or ""
    except Exception as exc:
        logger.debug("context_adapter: skills load failed: %s", exc)
        return ""


def _doors_open_this_boot() -> dict[str, bool]:
    """Boot's verdict on the chat doors, through the SAME helper the launcher
    uses, so this block and the launcher can never disagree. A module-level
    seam so a test can stand in for it. Raises are the caller's to absorb."""
    from core.launcher_runtime import build_channel_enabled_map

    return dict(build_channel_enabled_map())


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _render_where_i_am(ctx: TurnContext) -> str:
    """The self-facts she used to go looking for — and get wrong.

    2026-09-24, the Telegram incident: asked whether the bot was running, she
    reached for a shell, walked into a sibling copy of her own codebase (the
    assembled release tree, which has no token file because credentials never
    ship), found nothing, and told him the Telegram door was shut — through
    the Telegram door, fifty-three seconds after answering him in the group.
    The honesty gate passed it: her words matched her evidence; her evidence
    was pointed at the wrong house. Her own process shows in `ps` as nothing
    but `python main.py`, so a grep for her name cannot find her either.

    The July time fix is the precedent (see _render_runtime above): a fact she
    would otherwise fetch with a tool, and possibly fetch wrong, goes in the
    block with one sentence — you don't need a tool for this, it is right
    here. Four facts, a few hundred characters, true on every turn. Nothing
    else belongs here: "useful context" is how prompts rot, and this one is
    already thirty thousand tokens before the conversation starts.
    """
    import os

    turn = getattr(ctx, "turn", None)
    surface = str(getattr(turn, "surface", "") or "")
    platform = surface.split("_", 1)[0] if surface else ""
    meta = getattr(turn, "metadata", None)
    channel_id = getattr(meta, "channel_id", None)
    is_dm = bool(getattr(meta, "is_dm", False))

    lines = [
        "## Where I Am",
        f"I am running from {_REPO_ROOT} as process {os.getpid()}. That directory is "
        "my home — my code, my config, my data. Any other copy of my code on this "
        "machine is not me.",
    ]
    if bool(getattr(meta, "is_voice", False)) or surface in ("discord_voice", "voice_local"):
        # Step 2, part 5 (the plan, Voice): a voice turn carries the voice
        # channel's id, but its conversation is keyed on the owner, one
        # history in every voice channel; the line used to name a text room.
        from core.session_keys import session_key_for

        key = session_key_for(surface, channel_id, getattr(turn, "owner_id", 0))
        if surface == "discord_voice":
            lines.append(
                f"This turn is spoken, in a Discord voice channel (session {key}). My "
                "voice history is one thread shared by every voice channel; the text "
                "chats keep their own, which I can't see from here."
            )
        else:
            lines.append(
                f"This turn is spoken (session {key}). The text chats keep their own "
                "history, which I can't see from here."
            )
    elif platform and channel_id is not None:
        room = "a direct message" if is_dm else "a group"
        lines.append(
            f"This turn is on {platform}, in {room}, session {platform}:{channel_id}. "
            "I can see this session's history; other rooms and other doors keep their "
            "own, and I cannot see theirs from here."
        )
    elif surface:
        lines.append(f"This turn is on the {surface} surface.")

    try:
        doors = _doors_open_this_boot()
        names = {"discord_bot": "Discord", "telegram_bot": "Telegram", "mobile_door": "the phone app"}
        open_doors = [names.get(k, k) for k, v in doors.items() if v]
        if open_doors:
            lines.append(f"Doors open this boot: {', '.join(open_doors)}.")
        else:
            lines.append("Doors open this boot: none — no chat door is open.")
    except Exception as exc:  # a stand-in config, a half-booted process
        logger.debug("context_adapter: door state unavailable: %s", exc)
        lines.append("Doors open this boot: couldn't be read this turn.")

    lines.append(
        "You don't need a tool to find any of this — it's right here. If asked "
        "whether I am running, where I live, or which doors are open, answer from "
        "this block; a shell or a file search can only find a copy, never me."
    )
    return "\n".join(lines)


def _render_runtime(ctx: TurnContext, *, is_owner: bool = True) -> str:
    """Render runtime / current-time context — the "where and when am I"
    section.

    Combines two pipeline-mode sections: the current-time block (mirrors
    :func:`core.turn.respond_tier0._current_time_section`) and a brief
    time-context-for-this-turn note (gap since previous message). The
    capability disclosure isn't bundled here — it's static prose that
    belongs in the system_prompt template rather than per-turn runtime,
    so the system_prompt assembly can carry it separately if needed.

    ``is_owner`` (fix round 1, 2026-09-25 review): the clock and the gap
    framing above are not private — a guest hears the time too — so they
    always render. Only the §32 "Where I Am" block and the sidecar's
    parent snapshot are owner-only; gated here, in place, rather than by
    wrapping this function's whole return value the way the first pass at
    this fix wrongly did (it took the clock away from guests as well).
    """
    parts: list[str] = []

    received = getattr(getattr(ctx, "turn", None), "received_at", None)
    if received is not None:
        try:
            # received_at is naive UTC by convention (Turn's default factory
            # is datetime.utcnow, and every live surface uses it). Convert to
            # the HOST's local timezone for display and name the real zone.
            # 2026-07-10 bug (caught by PaLLi herself): the raw UTC digits
            # were rendered here and framed as the user's local time — four
            # hours off — while the situational sense, which uses localtime,
            # said otherwise in the same prompt. Display-only conversion:
            # the gap arithmetic below stays on the naive-UTC values.
            from datetime import timezone as _tz

            aware = received if received.tzinfo is not None else received.replace(tzinfo=_tz.utc)
            local = aware.astimezone()
            zone = local.tzname() or "local time"
            parts.append(
                "## Current Time\n"
                f"It is now {local.strftime('%Y-%m-%d %H:%M')} "
                f"({local.strftime('%A')}), {zone} — the server's local "
                "timezone, which is their own unless their profile "
                "says otherwise.\n"
                "\n"
                # Batch 7, her voice (audit 4.4): one rule for the time, said
                # once. "No tool needed" is the July fix (she ran `date`
                # through local_shell), folded into the same sentence.
                "I mention the time only when they bring it up or it matters, "
                "and since it's right here, I have no need to look it up with "
                "a tool."
            )
        except Exception as exc:
            logger.debug("context_adapter: time render failed: %s", exc)

    # Brief gap framing — mirrors part of
    # respond_tier0._time_context_for_this_turn_section. Cross-session
    # signal that helps the LLM not pick up stale threads from days ago.
    history = tuple(getattr(ctx, "recent_history", ()) or ())
    prev_at = None
    for entry in reversed(history):
        at = getattr(entry, "at", None)
        if at:
            prev_at = at
            break
    if prev_at is not None and received is not None:
        try:
            gap_seconds = (received - prev_at).total_seconds()
            gap_hours = gap_seconds / 3600.0
            if gap_hours < 0.1:
                framing = "Continuation — back-and-forth flow."
            elif gap_hours < 0.5:
                framing = "Brief pause — same conversation, mid-thread."
            elif gap_hours < 4:
                framing = "Moderate break — likely same conversation."
            elif gap_hours < 12:
                framing = (
                    "Significant gap — likely a NEW conversation. Don't "
                    "pick up mid-thread; let the user steer."
                )
            else:
                framing = (
                    "Long gap (likely overnight or longer) — treat prior "
                    "conversation as background memory, NOT a live thread."
                )
            parts.append(
                "## Time Context for THIS Turn\n"
                f"{framing}"
            )
        except Exception as exc:
            logger.debug("context_adapter: gap render failed: %s", exc)

    if is_owner:
        # The §32 "Where I Am" block: process/pid/session/doors — hers to
        # know, not a stranger's map of where she lives. Owner-only.
        try:
            parts.append(_render_where_i_am(ctx))
        except Exception as exc:  # never costs her the turn
            logger.debug("context_adapter: where-i-am render failed: %s", exc)

        # The sidecar's parent snapshot (step 27 Phase B) — a real
        # TurnContext field since 2026-09-09; "" on every ordinary turn.
        # Owner-only alongside the §32 block: it's her own work state.
        snap = str(getattr(ctx, "sidecar_snapshot_text", "") or "").strip()
        if snap:
            parts.append(snap)
    return "\n\n".join(p for p in parts if p.strip())


def _render_memory(ctx: TurnContext) -> str:
    """Render memory recall — inner-memory hits + monologue digest.

    Mirrors :func:`core.turn.respond_tier0._memory_section_demoted` (for
    inner-memory hits) and :func:`core.turn.respond_tier0._monologue_digest_section`
    (for the weekly digest). The two combine here because the agent
    loop only has one memory_text slot; pipeline-mode keeps them
    separate for ordering nuance, but the slot collapse is a Wave 1
    simplification we can fan out later if needed.
    """
    parts: list[str] = []

    hits = tuple(getattr(ctx, "recent_memory_hits", ()) or ())
    hit_lines: list[str] = []
    for hit in hits[:3]:
        snippet = str(hit or "").strip()
        if snippet:
            hit_lines.append(f"- {snippet}")
    if hit_lines:
        parts.append(
            "## Background memory hits (use ONLY if directly relevant)\n"
            "These older snippets matched semantically. They are "
            "background context, not material for the reply. Use them "
            "ONLY when directly relevant to the current message. Do NOT "
            "lead with them; do NOT trail the reply with a PS.\n\n"
            + "\n".join(hit_lines)
        )

    digest = str(getattr(ctx, "monologue_digest", "") or "").strip()
    if digest:
        parts.append(f"## Inner Monologue Digest (last week)\n{digest}")

    return "\n\n".join(parts)


# The today slot's section (R15, T12) — PaLLi's words, Fable's items 20–21,
# first person like the band above it.
_TODAY_HEADER = "## Today, so far"
_TODAY_LEAD = "What they told me earlier today, kept here until the nightly pass files it:"


def _render_facts(ctx: TurnContext) -> str:
    """The "## Things I Know About This User" band — its header is pinned
    (ruling R30). Two entry shapes since Batch 5, task 4 (ruling R39): a
    looked-up MEMORY.md bullet carries its rendered ``line`` (``- [stamp]
    content``, built by recall) and is printed verbatim; a user_memory fact
    carries key/value and renders ``- key: value`` as it always has
    (respond_tier0._facts_section's shape). Recall orders the tuple —
    bullets first — and caps it at eight; the ``[:8]`` here is the old
    guard, kept.

    The today slot (R15, T12): the owner's ``today`` rows ride after the
    band (core.turn.memory_lookup.plan_band). They are never band lines;
    with the slot's switch on they render under their own heading after
    it (``_render_today``), off they render nowhere. No today rows: the
    band, byte for byte as before.

    Returns "" when no facts are present so empty-section pruning in
    :func:`core.agent.system_prompt.build_system_prompt` keeps the prompt
    clean.
    """
    facts = tuple(getattr(ctx, "relevant_facts", ()) or ())
    if not facts:
        return ""
    from core.turn.memory_lookup import split_today

    band, today = split_today(facts)
    lines = ["## Things I Know About This User"]
    for fact in band[:8]:
        if not isinstance(fact, dict):
            continue
        if "line" in fact:
            line = str(fact.get("line") or "").strip()
            if line:
                lines.append(line)
            continue
        key = str(fact.get("key") or "fact").strip()
        value = str(fact.get("value") or "").strip()
        if not value:
            continue
        lines.append(f"- {key}: {value}")
    parts = ["\n".join(lines)] if len(lines) > 1 else []
    today_text = _render_today(today)
    if today_text:
        parts.append(today_text)
    return "\n\n".join(parts)


def _render_today(today: list[dict]) -> str:
    """The "## Today, so far" section (R15, T12): what the owner told PaLLi
    earlier today, newest first, one ``- `` line per row — the row's text is
    already ``key: value`` (the reflection's fact) or the moment as the turn
    read put it. "" when the slot's switch is off (read at call time, so the
    suite's pin holds) or no row has text; at most eight, the slot's cap."""
    from core.turn.companion_flags import today_slot_on

    if not today or not today_slot_on():
        return ""
    lines = [
        f"- {value}"
        for value in (str(fact.get("value") or "").strip() for fact in today[:8])
        if value
    ]
    if not lines:
        return ""
    return "\n".join([_TODAY_HEADER, _TODAY_LEAD, *lines])


def _render_recent_history(ctx: TurnContext) -> str:
    """Mirror :func:`core.turn.respond_tier0._recent_history_with_timing_section`.

    Renders history entries with absolute + relative time framing. Used
    by cadence's shape_response (which feeds the recent history into a
    small "should-I-respond?" classification call) and the runner's
    pre-loop context-build.

    Truncates very long entries (~280 chars per line) for prompt
    economy. Falls back to raw "role: content" lines if temporal_format
    isn't importable.
    """
    history = tuple(getattr(ctx, "recent_history", ()) or ())
    if not history:
        return ""

    received = getattr(getattr(ctx, "turn", None), "received_at", None)

    try:
        from core.turn.temporal_format import format_message_with_temporal_context
    except Exception as exc:
        logger.debug(
            "context_adapter: temporal_format unavailable (%s); using raw render",
            exc,
        )
        # Best-effort fallback — role + content, no time framing.
        return "\n".join(
            f"{h.role}: {h.content}"
            for h in history
            if isinstance(h, HistoryEntry)
            and h.role in ("user", "assistant")
            and h.content
        )

    lines = ["## Recent Conversation (with timing)"]
    prev_at: Any = None
    for h in history:
        if not isinstance(h, HistoryEntry):
            continue
        if h.role not in ("user", "assistant"):
            continue
        if not h.content:
            continue
        try:
            line = format_message_with_temporal_context(
                role=h.role,
                content=h.content,
                sent_at=h.at,
                now=received or h.at,
                previous_sent_at=prev_at,
            )
        except Exception as exc:
            logger.debug(
                "context_adapter: temporal format failed (%s); using raw line",
                exc,
            )
            line = f"{h.role}: {h.content}"
        max_len = 280
        if len(line) > max_len:
            line = line[:max_len].rstrip() + " […]"
        lines.append(line)
        prev_at = h.at

    if len(lines) == 1:
        return ""
    return "\n".join(lines)

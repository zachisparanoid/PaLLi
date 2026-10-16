"""Tests for the TurnContext → AgentLoopContext adapter.

D.2 wave 1 (2026-05): the agent loop reads named string fields
(``identity_core_text``, ``user_text``, ``recent_history_text``,
``memory_text``, ``facts_text``, ``runtime_text``, ``skills_text``) via
``getattr(ctx, "X", "")``. None of those fields exist on
:class:`core.turn.types.TurnContext`. This adapter renders them once at
dispatch time so the agent loop sees real strings — these tests pin
that the renders produce sensible output for representative
TurnContext shapes (canonical happy path, partial recall failure,
empty-history first-turn).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from core.agent.context_adapter import (
    AgentLoopContext,
    build_agent_loop_context,
    _render_facts,
    _render_identity_core,
    _render_memory,
    _render_recent_history,
    _render_runtime,
    _render_situational,
    _render_skills,
)
from core.turn.types import HistoryEntry, Turn, TurnContext, TurnMetadata


def _make_turn(
    content: str = "hi",
    *,
    received_at: datetime | None = None,
    metadata: TurnMetadata | None = None,
) -> Turn:
    return Turn(
        surface="discord_text",
        owner_id=1,
        content=content,
        metadata=metadata or TurnMetadata(),
        received_at=received_at or datetime(2026, 5, 13, 14, 30, 0),
    )


def _make_ctx(turn: Turn, **kwargs) -> TurnContext:
    """Build a TurnContext with sensible defaults; override with kwargs."""
    return TurnContext(turn=turn, **kwargs)


def test_returns_agent_loop_context_with_named_fields():
    """The seven string fields the agent loop expects all exist on the
    returned AgentLoopContext object."""
    turn = _make_turn(content="hello")
    ctx = _make_ctx(turn)
    out = build_agent_loop_context(turn, ctx)
    assert isinstance(out, AgentLoopContext)
    # All seven named string slots are present and string-typed.
    for field_name in (
        "identity_core_text", "skills_text", "runtime_text",
        "memory_text", "facts_text", "user_text", "recent_history_text",
    ):
        assert hasattr(out, field_name)
        assert isinstance(getattr(out, field_name), str)
    assert out.turn is turn
    assert out.ctx is ctx
    assert out.is_owner is True


def test_user_text_is_turn_content():
    """user_text reads from turn.content. Pre-D.2 this read getattr(ctx,
    'user_text', '') and got "" because TurnContext has no user_text
    field — empty user message was the silent bug."""
    turn = _make_turn(content="what time is it?")
    ctx = _make_ctx(turn)
    out = build_agent_loop_context(turn, ctx)
    assert out.user_text == "what time is it?"


def test_user_text_empty_content_renders_empty_string():
    """Empty content stays empty — the adapter doesn't manufacture a
    placeholder."""
    turn = _make_turn(content="")
    ctx = _make_ctx(turn)
    out = build_agent_loop_context(turn, ctx)
    assert out.user_text == ""


def test_identity_core_renders_text_prompt_from_compiled_identity():
    """When compiled_identity is present, the renderer calls .text_prompt()
    and uses the result — matches respond_tier0._identity_section."""
    turn = _make_turn()
    fake_identity = MagicMock()
    fake_identity.text_prompt.return_value = "## I am PaLLi, a companion AI."
    ctx = _make_ctx(turn, compiled_identity=fake_identity)
    rendered = _render_identity_core(ctx)
    assert rendered == "## I am PaLLi, a companion AI."
    fake_identity.text_prompt.assert_called_once()


def test_identity_core_returns_empty_when_compile_failed():
    """compiled_identity=None happens when identity service fails in
    recall — render must degrade to empty without exploding."""
    turn = _make_turn()
    ctx = _make_ctx(turn, compiled_identity=None)
    assert _render_identity_core(ctx) == ""


def test_identity_core_returns_empty_when_text_prompt_raises():
    """A broken .text_prompt() must not crash the runner — degrade to ""."""
    turn = _make_turn()
    fake_identity = MagicMock()
    fake_identity.text_prompt.side_effect = RuntimeError("boom")
    ctx = _make_ctx(turn, compiled_identity=fake_identity)
    assert _render_identity_core(ctx) == ""


def test_facts_renders_relevant_facts_as_bulleted_list():
    """The fact half of the band (Batch 5, task 4, ruling R5b): each
    user_memory fact becomes a bullet with key: value — the shape it had
    when the band mirrored respond_tier0._facts_section. Recall now puts
    its looked-up memory bullets AHEAD of these in the same tuple, as
    ``line`` entries (the test below); the facts render unchanged."""
    turn = _make_turn()
    ctx = _make_ctx(
        turn,
        relevant_facts=(
            {"key": "favorite_color", "value": "blue"},
            {"key": "dog_name", "value": "Miso"},
        ),
    )
    rendered = _render_facts(ctx)
    assert "## Things I Know About This User" in rendered
    assert "- favorite_color: blue" in rendered
    assert "- dog_name: Miso" in rendered


def test_facts_empty_when_no_relevant_facts():
    """Empty fact tuple renders as empty string so system_prompt's
    empty-section pruning keeps the output clean."""
    turn = _make_turn()
    ctx = _make_ctx(turn, relevant_facts=())
    assert _render_facts(ctx) == ""


def test_facts_skips_facts_without_value():
    """A fact with an empty value is dropped — section header alone
    isn't useful, so the renderer returns "" rather than a header
    with no bullets."""
    turn = _make_turn()
    ctx = _make_ctx(
        turn,
        relevant_facts=(
            {"key": "favorite_color", "value": ""},
            {"key": "no_key", "value": "  "},  # whitespace-only value
        ),
    )
    assert _render_facts(ctx) == ""


def test_facts_renders_a_memory_bullet_entry_verbatim_ahead_of_the_facts():
    """Batch 5, task 4 (ruling R39): recall's band entries come in two
    shapes. A looked-up MEMORY.md bullet carries its rendered ``line``
    (``- [stamp] content``, or ``- content`` when unstamped; a trailing
    ``[archived …]`` marker stays), printed as it is; a user_memory fact
    keeps today's ``- key: value``. The renderer keeps the order recall
    gave — bullets first — under the byte-identical header."""
    turn = _make_turn()
    ctx = _make_ctx(
        turn,
        relevant_facts=(
            {"line": "- [2026-03-04] their sister Mara lives in Leeds", "item_id": "mcb_000000000001", "source_kind": "memory_bullet"},
            {"line": "- no stamp on this one", "item_id": "mcb_000000000002", "source_kind": "memory_bullet"},
            {"line": "- [2026-01-01] the old boat [archived 2026-09-10]", "item_id": "mcb_000000000003", "source_kind": "memory_bullet"},
            {"key": "fact", "value": "User's cat is named Mochi.", "id": 12, "memory_type": "user_fact"},
        ),
    )
    assert _render_facts(ctx) == (
        "## Things I Know About This User\n"
        "- [2026-03-04] their sister Mara lives in Leeds\n"
        "- no stamp on this one\n"
        "- [2026-01-01] the old boat [archived 2026-09-10]\n"
        "- fact: User's cat is named Mochi."
    )


def test_facts_skips_a_blank_bullet_entry_and_a_band_of_blanks_renders_nothing():
    turn = _make_turn()
    ctx = _make_ctx(
        turn,
        relevant_facts=(
            {"line": "   ", "item_id": "mcb_000000000001", "source_kind": "memory_bullet"},
            {"key": "dog_name", "value": "Miso"},
        ),
    )
    assert _render_facts(ctx) == "## Things I Know About This User\n- dog_name: Miso"
    ctx = _make_ctx(turn, relevant_facts=({"line": "", "item_id": "mcb_000000000001"},))
    assert _render_facts(ctx) == ""


def test_memory_renders_recent_memory_hits_with_anti_leak_label():
    """Inner-memory hits render under the "background memory hits"
    label with the anti-leak instructions — mirrors
    respond_tier0._memory_section_demoted's guard against PS-style
    memory leaks."""
    turn = _make_turn()
    ctx = _make_ctx(
        turn,
        recent_memory_hits=(
            "user mentioned a lathe plan last week",
            "user has a dog named Miso",
        ),
    )
    rendered = _render_memory(ctx)
    assert "Background memory hits" in rendered
    assert "directly relevant" in rendered  # anti-leak guidance phrase
    assert "- user mentioned a lathe plan last week" in rendered


def test_memory_renders_monologue_digest_section():
    """When recall surfaced a weekly monologue digest, it appears under
    its own labeled heading."""
    turn = _make_turn()
    ctx = _make_ctx(
        turn,
        monologue_digest="Last week: user wrapped up the lathe shortlist.",
    )
    rendered = _render_memory(ctx)
    assert "## Inner Monologue Digest (last week)" in rendered
    assert "lathe shortlist" in rendered


def test_memory_empty_when_no_hits_and_no_digest():
    """Both slots empty → empty string. system_prompt's joiner drops
    the section entirely."""
    turn = _make_turn()
    ctx = _make_ctx(turn, recent_memory_hits=(), monologue_digest="")
    assert _render_memory(ctx) == ""


def test_recent_history_renders_with_timing():
    """Recent history renders under "## Recent Conversation (with
    timing)" — mirrors respond_tier0._recent_history_with_timing_section.
    Empty content entries are skipped."""
    now = datetime(2026, 5, 13, 14, 30, 0)
    turn = _make_turn(received_at=now)
    ctx = _make_ctx(
        turn,
        recent_history=(
            HistoryEntry(role="user", content="how's it going?",
                         at=now - timedelta(minutes=5)),
            HistoryEntry(role="assistant", content="all good, you?",
                         at=now - timedelta(minutes=4)),
        ),
    )
    rendered = _render_recent_history(ctx)
    assert "## Recent Conversation (with timing)" in rendered
    assert "how's it going?" in rendered
    assert "all good, you?" in rendered


def test_recent_history_empty_when_no_entries():
    turn = _make_turn()
    ctx = _make_ctx(turn, recent_history=())
    assert _render_recent_history(ctx) == ""


def test_recent_history_truncates_long_entries():
    """A 500-char history entry should land under ~280 chars in the
    rendered output (prompt economy — the chat-completions messages
    array carries the full content separately)."""
    now = datetime(2026, 5, 13, 14, 30, 0)
    turn = _make_turn(received_at=now)
    long_content = "a" * 500
    ctx = _make_ctx(
        turn,
        recent_history=(
            HistoryEntry(role="user", content=long_content,
                         at=now - timedelta(minutes=5)),
        ),
    )
    rendered = _render_recent_history(ctx)
    # The render must include some trace of the truncation marker
    assert "[…]" in rendered or len(rendered) < 600


def test_runtime_includes_current_time():
    """Runtime section embeds the turn's received_at so the LLM doesn't
    need a tool to look up the wallclock. Since the 2026-07-10 timezone fix
    the naive-UTC value renders converted to the HOST's local zone, so this
    test asserts against the same conversion rather than raw UTC digits."""
    from datetime import timezone as _tz

    received = datetime(2026, 5, 13, 14, 30, 0)  # naive UTC by convention
    turn = _make_turn(received_at=received)
    ctx = _make_ctx(turn)
    rendered = _render_runtime(ctx)
    local = received.replace(tzinfo=_tz.utc).astimezone()
    assert "Current Time" in rendered
    assert local.strftime("%Y-%m-%d %H:%M") in rendered
    assert (local.tzname() or "local time") in rendered


def test_runtime_includes_gap_framing_when_history_present():
    """When recent_history has a previous entry, the runtime section
    adds a brief gap-since-prior-message note — mirrors
    respond_tier0._time_context_for_this_turn_section."""
    now = datetime(2026, 5, 13, 14, 30, 0)
    turn = _make_turn(received_at=now)
    ctx = _make_ctx(
        turn,
        recent_history=(
            HistoryEntry(
                role="user",
                content="see you tomorrow",
                at=now - timedelta(hours=20),
            ),
        ),
    )
    rendered = _render_runtime(ctx)
    assert "Time Context" in rendered
    # 20h gap → "Long gap" framing
    assert "Long gap" in rendered or "overnight" in rendered.lower() or "longer" in rendered.lower()


def test_skills_renders_capabilities_block_or_empty():
    """skills_text renders the prompts/skills/ catalog via
    skills_loader.render_capabilities_block. Empty when the directory
    is empty or unreadable — must not raise."""
    turn = _make_turn()
    ctx = _make_ctx(turn)
    rendered = _render_skills(ctx)
    # Either the catalog block (when prompts/skills/ has content) or
    # empty (when not). Both are valid; just must not raise and must
    # be a string.
    assert isinstance(rendered, str)


def test_build_context_round_trip_preserves_turn_and_ctx_handles():
    """Tools / observability code that holds onto AgentLoopContext can
    still reach the raw turn and TurnContext via the named attrs."""
    turn = _make_turn(content="ping")
    ctx = _make_ctx(turn)
    out = build_agent_loop_context(turn, ctx, is_owner=False)
    assert out.turn is turn
    assert out.ctx is ctx
    assert out.is_owner is False


def test_build_context_produces_nonempty_for_canonical_turncontext():
    """End-to-end: a representative TurnContext with identity + history +
    facts produces at least some non-empty render slots. This is the
    "ReAct is no longer silently empty" pin — the bug the audit
    surfaced was every slot getting "" from getattr."""
    now = datetime(2026, 5, 13, 14, 30, 0)
    turn = _make_turn(content="what's up?", received_at=now)
    fake_identity = MagicMock()
    fake_identity.text_prompt.return_value = "## I'm PaLLi.\nA friend."
    ctx = _make_ctx(
        turn,
        compiled_identity=fake_identity,
        relevant_facts=({"key": "favorite_color", "value": "blue"},),
        recent_history=(
            HistoryEntry(
                role="user",
                content="hey",
                at=now - timedelta(minutes=3),
            ),
        ),
        monologue_digest="Quiet week.",
    )
    out = build_agent_loop_context(turn, ctx)
    # The silent-empty bug would leave every one of these as "".
    assert out.user_text == "what's up?"
    assert "PaLLi" in out.identity_core_text
    assert "favorite_color" in out.facts_text
    assert "Quiet week" in out.memory_text
    assert "hey" in out.recent_history_text
    assert "Current Time" in out.runtime_text


def test_runtime_renders_received_at_in_host_local_timezone(monkeypatch):
    """``received_at`` is naive UTC by convention (Turn's default factory is
    ``datetime.utcnow``); the Current Time block must render it converted to
    the HOST's local timezone and name the real zone — not print raw UTC
    digits framed as the user's local time. This is the 2026-07-10 bug PaLLi
    caught herself: her prompt said 13:35 while the owner's clock read 9:35
    EDT, and her situational sense (which uses localtime) contradicted it."""
    import os
    import time as _time

    prior_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    _time.tzset()
    try:
        turn = _make_turn(received_at=datetime(2026, 7, 10, 13, 35, 0))  # naive UTC
        ctx = _make_ctx(turn)
        out = _render_runtime(ctx)
        assert "2026-07-10 09:35" in out, out  # converted to EDT
        assert "EDT" in out, out  # the real zone name, not a hardcoded claim
        assert "13:35" not in out, out  # UTC digits must not leak
        assert "Friday" in out, out  # weekday computed on the LOCAL date
    finally:
        if prior_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prior_tz
        _time.tzset()


def test_runtime_weekday_crosses_date_line_correctly(monkeypatch):
    """A late-UTC timestamp that is still the previous calendar day locally
    must render the LOCAL date and weekday (the raw-UTC bug showed the wrong
    day for every evening conversation after 8pm ET)."""
    import os
    import time as _time

    prior_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    _time.tzset()
    try:
        # 2026-07-11 01:30 UTC == 2026-07-10 21:30 EDT (Friday evening)
        turn = _make_turn(received_at=datetime(2026, 7, 11, 1, 30, 0))
        ctx = _make_ctx(turn)
        out = _render_runtime(ctx)
        assert "2026-07-10 21:30" in out, out
        assert "Friday" in out, out
        assert "2026-07-11" not in out, out
    finally:
        if prior_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prior_tz
        _time.tzset()


def test_the_time_is_mentioned_only_when_they_raise_it_or_it_matters():
    """Batch 7, her voice (audit 4.4): her prompt told her two things about
    the clock. The runtime block said "You can refer to this time directly
    in replies"; the ambient band shouted "Do NOT state the time … unless
    THEY raise it". One rule now, said once, in her own words. The July
    fix stays folded into it: the time is right here, so no tool is needed
    (she used to run `date` through local_shell)."""
    import re

    ctx = _make_ctx(
        _make_turn(received_at=datetime(2026, 5, 13, 14, 30)),
        situational_note="It's Wednesday morning (10:30 AM).",
    )
    runtime = _render_runtime(ctx)
    situational = _render_situational(ctx)
    assert situational, "the ambient half must be real, not the empty band"
    both = runtime + situational
    assert "refer to this time directly in replies" not in both   # the permission is gone
    assert both.count("only when") >= 1
    assert both.count("mention the time only when") == 1          # one rule, stated once
    assert "Do NOT state the time" not in both                    # no shouting second rule
    assert "tool" in runtime                                      # the July fix survives
    band = situational.split("It's Wednesday morning")[0]
    assert "## Ambient awareness" in band
    assert "the time" not in band                                 # the time's rule lives in one place
    assert not re.search(r"\b(?:never|do not|don['’]t)\b", band, re.I), band


def test_present_moment_text_passes_through():
    """Slice 2: the system prompt reads ONLY flat fields — a TurnContext
    field that isn't flattened here never reaches the responder."""
    turn = _make_turn()
    ctx = _make_ctx(
        turn,
        present_moment_text="Your last moment, written just now:\nThe quiet holds.",
    )
    out = build_agent_loop_context(turn, ctx)
    assert out.present_moment_text.startswith("Your last moment, written just now:")


def test_present_moment_text_defaults_empty():
    turn = _make_turn()
    out = build_agent_loop_context(turn, _make_ctx(turn))
    assert out.present_moment_text == ""


# ── guests see none of his private bands (item 7a, 2026-08-19) ────────────

def test_non_owner_context_carries_no_private_bands():
    """A guest turn renders her identity core and the channel history, and
    nothing about HIM: no memory hits, facts, relationship, stakes, moment,
    deeds, trace, reflection, story-of-us, care spine, turn read."""
    turn = _make_turn(content="who are you?")
    ctx = _make_ctx(
        turn,
        recent_memory_hits=("he told me his cat is Mochi",),
        relationship_posture="more open",
        held_stakes_text="I'm hoping his survey comes back clean",
        present_moment_text="he just came back after a long absence",
        recent_deeds_text="sent him the STL",
        own_work_trace="- claw/ — a puzzle box",
        night_cycle_reflection="today meant…",
        narrative_story_of_us="we met in April",
        narrative_care_spine="I care about his steadiness",
        emotional_arc_text="he's been lighter",
    )
    owner = build_agent_loop_context(turn, ctx, is_owner=True)
    guest = build_agent_loop_context(turn, ctx, is_owner=False)
    assert owner.held_stakes_text and owner.present_moment_text and owner.memory_text
    for field in ("memory_text", "facts_text", "relationship_text", "turn_read_text",
                  "emotional_arc_text", "held_stakes_text", "night_cycle_reflection_text",
                  "present_moment_text", "own_work_trace_text", "recent_deeds_text",
                  "narrative_story_of_us_text", "narrative_care_spine_text", "skills_text"):
        assert getattr(guest, field) == "", field
    assert guest.identity_core_text == owner.identity_core_text
    assert guest.recent_history_text == owner.recent_history_text
    assert guest.user_text == "who are you?"


def test_guest_gets_no_story_and_no_where_i_am_block():
    """Task 5 (Batch 1, 2026-09-25) + fix round 1 (review): her life story
    and the §32 'Where I Am' runtime block (plus the sidecar snapshot) are
    owner-only. The clock and the time-gap framing inside runtime_text are
    NOT private — round 1 caught the whole-field P() wrap taking the clock
    away from guests too, which the ruling says never to do."""
    turn = _make_turn(content="who are you?")
    ctx = _make_ctx(
        turn,
        narrative_my_story="I've been thinking about the boat.",
        sidecar_snapshot_text="parent: mid-tool-round on the STL cleanup",
    )
    owner = build_agent_loop_context(turn, ctx, is_owner=True)
    guest = build_agent_loop_context(turn, ctx, is_owner=False)
    assert owner.narrative_my_story_text == "I've been thinking about the boat."
    assert guest.narrative_my_story_text == ""
    assert "## Where I Am" in owner.runtime_text
    assert "## Current Time" in guest.runtime_text  # the clock is not private
    assert "## Where I Am" not in guest.runtime_text
    assert "parent: mid-tool-round" not in guest.runtime_text


def test_spoken_register_rides_is_voice_for_owner_and_guest():
    """Step 31 (spec §4): the band renders from the turn's own metadata —
    voice ⇒ present (guests included; register is not private — a guest
    hears her speak too), text ⇒ "" (byte-identical prompts by
    construction; no flag)."""
    turn_v = _make_turn(metadata=TurnMetadata(is_voice=True))
    turn_t = _make_turn()
    on = build_agent_loop_context(turn_v, _make_ctx(turn_v), is_owner=True)
    guest = build_agent_loop_context(turn_v, _make_ctx(turn_v), is_owner=False)
    off = build_agent_loop_context(turn_t, _make_ctx(turn_t), is_owner=True)
    assert "SPOKEN, NOT WRITTEN" in on.spoken_register_text
    assert guest.spoken_register_text == on.spoken_register_text  # not P()-wrapped
    assert off.spoken_register_text == ""


def _voice_turn(surface: str) -> Turn:
    """A turn marked as arriving by voice, on the named surface."""
    return Turn(
        surface=surface,
        owner_id=1,
        content="hi",
        metadata=TurnMetadata(is_voice=True),
        received_at=datetime(2026, 9, 14, 14, 30, 0),
    )


def test_a_telegram_voice_note_gets_no_spoken_band():
    """Telegram door, final wave (I3): the band said "this reply will be
    said aloud in the voice channel" on every Telegram voice NOTE — and it
    will not be. ``metadata.is_voice`` there means the message she is
    answering was audio (voice or an audio file, owner or guest); her reply
    goes out as TEXT, with a voice note only ever ALONGSIDE it. So the band
    was an untruth that cost him the long form — no lists, no links, three
    sentences — in a chat that renders all three."""
    turn = _voice_turn("telegram_text")
    out = build_agent_loop_context(turn, _make_ctx(turn), is_owner=True)
    assert out.spoken_register_text == ""


def test_a_discord_voice_turn_still_gets_the_band():
    """The Discord half of the same question is a DEFECT entry for the owner,
    not this wave's to change: byte-identical."""
    turn = _voice_turn("discord_text")
    out = build_agent_loop_context(turn, _make_ctx(turn), is_owner=True)
    assert out.spoken_register_text.startswith(
        "SPOKEN, NOT WRITTEN: this reply will be said aloud in the voice "
        "channel."
    )


def test_the_local_voice_lane_keeps_the_band():
    """Where the reply really is spoken, nothing moved."""
    turn = _voice_turn("voice_local")
    out = build_agent_loop_context(turn, _make_ctx(turn), is_owner=True)
    assert "SPOKEN, NOT WRITTEN" in out.spoken_register_text


# ── "Where I Am" — the self-facts she used to go looking for (2026-09-24) ──


def test_runtime_tells_her_where_she_lives_so_she_never_goes_looking():
    """The 2026-09-24 Telegram incident: asked whether the bot was running,
    she reached for a shell, walked into a sibling copy of her own codebase
    (the assembled release tree), found no token file there, and told him the
    Telegram door was shut — through the Telegram door. The honesty gate
    passed it: her words matched her evidence; her evidence was pointed at
    the wrong house.

    The July time fix is the precedent: put the fact in the runtime block
    with "you don't need a tool for this — it's right here." Same cure.
    """
    import os
    from pathlib import Path

    turn = _make_turn(received_at=datetime(2026, 9, 24, 10, 20, 0))
    ctx = _make_ctx(turn)
    rendered = _render_runtime(ctx)

    assert "## Where I Am" in rendered
    repo = str(Path(__file__).resolve().parents[2])
    assert repo in rendered, "the absolute path she runs from must be stated"
    assert f"process {os.getpid()}" in rendered, "her own pid, so she can find herself"
    assert "don't need a tool" in rendered, "the sentence that keeps her from going hunting"


def test_runtime_names_this_chat_by_platform_and_channel():
    """The session she is speaking in, as the runner keys it — platform and
    channel — and whether it is a DM or a group. From inside a DM she cannot
    see a group's history, and saying which room she is in is what lets her
    say so honestly instead of concluding the door is closed."""
    meta = TurnMetadata(channel_id=4242424242, is_dm=True)
    turn = Turn(surface="telegram_text", owner_id=1, content="hi", metadata=meta,
                received_at=datetime(2026, 9, 24, 10, 20, 0))
    rendered = _render_runtime(_make_ctx(turn))
    assert "telegram:4242424242" in rendered
    assert "direct message" in rendered

    meta = TurnMetadata(channel_id=-1009900001234, is_dm=False)
    turn = Turn(surface="telegram_text", owner_id=1, content="hi", metadata=meta,
                received_at=datetime(2026, 9, 24, 10, 20, 0))
    rendered = _render_runtime(_make_ctx(turn))
    assert "telegram:-1009900001234" in rendered
    assert "group" in rendered


def test_a_voice_turn_names_its_own_conversation_and_says_it_is_spoken():
    """Step 2, part 5 (the plan, Voice): a Discord voice turn carries the voice
    channel's id and is_dm False, so the line used to say "in a group, session
    discord:<voice channel>" — a text room's key she never reads from there.
    Her voice conversation is keyed on the owner (discord_voice:<owner>), one
    history across voice channels, and the turn is spoken."""
    meta = TurnMetadata(channel_id=987654321, is_dm=False, is_voice=True)
    turn = Turn(surface="discord_voice", owner_id=1, content="hi", metadata=meta,
                received_at=datetime(2026, 10, 8, 21, 0, 0))
    rendered = _render_runtime(_make_ctx(turn))
    assert "This turn is spoken, in a Discord voice channel" in rendered
    assert "(session discord_voice:1)" in rendered and "one thread shared by every voice channel" in rendered
    assert "discord:987654321" not in rendered and "in a group" not in rendered


def test_runtime_names_the_doors_open_this_boot(monkeypatch):
    """Which doors are open is boot's verdict, read through the same helper
    boot uses, so the block and the launcher can never disagree."""
    import core.agent.context_adapter as ca

    monkeypatch.setattr(
        ca, "_doors_open_this_boot",
        lambda: {"discord_bot": True, "telegram_bot": True},
    )
    rendered = _render_runtime(_make_ctx(_make_turn()))
    assert "Discord" in rendered and "Telegram" in rendered
    assert "open" in rendered

    monkeypatch.setattr(ca, "_doors_open_this_boot", lambda: {"discord_bot": False, "telegram_bot": False})
    rendered = _render_runtime(_make_ctx(_make_turn()))
    assert "no chat door is open" in rendered


def test_runtime_survives_a_door_helper_that_raises(monkeypatch):
    """A stand-in config, a half-booted process, an import that fails — none
    of it may cost her the turn. The self-facts render; the doors line says
    it could not be read."""
    import core.agent.context_adapter as ca

    def boom():
        raise RuntimeError("no config here")
    monkeypatch.setattr(ca, "_doors_open_this_boot", boom)
    rendered = _render_runtime(_make_ctx(_make_turn()))
    assert "## Where I Am" in rendered
    assert "couldn't be read" in rendered


# ── Batch 2, task 3 (cut the cost, 2026-09-27): the memory doc leaves the
# identity rendering and rides as its own field, so the system prompt can put
# it LAST in the cache-stable prefix; the volatile tail carries no owner band
# a guest was not already getting.

from core.agent.context_adapter import _render_owner_memory
from core.identity.compiler import render_memory_core, render_text_prompt
from core.identity.models import CompiledIdentity, IdentityDocument

_MEMORY_FILE = (
    "# Memory\n\nThis file is my curated memory of them, not a transcript.\n\n"
    "## Durable Context\n- likes tea\n\n## Active Threads\n- [09-27] the boat\n\n"
    "## Important Decisions\n- no essays\n\n## Useful References\n- the NAS\n"
)


def _compiled() -> CompiledIdentity:
    def doc(key: str, text: str) -> IdentityDocument:
        return IdentityDocument(doc_key=key, path=key, frontmatter={}, content=text)
    return CompiledIdentity(
        core={"core_identity": doc("core_identity", "# Core\nI am PaLLi."),
              "voice_identity": doc("voice_identity", "# Voice\nPlain words.")},
        owner={"personality_profile": doc("personality_profile", "# Profile\nWarm."),
               "memory": doc("memory", _MEMORY_FILE)},
    )


def test_identity_core_excludes_the_memory_doc_and_owner_memory_text_carries_it():
    turn = _make_turn()
    out = build_agent_loop_context(turn, _make_ctx(turn, compiled_identity=_compiled()))
    assert "### Memory" not in out.identity_core_text and "## Active Threads" not in out.identity_core_text
    assert "### Core Identity" in out.identity_core_text and "### Voice Identity" in out.identity_core_text
    # Batch 5, task 3 (2026-09-28, R9 re-pin): the slot carries the memory
    # CORE at the default budget, not the whole doc.
    assert out.owner_memory_text == render_memory_core(_compiled(), budget_chars=12000, refs={})
    assert out.owner_memory_text.startswith("### Memory\n")
    assert out.owner_memory_text.index("## Important Decisions") < out.owner_memory_text.index("## Active Threads")


def test_identity_core_asks_text_prompt_to_exclude_memory():
    turn = _make_turn()
    fake_identity = MagicMock()
    fake_identity.text_prompt.return_value = "## I am PaLLi."
    assert _render_identity_core(_make_ctx(turn, compiled_identity=fake_identity)) == "## I am PaLLi."
    fake_identity.text_prompt.assert_called_once_with(exclude=("memory",))


def test_owner_memory_text_degrades_to_empty_like_identity_core():
    turn = _make_turn()
    assert _render_owner_memory(_make_ctx(turn, compiled_identity=None)) == ""
    # a mocked / duck-typed bundle renders nothing rather than a repr
    assert _render_owner_memory(_make_ctx(turn, compiled_identity=MagicMock())) == ""
    broken = MagicMock()
    broken.owner.get.side_effect = RuntimeError("boom")
    assert _render_owner_memory(_make_ctx(turn, compiled_identity=broken)) == ""
    assert build_agent_loop_context(turn, _make_ctx(turn)).owner_memory_text == ""


def test_owner_memory_text_is_wired_to_the_refs_snapshot_and_the_settings_budget(monkeypatch):
    """Batch 5, task 3, fix round 1 (M2): the adapter hands plan_memory_core
    the owner's refs snapshot and the settings budget — neither is
    hard-coded. Seed owner 1's snapshot with a reference on the OLDER
    thread and shrink the budget to one thread: the referenced one makes
    the slot and the newer one does not; at the default budget both would,
    and without the reference the newer stamp would win the one place."""
    import time as _time

    import core.agent.context_adapter as ca
    from core import memory_refs
    from core.memory_bullets import item_id_for

    compiled = CompiledIdentity(owner={"memory": IdentityDocument(
        doc_key="memory", path="m", frontmatter={},
        content="## Active Threads\n- [2026-01-02] the newer boat\n- [2026-01-01] the old boat\n",
    )})
    turn = _make_turn()  # owner_id=1
    monkeypatch.setattr(ca, "_memory_core_budget", lambda: 50)
    monkeypatch.setitem(
        memory_refs._snapshots, 1,
        {item_id_for("the old boat"): {"last_referenced_at": _time.time(), "count": 1}},
    )
    out = build_agent_loop_context(turn, _make_ctx(turn, compiled_identity=compiled))
    assert "- [2026-01-01] the old boat" in out.owner_memory_text
    assert "the newer boat" not in out.owner_memory_text
    assert "(Any memory line not shown here is looked up" in out.owner_memory_text
    monkeypatch.delitem(memory_refs._snapshots, 1)
    out = build_agent_loop_context(turn, _make_ctx(turn, compiled_identity=compiled))
    assert "the newer boat" in out.owner_memory_text and "the old boat" not in out.owner_memory_text


def test_the_adapters_budget_helper_is_memory_refs_budget_helper(monkeypatch):
    """Batch 5, task 4 (ruling R38): one budget helper, core.memory_refs
    .memory_core_budget, so recall's dedup against the core and this render
    plan the SAME core. The adapter keeps its name (the test above patches
    it) and delegates — patching the shared helper reaches the render."""
    import core.agent.context_adapter as ca
    from core import memory_refs

    monkeypatch.setattr(memory_refs, "memory_core_budget", lambda: 77)
    assert ca._memory_core_budget() == 77


def test_owner_memory_text_defaults_empty_for_hand_built_contexts():
    turn = _make_turn()
    ctx = AgentLoopContext(
        turn=turn, ctx=_make_ctx(turn), identity_core_text="", skills_text="", runtime_text="",
        memory_text="", facts_text="", user_text="", recent_history_text="",
    )
    assert ctx.owner_memory_text == ""


def test_guest_volatile_tail_carries_no_owner_bands():
    """R19: the guest tail has none of the owner's bands — and the grounding
    law, which is always present by design, rides with the empty-ring line
    and never his deeds. Nothing about guests changes: the same blanks the
    adapter applied before the split."""
    from core.agent.system_prompt import build_system_prompt_parts
    turn = _make_turn(content="who are you?")
    ctx = _make_ctx(
        turn,
        compiled_identity=_compiled(),
        recent_memory_hits=("he told me his cat is Mochi",),
        held_stakes_text="I'm hoping his survey comes back clean",
        narrative_my_story="I've been thinking about the boat.",
        disclosures_text="- (3d) I was glad about the STL",
        callbacks_text="- 'the claw' — his puzzle box",
        recent_deeds_text="sent him the STL",
    )
    owner = build_agent_loop_context(turn, ctx, is_owner=True)
    guest = build_agent_loop_context(turn, ctx, is_owner=False)
    _, owner_tail = build_system_prompt_parts(owner, cadence_directive="")
    _, guest_tail = build_system_prompt_parts(guest, cadence_directive="")
    for band in ("## What's on your mind", "## My story", "## Your small truths", "## Our private language"):
        assert band in owner_tail, band
        assert band not in guest_tail, band
    assert "## What I've actually done" in guest_tail
    assert "(no recorded actions in the last day)" in guest_tail
    assert "sent him the STL" in owner_tail and "sent him the STL" not in guest_tail
    assert "Mochi" not in guest_tail
    assert "## Current Time" in guest_tail  # the clock is not private


def test_words_unchanged_the_split_only_reorders():
    """R7 — the proof that no words changed. Every paragraph of today's
    identity rendering appears verbatim in the stable part, and the two
    parts together carry exactly the paragraphs of the whole prompt. Batch
    5, task 3 (2026-09-28, R9 re-pin): the memory doc is represented by its
    CORE — the label and, for this small doc, every one of its bullets."""
    from core.agent.system_prompt import build_system_prompt, build_system_prompt_parts
    turn = _make_turn()
    compiled = _compiled()
    out = build_agent_loop_context(
        turn, _make_ctx(turn, compiled_identity=compiled, recent_memory_hits=("Mochi",), held_stakes_text="hoping"),
    )
    stable, volatile = build_system_prompt_parts(out, cadence_directive="[short]")
    for paragraph in render_text_prompt(compiled, exclude=("memory",)).split("\n\n"):
        assert paragraph in stable, paragraph
    for line in ("- likes tea", "- [09-27] the boat", "- no essays", "- the NAS"):
        assert line in stable, line
    whole = build_system_prompt(out, cadence_directive="[short]")
    assert sorted((stable + "\n\n" + volatile).split("\n\n")) == sorted(whole.split("\n\n"))
    assert "## Current Time" in volatile and "## Current Time" not in stable
    assert "### Memory" in stable and "### Memory" not in volatile


# ── Batch 8, task 1 (B8-1, ruling R1): the house stays the owner's ────────
# The situational band was the one owner band a guest still got: the
# owner's Discord presence and "the house right now" (devices, whose phone
# is home since when). A live leak until this task.

def test_the_ambient_awareness_band_is_the_owners_only():
    from core.agent.system_prompt import build_system_prompt_parts

    turn = _make_turn(content="who are you?")
    ctx = _make_ctx(turn, situational_note="the house right now: 4 devices; the owner's phone home since 14:02")
    owner = build_agent_loop_context(turn, ctx, is_owner=True)
    guest = build_agent_loop_context(turn, ctx, is_owner=False)
    assert "## Ambient awareness" in owner.situational_text
    assert guest.situational_text == ""
    _, owner_tail = build_system_prompt_parts(owner, cadence_directive="")
    _, guest_tail = build_system_prompt_parts(guest, cadence_directive="")
    assert "## Ambient awareness" in owner_tail and "phone home" in owner_tail
    assert "## Ambient awareness" not in guest_tail and "phone home" not in guest_tail


def test_where_she_lives_is_told_to_the_owner_and_not_to_a_guest(monkeypatch):
    """The house's shared drive (and its path) and her home's network are
    the owner's house: a guest's prompt carries none of it. The owner's
    prefix is unchanged."""
    import core.agent.where_you_live as wyl
    from core.agent.system_prompt import build_system_prompt_parts

    monkeypatch.setattr(wyl, "_enabled", lambda: True)
    monkeypatch.setattr(wyl, "nas_root", lambda: "/srv/house-share")
    turn = _make_turn(content="who are you?")
    owner = build_agent_loop_context(turn, _make_ctx(turn), is_owner=True)
    guest = build_agent_loop_context(turn, _make_ctx(turn), is_owner=False)
    owner_stable, _ = build_system_prompt_parts(owner, cadence_directive="")
    guest_stable, _ = build_system_prompt_parts(guest, cadence_directive="")
    assert wyl.passage() in owner_stable
    assert "## Where I live" in owner_stable      # the heading this test looks for is real
    assert "## Where I live" not in guest_stable and "/srv/house-share" not in guest_stable

"""One thing on her mind: the band in the reply prompt (Batch 6, task 1;
item 3.1; ruling R8).

The path is the callbacks band's, hop for hop: the store's render → the
reader in core.turn.organ_bands → recall's thin wrapper → TurnContext →
the context adapter (blank for a guest) → a section in the VOLATILE tail
of the system prompt, right after "Our private language". Every hop is
run, because a band that renders and never reaches the prompt passes
every test of the render.

The band shows only while the night's pick is unsaid, and only on a turn
where the two of them talk. It sits after the history, so the cached
prefix is the same bytes with and without a pick.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from ai import tonight

PICK_TEXT = "I keep thinking about the concert on Friday."
BAND = (
    "## One thing on my mind\n"
    "Say it once, when it fits, in your own words — never force it, "
    "never repeat it once it's said.\n"
    + PICK_TEXT
)


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(tonight, "TONIGHT_DIR", tmp_path / "tonight")
    return tmp_path / "tonight"


def _on(monkeypatch):
    monkeypatch.setattr(tonight, "tonight_enabled", lambda: True)


async def _write_pick(*, null: bool = False) -> str:
    reply = '{"pick": null}' if null else json.dumps({"pick": {
        "source": "stake", "source_id": "42", "text": PICK_TEXT, "why": "PRIVATE_REASON"}})

    async def llm(prompt):
        return reply

    now = time.time()
    await tonight.pick_tonight(1, llm_call=llm, now=now, stakes=[{"id": 42}],
                               journal_pages=[], daydreams=[], recent_conversation="")
    return tonight.day_for(now)


# ── the reader (core/turn/organ_bands.py) ───────────────────────────────

@pytest.mark.asyncio
async def test_the_reader_hands_up_the_band_while_the_pick_is_unsaid(folder, monkeypatch):
    from core.turn import organ_bands

    _on(monkeypatch)
    day = await _write_pick()
    assert organ_bands.read_tonight_band("dashboard") == BAND
    tonight.mark_said(day, at=time.time())
    assert organ_bands.read_tonight_band("dashboard") == ""


@pytest.mark.asyncio
async def test_the_reader_is_empty_while_the_flag_is_off(folder):
    from core.turn import organ_bands

    await _write_pick()
    assert organ_bands.read_tonight_band("dashboard") == ""      # the suite's off-pin


@pytest.mark.asyncio
async def test_the_reader_is_empty_on_a_null_night_and_with_no_file(folder, monkeypatch):
    from core.turn import organ_bands

    _on(monkeypatch)
    assert organ_bands.read_tonight_band("dashboard") == ""
    await _write_pick(null=True)
    assert organ_bands.read_tonight_band("dashboard") == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["discord_text", "discord_voice", "voice_local",
                                     "dashboard", "telegram_text"])
async def test_the_band_shows_on_every_door_where_they_talk(folder, monkeypatch, surface):
    from core.turn import organ_bands

    _on(monkeypatch)
    await _write_pick()
    assert organ_bands.read_tonight_band(surface) == BAND


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["reminder_fire", "proactive", "workbench", "local", "", None])
async def test_no_band_on_a_turn_that_is_not_a_conversation(folder, monkeypatch, surface):
    """The said-check runs only where the two of them talk, so the band
    shows only there: a pick said in a reminder would never be recorded."""
    from core.turn import organ_bands

    _on(monkeypatch)
    await _write_pick()
    assert organ_bands.read_tonight_band(surface) == ""


@pytest.mark.asyncio
async def test_a_dark_organ_never_opens_the_folder(folder, monkeypatch):
    from core.turn import organ_bands

    def must_not_read(*a, **kw):
        raise AssertionError("the pick was read while the organ is off")

    monkeypatch.setattr(tonight, "load_current", must_not_read)
    assert organ_bands.read_tonight_band("dashboard") == ""


def test_the_reader_is_empty_when_the_store_raises(folder, monkeypatch):
    from core.turn import organ_bands

    _on(monkeypatch)

    def boom(*a, **kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(tonight, "load_current", boom)
    assert organ_bands.read_tonight_band("dashboard") == ""


# ── recall's wrapper ────────────────────────────────────────────────────

def test_recall_delegates_to_the_organ_bands_module(monkeypatch):
    import core.turn.recall as recall
    from core.turn import organ_bands

    seen = []

    def fake(surface):
        seen.append(surface)
        return "FROM_ORGAN_BANDS"

    monkeypatch.setattr(organ_bands, "read_tonight_band", fake)
    assert recall._read_tonight_band("telegram_text") == "FROM_ORGAN_BANDS"
    assert seen == ["telegram_text"]


# ── the constructor hop: recall's real TurnContext build ────────────────

def _build_turn(content: str, surface: str = "dashboard", owner_id: int = 7):
    from core.turn.types import Turn, TurnMetadata

    return Turn(surface=surface, owner_id=owner_id, content=content,
                images=[], metadata=TurnMetadata(),
                received_at=datetime(2026, 9, 29, 3, 0, 0))


def _stub_recall_dependencies(monkeypatch):
    """Stub the heavy recall calls so build_turn_context runs cleanly (the
    twin in tests/ai/test_callbacks_reply_band.py)."""
    import ai.fact_retrieval as fact_retrieval

    monkeypatch.setattr(fact_retrieval, "get_decay_weighted_facts", AsyncMock(return_value=[]))
    import core.identity.service as identity_service_module

    monkeypatch.setattr(identity_service_module.identity_service, "compile_for_owner",
                        lambda owner_id: None)
    import core.conversation_history_store as chs

    monkeypatch.setattr(chs.ConversationHistoryStore, "get_conversation_history",
                        AsyncMock(return_value=[]))
    import ai.inner_memory_index as ami

    monkeypatch.setattr(ami, "query", AsyncMock(return_value=[]))
    import core.first_contact_targets as fct

    monkeypatch.setattr(fct, "unfulfilled_targets_for_owner", lambda owner_id: [])
    # One stub the twin does not have (fix round 1): recall's per-turn read
    # is a model call, and unstubbed it went out to the brain the test
    # settings name — a real request from a test about a prompt band.
    import ai.turn_read as turn_read

    monkeypatch.setattr(turn_read, "take_turn_read", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_the_band_survives_the_constructor_hop(folder, monkeypatch):
    """A field computed in recall but missed from the TurnContext call is
    blank everywhere, and every band test still passes. So run the hop: a
    real file, the real reader, the real build_turn_context."""
    import core.turn.recall as recall

    _on(monkeypatch)
    await _write_pick()
    _stub_recall_dependencies(monkeypatch)
    ctx = await recall.build_turn_context(_build_turn("hey"))
    assert ctx.tonight_text == BAND


@pytest.mark.asyncio
async def test_a_reminder_turn_carries_no_band_through_recall(folder, monkeypatch):
    import core.turn.recall as recall

    _on(monkeypatch)
    await _write_pick()
    _stub_recall_dependencies(monkeypatch)
    ctx = await recall.build_turn_context(_build_turn("time to stretch", "reminder_fire"))
    assert ctx.tonight_text == ""


@pytest.mark.asyncio
async def test_the_flag_off_carries_no_band_through_recall(folder, monkeypatch):
    import core.turn.recall as recall

    await _write_pick()
    _stub_recall_dependencies(monkeypatch)
    ctx = await recall.build_turn_context(_build_turn("hey"))
    assert ctx.tonight_text == ""


# ── the carriers: TurnContext → adapter (blank for a guest) ─────────────

def test_ctx_and_adapter_carry_the_field():
    from core.agent.context_adapter import AgentLoopContext
    from core.turn.types import TurnContext

    assert TurnContext.__dataclass_fields__["tonight_text"].default == ""
    assert AgentLoopContext.__dataclass_fields__["tonight_text"].default == ""


def test_a_guest_never_sees_what_is_on_her_mind():
    from core.agent.context_adapter import build_agent_loop_context
    from core.turn.types import Turn, TurnContext, TurnMetadata

    turn = Turn(surface="discord_text", owner_id=1, content="who are you?",
                metadata=TurnMetadata(), received_at=datetime(2026, 9, 29, 3, 0, 0))
    ctx = TurnContext(turn=turn, tonight_text=BAND)
    assert build_agent_loop_context(turn, ctx, is_owner=True).tonight_text == BAND
    assert build_agent_loop_context(turn, ctx, is_owner=False).tonight_text == ""


def test_a_guest_prompt_is_the_same_bytes_with_and_without_a_pick():
    from core.agent.context_adapter import build_agent_loop_context
    from core.agent.system_prompt import build_system_prompt
    from core.turn.types import Turn, TurnContext, TurnMetadata

    turn = Turn(surface="discord_text", owner_id=1, content="who are you?",
                metadata=TurnMetadata(), received_at=datetime(2026, 9, 29, 3, 0, 0))
    with_pick = build_agent_loop_context(turn, TurnContext(turn=turn, tonight_text=BAND),
                                         is_owner=False)
    without = build_agent_loop_context(turn, TurnContext(turn=turn), is_owner=False)
    out = build_system_prompt(with_pick, cadence_directive="")
    assert "One thing on my mind" not in out and PICK_TEXT not in out
    assert out == build_system_prompt(without, cadence_directive="")


# ── the section (core/agent/system_prompt.py) ───────────────────────────

@dataclass
class _Ctx:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = "skills here"
    runtime_text: str = "## Current Time\n10:00"
    memory_text: str = "memory snapshot"
    facts_text: str = ""
    recent_deeds_text: str = ""
    own_work_trace_text: str = ""
    present_moment_text: str = ""
    situational_text: str = ""
    disclosures_text: str = ""
    callbacks_text: str = ""
    owner_memory_text: str = "### Memory\n## Durable Context\n- likes tea"
    corrections_text: str = "## Corrections of record\n- the meeting is on Tuesday"
    tonight_text: str = ""


@dataclass
class _NoField:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = "skills here"
    runtime_text: str = "## Current Time\n10:00"
    memory_text: str = "memory snapshot"
    facts_text: str = ""
    recent_deeds_text: str = ""
    own_work_trace_text: str = ""
    present_moment_text: str = ""
    situational_text: str = ""
    disclosures_text: str = ""
    callbacks_text: str = ""
    owner_memory_text: str = "### Memory\n## Durable Context\n- likes tea"
    corrections_text: str = "## Corrections of record\n- the meeting is on Tuesday"


def test_the_band_is_in_the_volatile_tail_and_not_in_the_stable_prefix():
    from core.agent.system_prompt import build_system_prompt_parts

    stable, volatile = build_system_prompt_parts(_Ctx(tonight_text=BAND), cadence_directive="[short]")
    assert BAND in volatile
    assert "One thing on my mind" not in stable and PICK_TEXT not in stable
    # after the clock, so after everything the cache covers
    assert volatile.index("## Current Time") < volatile.index("## One thing on my mind")
    assert volatile.endswith("[short]")


def test_the_stable_prefix_is_the_same_bytes_with_and_without_a_pick():
    from core.agent.system_prompt import build_system_prompt_parts

    with_pick, _ = build_system_prompt_parts(_Ctx(tonight_text=BAND), cadence_directive="[short]")
    without, _ = build_system_prompt_parts(_Ctx(), cadence_directive="[short]")
    never_heard, _ = build_system_prompt_parts(_NoField(), cadence_directive="[short]")
    assert with_pick == without == never_heard
    assert with_pick.endswith("- the meeting is on Tuesday")


def test_no_pick_is_byte_identical_to_a_build_without_the_band():
    from core.agent.system_prompt import build_system_prompt, build_system_prompt_parts

    off = build_system_prompt(_Ctx(), cadence_directive="[short]")
    assert "One thing on my mind" not in off
    assert off == build_system_prompt(_NoField(), cadence_directive="[short]")
    assert (build_system_prompt_parts(_Ctx(), cadence_directive="[short]")
            == build_system_prompt_parts(_NoField(), cadence_directive="[short]"))


def test_the_band_adds_its_own_bytes_and_nothing_else():
    from core.agent.system_prompt import build_system_prompt_parts

    _, with_pick = build_system_prompt_parts(_Ctx(tonight_text=BAND), cadence_directive="[short]")
    _, without = build_system_prompt_parts(_Ctx(), cadence_directive="[short]")
    assert with_pick.replace("\n\n" + BAND, "", 1) == without
    assert with_pick.count("## One thing on my mind") == 1


def test_the_section_sits_right_after_our_private_language():
    from core.agent.system_prompt import build_system_prompt

    out = build_system_prompt(
        _Ctx(disclosures_text="TRUTHS_MARKER", callbacks_text="CALLBACKS_MARKER",
             tonight_text=BAND, present_moment_text="MOMENT_MARKER"),
        cadence_directive="")
    assert out.index("## Our private language") < out.index("## One thing on my mind")
    assert out.index("## One thing on my mind") < out.index("MOMENT_MARKER")
    between = out[out.index("CALLBACKS_MARKER") + len("CALLBACKS_MARKER"):
                  out.index("## One thing on my mind")]
    assert between == "\n\n"


def test_her_reason_and_the_id_never_reach_the_prompt():
    from core.agent.system_prompt import build_system_prompt

    pick = tonight.Pick(day="2026-09-29", source="stake", source_id="4242",
                        text=PICK_TEXT, why="PRIVATE_REASON")
    out = build_system_prompt(_Ctx(tonight_text=tonight.render_band(pick)), cadence_directive="")
    assert PICK_TEXT in out
    assert "PRIVATE_REASON" not in out and "4242" not in out

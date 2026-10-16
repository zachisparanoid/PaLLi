"""Her record of quiet choices, and the switch (Batch 8, task 2a — T2-1, T2-4, T2a-3).

- Every quiet choice is written to ``data/quiet_choices/<date>.jsonl`` with a
  one-line reason in her own words. It is owner-only: she reads it through
  her owner-only ``introspect`` tool, the owner through ``palli_ctl quiet``.
- ``companion.she_decides`` (schema default OFF, dark until the wrap) gates
  the decision flow only. With it off her decision behaviour is today's,
  byte for byte: the golden below was captured at the Task 1 head.

Stand-in names only: Robin is the owner, Sam a guest.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.agent.she_decides_harness import (
    DM_CHANNEL,
    GUEST_ID,
    OWNER_DISCORD_ID,
    ROOM_CHANNEL,
    DmHarness,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def dm_harness(monkeypatch, tmp_path) -> DmHarness:
    return DmHarness(monkeypatch, tmp_path)


# ── the record ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_every_quiet_choice_is_recorded_with_her_reason(dm_harness):
    dm_harness.model_chooses_quiet("they asked me not to")
    await dm_harness.says("do not respond, I'm just thinking out loud here")
    rows = dm_harness.quiet_record()
    assert rows[-1]["reason"] == "they asked me not to"
    assert rows[-1]["excerpt"].startswith("do not respond")
    assert rows[-1]["source"] == "reply"
    assert rows[-1]["door"] == "discord_text"
    assert rows[-1]["conversation"] == f"discord:{DM_CHANNEL}"
    assert rows[-1]["who"] == {"id": str(OWNER_DISCORD_ID), "name": "robin_example", "owner": True}
    assert datetime.fromisoformat(rows[-1]["ts"])


@pytest.mark.asyncio
async def test_a_guests_quiet_choice_names_the_guest(dm_harness):
    dm_harness.model_chooses_quiet("Sam was talking to someone else")
    await dm_harness.says("ha, see you later", who="Sam", channel=ROOM_CHANNEL, is_dm=False)
    row = dm_harness.quiet_record()[-1]
    assert row["who"] == {"id": str(GUEST_ID), "name": "sam_example", "owner": False}
    assert row["conversation"] == f"discord:{ROOM_CHANNEL}"
    assert row["excerpt"] == "[Sam]: ha, see you later"


@pytest.mark.asyncio
async def test_an_answer_writes_nothing_to_the_record(dm_harness):
    dm_harness.model_answers("hi Robin")
    await dm_harness.says("hi")
    assert dm_harness.quiet_record() == []


def test_the_record_keeps_the_first_120_characters_and_one_file_a_day(tmp_path, monkeypatch):
    from core import quiet_record

    monkeypatch.setenv("PALLI_QUIET_CHOICES_DIR", str(tmp_path))
    day1 = datetime(2026, 10, 1, 23, 59)
    day2 = datetime(2026, 10, 2, 0, 1)
    assert quiet_record.record(door="dashboard", conversation="dashboard:1", who_id="1",
                               who_name="", owner=True, text="x" * 300, reason="r1",
                               source="reply", now=day1)
    quiet_record.record(door="discord_text", conversation="discord:5", who_id="9",
                        who_name="Sam", owner=False, text="hello", reason="r2",
                        source="first_pass", now=day2)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-10-01.jsonl", "2026-10-02.jsonl"]
    rows = quiet_record.read(2, now=day2)
    assert [r["reason"] for r in rows] == ["r1", "r2"]
    assert len(rows[0]["excerpt"]) == 120
    assert quiet_record.read(1, now=day2 + timedelta(days=1)) == []
    assert [r["reason"] for r in quiet_record.read(1, now=day2)] == ["r2"]


def test_the_record_never_raises_and_skips_a_bad_line(tmp_path, monkeypatch):
    from core import quiet_record

    blocked = tmp_path / "a-file"
    blocked.write_text("not a dir")
    monkeypatch.setenv("PALLI_QUIET_CHOICES_DIR", str(blocked))
    assert quiet_record.record(door="d", conversation="c", who_id="1", who_name="",
                               owner=True, text="t", reason="r") is False
    monkeypatch.setenv("PALLI_QUIET_CHOICES_DIR", str(tmp_path / "ok"))
    quiet_record.record(door="d", conversation="c", who_id="1", who_name="", owner=True,
                        text="t", reason="r")
    path = next((tmp_path / "ok").iterdir())
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert [r["reason"] for r in quiet_record.read(1)] == ["r"]


def test_an_unknown_source_is_refused(tmp_path, monkeypatch):
    from core import quiet_record

    monkeypatch.setenv("PALLI_QUIET_CHOICES_DIR", str(tmp_path))
    assert set(quiet_record.SOURCES) == {"reply", "first_pass", "spoke_during_note"}
    with pytest.raises(ValueError):
        quiet_record.record(door="d", conversation="c", who_id="1", who_name="",
                            owner=True, text="t", reason="r", source="vibes")


def test_the_suite_never_touches_her_live_record():
    from core import quiet_record

    root = Path(os.environ["PALLI_TEST_DATA_ROOT"])
    assert quiet_record.record_dir().resolve().is_relative_to(root.resolve())
    assert "data/quiet_choices/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()


# ── she can read it (owner-only) ──────────────────────────────────────────

def _seed_rows(monkeypatch, tmp_path) -> None:
    from core import quiet_record

    monkeypatch.setenv("PALLI_QUIET_CHOICES_DIR", str(tmp_path / "quiet"))
    quiet_record.record(door="discord_text", conversation="discord:5", who_id="9",
                        who_name="Sam", owner=False, text="[Sam]: polly don't answer this",
                        reason="Sam asked me not to", source="reply")


def test_introspect_reads_her_quiet_choices_when_the_switch_is_on(monkeypatch, tmp_path):
    from core.agent import quiet_choice
    from core.agent.builtin_tools.introspect import IntrospectTool

    _seed_rows(monkeypatch, tmp_path)
    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    tool = IntrospectTool()
    assert tool.owner_only is True                       # never a guest's to ask
    src = asyncio.run(tool._quiet_choices([], 7, True))
    assert src.enabled and src.found == 1
    assert "Sam asked me not to" in src.entries[0] and "polly don't answer this" in src.entries[0]
    hit = asyncio.run(tool._quiet_choices(["polly"], 7, False))
    miss = asyncio.run(tool._quiet_choices(["zebra"], 7, False))
    assert hit.found == 1 and miss.found == 0 and miss.absence


def test_introspect_leaves_the_source_out_when_the_switch_is_off(monkeypatch, tmp_path):
    from core.agent.builtin_tools.introspect import IntrospectTool

    _seed_rows(monkeypatch, tmp_path)
    src = asyncio.run(IntrospectTool()._quiet_choices([], 7, True))
    assert src.enabled is False


def test_the_introspect_description_names_the_source_only_with_the_switch_on(monkeypatch):
    from core.agent import quiet_choice
    from core.agent.builtin_tools.introspect import QUIET_CHOICES_ITEM, IntrospectTool

    off = IntrospectTool().description
    assert off == IntrospectTool.description and QUIET_CHOICES_ITEM not in off
    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    on = IntrospectTool().description
    assert QUIET_CHOICES_ITEM in on
    assert on.replace(QUIET_CHOICES_ITEM, "") == off      # one short item, nothing else
    assert IntrospectTool.description == off              # the class text is never changed


def test_the_source_label_is_plain_words():
    from core.agent.builtin_tools import introspect
    from tests.prompts.test_plain_words import PHRASE_FAMILY

    assert not PHRASE_FAMILY.search(introspect.QUIET_CHOICES_LABEL)
    assert not PHRASE_FAMILY.search(introspect.QUIET_CHOICES_ITEM)


# ── her quiet reasons never reach a guest's payload (ruling T2-4a) ────────

def _room_with_a_quiet_choice():
    """A shared room: the owner's message she chose quiet for (her reason
    on the call), then a stranger's line and her reply, then this run."""
    from core.agent.session import AgentMessage, TextBlock, ToolResultBlock, ToolUseBlock

    return [
        AgentMessage(role="user", content=[TextBlock(text="don't reply, just noting it")]),
        AgentMessage(role="assistant", content=[ToolUseBlock(
            id="q1", name="stay_quiet", input={"reason": "Robin asked me not to"})]),
        AgentMessage(role="tool", content=[ToolResultBlock(
            tool_use_id="q1", content="You stayed quiet. Nothing was sent.")]),
        AgentMessage(role="user", content=[TextBlock(text="can you check the disk?")]),
        AgentMessage(role="assistant", content=[
            TextBlock(text="checking"),
            ToolUseBlock(id="t1", name="local_shell", input={"command": "df -h"})]),
        AgentMessage(role="tool", content=[ToolResultBlock(tool_use_id="t1", content="91% used")]),
        AgentMessage(role="assistant", content=[TextBlock(text="The disk is 91% full.")]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: why so quiet earlier?")]),
    ]


def _all_text(msgs) -> str:
    from core.agent.session import TextBlock, ToolResultBlock, ToolUseBlock

    out = []
    for m in msgs:
        for b in m.content:
            if isinstance(b, TextBlock):
                out.append(b.text)
            elif isinstance(b, ToolUseBlock):
                out.append(f"{b.name} {json.dumps(b.input)}")
            elif isinstance(b, ToolResultBlock):
                out.append(str(b.content))
    return "\n".join(out)


@pytest.mark.parametrize("audience", ["collaborator", "stranger", "nonsense"])
def test_her_quiet_reasons_never_reach_a_guests_payload(audience):
    from core.agent.session import diet_for_llm

    text = _all_text(diet_for_llm(_room_with_a_quiet_choice(), keep_from=7, audience=audience))
    assert "stay_quiet" not in text and "Robin asked me not to" not in text
    assert "You stayed quiet" not in text
    assert "don't reply, just noting it" in text            # what was said stays
    if audience == "collaborator":
        assert "local_shell(" in text                        # the room's other work still shows


def test_the_owner_still_reads_her_quiet_choices_as_before():
    from core.agent.session import RECEIPT_MARK, diet_for_llm

    out = diet_for_llm(_room_with_a_quiet_choice(), keep_from=7, audience="owner")
    receipt = out[1].content[0].text
    assert receipt == (f'{RECEIPT_MARK} stay_quiet({{"reason": "Robin asked me not to"}}) '
                       "→ You stayed quiet. Nothing was sent.")
    assert [m.role for m in out] == ["user", "assistant", "user", "assistant",
                                     "assistant", "user"]


@pytest.mark.asyncio
async def test_a_collaborators_run_never_carries_her_quiet_reason(tmp_path, monkeypatch):
    """Through the runner and the real diet: Sam is a collaborator, and the
    owner's earlier quiet choice in the room never reaches Sam's payload."""
    import core.agent.llm_client as llm_mod
    from core.agent.runner import RunnerDeps, run_turn
    from core.agent.session import Session
    from core.turn.types import Turn, TurnMetadata

    room = _room_with_a_quiet_choice()
    s = await Session.open_or_create(f"discord:{ROOM_CHANNEL}", root=tmp_path)
    for m in room[:-1]:
        await s.append(m)
    seen: list[dict] = []

    async def fake_call(**kw):
        seen.append(kw)
        return llm_mod.LLMResponse(text_blocks=["just busy"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)

    async def recall(t):
        return SimpleNamespace(turn=t, user_text=t.content, recent_history_text="",
                               identity_core_text="I'm PaLLi.", is_owner=False)

    async def cadence(prompt):
        return '{"should_respond": true}'

    turn = Turn(surface="discord_text", owner_id=GUEST_ID, content="[Sam]: why so quiet earlier?",
                metadata=TurnMetadata(channel_id=ROOM_CHANNEL, origin="guest"),
                received_at=datetime(2026, 10, 1, 9, 0, 0))
    deps = RunnerDeps(build_turn_context=recall, cadence_llm=cadence, sessions_root=tmp_path,
                      provider="openai_compat", model="m", client=None, tool_registry=None,
                      polling_tools=set(), is_owner=False, audience="collaborator")
    await run_turn(turn, deps=deps)
    text = _all_text(seen[0]["messages"])
    assert "Robin asked me not to" not in text and "stay_quiet" not in text
    assert "local_shell(" in text


# ── the owner can read it ─────────────────────────────────────────────────

def _ctl():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "palli_ctl_quiet_test_module", REPO / "scripts" / "palli_ctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_palli_ctl_quiet_lists_her_choices(monkeypatch, tmp_path):
    _seed_rows(monkeypatch, tmp_path)
    ctl = _ctl()
    lines: list[str] = []
    args = ctl.build_parser().parse_args(["quiet", "--days", "3"])
    assert ctl.run_quiet_command(args, out=lines.append) == 0
    text = "\n".join(lines)
    assert "Sam asked me not to" in text and "polly don't answer this" in text
    assert "discord_text" in text


def test_palli_ctl_quiet_says_so_when_there_is_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("PALLI_QUIET_CHOICES_DIR", str(tmp_path / "empty"))
    ctl = _ctl()
    lines: list[str] = []
    assert ctl.run_quiet_command(ctl.build_parser().parse_args(["quiet"]), out=lines.append) == 0
    assert lines and "no quiet choices" in lines[0].lower()


def test_palli_ctl_is_still_crlf_throughout():
    raw = (REPO / "scripts" / "palli_ctl.py").read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")


# ── the switch ────────────────────────────────────────────────────────────

def test_the_switch_ships_dark_and_the_wait_has_a_floor():
    from pydantic import ValidationError

    from core.settings_schema import CompanionConfig

    f = CompanionConfig.model_fields
    assert f["she_decides"].default is False
    assert f["room_wait_seconds"].default == 45
    with pytest.raises(ValidationError):
        CompanionConfig(room_wait_seconds=4)


def test_the_switch_reads_the_setting_and_fails_closed(monkeypatch):
    from core.agent import quiet_choice

    real = quiet_choice._she_decides_enabled_impl
    monkeypatch.setattr("core.turn.companion_flags.companion_settings", lambda: None)
    assert real() is False
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(she_decides=True))
    assert real() is True

    def boom():
        raise RuntimeError("settings down")

    monkeypatch.setattr("core.turn.companion_flags.companion_settings", boom)
    assert real() is False


def test_the_suite_holds_the_switch_off():
    from core.agent import quiet_choice

    assert quiet_choice.she_decides_enabled() is False


@pytest.mark.asyncio
async def test_the_switch_off_is_todays_behaviour_byte_for_byte(tmp_path, monkeypatch):
    """Ruling T2a-3: a stranger's turn in a shared room and the owner's DM turn
    (the quick vote saying "don't respond") send the model exactly what they
    sent at the Task 1 head — the cadence prompt and every llm_client.call
    keyword, compared as JSON."""
    from core.agent import quiet_choice
    from tests.agent import she_decides_golden as golden

    assert quiet_choice.she_decides_enabled() is False
    now = await golden.capture(tmp_path / "sessions", monkeypatch)
    expected = json.loads(golden.GOLDEN_PATH.read_text(encoding="utf-8"))
    for turn in ("guest_room_turn", "owner_dm_turn"):
        assert now[turn]["cadence_prompts"] == expected[turn]["cadence_prompts"], turn
        assert now[turn]["llm_calls"] == expected[turn]["llm_calls"], turn
    assert golden.dumps(now) == golden.GOLDEN_PATH.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_the_same_turns_with_the_switch_on_do_differ(tmp_path, monkeypatch):
    """The golden test has teeth: with the switch on, the same two turns are
    offered the quiet choice and the owner's DM loses the silence note."""
    from core.agent import quiet_choice
    from core.agent.runner import SILENCE_NOTE
    from tests.agent import she_decides_golden as golden

    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    now = await golden.capture(tmp_path / "sessions", monkeypatch)
    expected = json.loads(golden.GOLDEN_PATH.read_text(encoding="utf-8"))
    assert now != expected
    guest_call = now["guest_room_turn"]["llm_calls"][0]
    owner_call = now["owner_dm_turn"]["llm_calls"][0]
    assert [t["name"] for t in guest_call["tools"]] == ["stay_quiet"]
    assert [t["name"] for t in owner_call["tools"]] == ["look_up", "stay_quiet"]
    assert SILENCE_NOTE not in (owner_call["trailing_system_note"] or "")

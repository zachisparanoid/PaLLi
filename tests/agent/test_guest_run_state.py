"""A guest's run never touches the owner's run state (Batch 8, task 1 —
B8-1, ruling R3; Review Focus 1).

In a shared room the room file is one conversation, written by anyone. The
run state is not: the owner's run phase, tool count, now-doing line, stop
and pause requests, queued steers, task slot and her presence signage are
keyed per session, and a guest's run used to key them on the ROOM, so a
stranger saying "hi!" while she worked for the owner could:

- take the owner's queued steer into the guest's run and count it answered;
- clear the owner's phase and tool count (the owner's next message then
  cancelled the work instead of steering it, and the watchdog called a
  live run wedged);
- take the owner's "hold on" or "forget it" as its own;
- pop her presence and clear her status in the middle of the work.

Now a guest run keys all of that on ``{room_key}:guest:{user_id}``, while
its visible words and her visible reply still go into the room file. The
harness holds the owner's run mid-tool (a gated fake tool) on the REAL
steering module, and runs a guest's turn to completion beside it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent import steering, task_slots
from core.agent.session import TextBlock, ToolResultBlock, ToolUseBlock
from core.turn.types import Turn, TurnMetadata

CADENCE_JSON = ('{"should_respond": true, "register": "neutral", '
                '"length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "ok"}')
OWNER_ID = 1


@dataclass
class FakeCtx:
    turn: Any = None
    user_text: str = ""
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


class GatedRegistry:
    """The owner's one tool: it blocks until the test releases it."""

    def __init__(self, gate: asyncio.Event):
        self.gate = gate

    def all_names(self):
        return ["slow_tool"]

    def schemas_for(self, ctx):
        return [{"name": "slow_tool", "description": "slow", "input_schema": {"type": "object"}}]

    async def dispatch(self, tu, ctx):
        await self.gate.wait()
        return ToolResultBlock(tool_use_id=tu.id, content="tool finished", is_error=False)


class RunnerHarness:
    def __init__(self, root, monkeypatch, *, guest_search: bool = False):
        import core.agent.llm_client as llm_mod

        self.root = root
        self.gate = asyncio.Event()
        self.room: int | None = None
        self.owner_task: asyncio.Task | None = None
        self.guest_payloads: list[list] = []
        self.presence_changes: list[dict] = []
        self.guest_states: list[dict] = []
        self.guest_tools: list[list[str]] = []
        self._owner_calls = 0
        self._llm = llm_mod

        async def fake_call(**kw):
            names = {str(t.get("name")) for t in (kw.get("tools") or [])}
            if "slow_tool" in names:            # the owner's run has her tool belt
                self._owner_calls += 1
                if self._owner_calls == 1:
                    return llm_mod.LLMResponse(
                        text_blocks=["checking"],
                        tool_uses=[ToolUseBlock(id="t1", name="slow_tool", input={})],
                        stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
                return llm_mod.LLMResponse(text_blocks=["all done"], tool_uses=[],
                                           stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)
            # a guest's run: no tools at all (web_search only, with
            # features.guest_web_search on)
            self.guest_payloads.append(list(kw["messages"]))
            self.guest_tools.append(sorted(names))
            self.guest_states.append({
                "owner_phase": steering.get_run_phase(self.room_key),
                "guest_phase": steering.get_run_phase(self.guest_key(42)),
            })
            return llm_mod.LLMResponse(text_blocks=["hi! nice to meet you."], tool_uses=[],
                                       stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)

        monkeypatch.setattr(llm_mod, "call", fake_call)
        # Duplex on, so every run-state site the guest could touch is live:
        # the stop and pause checks, the task slot, the presence clear.
        monkeypatch.setattr("core.agent.duplex.duplex_enabled", lambda: True)
        # The promise gate is a model call of its own; it is not under test.
        monkeypatch.setattr("core.agent.promise_gate.gate_flags", lambda: (False, False))
        # Moved deliberately (R14, T8; plan §2b.6): a guest's belt is what
        # dispatch builds for them — none with features.guest_web_search off
        # (as shipped), web_search only with it on; their run state is their
        # own either way. (she_decides is off here, so the look never runs.)
        self.guest_search = guest_search
        if guest_search:
            from tests.agent.she_decides_harness import guest_search_on

            guest_search_on(monkeypatch)

        import core.agent.hands_runtime as hr

        async def change_presence(**kw):
            self.presence_changes.append(kw)

        self._hr = hr
        hr.register_client(SimpleNamespace(guilds=[], change_presence=change_presence))

    # ── keys ──
    @property
    def room_key(self) -> str:
        return f"discord:{self.room}"

    def guest_key(self, guest_id: int) -> str:
        return f"{self.room_key}:guest:{guest_id}"

    # ── runs ──
    def _deps(self, *, owner: bool):
        from core.agent.builtin_tools import build_guest_registry
        from core.agent.runner import RunnerDeps

        async def fake_recall(turn):
            return FakeCtx(turn=turn, user_text=str(turn.content), is_owner=owner)

        async def fake_cadence_llm(prompt):
            return CADENCE_JSON

        return RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence_llm,
            sessions_root=self.root, provider="openai_compat", model="m", client=None,
            tool_registry=GatedRegistry(self.gate) if owner else build_guest_registry(),
            polling_tools=set(), is_owner=owner,
            audience="owner" if owner else "stranger",
        )

    async def start_owner_run(self, room: int) -> None:
        from core.agent.runner import run_turn

        self.room = room
        turn = Turn(surface="discord_text", owner_id=OWNER_ID, content="check the disk",
                    metadata=TurnMetadata(channel_id=room, origin="user"),
                    received_at=datetime(2026, 10, 1, 9, 0, 0))
        self.owner_task = asyncio.get_running_loop().create_task(
            run_turn(turn, deps=self._deps(owner=True)))
        for _ in range(200):                     # wait until it is inside the tool
            if steering.get_tool_call_count(self.room_key) >= 1:
                break
            await asyncio.sleep(0.01)
        assert steering.get_tool_call_count(self.room_key) == 1, "the owner's run never reached its tool"

    async def run_guest_turn(self, room: int, guest_id: int, text: str):
        from core.agent.runner import run_turn
        from core.owner_gate import GUEST_ORIGIN, guest_content

        turn = Turn(surface="discord_text", owner_id=guest_id,
                    content=guest_content(f"guest {guest_id}", text),
                    metadata=TurnMetadata(channel_id=room, origin=GUEST_ORIGIN),
                    received_at=datetime(2026, 10, 1, 9, 1, 0))
        return await run_turn(turn, deps=self._deps(owner=False))

    async def finish_owner_run(self):
        self.gate.set()
        return await asyncio.wait_for(self.owner_task, timeout=5)

    # ── reads ──
    def owner_run_phase_untouched(self) -> bool:
        return (steering.get_run_phase(self.room_key) == "generating"
                and steering.get_tool_call_count(self.room_key) == 1
                and "slow_tool" in steering.get_now_doing(self.room_key)
                and task_slots.held(self.room_key) == ["the run"])

    def room_file_rows(self) -> list[tuple[str, str]]:
        import json

        path = self.root / f"discord_{self.room}.jsonl"
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            text = " ".join(b.get("text", "") for b in d["content"] if b.get("type") == "text")
            rows.append((d["role"], text))
        return rows

    def room_file_has(self, text: str) -> bool:
        return any(text in row_text for _, row_text in self.room_file_rows())

    def cleanup(self) -> None:
        for key in (self.room_key, self.guest_key(42)):
            steering.clear(key)
            steering.clear_run_state(key)
            steering.clear_paused(key)
        task_slots.reset_for_tests()
        self._hr.register_client(None)


@pytest.fixture(params=[False, True], ids=["guest_search_off", "guest_search_on"])
def runner_harness(request, tmp_path, monkeypatch):
    task_slots.reset_for_tests()
    h = RunnerHarness(tmp_path, monkeypatch, guest_search=request.param)
    try:
        yield h
    finally:
        if h.owner_task is not None and not h.owner_task.done():
            h.owner_task.cancel()
        if h.room is not None:
            h.cleanup()


@pytest.mark.asyncio
async def test_a_guest_run_never_clears_the_owners_run_state(runner_harness):
    await runner_harness.start_owner_run(room=555)              # the owner's work is in flight
    result = await runner_harness.run_guest_turn(room=555, guest_id=42, text="hi!")
    assert result.response_text == "hi! nice to meet you."
    assert runner_harness.owner_run_phase_untouched()
    assert runner_harness.room_file_has("[guest 42]: hi!")      # visible room content is shared
    assert runner_harness.room_file_has("hi! nice to meet you.")
    # The guest's run kept its own state while it ran, and cleared only that.
    assert runner_harness.guest_states == [{"owner_phase": "generating", "guest_phase": "generating"}]
    assert runner_harness.guest_tools == [["web_search"] if runner_harness.guest_search else []]
    assert steering.get_run_phase(runner_harness.guest_key(42)) == ""
    owner = await runner_harness.finish_owner_run()
    assert owner.response_text == "all done"


@pytest.mark.asyncio
async def test_a_guest_run_never_drains_the_owners_queued_steer(runner_harness):
    await runner_harness.start_owner_run(room=555)
    bundle = steering.SteerBundle(turn=SimpleNamespace(content="actually check /var too"))
    assert steering.enqueue(runner_harness.room_key, bundle)
    await runner_harness.run_guest_turn(room=555, guest_id=42, text="hi!")
    assert steering.peek(runner_harness.room_key) == [bundle]   # still the owner's to drain
    assert bundle.drained is False
    guest_text = " ".join(b.text for m in runner_harness.guest_payloads[0] for b in m.content
                          if isinstance(b, TextBlock))
    assert "check /var too" not in guest_text
    assert not runner_harness.room_file_has("check /var too")   # not written under the guest
    await runner_harness.finish_owner_run()
    # The owner's own run drained it at its next boundary.
    assert runner_harness.room_file_has("actually check /var too")


@pytest.mark.asyncio
async def test_a_guest_run_never_clears_her_presence(runner_harness):
    await runner_harness.start_owner_run(room=555)
    steering.note_presence_activity(runner_harness.room_key, "heads down on the disk")
    await runner_harness.run_guest_turn(room=555, guest_id=42, text="hi!")
    assert runner_harness.presence_changes == []                # her status stays up
    await runner_harness.finish_owner_run()
    assert runner_harness.presence_changes == [{"activity": None}]   # the owner's run clears it


@pytest.mark.asyncio
async def test_a_guest_run_never_takes_the_owners_hold_on(runner_harness):
    """The owner said "hold on" mid-tool: the request waits for the OWNER's
    next boundary. A guest's run neither pauses itself on it nor drops it."""
    await runner_harness.start_owner_run(room=555)
    steering.request_pause(runner_harness.room_key)
    result = await runner_harness.run_guest_turn(room=555, guest_id=42, text="hi!")
    assert result.terminated_by == "natural"
    assert result.response_text == "hi! nice to meet you."
    assert steering.pause_requested(runner_harness.room_key)
    assert steering.get_paused(runner_harness.room_key) is None
    owner = await runner_harness.finish_owner_run()
    assert owner.terminated_by == "paused"
    assert steering.get_paused(runner_harness.room_key) is not None


@pytest.mark.asyncio
async def test_a_guest_run_never_takes_the_owners_forget_it(runner_harness):
    await runner_harness.start_owner_run(room=555)
    steering.request_stop(runner_harness.room_key)
    result = await runner_harness.run_guest_turn(room=555, guest_id=42, text="hi!")
    assert result.terminated_by == "natural" and result.response_text
    assert steering.stop_requested(runner_harness.room_key)
    owner = await runner_harness.finish_owner_run()
    assert owner.terminated_by == "stopped"


def test_a_guests_run_key_is_never_a_transcript_file_name():
    """The guest key only names run state: the transcript key is the room's."""
    from core.agent.runner import _derive_session_key
    from core.session_keys import guest_run_key, room_key

    turn = Turn(surface="discord_text", owner_id=42, content="[Sam]: hi",
                metadata=TurnMetadata(channel_id=555, origin="guest"),
                received_at=datetime(2026, 10, 1, 9, 0, 0))
    assert _derive_session_key(turn) == "discord:555" == room_key("discord", 555)
    assert guest_run_key(room_key("discord", 555), 42) == "discord:555:guest:42"

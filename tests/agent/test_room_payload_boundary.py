"""A guest's payload is built from what was said, and ends on their own words
(Batch 8, final fix wave — ruling FW-1, the whole-branch review's C1).

With ``companion.she_decides`` on, the room's first read writes a guest's line
to the room file the moment it arrives, and the turn that answers it is marked
already recorded (``user_side_prerecorded``). The guest's run opens the room
file seconds later. Every row another writer appended in between is in that
snapshot, after the guest's line: the owner's tool call and its result, her
quiet choice for the owner (and her reason), another guest's line. The runner
used to take the last row it loaded as the turn's own row and keep it
verbatim, for every audience, so a stranger's model call could receive the
owner's tool output as ``[system observation] …`` and end on someone else's
message.

Now (payload-only; the room file is untouched):

- the turn's own rows are found by their words (searching back, the way a
  burst or a read of several lines is coalesced);
- for a guest (a stranger or a collaborator) every row loaded from the file
  goes through the audience diet, and the turn's own rows come last;
- for the owner's burst recorded on arrival, the burst's own rows come last,
  so a guest's arrival line is never the last word of the owner's run.

The owner's payload with nothing interleaved is unchanged, byte for byte
(``test_the_switch_off_is_todays_behaviour_byte_for_byte`` pins the rest).
Stand-in names only: Robin is the owner, Sam and Bob are guests.
"""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent import steering, task_slots
from core.agent.session import (
    RECEIPT_MARK,
    WORKING_NOTE_MARK,
    AgentMessage,
    Session,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from core.turn.types import Turn, TurnMetadata

CADENCE_JSON = ('{"should_respond": true, "register": "neutral", '
                '"length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "ok"}')
OWNER_ID = 1
ROOM = 777


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


# ── reading a payload ──────────────────────────────────────────────────────

def _everything(msgs: list[AgentMessage]) -> str:
    """Every byte a provider would see: text, tool names and inputs, results."""
    parts: list[str] = []
    for m in msgs:
        for b in m.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
            elif isinstance(b, ToolUseBlock):
                parts.append(f"{b.name} {json.dumps(b.input)}")
            elif isinstance(b, ToolResultBlock):
                parts.append(str(b.content))
    return "\n".join(parts)


def _tool_blocks(msgs: list[AgentMessage]) -> list[Any]:
    return [b for m in msgs for b in m.content if isinstance(b, (ToolUseBlock, ToolResultBlock))]


def _words(m: AgentMessage) -> str:
    return "".join(b.text for b in m.content if isinstance(b, TextBlock))


def _last_user_words(msgs: list[AgentMessage]) -> str:
    assert msgs and msgs[-1].role == "user", [m.role for m in msgs]
    return _words(msgs[-1])


# ── a room with the real runner ────────────────────────────────────────────

class Room:
    """One shared room on the real runner. The owner's run has one tool,
    ``slow_tool``; a guest's run has none (only the quiet choice, with the
    switch on). Every guest model call's payload is kept, in order."""

    def __init__(self, root, monkeypatch):
        import core.agent.llm_client as llm_mod
        import core.agent.hands_runtime as hr

        self.root = root
        self.owner_steps: list[Any] = []        # what the owner's model says, call by call
        self.owner_calls = 0
        self.owner_payloads: list[list[AgentMessage]] = []
        self.guest_payloads: list[list[AgentMessage]] = []
        self.owner_parked = asyncio.Event()      # the owner's run sits on its next model call
        self.release_owner = asyncio.Event()
        self.tool_gate: asyncio.Event | None = None   # set: slow_tool waits on it
        self.tool_started = asyncio.Event()
        self.tool_result = "tool finished"
        self.owner_task: asyncio.Task | None = None
        self._llm = llm_mod

        async def fake_call(**kw):
            names = {t.get("name") for t in (kw.get("tools") or [])}
            if "slow_tool" in names:
                self.owner_calls += 1
                self.owner_payloads.append(list(kw["messages"]))
                if self.owner_steps:
                    return self.owner_steps.pop(0)
                self.owner_parked.set()
                await self.release_owner.wait()
                return self._text("all done")
            self.guest_payloads.append(list(kw["messages"]))
            return self._text("hi there!")

        monkeypatch.setattr(llm_mod, "call", fake_call)
        monkeypatch.setattr("core.agent.duplex.duplex_enabled", lambda: True)
        monkeypatch.setattr("core.agent.promise_gate.gate_flags", lambda: (False, False))
        monkeypatch.setattr("core.agent.quiet_choice.she_decides_enabled", lambda: True)
        hr.register_client(None)
        self._hr = hr
        task_slots.reset_for_tests()

    # ── what the owner's model says ──
    def _text(self, text: str):
        return self._llm.LLMResponse(text_blocks=[text], tool_uses=[], stop_reason="end_turn",
                                     prompt_tokens=1, completion_tokens=1)

    def owner_says(self, *calls: ToolUseBlock, text: str = "") -> None:
        self.owner_steps.append(self._llm.LLMResponse(
            text_blocks=[text] if text else [], tool_uses=list(calls),
            stop_reason="tool_use" if calls else "end_turn", prompt_tokens=1, completion_tokens=1))

    # ── deps ──
    def registry(self):
        room = self

        class Registry:
            def all_names(self):
                return ["slow_tool"]

            def schemas_for(self, ctx):
                return [{"name": "slow_tool", "description": "x", "input_schema": {"type": "object"}}]

            async def dispatch(self, tu, ctx):
                room.tool_started.set()
                if room.tool_gate is not None:
                    await room.tool_gate.wait()
                return ToolResultBlock(tool_use_id=tu.id, content=room.tool_result, is_error=False)

        return Registry()

    def deps(self, *, owner: bool, audience: str | None = None):
        from core.agent.runner import RunnerDeps

        async def recall(turn):
            return FakeCtx(turn=turn, user_text=str(turn.content), is_owner=owner)

        async def cadence(prompt):
            return CADENCE_JSON

        return RunnerDeps(
            build_turn_context=recall, cadence_llm=cadence, sessions_root=self.root,
            provider="openai_compat", model="m", client=None,
            tool_registry=self.registry() if owner else None, polling_tools=set(),
            is_owner=owner, audience=audience or ("owner" if owner else "stranger"))

    # ── the room file ──
    @property
    def key(self) -> str:
        return f"discord:{ROOM}"

    async def record(self, text: str) -> None:
        """A line written on arrival (the room read's write, or a burst fragment)."""
        from core.agent.runner import _open_session_with_user_message

        await _open_session_with_user_message(self.key, self.root, text)

    async def write(self, *rows: AgentMessage) -> None:
        s = await Session.open_or_create(self.key, root=self.root)
        for row in rows:
            await s.append(row)

    # ── turns ──
    def guest_turn(self, name: str, guest_id: int, words: str, *, prerecorded: bool = True):
        from core.owner_gate import GUEST_ORIGIN, guest_content

        return Turn(surface="discord_text", owner_id=guest_id, content=guest_content(name, words),
                    metadata=TurnMetadata(channel_id=ROOM, origin=GUEST_ORIGIN,
                                          user_side_prerecorded=prerecorded),
                    received_at=datetime(2026, 10, 1, 9, 1))

    def owner_turn(self, words: str, *, prerecorded: bool = False):
        return Turn(surface="discord_text", owner_id=OWNER_ID, content=words,
                    metadata=TurnMetadata(channel_id=ROOM, origin="user",
                                          user_side_prerecorded=prerecorded),
                    received_at=datetime(2026, 10, 1, 9, 0))

    async def run_guest(self, name: str, guest_id: int, words: str, *, audience: str = "stranger"):
        from core.agent.runner import run_turn

        return await run_turn(self.guest_turn(name, guest_id, words),
                              deps=self.deps(owner=False, audience=audience))

    async def run_owner(self, words: str, *, prerecorded: bool = False):
        from core.agent.runner import run_turn

        return await run_turn(self.owner_turn(words, prerecorded=prerecorded),
                              deps=self.deps(owner=True))

    def start_owner(self, words: str) -> None:
        from core.agent.runner import run_turn

        self.owner_task = asyncio.get_running_loop().create_task(
            run_turn(self.owner_turn(words), deps=self.deps(owner=True)))

    async def wait_for(self, event: asyncio.Event) -> None:
        await asyncio.wait_for(event.wait(), timeout=5)

    async def finish_owner(self):
        self.release_owner.set()
        if self.tool_gate is not None:
            self.tool_gate.set()
        return await asyncio.wait_for(self.owner_task, timeout=5)

    def cleanup(self) -> None:
        if self.owner_task is not None and not self.owner_task.done():
            self.owner_task.cancel()
        for key in [self.key] + [f"{self.key}:guest:{gid}" for gid in (42, 43)]:
            steering.clear(key)
            steering.clear_run_state(key)
            steering.clear_paused(key)
        task_slots.reset_for_tests()


@pytest.fixture(autouse=True)
def _speaker_labels_are_pinned_elsewhere(monkeypatch):
    """Step 2 (2026-10-08) renders who said each line as the payload's LAST pass
    in a shared room with she_decides on (core/agent/speakers.py). This module
    pins row ORDER and the byte-identity of the passes before it (FW-1, FW-17),
    so the label pass is held to identity here. The labels are pinned in
    tests/agent/test_speaker_labels.py and tests/bots/test_room_speaker_labels.py,
    and test_the_order_is_right_with_labels_on (below) pins the two together."""
    monkeypatch.setattr("core.agent.runner.label_speakers_for_llm", lambda msgs: msgs)


@pytest.fixture
def room(tmp_path, monkeypatch):
    r = Room(tmp_path, monkeypatch)
    try:
        yield r
    finally:
        r.cleanup()


# ── the review's three cases ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_strangers_recorded_line_never_carries_the_owners_tool_round(room):
    """The review's probe: Sam's line is recorded on arrival; the owner's run
    in the same room then does a tool round and its result is on disk; Sam's
    run opens the room after that. Sam's model call gets none of it."""
    await room.record("[Sam]: hey what's up")
    room.owner_says(ToolUseBlock(id="t1", name="slow_tool", input={"path": "/home/owner/private.txt"}))
    room.tool_result = "OWNER-SECRET: bank pin 4321"
    room.start_owner("read my private file")
    await room.wait_for(room.owner_parked)        # the result is on disk; the run waits on its next call

    result = await room.run_guest("Sam", 42, "hey what's up")

    assert result.response_text == "hi there!"
    payload = room.guest_payloads[0]
    blob = _everything(payload)
    assert "OWNER-SECRET" not in blob and "private.txt" not in blob
    assert "[system observation]" not in blob
    assert _tool_blocks(payload) == []
    assert "read my private file" in blob                     # said out loud in the room
    assert _last_user_words(payload) == "[Sam]: hey what's up"
    assert (await room.finish_owner()).response_text == "all done"


@pytest.mark.asyncio
async def test_a_stranger_mid_way_through_the_owners_quiet_step_never_reads_her_reason(room):
    """The owner asked for a reminder and no reply: she calls stay_quiet beside
    the tool, and while the tool runs the step (her reason in it) is the last
    row on disk. A guest's run that opens the room then never sees the reason."""
    await room.record("[Sam]: anyone around?")
    room.tool_gate = asyncio.Event()
    room.owner_says(
        ToolUseBlock(id="q1", name="stay_quiet", input={"reason": "REASON-SECRET Robin asked for no reply"}),
        ToolUseBlock(id="t1", name="slow_tool", input={"when": "5pm"}))
    room.start_owner("remind me at 5, no need to reply")
    await room.wait_for(room.tool_started)

    await room.run_guest("Sam", 42, "anyone around?")

    payload = room.guest_payloads[0]
    blob = _everything(payload)
    assert "REASON-SECRET" not in blob and "stay_quiet" not in blob and "5pm" not in blob
    assert _tool_blocks(payload) == []
    assert _last_user_words(payload) == "[Sam]: anyone around?"
    owner = await room.finish_owner()
    assert owner.quiet is True


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", ["stranger", "collaborator"])
async def test_a_guest_after_the_owners_quiet_round_never_reads_it(room, audience):
    """The owner's run chose quiet on its own after Sam's line was recorded:
    the quiet call and its result are the last rows. A guest — stranger or
    collaborator — never reads her quiet choice for the owner (ruling T2-4a)."""
    await room.record("[Sam]: anyone around?")
    room.owner_says(ToolUseBlock(id="q1", name="stay_quiet",
                                 input={"reason": "REASON-SECRET Robin said not to answer"}))
    owner = await room.run_owner("don't answer this one")
    assert owner.quiet is True

    await room.run_guest("Sam", 42, "anyone around?", audience=audience)

    payload = room.guest_payloads[0]
    blob = _everything(payload)
    assert "REASON-SECRET" not in blob and "You stayed quiet" not in blob
    assert "[system observation]" not in blob
    assert _tool_blocks(payload) == []
    assert _last_user_words(payload) == "[Sam]: anyone around?"


@pytest.mark.asyncio
async def test_two_guests_each_run_ends_on_its_own_line(room):
    """Sam and Bob both spoke before either run read the room: each run
    answers its own line, never the other's (which would then be answered
    twice)."""
    await room.record("[Sam]: anyone around?")
    await room.record("[Bob]: what's the plan for tonight?")

    await room.run_guest("Sam", 42, "anyone around?")
    await room.run_guest("Bob", 43, "what's the plan for tonight?")

    sam, bob = room.guest_payloads
    assert _last_user_words(sam) == "[Sam]: anyone around?"
    assert "[Bob]: what's the plan for tonight?" in _everything(sam)   # still in the room, earlier
    assert _last_user_words(bob) == "[Bob]: what's the plan for tonight?"


# Guests' display names as the doors pass them to guest_content — brackets
# included (common on Discord, and a guest can set one on purpose; FW-17a).
GUEST_NAMES = ["Sam", "[CEO] Sam", "Sam [he/him]"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", GUEST_NAMES)
async def test_a_guests_arrival_line_is_never_the_last_word_of_the_owners_burst(room, name):
    """The owner's fragments are recorded on arrival, then a guest's line lands
    before the owner's run reads the room: the owner's run still ends on the
    owner's own words, with the guest's line above them."""
    from core.owner_gate import guest_content

    line = guest_content(name, "yo")
    await room.record("hey")
    await room.record("can you look at the logs")
    await room.record(line)
    room.owner_says(text="on it")

    await room.run_owner("hey\ncan you look at the logs", prerecorded=True)

    payload = room.owner_payloads[0]
    users = [_words(m) for m in payload if m.role == "user"]
    assert users[-3:] == [line, "hey", "can you look at the logs"]


@pytest.mark.asyncio
async def test_the_order_is_right_with_labels_on(room, monkeypatch):
    """The same burst as above with the speaker labels rendered: the guest's line
    is still above the owner's own rows, and only the owner's rows carry the
    owner's mark (rows recorded without a speaker read as they always did)."""
    from core.agent import speakers as sp
    from core.owner_gate import guest_content

    monkeypatch.setattr("core.agent.runner.label_speakers_for_llm", sp.label_speakers_for_llm)
    line = guest_content("Sam", "yo")
    await room.record("hey")
    await room.record("can you look at the logs")
    await room.record(line)
    room.owner_says(text="on it")

    await room.run_owner("hey\ncan you look at the logs", prerecorded=True)

    users = [_words(m) for m in room.owner_payloads[0] if m.role == "user"]
    mark = sp.owner_label()
    assert users[-3:] == [line, mark + "hey", mark + "can you look at the logs"]


@pytest.mark.asyncio
async def test_a_guest_whose_line_cannot_be_found_still_ends_on_it(room):
    """Fail closed: when the run cannot find the guest's recorded row (the
    file no longer holds it), the payload still ends on the guest's own words,
    never on someone else's."""
    await room.record("[Bob]: what's the plan for tonight?")

    await room.run_guest("Sam", 42, "anyone around?")

    assert _last_user_words(room.guest_payloads[0]) == "[Sam]: anyone around?"


# ── nothing changes where nothing is interleaved ──────────────────────────

def _old_payload(messages: list[AgentMessage], keep_from: int, audience: str) -> list[AgentMessage]:
    """The runner's payload passes as they were before this fix, for the
    first model call of a run that loaded ``messages``."""
    from core.agent.session import (
        diet_for_llm, ensure_user_tail, fold_interims_for_thinking, mark_undelivered_for_llm,
        mark_working_notes_for_llm, normalize_for_llm, window_for_llm,
    )

    from core.agent.runner import _resolve_history_max_messages, _resolve_history_step

    out = normalize_for_llm(window_for_llm(diet_for_llm(
        mark_undelivered_for_llm(mark_working_notes_for_llm(messages)),
        keep_from=keep_from, audience=audience),
        _resolve_history_max_messages(surface="discord_text"), step=_resolve_history_step()))
    return ensure_user_tail(fold_interims_for_thinking(out))


def _dump(msgs: list[AgentMessage]) -> list[str]:
    from core.agent.session import _msg_to_jsonl

    return [_msg_to_jsonl(m) for m in msgs]


async def _seed_an_owner_round(room: Room) -> None:
    await room.write(
        AgentMessage(role="user", content=[TextBlock(text="can you check the disk?")]),
        AgentMessage(role="assistant", content=[
            TextBlock(text="checking the mounts"),
            ToolUseBlock(id="t0", name="local_shell", input={"command": "df -h"})]),
        AgentMessage(role="tool", content=[ToolResultBlock(tool_use_id="t0", content="91% used")]),
        AgentMessage(role="assistant", content=[TextBlock(text="The disk is 91% full.")]),
    )


@pytest.mark.asyncio
async def test_the_owners_burst_with_nothing_interleaved_is_byte_identical(room):
    await _seed_an_owner_round(room)
    await room.record("hey")
    await room.record("what else is on there?")
    loaded = (await Session.open_or_create(room.key, root=room.root)).messages
    room.owner_says(text="mostly photos")

    await room.run_owner("hey\nwhat else is on there?", prerecorded=True)

    assert _dump(room.owner_payloads[0]) == _dump(_old_payload(loaded, len(loaded) - 1, "owner"))


@pytest.mark.asyncio
async def test_a_strangers_line_with_nothing_after_it_is_byte_identical(room):
    await _seed_an_owner_round(room)
    await room.record("[Sam]: nice, what else is on there?")
    loaded = (await Session.open_or_create(room.key, root=room.root)).messages

    await room.run_guest("Sam", 42, "nice, what else is on there?")

    assert _dump(room.guest_payloads[0]) == _dump(_old_payload(loaded, len(loaded) - 1, "stranger"))


# ── FW-17: the owner's reorder is for another speaker's line only ─────────
# Steering's recovery (bots/common/steering.py) writes his words and then her
# holding reply (_transcribe_bundle), then re-dispatches his words as a turn
# already recorded (_fallback_turn keeps user_side_prerecorded). His own rows
# are then not the last rows loaded, yet no one else spoke: the owner's
# payload must stay exactly what it was before this wave (law 4).

def _as_sent(msgs: list[AgentMessage]) -> str:
    """The messages the way the 2a golden compares them: role, blocks,
    reasoning and delivery — what the provider is sent. (A row
    ensure_user_tail re-presents gets a fresh timestamp, which never leaves
    the process.)"""
    from tests.agent.she_decides_golden import _message

    return json.dumps([_message(m) for m in msgs], sort_keys=True, default=str)


def _recovered_bundle(room: Room, words: str, reply: str):
    return SimpleNamespace(turn=room.owner_turn(words, prerecorded=True), reply_text=reply,
                           replied=True, drained=False)


async def _recover(room: Room, words: str, reply: str):
    from bots.common import steering as door_steering

    bundle = _recovered_bundle(room, words, reply)
    assert await door_steering._transcribe_bundle(room.key, bundle, room.root)
    turn = door_steering._fallback_turn(bundle)
    assert turn.metadata.user_side_prerecorded is True
    return turn


@pytest.mark.asyncio
@pytest.mark.parametrize("switch_on", [False, True])
async def test_the_owners_steering_recovery_payload_is_byte_identical(room, monkeypatch, switch_on):
    """Final fix wave (ruling FW-17): with the switch off and on, no guest —
    his words, her holding reply, and his words again at the tail
    (ensure_user_tail), exactly as before the wave."""
    from core.agent.runner import run_turn

    monkeypatch.setattr("core.agent.quiet_choice.she_decides_enabled", lambda: switch_on)
    await _seed_an_owner_round(room)
    turn = await _recover(room, "actually check /var too", "on it, one sec")
    loaded = (await Session.open_or_create(room.key, root=room.root)).messages
    room.owner_says(text="/var is fine")

    await run_turn(turn, deps=room.deps(owner=True))

    payload = room.owner_payloads[0]
    assert _as_sent(payload) == _as_sent(_old_payload(loaded, len(loaded) - 1, "owner"))
    assert [_words(m) for m in payload[-3:]] == [
        "actually check /var too", "on it, one sec", "actually check /var too"]


@pytest.mark.asyncio
async def test_another_of_the_owners_own_lines_is_not_another_speaker(room):
    """Two of his recovered exchanges on the record before the first one's
    re-dispatch reads the room: still only the owner spoke, so the payload is
    the pre-wave one, byte for byte."""
    from core.agent.runner import run_turn

    first = await _recover(room, "check the disk", "checking")
    await _recover(room, "and the backups", "those too")
    loaded = (await Session.open_or_create(room.key, root=room.root)).messages
    room.owner_says(text="disk is fine")

    await run_turn(first, deps=room.deps(owner=True))

    assert _as_sent(room.owner_payloads[0]) == _as_sent(
        _old_payload(loaded, len(loaded) - 1, "owner"))


@pytest.mark.parametrize("name", GUEST_NAMES + ["", "]:)", "[[CEO]] Sam [he/him]"])
def test_another_speaker_is_known_by_the_guest_tag(name):
    """Every line guest_content writes is a guest's — any display name,
    brackets included (FW-17a), and an unnamed guest."""
    from core.owner_gate import guest_content, is_guest_line

    assert is_guest_line(guest_content(name, "yo"))
    assert is_guest_line(guest_content(name, "line one\nline two"))


def test_her_own_markers_and_the_owners_words_are_not_a_guests_line():
    from core.agent.session import RECEIPT_MARK, WORKING_NOTE_MARK
    from core.owner_gate import is_guest_line

    for owners in ("actually check /var too", "[image attached]", "[file attached]",
                   "[image attached — vision analysis: a cat on a desk]",
                   "[file attached — notes.txt]\nline one",
                   "[system observation] OWNER-SECRET: bank pin 4321",
                   WORKING_NOTE_MARK + "pulling the statement: 4k",
                   f'{RECEIPT_MARK} local_shell({{"command": "df -h"}}) → 91% used',
                   ""):
        assert not is_guest_line(owners), owners


@pytest.mark.asyncio
@pytest.mark.parametrize("name", GUEST_NAMES)
async def test_a_guests_line_after_his_recovered_words_never_ends_his_run(room, name):
    """A guest's arrival line after his recovered exchange is another speaker:
    his run still ends on his own words, with the guest's line above them."""
    from core.agent.runner import run_turn
    from core.owner_gate import guest_content

    line = guest_content(name, "yo")
    turn = await _recover(room, "actually check /var too", "on it, one sec")
    await room.record(line)
    room.owner_says(text="/var is fine")

    await run_turn(turn, deps=room.deps(owner=True))

    payload = room.owner_payloads[0]
    users = [_words(m) for m in payload if m.role == "user"]
    assert users[-2:] == [line, "actually check /var too"]


# ── the invariant, over many rooms (the review's Recommendation 2) ────────
# Whatever an owner run wrote into the room — tool rounds (finished or still
# in flight), quiet rounds, working notes, things she chimed in — before and
# AFTER a stranger's line recorded on arrival, the stranger's payload holds no
# tool call, no tool result, no quiet reason and no working note, and it ends
# on the stranger's own line.

SECRET = "SECRET"
STRANGER_LINE = "[Sam]: is anyone here?"
CASES = 2000


def _random_owner_rows(rng: random.Random, n: int, tag: str) -> list[AgentMessage]:
    rows: list[AgentMessage] = []
    for k in range(n):
        kind = rng.choice(("words", "reply", "tool", "tool", "quiet", "chime", "guest", "tool_in_flight"))
        cid = f"{tag}{k}"
        if kind == "words":
            rows.append(AgentMessage(role="user", content=[TextBlock(text=f"robin says {cid}")]))
        elif kind == "reply":
            rows.append(AgentMessage(role="assistant", content=[TextBlock(text=f"she replies {cid}")]))
        elif kind == "guest":
            rows.append(AgentMessage(role="user", content=[TextBlock(text=f"[Bob]: bob says {cid}")]))
        elif kind in ("tool", "tool_in_flight"):
            calls = [ToolUseBlock(id=f"t{cid}{j}", name=rng.choice(("local_shell", "read_file", "send_message")),
                                  input={"command": f"{SECRET}-in-{cid}-{j}"})
                     for j in range(rng.randint(1, 3))]
            blocks: list[Any] = []
            if rng.random() < 0.6:
                blocks.append(TextBlock(text=f"{SECRET}-note-{cid}"))      # a working note (duplex)
            rows.append(AgentMessage(role="assistant", content=blocks + calls))
            if kind == "tool":
                rows.append(AgentMessage(role="tool", content=[
                    ToolResultBlock(tool_use_id=c.id, content=f"{SECRET}-out-{cid}", is_error=rng.random() < 0.2)
                    for c in calls]))
        elif kind == "quiet":
            calls = [ToolUseBlock(id=f"q{cid}", name="stay_quiet", input={"reason": f"{SECRET}-reason-{cid}"})]
            if rng.random() < 0.5:
                calls.append(ToolUseBlock(id=f"r{cid}", name="create_reminder",
                                          input={"text": f"{SECRET}-in-{cid}"}))
            rows.append(AgentMessage(role="assistant", content=calls))
            if rng.random() < 0.7:
                rows.append(AgentMessage(role="tool", content=[
                    ToolResultBlock(tool_use_id=c.id, content=(
                        "You stayed quiet. Nothing was sent." if c.name == "stay_quiet"
                        else f"{SECRET}-out-{cid}")) for c in calls]))
        else:   # she chimed in mid-task, as text, then worked on
            calls = [ToolUseBlock(id=f"c{cid}", name="chime_in", input={"text": f"said aloud {cid}"}),
                     ToolUseBlock(id=f"s{cid}", name="local_shell", input={"command": f"{SECRET}-in-{cid}"})]
            rows.append(AgentMessage(role="assistant", content=calls))
            rows.append(AgentMessage(role="tool", content=[
                ToolResultBlock(tool_use_id=f"c{cid}", content=f"Said (text): said aloud {cid}"),
                ToolResultBlock(tool_use_id=f"s{cid}", content=f"{SECRET}-out-{cid}")]))
    return rows


def _write_room(path, rows: list[AgentMessage]) -> None:
    from core.agent.session import _msg_to_jsonl

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(_msg_to_jsonl(m) + "\n" for m in rows), encoding="utf-8")


@pytest.mark.asyncio
async def test_no_owner_work_ever_reaches_a_strangers_payload_over_many_rooms(room):
    from core.agent.runner import run_turn

    rng = random.Random(20261001)
    turn = room.guest_turn("Sam", 42, "is anyone here?")
    deps = room.deps(owner=False)
    for case in range(CASES):
        before = _random_owner_rows(rng, rng.randint(0, 8), f"b{case}x")
        after = _random_owner_rows(rng, rng.randint(0, 5), f"a{case}x")
        _write_room(room.root / f"discord_{ROOM}.jsonl", before + [
            AgentMessage(role="user", content=[TextBlock(text=STRANGER_LINE)])] + after)
        room.guest_payloads.clear()

        await run_turn(turn, deps=deps)

        payload = room.guest_payloads[0]
        blob = _everything(payload)
        assert SECRET not in blob, (case, blob)
        assert _tool_blocks(payload) == [], case
        for mark in (WORKING_NOTE_MARK, RECEIPT_MARK, "You stayed quiet", "[system observation]"):
            assert mark not in blob, (case, mark)
        assert _last_user_words(payload) == STRANGER_LINE, case

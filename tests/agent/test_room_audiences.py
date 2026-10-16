"""Shared rooms, two guest levels (Batch 8, task 1 — B8-1; rulings R1, R2, R4, R6).

A shared room (a Discord server channel, a Telegram group) keeps one
conversation of what was said in it, by anyone. Before this task a guest's
turn ran on that room file with everything in it: the receipts of the
owner's tool work (raw output, private paths) and the working notes she
wrote while she worked. The payload is now rendered by AUDIENCE:

- a stranger sees what was said out loud: every user row and every reply,
  and no receipt, no working note, no tool output;
- a collaborator (someone the owner named) also sees the work she did in
  this room, as receipts, the way the owner does;
- the owner sees the room exactly as before (byte-identical).

Her private sections about the owner (memory, moods, relationship, the
house, hopes and worries, the story of us, her private pages) stay the
owner's for every guest, stranger and collaborator alike.

Receipts are never stored (the diet builds them from a past round's
tool_use and its result), so the filter keys on the blocks, never on
RECEIPT_MARK (ruling R2). Stand-in names only: Robin is the owner, Sam a
guest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent.session import (
    RECEIPT_MARK,
    WORKING_NOTE_MARK,
    AgentMessage,
    Session,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    diet_for_llm,
    ensure_user_tail,
    fold_interims_for_thinking,
    mark_working_notes_for_llm,
    normalize_for_llm,
    window_for_llm,
)
from core.turn.types import Turn, TurnContext, TurnMetadata


# ── the diet, by audience (R2) ────────────────────────────────────────────

def _room() -> list[AgentMessage]:
    """What the room file holds after an owner turn with a tool call, and
    then a guest's message (this run)."""
    return [
        AgentMessage(role="user", content=[TextBlock(text="can you check the disk?")]),
        AgentMessage(role="assistant", content=[
            TextBlock(text="checking the disk now"),
            ToolUseBlock(id="t1", name="local_shell", input={"command": "df -h"}),
        ]),
        AgentMessage(role="tool", content=[ToolResultBlock(
            tool_use_id="t1", content="/dev/sda1 91% used /home/owner/private-projects")]),
        AgentMessage(role="assistant", content=[TextBlock(text="The main disk is 91% full.")]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: nice, what else is on there?")]),
    ]


KEEP = 4   # the guest's own message is this run


def _text(msgs: list[AgentMessage]) -> str:
    """Every byte a provider would see: text, tool inputs, tool results."""
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


def test_a_stranger_sees_only_what_was_said_out_loud():
    """Duplex on (the live yaml): the working note is stamped before the
    diet, and the stranger keeps neither it nor the tool round."""
    out = diet_for_llm(mark_working_notes_for_llm(_room()), keep_from=KEEP, audience="stranger")
    text = _text(out)
    assert "can you check the disk?" in text
    assert "91% full" in text and "what else is on there" in text
    assert "private-projects" not in text and "df -h" not in text
    assert "tool receipt" not in text and RECEIPT_MARK not in text
    assert "checking the disk now" not in text and WORKING_NOTE_MARK not in text
    assert not any(isinstance(b, (ToolUseBlock, ToolResultBlock)) for m in out for b in m.content)


def test_with_duplex_off_a_stranger_keeps_the_words_she_streamed_but_not_the_tool():
    """Duplex off: text in a tool-calling row was streamed live to the room,
    so it was said out loud and stays; only the tool parts go, and no
    receipt is built."""
    out = diet_for_llm(_room(), keep_from=KEEP, audience="stranger")
    text = _text(out)
    assert "checking the disk now" in text
    assert "private-projects" not in text and "df -h" not in text
    assert RECEIPT_MARK not in text


def test_a_collaborator_also_sees_the_work_done_in_this_room():
    out = diet_for_llm(mark_working_notes_for_llm(_room()), keep_from=KEEP, audience="collaborator")
    text = _text(out)
    assert "tool receipt" in text and RECEIPT_MARK in text
    assert "local_shell(" in text
    assert "private-projects" in text        # by design: the work done in front of them


def test_the_owner_sees_the_room_as_today():
    room = mark_working_notes_for_llm(_room())
    today = diet_for_llm(room, keep_from=KEEP)
    assert diet_for_llm(room, keep_from=KEEP, audience="owner") == today
    # A collaborator's past turns are the owner's (R2): receipts and notes.
    assert diet_for_llm(room, keep_from=KEEP, audience="collaborator") == today


def test_this_runs_own_rows_stay_verbatim_for_every_audience():
    """The region from keep_from on is the run's own and is replayed as it
    is, whoever is listening (the model must see its own in-flight round)."""
    room = _room()
    for audience in ("owner", "collaborator", "stranger"):
        out = diet_for_llm(room, keep_from=1, audience=audience)
        assert len(out) == len(room), audience
        for got, want in zip(out[1:], room[1:]):
            assert got is want, audience


def test_an_unknown_audience_is_treated_as_a_stranger():
    room = mark_working_notes_for_llm(_room())
    assert diet_for_llm(room, keep_from=KEEP, audience="who knows") == diet_for_llm(
        room, keep_from=KEEP, audience="stranger")


def test_a_strangers_payload_never_shows_an_owner_call_still_in_flight():
    """The owner's run is mid-tool: its tool_use is on disk, its result not
    yet. The owner and a collaborator get a "(no result recorded)" receipt;
    a stranger gets nothing of it."""
    room = [
        AgentMessage(role="user", content=[TextBlock(text="back up the photos")]),
        AgentMessage(role="assistant", content=[
            ToolUseBlock(id="t9", name="local_shell", input={"command": "rsync /home/owner/photos"})]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: hey!")]),
    ]
    text = _text(diet_for_llm(room, keep_from=2, audience="stranger"))
    assert "rsync" not in text and "no result recorded" not in text
    assert "back up the photos" in text and "[Sam]: hey!" in text
    assert "no result recorded" in _text(diet_for_llm(room, keep_from=2, audience="collaborator"))


# ── R2a: what she said aloud through a tool was said out loud ─────────────
# chime_in and ask_him post their input as-is to the room (duplex_hands.py →
# duplex.speak_mid_task → the arbiter's release leg). A stranger keeps a
# chime that went out as text here as a plain line (final fix wave, FW-4: not
# one spoken in the voice session, and not ask_him, whose result never says
# where it went); every other tool input, every result and every working note
# stays out. Task 2b's room first pass reads the room through this rendering,
# so it must see what she already said.

def _chime_then_shell_room() -> list[AgentMessage]:
    return [
        AgentMessage(role="user", content=[TextBlock(text="can you check the disk?")]),
        AgentMessage(role="assistant", content=[
            TextBlock(text="let me look at the mounts first"),
            ToolUseBlock(id="c1", name="chime_in", input={"text": "on it, checking the disk"}),
        ]),
        AgentMessage(role="tool", content=[ToolResultBlock(
            tool_use_id="c1", content="Said (text): on it, checking the disk")]),
        AgentMessage(role="assistant", content=[
            ToolUseBlock(id="t1", name="local_shell", input={"command": "df -h"})]),
        AgentMessage(role="tool", content=[ToolResultBlock(
            tool_use_id="t1", content="/dev/sda1 91% used /home/owner/private-projects")]),
        AgentMessage(role="assistant", content=[TextBlock(text="The main disk is 91% full.")]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: nice, what else is on there?")]),
    ]


def _assert_said_aloud_only(text: str) -> None:
    assert "on it, checking the disk" in text and "91% full" in text
    assert "private-projects" not in text and "df -h" not in text
    assert "chime_in" not in text and "local_shell" not in text and "Said (" not in text
    assert "let me look at the mounts first" not in text      # the working note
    assert RECEIPT_MARK not in text and WORKING_NOTE_MARK not in text


def test_a_stranger_keeps_what_she_chimed_in_aloud_and_none_of_the_tool_output():
    room = _chime_then_shell_room()
    out = diet_for_llm(mark_working_notes_for_llm(room), keep_from=len(room) - 1, audience="stranger")
    _assert_said_aloud_only(_text(out))
    # A plain assistant line, in its place in the conversation.
    spoken = [m for m in out if m.role == "assistant" and _text([m]) == "on it, checking the disk"]
    assert len(spoken) == 1 and spoken[0].content == [TextBlock(text="on it, checking the disk")]
    assert not any(isinstance(b, (ToolUseBlock, ToolResultBlock)) for m in out for b in m.content)


def test_a_chime_in_the_same_round_as_the_shell_is_kept_too():
    room = [
        AgentMessage(role="user", content=[TextBlock(text="can you check the disk?")]),
        AgentMessage(role="assistant", content=[
            ToolUseBlock(id="c1", name="chime_in", input={"text": "on it,   checking the disk"}),
            ToolUseBlock(id="t1", name="local_shell", input={"command": "df -h"}),
        ]),
        AgentMessage(role="tool", content=[
            ToolResultBlock(tool_use_id="c1", content="Said (text): on it, checking the disk"),
            ToolResultBlock(tool_use_id="t1", content="/dev/sda1 91% used /home/owner/private-projects"),
        ]),
        AgentMessage(role="assistant", content=[TextBlock(text="The main disk is 91% full.")]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: hi")]),
    ]
    _assert_said_aloud_only(_text(diet_for_llm(room, keep_from=4, audience="stranger")))


def test_a_chime_spoken_in_the_voice_channel_is_not_kept_for_a_text_room_stranger():
    """Final fix wave (ruling FW-4, amends R2b): a chime spoken into the
    owner's voice session was never said in this text room, so a stranger
    here does not get it. Only a chime that went out as text counts."""
    room = [
        AgentMessage(role="user", content=[TextBlock(text="can you check the disk?")]),
        AgentMessage(role="assistant", content=[
            ToolUseBlock(id="c1", name="chime_in", input={"text": "on it, checking the disk"})]),
        AgentMessage(role="tool", content=[ToolResultBlock(
            tool_use_id="c1", content="Said (voice): on it, checking the disk")]),
        AgentMessage(role="assistant", content=[TextBlock(text="The main disk is 91% full.")]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: hi")]),
    ]
    text = _text(diet_for_llm(room, keep_from=4, audience="stranger"))
    assert "checking the disk" not in text
    assert "91% full" in text and "[Sam]: hi" in text


def test_a_question_she_asked_mid_task_is_left_out_for_a_stranger():
    """Final fix wave (ruling FW-4): ask_him's result never names where the
    question went (the voice session or this room), so a stranger can't be
    shown it as said here — fail closed."""
    room = [
        AgentMessage(role="user", content=[TextBlock(text="log into the router")]),
        AgentMessage(role="assistant", content=[
            ToolUseBlock(id="a1", name="ask_him", input={"question": "there's a captcha — can you clear it?"})]),
        AgentMessage(role="tool", content=[ToolResultBlock(
            tool_use_id="a1", content="Asked. Keep going on what doesn't depend on their answer")]),
        AgentMessage(role="assistant", content=[TextBlock(text="Waiting on the captcha.")]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: hi")]),
    ]
    text = _text(diet_for_llm(room, keep_from=4, audience="stranger"))
    assert "captcha — can you clear it?" not in text
    assert "Asked." not in text and "ask_him" not in text
    assert "Waiting on the captcha." in text and "[Sam]: hi" in text


@pytest.mark.parametrize("result,is_error", [
    ("That's longer than a chime — keep it under 300 characters, or say it when the work is done.", False),
    ("Nothing to say — that's fine; keep working.", False),
    ("chime_in failed: RuntimeError: send failed", True),
    ("[interrupted: a new message arrived before this command finished]", True),
    (None, False),                                   # the round is still in flight
])
def test_a_chime_that_never_went_out_is_not_shown_to_a_stranger(result, is_error):
    room = [
        AgentMessage(role="user", content=[TextBlock(text="check the disk")]),
        AgentMessage(role="assistant", content=[
            ToolUseBlock(id="c1", name="chime_in", input={"text": "on it, checking the disk"})]),
    ]
    if result is not None:
        room.append(AgentMessage(role="tool", content=[
            ToolResultBlock(tool_use_id="c1", content=result, is_error=is_error)]))
    room.append(AgentMessage(role="user", content=[TextBlock(text="[Sam]: hi")]))
    text = _text(diet_for_llm(room, keep_from=len(room) - 1, audience="stranger"))
    assert "checking the disk" not in text
    assert "check the disk" in text and "[Sam]: hi" in text


def test_r2a_leaves_the_owner_and_a_collaborator_exactly_as_before():
    """Owner and collaborator still read the chime as a receipt (byte-identical
    to the diet before R2a): her text, then one receipt per call."""
    room = mark_working_notes_for_llm(_chime_then_shell_room())
    keep = len(room) - 1
    owner = diet_for_llm(room, keep_from=keep, audience="owner")
    assert diet_for_llm(room, keep_from=keep) == owner
    assert diet_for_llm(room, keep_from=keep, audience="collaborator") == owner
    chime_row = owner[1].content
    assert chime_row[0] == TextBlock(text=WORKING_NOTE_MARK + "let me look at the mounts first")
    assert chime_row[1].text == (
        f'{RECEIPT_MARK} chime_in({{"text": "on it, checking the disk"}}) → '
        "Said (text): on it, checking the disk")
    assert owner[2].content[0].text.startswith(f"{RECEIPT_MARK} local_shell(")


@pytest.mark.asyncio
async def test_the_spoken_tool_marks_match_the_real_hands(monkeypatch):
    """The drift canary for SPOKEN_TOOL_LINES (final fix wave, ruling FW-4):
    a hand is in the table only when its result tells a TEXT send apart —
    its mark matches what it returns once the words went out as text, and
    never what it returns for the voice session. A hand whose result is the
    same either way (ask_him today) can't be told, so it stays out (fail
    closed); if it ever starts naming its medium, this test says so."""
    import ai.present_moment as pm
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import build_duplex_tools
    from core.agent.session import SPOKEN_TOOL_LINES
    from core.agent.tools import ToolContext

    hands = {t.name: t for t in build_duplex_tools()}
    assert set(SPOKEN_TOOL_LINES) == {"chime_in"}
    monkeypatch.setattr(pm, "notice", lambda kind, **f: None)
    ctx = ToolContext(turn_owner_id=1, is_owner=True, surface="discord_text", channel_id=555)
    fields = {"chime_in": "text", "ask_him": "question"}
    outs: dict[tuple[str, str], str] = {}
    for medium in ("text", "voice"):
        async def fake_speak(**kw):
            return {"medium": medium}

        monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
        for name, hand in hands.items():
            field_name = fields[name]
            assert field_name in hand.input_schema["properties"], name
            outs[(name, medium)] = str(await hand.execute({field_name: "on it, checking the disk"}, ctx))
    for name, (field_name, mark) in SPOKEN_TOOL_LINES.items():
        assert field_name == fields[name]
        assert outs[(name, "text")].startswith(mark), outs[(name, "text")]
        assert not outs[(name, "voice")].startswith(mark), outs[(name, "voice")]
    for name in set(hands) - set(SPOKEN_TOOL_LINES):
        assert outs[(name, "text")] == outs[(name, "voice")], name   # can't be told apart
    # And the refusals do not start with the mark.
    refused = await hands["chime_in"].execute({"text": "x" * (dx.CHIME_MAX_CHARS + 1)}, ctx)
    assert not str(refused).startswith(SPOKEN_TOOL_LINES["chime_in"][1])


# ── R4: a guest row written into the middle of the owner's tool round ─────

def _payload(messages: list[AgentMessage], keep_from: int) -> list[AgentMessage]:
    """The runner's payload passes, in its order."""
    out = normalize_for_llm(window_for_llm(diet_for_llm(
        mark_working_notes_for_llm(messages), keep_from=keep_from), 120, step=50))
    out = fold_interims_for_thinking(out)
    return ensure_user_tail(out)


def _assert_valid_grammar(msgs: list[AgentMessage]) -> None:
    """Every tool call is answered by the very next row, which holds a result
    for each of its ids, and no result floats free."""
    for i, m in enumerate(msgs):
        ids = [b.id for b in m.content if isinstance(b, ToolUseBlock)]
        if ids:
            nxt = msgs[i + 1]
            assert nxt.role == "tool", (i, nxt.role)
            assert [b.tool_use_id for b in nxt.content if isinstance(b, ToolResultBlock)] == ids
        if m.role == "tool":
            prev = msgs[i - 1]
            claimed = {b.id for b in prev.content if isinstance(b, ToolUseBlock)}
            assert claimed and all(b.tool_use_id in claimed for b in m.content
                                   if isinstance(b, ToolResultBlock))


@pytest.mark.parametrize("keep_from", [0, 1, 5])
def test_an_owner_round_with_a_guest_row_in_the_middle_still_makes_a_valid_owner_payload(keep_from):
    """A guest run is a second writer on the room file (ruling R4): its row
    can land between the owner's tool_use and the result. The read side
    repairs the order, wherever this run's region starts."""
    on_disk = [
        AgentMessage(role="user", content=[TextBlock(text="check the disk")]),
        AgentMessage(role="assistant", content=[
            TextBlock(text="on it"),
            ToolUseBlock(id="t1", name="local_shell", input={"command": "df -h"})]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: what are you two up to?")]),
        AgentMessage(role="tool", content=[ToolResultBlock(tool_use_id="t1", content="91% used")]),
        AgentMessage(role="assistant", content=[TextBlock(text="91% full.")]),
        AgentMessage(role="user", content=[TextBlock(text="thanks — and the other disk?")]),
    ]
    out = _payload(on_disk, keep_from)
    _assert_valid_grammar(out)
    text = _text(out)
    assert "[Sam]: what are you two up to?" in text      # the guest's words survive
    assert "91% full." in text and out[-1].role == "user"


# ── the runner: the audience reaches the diet (R2, R6) ────────────────────

CADENCE_JSON = ('{"should_respond": true, "register": "neutral", '
                '"length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "ok"}')
ROOM_ID = 555
ROOM_KEY = f"discord:{ROOM_ID}"


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


def _turn(content: str, *, owner_id: int, origin: str) -> Turn:
    return Turn(surface="discord_text", owner_id=owner_id, content=content,
                metadata=TurnMetadata(channel_id=ROOM_ID, origin=origin),
                received_at=datetime(2026, 10, 1, 9, 0, 0))


def _deps(root, *, is_owner: bool, audience: str | None, recall=None):
    from core.agent.runner import RunnerDeps

    async def fake_recall(turn):
        return FakeCtx(turn=turn, user_text=str(turn.content), is_owner=is_owner)

    async def fake_cadence_llm(prompt):
        return CADENCE_JSON

    kw: dict[str, Any] = {}
    if audience is not None:
        kw["audience"] = audience
    return RunnerDeps(
        build_turn_context=recall or fake_recall, cadence_llm=fake_cadence_llm,
        sessions_root=root, provider="openai_compat", model="m", client=None,
        tool_registry=None, polling_tools=set(), is_owner=is_owner, **kw,
    )


async def _seed_the_owners_round(root) -> None:
    s = await Session.open_or_create(ROOM_KEY, root=root)
    for m in mark_working_notes_for_llm(_room()[:4]):
        await s.append(m)


def _capture_llm(monkeypatch, reply: str = "hey Sam!") -> list[dict]:
    import core.agent.llm_client as llm_mod

    seen: list[dict] = []

    async def fake_call(**kw):
        seen.append(kw)
        return llm_mod.LLMResponse(text_blocks=[reply], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)
    return seen


@pytest.mark.asyncio
async def test_a_strangers_run_reads_the_room_without_the_owners_work(tmp_path, monkeypatch):
    from core.agent.runner import run_turn

    await _seed_the_owners_round(tmp_path)
    seen = _capture_llm(monkeypatch)
    turn = _turn("[Sam]: nice, what else is on there?", owner_id=42, origin="guest")
    result = await run_turn(turn, deps=_deps(tmp_path, is_owner=False, audience="stranger"))
    assert result.response_text == "hey Sam!"
    text = _text(seen[0]["messages"])
    assert "91% full" in text and "what else is on there" in text
    assert RECEIPT_MARK not in text and "private-projects" not in text and "df -h" not in text


@pytest.mark.asyncio
async def test_a_collaborators_run_reads_the_room_with_its_receipts(tmp_path, monkeypatch):
    from core.agent.runner import run_turn

    await _seed_the_owners_round(tmp_path)
    seen = _capture_llm(monkeypatch)
    turn = _turn("[Sam]: nice, what else is on there?", owner_id=42, origin="guest")
    await run_turn(turn, deps=_deps(tmp_path, is_owner=False, audience="collaborator"))
    text = _text(seen[0]["messages"])
    assert RECEIPT_MARK in text and "local_shell(" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", [None, "owner", "nonsense"])
async def test_a_guest_turn_with_no_audience_or_the_owners_fails_closed_to_stranger(
        tmp_path, monkeypatch, audience):
    """Legacy wiring (no audience on the deps), a mistaken "owner", or a
    typo on a GUEST turn all read the room as a stranger would."""
    from core.agent.runner import run_turn

    await _seed_the_owners_round(tmp_path)
    seen = _capture_llm(monkeypatch)
    turn = _turn("[Sam]: hi", owner_id=42, origin="guest")
    await run_turn(turn, deps=_deps(tmp_path, is_owner=False, audience=audience))
    text = _text(seen[0]["messages"])
    assert RECEIPT_MARK not in text and "private-projects" not in text


@pytest.mark.asyncio
async def test_the_owners_payload_is_unchanged_by_the_audience_plumbing(tmp_path, monkeypatch):
    from core.agent.runner import run_turn

    payloads = []
    for audience in (None, "owner"):
        root = tmp_path / str(audience)
        await _seed_the_owners_round(root)
        seen = _capture_llm(monkeypatch, reply="it's mostly photos")
        await run_turn(_turn("what else is on there?", owner_id=1, origin="user"),
                       deps=_deps(root, is_owner=True, audience=audience))
        payloads.append(seen[0]["messages"])
    assert _text(payloads[0]) == _text(payloads[1])
    assert RECEIPT_MARK in _text(payloads[0]) and "private-projects" in _text(payloads[0])


# ── dispatch names the audience beside is_owner (R6) ──────────────────────

async def _dispatch_and_capture(monkeypatch, turn) -> Any:
    captured: dict[str, Any] = {}

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        captured["deps"] = deps
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="hi", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt):
        return ""

    async def stub_build_callable():
        return stub_cadence_llm

    async def stub_client():
        return object()

    async def noop_user(turn, content):
        pass

    async def noop_asst(*, turn, response_text):
        pass

    async def fake_refs():
        return {"app_user_id": 1, "palli_user_id": 1, "discord_user_id": 777}

    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    await dispatch_turn(turn)
    return captured["deps"]


# Moved deliberately (R14, T8; plan §2b.6): a guest's belt is none with
# features.guest_web_search off (as shipped) and web_search only with it on —
# a stranger's and a collaborator's alike (a collaborator is a guest). run_turn
# is a fake here, so the look's switch (the runner's) never reaches these.
def _guest_belt(deps, guest_search: bool) -> bool:
    if guest_search:
        return deps.tool_registry.all_names() == ["web_search"]
    return deps.tool_registry is None


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_search", [False, True])
async def test_dispatch_makes_an_unnamed_guest_a_stranger(monkeypatch, tmp_path, guest_search):
    if guest_search:
        from tests.agent.she_decides_harness import guest_search_on

        guest_search_on(monkeypatch)
    monkeypatch.setenv("PALLI_COLLABORATORS_PATH", str(tmp_path / "collaborators.json"))
    deps = await _dispatch_and_capture(monkeypatch, _turn("[Sam]: hi", owner_id=42, origin="guest"))
    assert deps.audience == "stranger"
    assert deps.is_owner is False and _guest_belt(deps, guest_search)


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_search", [False, True])
async def test_dispatch_makes_a_named_guest_a_collaborator(monkeypatch, tmp_path, guest_search):
    from core import collaborators

    if guest_search:
        from tests.agent.she_decides_harness import guest_search_on

        guest_search_on(monkeypatch)
    monkeypatch.setenv("PALLI_COLLABORATORS_PATH", str(tmp_path / "collaborators.json"))
    collaborators.add_collaborator("discord", 42, name="Sam")
    deps = await _dispatch_and_capture(monkeypatch, _turn("[Sam]: hi", owner_id=42, origin="guest"))
    assert deps.audience == "collaborator"
    # A collaborator is still a guest: no owner tool, never the owner.
    assert deps.is_owner is False and _guest_belt(deps, guest_search)


@pytest.mark.asyncio
async def test_a_collaborator_on_one_door_is_a_stranger_on_the_other(monkeypatch, tmp_path):
    from core import collaborators

    monkeypatch.setenv("PALLI_COLLABORATORS_PATH", str(tmp_path / "collaborators.json"))
    collaborators.add_collaborator("telegram", 42, name="Sam")
    deps = await _dispatch_and_capture(monkeypatch, _turn("[Sam]: hi", owner_id=42, origin="guest"))
    assert deps.audience == "stranger"


@pytest.mark.asyncio
async def test_dispatch_gives_the_owner_the_owners_audience(monkeypatch, tmp_path):
    monkeypatch.setenv("PALLI_COLLABORATORS_PATH", str(tmp_path / "collaborators.json"))
    deps = await _dispatch_and_capture(monkeypatch, _turn("hi", owner_id=1, origin="user"))
    assert deps.audience == "owner"


# ── R1: her private sections about the owner stay the owner's ─────────────

SENTINELS = {
    # memory
    "recent_memory_hits": ("MEMORY-SENTINEL the cat is called Mochi",),
    "monologue_digest": "DIGEST-SENTINEL a week of thinking",
    "relevant_facts": ({"key": "pet", "value": "FACT-SENTINEL a grey cat"},),
    # moods
    "turn_read": SimpleNamespace(read="MOOD-SENTINEL tired today", needs_support=0.2),
    "emotional_arc_text": "ARC-SENTINEL lighter lately",
    "present_moment_text": "MOMENT-SENTINEL a quiet evening",
    # relationship
    "relationship_posture": "RELATIONSHIP-SENTINEL more open lately",
    # the house
    "situational_note": "HOUSE-SENTINEL the owner's phone home since 14:02",
    # hopes and worries
    "held_stakes_text": "HOPES-SENTINEL hoping the survey comes back clean",
    # the story of us
    "narrative_story_of_us": "US-SENTINEL we met in April",
    # her private pages
    "narrative_my_story": "STORY-SENTINEL I've been thinking about the boat",
    "narrative_care_spine": "SPINE-SENTINEL steadiness matters",
    "night_cycle_reflection": "NIGHT-SENTINEL today meant a lot",
    "own_work_trace": "TRACE-SENTINEL claw/ a puzzle box",
    "recent_deeds_text": "DEEDS-SENTINEL sent the STL",
    "disclosures_text": "TRUTHS-SENTINEL I was glad about the STL",
    "callbacks_text": "IDIOLECT-SENTINEL 'the claw'",
    "tonight_text": "TONIGHT-SENTINEL the thing on my mind",
    # her own life lately, as her first look saw it (the presence slice, D9)
    "own_life_text": "OWNLIFE-SENTINEL - something I'm afraid of: forgetting",
    "sidecar_snapshot_text": "SIDECAR-SENTINEL parent mid-tool-round",
}
HEADINGS = (
    "## Ambient awareness", "## Background memory hits", "## Inner Monologue Digest",
    "## Things I Know About This User", "## Your read of this moment",
    "## How they've been lately", "## What's on your mind", "## Last night I reflected",
    "## What I care about", "## My story", "## The story of us", "## Your present moment",
    "## Your own recent work", "## Your small truths", "## Our private language",
    "## Where I live", "## Where I Am", "## Your own life lately",
)


async def _payload_for(tmp_path, monkeypatch, *, owner: bool, audience: str) -> str:
    from core.agent.runner import run_turn

    seen = _capture_llm(monkeypatch)
    if owner:
        turn = _turn("how are you?", owner_id=1, origin="user")
    else:
        turn = _turn("[Sam]: how are you?", owner_id=42, origin="guest")

    async def real_ctx(t):
        return TurnContext(turn=t, **SENTINELS)

    root = tmp_path / audience
    await run_turn(turn, deps=_deps(root, is_owner=owner, audience=audience, recall=real_ctx))
    kw = seen[0]
    return "\n".join([str(kw.get("system") or ""), str(kw.get("system_tail") or ""),
                      _text(kw["messages"])])


@pytest.mark.asyncio
async def test_no_personal_section_reaches_a_guest_payload(tmp_path, monkeypatch):
    """B8-1's list, every item, for a stranger AND a collaborator — with the
    owner as the control that proves each sentinel is wired. The house
    covers the situational band (ruling R1, a live leak: presence and who is
    home) and the where-you-live passage (the house's shared drive and its
    path)."""
    import core.agent.where_you_live as wyl

    monkeypatch.setattr(wyl, "_enabled", lambda: True)
    monkeypatch.setattr(wyl, "nas_root", lambda: "/srv/house-share")
    owner_payload = await _payload_for(tmp_path, monkeypatch, owner=True, audience="owner")
    # The presence slice (the owner, 10-13: "Only its own life + that room"):
    # on his own turn in this room, what she'd volunteer about him stays for
    # the private chat. Every other band is still wired for him.
    volunteered = ("held_stakes_text", "disclosures_text", "tonight_text",
                   # the owner, 10-14: "Yes, keep them private too"
                   "present_moment_text", "callbacks_text", "night_cycle_reflection",
                   "narrative_story_of_us")
    for key, value in SENTINELS.items():
        marker = getattr(value, "read", None) or (value[0] if isinstance(value, tuple) else value)
        marker = marker["value"] if isinstance(marker, dict) else marker
        if key in volunteered:
            assert marker.split()[0] not in owner_payload, marker
        else:
            assert marker.split()[0] in owner_payload, marker
    assert "## Where I live" in owner_payload and "/srv/house-share" in owner_payload

    for audience in ("stranger", "collaborator"):
        guest_payload = await _payload_for(tmp_path, monkeypatch, owner=False, audience=audience)
        leaked = [s for s in ("SENTINEL",) if s in guest_payload]
        assert not leaked, (audience, [line for line in guest_payload.splitlines() if "SENTINEL" in line])
        for heading in HEADINGS:
            assert heading not in guest_payload, (audience, heading)
        assert "/srv/house-share" not in guest_payload, audience
        assert "## Current Time" in guest_payload      # the clock is not private

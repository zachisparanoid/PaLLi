"""Memory that reaches, in the runner (R15; plan
docs/2026-10-13-memory-that-reaches-plan.md, group W).

W3, the reply-sources part: at the end of a run the runner writes one line
of what the reply drew on (core/reply_sources.py) — the dated lines
``search_what_we_said`` returned, the memory notes, the facts band and
today's lines her prompt carried, a guest's room section, her queries and the
look's counts. Owner's and guests' conversation turns alike; never on a quiet
choice; never while companion.memory_reach is off; a failed write costs the
line, never the reply.

W2, the guest step: a guest's turn has no tools, so before the reply a code
step searches that room's own history (its scope from the turn, never from a
transcript key) and her prompt carries what it found under items 18–19; the
owner's turn never gets it; off, a guest's payload is today's.

The REAL runner with a fake ``llm_client.call``, a real TurnContext (so the
adapter renders the prompt the record is checked against), the real tool
with its search faked at its module, and a tmp record folder. Stand-in names
only: Robin is the owner, Dana a friend.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ai.memory_reach_index as mri
import ai.memory_reach_search as mrs
from ai.memory_reach_search import Hit
from core import reply_sources as rs
from core.agent.session import ToolUseBlock
from core.turn.types import Turn, TurnContext, TurnMetadata

TS = datetime(2026, 10, 5, 19, 2, tzinfo=timezone.utc).timestamp()
HITS = [
    Hit(id=1, room="discord:111", ts=TS, day="2026-10-05", speaker="owner", name="Robin",
        text="the kite shop on Elm opens at nine on Saturdays", score=0.8, words=0.8,
        meaning=0.8, exact=False),
    Hit(id=2, room="discord:111", ts=TS + 60, day="2026-10-05", speaker="palli", name="PaLLi",
        text="noted: nine on Saturdays", score=0.8, words=0.8, meaning=0.8, exact=False),
]
FACTS = (
    {"key": "hobby", "value": "kites", "memory_type": "user_fact", "id": 4},
    {"key": "plan", "value": "plan: kite shop Saturday", "memory_type": "today", "id": 9},
)
NOTES = ("Robin flies kites on Saturdays",)
REPLY = "The kite shop on Elm opens at nine on Saturdays."


def _NOW() -> datetime:
    """A turn's arrival as the door stamps it: UTC, naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def folder(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "reply_sources"
    monkeypatch.setenv("PALLI_REPLY_SOURCES_DIR", str(path))
    return path


@pytest.fixture
def switch_on(monkeypatch):
    from core.turn import companion_flags

    monkeypatch.setattr(mri, "enabled", lambda: True)
    monkeypatch.setattr(companion_flags, "today_slot_on", lambda: True)


@pytest.fixture
def fake_search(monkeypatch):
    searched: list[str] = []

    async def catch_up(**_kw):
        return {}

    async def search(query, **_kw):
        searched.append(query)
        return mrs.Hits(HITS)

    monkeypatch.setattr(mri, "catch_up", catch_up)
    monkeypatch.setattr(mrs, "search", search)
    return searched


def _turn(*, guest: bool = False) -> Turn:
    md = (TurnMetadata(origin="guest", channel_id=222, discord_username="dana_example")
          if guest else TurnMetadata(channel_id=111, is_dm=True, discord_username="robin_example"))
    return Turn(surface="discord_text", owner_id=7 if not guest else 2222,
                content="when does the kite shop open?", metadata=md, received_at=_NOW())


def _registry():
    from core.agent.builtin_tools.search_what_we_said import SearchWhatWeSaidTool
    from core.agent.tools import ToolRegistry

    registry = ToolRegistry()
    registry.register(SearchWhatWeSaidTool())
    return registry


async def _run(tmp_path, monkeypatch, *, turn: Turn, replies: list, is_owner: bool = True):
    """One run of the real runner; ``replies`` are the model's answers in order."""
    import core.agent.llm_client as llm
    import core.agent.runner as runner_mod

    calls: list[dict] = []

    async def fake_call(**kw):
        calls.append(kw)
        return replies[min(len(calls), len(replies)) - 1]

    async def fake_recall(t):
        return TurnContext(turn=t, recent_memory_hits=NOTES, relevant_facts=FACTS)

    async def fake_cadence(_prompt):
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})

    monkeypatch.setattr(llm, "call", fake_call)
    deps = runner_mod.RunnerDeps(
        build_turn_context=fake_recall, cadence_llm=fake_cadence, sessions_root=tmp_path / "sessions",
        provider="openai_compat", model="m", client=None, tool_registry=_registry(),
        polling_tools=set(), is_owner=is_owner,
    )
    result = await runner_mod.run_turn(turn, deps=deps, on_chunk=None)
    return result, calls


def _text(text: str):
    from core.agent.llm_client import LLMResponse

    return LLMResponse(text_blocks=[text], tool_uses=[], stop_reason="end_turn",
                       prompt_tokens=1, completion_tokens=1)


def _tool(name: str, tool_input: dict, call_id: str = "t1"):
    from core.agent.llm_client import LLMResponse

    return LLMResponse(text_blocks=[], tool_uses=[ToolUseBlock(id=call_id, name=name, input=tool_input)],
                       stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)


def _rows():
    return rs.read(1)


# ── W3: the runner records what each reply used ───────────────────────────

@pytest.mark.asyncio
async def test_a_reply_after_a_search_writes_one_line_of_what_it_drew_on(
        tmp_path, monkeypatch, folder, switch_on, fake_search):
    result, calls = await _run(tmp_path, monkeypatch, turn=_turn(), replies=[
        _tool("search_what_we_said", {"query": "kite shop"}), _text(REPLY)])

    assert result.responded and result.response_text == REPLY
    assert fake_search == ["kite shop"]
    (row,) = _rows()
    assert row["door"] == "discord_text" and row["conversation"] == "discord:111"
    assert row["who"] == {"name": "robin_example", "owner": True}
    assert row["reply"] == REPLY
    assert len(row["record_lines"]) == 2
    assert row["record_lines"][0].endswith("them: the kite shop on Elm opens at nine on Saturdays")
    assert row["record_lines"][1].endswith("me: noted: nine on Saturdays")
    assert row["notes"] == list(NOTES)
    assert row["facts"] == ["hobby: kites"]
    assert row["today"] == ["plan: kite shop Saturday"]
    assert row["room_lines"] == []                       # W2 fills a guest's section
    assert row["queries"] == ["kite shop"]
    assert row["look"]["rounds"] == 0 and row["look"]["calls"] == 0 and row["look"]["thin"] is False
    # What the record says the reply drew on is what her prompt carried.
    payload = json.dumps(calls, default=str)
    for item in (*row["notes"], *row["facts"], *row["today"]):
        assert item in payload


@pytest.mark.asyncio
async def test_a_reply_with_no_search_still_records_its_notes_and_facts(
        tmp_path, monkeypatch, folder, switch_on, fake_search):
    await _run(tmp_path, monkeypatch, turn=_turn(), replies=[_text(REPLY)])
    (row,) = _rows()
    assert row["record_lines"] == [] and row["queries"] == [] and row["look"] is None
    assert row["notes"] == list(NOTES) and row["facts"] == ["hobby: kites"]


@pytest.mark.asyncio
async def test_a_guests_reply_is_recorded_with_none_of_the_owners_bands(
        tmp_path, monkeypatch, folder, switch_on, fake_search):
    result, calls = await _run(tmp_path, monkeypatch, turn=_turn(guest=True),
                               replies=[_text("Hi Dana!")], is_owner=False)
    assert result.responded
    (row,) = _rows()
    assert row["who"] == {"name": "dana_example", "owner": False}
    assert row["conversation"] == "discord:222"
    assert row["notes"] == [] and row["facts"] == [] and row["today"] == []
    assert row["record_lines"] == [] and row["queries"] == [] and row["room_lines"] == []
    assert all("search_what_we_said" not in json.dumps(c.get("tools") or [], default=str) for c in calls)


@pytest.mark.asyncio
async def test_a_quiet_choice_writes_no_line(tmp_path, monkeypatch, folder, switch_on, fake_search):
    from core.agent import quiet_choice

    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    result, _calls = await _run(tmp_path, monkeypatch, turn=_turn(), replies=[
        _tool("search_what_we_said", {"query": "kite shop"}),
        _tool("stay_quiet", {"reason": "Robin was thinking out loud"}, call_id="q1")])
    assert result.quiet is True and result.response_text == ""
    assert fake_search == ["kite shop"]                  # she looked, then chose quiet
    assert _rows() == [] and not folder.exists()


@pytest.mark.asyncio
async def test_with_the_switch_off_nothing_is_written(tmp_path, monkeypatch, folder, fake_search):
    result, _calls = await _run(tmp_path, monkeypatch, turn=_turn(), replies=[_text(REPLY)])
    assert result.responded and result.response_text == REPLY
    assert not folder.exists()


@pytest.mark.asyncio
async def test_a_record_that_breaks_costs_the_line_never_the_reply(
        tmp_path, monkeypatch, folder, switch_on, fake_search, caplog):
    def boom(**_kw):
        raise RuntimeError("Robin's private words in an error")

    monkeypatch.setattr(rs, "record", boom)
    with caplog.at_level(logging.WARNING, logger="core.agent.runner"):
        result, _calls = await _run(tmp_path, monkeypatch, turn=_turn(), replies=[_text(REPLY)])
    assert result.responded and result.response_text == REPLY
    warned = [r.getMessage() for r in caplog.records if "reply-sources" in r.getMessage()]
    assert len(warned) == 1 and "RuntimeError" in warned[0]
    assert "private words" not in warned[0]


@pytest.mark.asyncio
async def test_her_own_runs_are_not_replies(tmp_path, monkeypatch, folder, switch_on, fake_search):
    turn = Turn(surface="discord_text", owner_id=7, content="a reminder is due",
                metadata=TurnMetadata(origin="reminder", channel_id=111), received_at=_NOW())
    await _run(tmp_path, monkeypatch, turn=turn, replies=[_text("Your reminder: the kite shop.")])
    assert not folder.exists()


# ── W2: a guest's turn reaches its own room's history ─────────────────────
# Dana (a friend) asks in discord:222. The record holds the same words in
# Robin's DM (discord:111) and in another room (discord:333); only her own
# room's line may reach her reply, under items 18–19. The record is built
# from hand-written rows in the test's tmp path; its catch-up is a recorder
# and the search runs by words only (no model).

DANA = 2222
OWN_ROOM, OWNERS_DM, OTHER_ROOM = "discord:222", "discord:111", "discord:333"
QUESTION = "[Dana]: where does the kite festival start?"
PIER = "the kite festival starts at the pier at noon"
RED_KITE = "I'll bring the red kite to the kite festival"
# (ref, room, ts, speaker, name, text)
ROOM_LINES = [
    ("db:1", OWNERS_DM, TS, "owner", "Robin", "the kite festival starts at the secret beach"),
    ("f3:1", OTHER_ROOM, TS + 60, "guest", "Sam", "the kite festival starts at the north field"),
    ("db:2", OWN_ROOM, TS + 120, "owner", "Robin", PIER),
    ("f2:1", OWN_ROOM, TS + 240, "guest", "Dana", RED_KITE),
]


@pytest.fixture
def room_record(tmp_path, monkeypatch) -> list[dict]:
    """The fixture record at the suite's record path; returns the catch-ups."""
    path = tmp_path / "memory_reach.sqlite3"
    rows = [mri.Row(source="db" if ref.startswith("db:") else "room_file", source_ref=ref,
                    room=room, ts=ts, day=mri._day(ts), speaker=speaker, name=name, text=text)
            for ref, room, ts, speaker, name, text in ROOM_LINES]
    conn = mri.connect(path)
    try:
        mri.add_rows(conn, rows, vectors=None)
    finally:
        conn.close()
    monkeypatch.setenv("PALLI_MEMORY_REACH_PATH", str(path))
    monkeypatch.setattr(mri, "local_embeddings_on", lambda: False)     # words only
    caught: list[dict] = []

    async def catch_up(**kw):
        caught.append(kw)
        return {}

    monkeypatch.setattr(mri, "catch_up", catch_up)
    return caught


@pytest.fixture
def steady_clock(monkeypatch):
    """The clock band, fixed: two runs' prompts can then be compared byte for byte."""
    from core.agent import context_adapter

    monkeypatch.setattr(context_adapter, "_render_runtime",
                        lambda ctx, is_owner=True: "## Current Time\nnoon")


def _dana(content: str = QUESTION, *, channel_id: int = 222, override: str | None = None,
          is_dm: bool = False) -> Turn:
    extra = {"session_key_override": override} if override else {}
    return Turn(surface="discord_text", owner_id=DANA, content=content,
                metadata=TurnMetadata(origin="guest", channel_id=channel_id, is_dm=is_dm,
                                      discord_username="dana_example", extra=extra),
                received_at=_NOW())


def _section(calls: list) -> str:
    """The guest's section in the first reply call's volatile tail, or ""."""
    from core.agent.system_prompt import ROOM_MEMORY_HEADING

    tail = str(calls[0].get("system_tail") or "")
    start = tail.find(ROOM_MEMORY_HEADING)
    if start < 0:
        return ""
    end = tail.find("\n\n## ", start + 1)
    return tail[start:] if end < 0 else tail[start:end]


def _comparable(calls: list) -> list[dict]:
    """What the model was sent, the rows' time stamps left out."""
    from core.agent.message_times import STAMP_RE

    out = []
    for kw in calls:
        msgs = [[m.role, [STAMP_RE.sub("[T]", b.text) for b in m.content if hasattr(b, "text")]]
                for m in kw.get("messages") or []]
        out.append({k: (msgs if k == "messages" else kw.get(k)) for k in
                    ("system", "system_tail", "messages", "tools", "trailing_system_note")})
    return json.loads(json.dumps(out, default=str))


@pytest.mark.asyncio
async def test_a_guest_turn_reaches_its_own_rooms_history_and_no_other(
        tmp_path, monkeypatch, folder, switch_on, room_record):
    from core.agent.system_prompt import ROOM_MEMORY_FRAMING, ROOM_MEMORY_HEADING

    result, calls = await _run(tmp_path, monkeypatch, turn=_dana(),
                               replies=[_text("At the pier, at noon!")], is_owner=False)

    assert result.responded
    section = _section(calls)
    assert section.startswith(f"{ROOM_MEMORY_HEADING}\n{ROOM_MEMORY_FRAMING}\nIn the Discord chat 222:\n")
    # The reply's history shows no line of the owner's, so no name for them:
    # the mark alone (W1), never the record's name.
    assert f"] ★owner: {PIER}" in section and f"Dana: {RED_KITE}" in section
    assert "Robin" not in section
    payload = json.dumps(calls, default=str)
    assert "secret beach" not in payload and "north field" not in payload
    assert len(room_record) == 1 and room_record[0]["budget_s"] < 1.5      # caught up first
    # What the reply used: the section's dated lines, without the room's heading.
    (row,) = _rows()
    assert len(row["room_lines"]) == 2
    assert row["room_lines"][0].endswith(f"] ★owner: {PIER}")
    assert row["room_lines"][1].endswith(f"Dana: {RED_KITE}")
    assert row["notes"] == [] and row["facts"] == [] and row["today"] == []


@pytest.mark.asyncio
async def test_the_section_names_the_owner_as_the_room_shows_them_never_by_the_handle(
        tmp_path, monkeypatch, folder, switch_on, room_record):
    """I2 / W1 (Fable's whole-branch review): an owner line from the database
    carries the account handle in the record; the room's labels name the
    owner by the speaker record's name. The section uses the room's name,
    taken from the rows this reply shows (the latest owner label there)."""
    from core.agent.session import AgentMessage, Session, TextBlock

    parade = "the kite festival starts with a parade on Elm"
    path = Path(os.environ["PALLI_MEMORY_REACH_PATH"])
    conn = mri.connect(path)
    try:
        mri.add_rows(conn, [mri.Row(source="db", source_ref="db:3", room=OWN_ROOM, ts=TS + 150,
                                    day=mri._day(TS + 150), speaker="owner",
                                    name="robin_example", text=parade)], vectors=None)
    finally:
        conn.close()
    session = await Session.open_or_create(OWN_ROOM, root=tmp_path / "sessions")
    await session.append(AgentMessage(
        role="user", content=[TextBlock(text="morning, everyone")],
        timestamp=datetime.fromtimestamp(TS + 600, tz=timezone.utc),
        speaker={"kind": "owner", "platform": "discord", "id": "1111", "name": "Robin",
                 "handle": "robin_example"}))

    _result, calls = await _run(tmp_path, monkeypatch, turn=_dana(),
                                replies=[_text("At the pier!")], is_owner=False)

    section = _section(calls)
    assert f"] Robin ★owner: {parade}" in section and f"] Robin ★owner: {PIER}" in section
    assert "robin_example" not in section


@pytest.mark.asyncio
async def test_a_guest_turn_whose_key_says_guest_still_reaches_its_room(
        tmp_path, monkeypatch, folder, switch_on, room_record):
    """The transcript key can carry ``:guest:<id>``; the record's room key
    never does. The step's scope is the turn's (scope_for_turn)."""
    turn = _dana(override=f"{OWN_ROOM}:guest:{DANA}")
    _result, calls = await _run(tmp_path, monkeypatch, turn=turn,
                                replies=[_text("At the pier!")], is_owner=False)
    section = _section(calls)
    assert PIER in section and RED_KITE in section
    assert "secret beach" not in section and "north field" not in section


@pytest.mark.asyncio
async def test_a_friends_dm_reaches_that_dm_only(tmp_path, monkeypatch, folder, switch_on,
                                                 room_record):
    """A friend's private chat with PaLLi is a guest turn in a room of its
    own (scope_for_turn: ``discord:<dm>``): their own lines and hers there."""
    path = Path(os.environ["PALLI_MEMORY_REACH_PATH"])
    conn = mri.connect(path)
    try:
        mri.add_rows(conn, [mri.Row(source="room_file", source_ref="f5:1", room="discord:555",
                                    ts=TS, day=mri._day(TS), speaker="guest", name="Dana",
                                    text="I asked where the kite festival starts last week")],
                     vectors=None)
    finally:
        conn.close()
    _result, calls = await _run(tmp_path, monkeypatch, turn=_dana(channel_id=555, is_dm=True),
                                replies=[_text("At the pier!")], is_owner=False)
    section = _section(calls)
    assert "I asked where the kite festival starts last week" in section
    assert PIER not in section and "secret beach" not in section


@pytest.mark.asyncio
async def test_a_line_the_history_already_shows_is_not_repeated(
        tmp_path, monkeypatch, folder, switch_on, room_record):
    from core.agent.session import AgentMessage, Session, TextBlock

    def at(ts: float) -> datetime:
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    session = await Session.open_or_create(OWN_ROOM, root=tmp_path / "sessions")
    for role, text, ts in (("user", PIER, TS + 120),
                           ("assistant", "the pier at noon, noted", TS + 180),
                           ("user", f"[Dana]: {RED_KITE}", TS + 240),
                           ("assistant", "a red kite, lovely", TS + 300)):
        await session.append(AgentMessage(role=role, content=[TextBlock(text=text)],
                                          timestamp=at(ts)))
    # The reply's history shows its last three rows (step 1): Dana's red kite,
    # her answer, and this turn; the owner's line about the pier is older.
    monkeypatch.setenv("PALLI_AGENT_HISTORY_MAX_MESSAGES", "3")
    monkeypatch.setenv("PALLI_AGENT_HISTORY_STEP", "1")
    _result, calls = await _run(tmp_path, monkeypatch, turn=_dana(),
                                replies=[_text("At the pier!")], is_owner=False)

    section = _section(calls)
    assert PIER in section and RED_KITE not in section
    history = json.dumps(_comparable(calls)[0]["messages"])
    assert RED_KITE in history and PIER not in history


@pytest.mark.asyncio
async def test_with_the_switch_off_a_guests_payload_is_todays(
        tmp_path, monkeypatch, folder, room_record, steady_clock):
    reached: list = []
    real = mrs.room_section_for

    async def spy(*a, **kw):
        reached.append(kw)
        return await real(*a, **kw)

    monkeypatch.setattr(mrs, "room_section_for", spy)
    # Off: the step never runs, nothing is caught up, no section.
    _r, off = await _run(tmp_path / "off", monkeypatch, turn=_dana(channel_id=999),
                         replies=[_text("At the pier!")], is_owner=False)
    assert reached == [] and room_record == [] and _section(off) == ""

    # On, in a room the record holds nothing for: the step runs, finds
    # nothing, and the payload is the switch-off payload, byte for byte.
    monkeypatch.setattr(mri, "enabled", lambda: True)
    _r, empty = await _run(tmp_path / "empty", monkeypatch, turn=_dana(channel_id=999),
                           replies=[_text("At the pier!")], is_owner=False)
    assert len(reached) == 1 and len(room_record) == 1
    assert _comparable(empty) == _comparable(off)


@pytest.mark.asyncio
async def test_the_owners_turn_never_gets_the_section(
        tmp_path, monkeypatch, folder, switch_on, room_record):
    reached: list = []

    async def spy(*a, **kw):
        reached.append(kw)
        return "In the Discord chat 111:\n[Oct 5 19:02] them: never"

    monkeypatch.setattr(mrs, "room_section_for", spy)
    owner = Turn(surface="discord_text", owner_id=7, content="where does the kite festival start?",
                 metadata=TurnMetadata(channel_id=222, discord_username="robin_example",
                                       speaker={"kind": "owner", "name": "Robin"}),
                 received_at=_NOW())
    _result, calls = await _run(tmp_path, monkeypatch, turn=owner, replies=[_text("At the pier!")])
    assert reached == [] and _section(calls) == ""       # the tool is theirs
    (row,) = _rows()
    assert row["room_lines"] == []


@pytest.mark.asyncio
async def test_a_step_that_raises_leaves_the_payload_as_without_it(
        tmp_path, monkeypatch, folder, switch_on, room_record, steady_clock, caplog):
    async def nothing(*a, **kw):
        return ""

    async def boom(*a, **kw):
        raise RuntimeError("Dana's words in an error")

    monkeypatch.setattr(mrs, "room_section_for", nothing)
    _r, without = await _run(tmp_path / "without", monkeypatch, turn=_dana(),
                             replies=[_text("At the pier!")], is_owner=False)
    monkeypatch.setattr(mrs, "room_section_for", boom)
    with caplog.at_level(logging.WARNING, logger="core.agent.runner"):
        result, broken = await _run(tmp_path / "broken", monkeypatch, turn=_dana(),
                                    replies=[_text("At the pier!")], is_owner=False)

    assert result.responded and result.response_text == "At the pier!"
    assert _comparable(broken) == _comparable(without)
    warned = [r.getMessage() for r in caplog.records if "room step" in r.getMessage()]
    assert len(warned) == 1 and "RuntimeError" in warned[0] and "Dana's words" not in warned[0]


@pytest.mark.asyncio
async def test_a_guests_record_has_no_owner_bands_even_if_called_the_owner(
        tmp_path, monkeypatch, folder, switch_on, fake_search):
    """The record's privacy holds on its own: a guest's turn writes no notes,
    facts or today lines even if dispatch had said is_owner=True."""
    result, _calls = await _run(tmp_path, monkeypatch, turn=_turn(guest=True),
                                replies=[_text("Hi Dana!")], is_owner=True)
    assert result.responded
    (row,) = _rows()
    assert row["who"]["owner"] is False
    assert row["notes"] == [] and row["facts"] == [] and row["today"] == []


def test_items_18_and_19_sit_right_after_the_facts_band_and_are_nothing_when_empty():
    from types import SimpleNamespace

    from core.agent.system_prompt import (
        ROOM_MEMORY_FRAMING,
        ROOM_MEMORY_HEADING,
        build_system_prompt_parts,
    )

    # Draft 2's items 18–19, as the owner approved them on 10-13, verbatim.
    assert ROOM_MEMORY_HEADING == "## Said earlier in this room"
    assert ROOM_MEMORY_FRAMING == (
        "Lines from this room's own history, older than the conversation above, that may "
        "bear on what was just said. Background only: use one if it really applies; never "
        "recite them.")
    base = dict(identity_core_text="I'm PaLLi.", skills_text="", memory_text="",
                runtime_text="## Current Time\nnoon", facts_text="## Facts\n- kite: red",
                owner_memory_text="", images_text="## Images\na kite", is_owner=False)
    lines = "In the Discord chat 222:\n[Oct 5 19:02] Dana: I'll bring the red kite"
    _stable, tail = build_system_prompt_parts(SimpleNamespace(**base, room_memory_text=lines), "")
    assert (f"## Facts\n- kite: red\n\n{ROOM_MEMORY_HEADING}\n{ROOM_MEMORY_FRAMING}\n{lines}"
            f"\n\n## Images\na kite") in tail
    # Empty (every owner's turn, every guest turn the step found nothing
    # for) is a context from before W2, byte for byte.
    before = build_system_prompt_parts(SimpleNamespace(**base), "")
    assert build_system_prompt_parts(SimpleNamespace(**base, room_memory_text=""), "") == before
    assert build_system_prompt_parts(SimpleNamespace(**base, room_memory_text=" \n"), "") == before


@pytest.mark.asyncio
async def test_a_friends_telegram_chat_reaches_that_chat_only(
        tmp_path, monkeypatch, folder, switch_on, room_record):
    path = Path(os.environ["PALLI_MEMORY_REACH_PATH"])
    conn = mri.connect(path)
    try:
        mri.add_rows(conn, [
            mri.Row(source="room_file", source_ref="tg:1", room="telegram:5150", ts=TS,
                    day=mri._day(TS), speaker="guest", name="Dana",
                    text="the kite festival starts at the lighthouse, I heard"),
            mri.Row(source="db", source_ref="db:77", room="telegram:777", ts=TS,
                    day=mri._day(TS), speaker="owner", name="Robin",
                    text="the kite festival starts at the old mill"),
        ], vectors=None)
    finally:
        conn.close()
    turn = Turn(surface="telegram_text", owner_id=5150, content=QUESTION,
                metadata=TurnMetadata(origin="guest", channel_id=5150, is_dm=True,
                                      extra={"telegram_username": "dana_example"}),
                received_at=_NOW())
    _result, calls = await _run(tmp_path, monkeypatch, turn=turn,
                                replies=[_text("At the lighthouse!")], is_owner=False)
    section = _section(calls)
    assert "In the Telegram chat 5150:" in section and "the lighthouse, I heard" in section
    payload = json.dumps(calls, default=str)
    assert "old mill" not in payload and PIER not in payload and "secret beach" not in payload

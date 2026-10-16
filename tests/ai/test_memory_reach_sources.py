"""Memory that reaches: the record's two sources, the dedupe rule, catch-up
by watermark and the one-time build (R15, T3; plan
docs/2026-10-13-memory-that-reaches-plan.md).

The ``conversations`` table is the record of every owner line and every
PaLLi line; the chat doors' room files add guests' lines only. A room file
is read, never written: a bad line is skipped and counted, a half-written
last line waits for its newline, a file that shrank or was replaced is read
again whole. Every test runs on tmp paths with a fake embedder: a tmp
database built from the real catalog, rows written by the real
``ConversationHistoryStore``, room-file lines written by the real
``_msg_to_jsonl``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai import memory_reach_index as mri
from core.agent.session import AgentMessage, TextBlock, ToolUseBlock, _msg_to_jsonl
from core.agent.speakers import make_speaker
from core.config import config
from core.conversation_history_store import ConversationHistoryStore
from core.database import PalliBrain
from core.owner_gate import guest_content
from core.session_keys import session_key_for
from core.sqlite_schema_catalog import ensure_catalog_schema_sync

ROBIN_APP = 7            # the owner's app id (dashboard, his DB rows)
ROBIN_DISCORD = 1111     # the owner's Discord account
DANA = 2222
SAM = 3333
ROOM = 1                 # a Discord server channel: discord_1.jsonl
T0 = 1_760_000_000.0     # 2025-10-09, mid-morning UTC


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d")


def _at(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


# --- fixtures ----------------------------------------------------------------

@pytest.fixture
def brain(tmp_path):
    db_path = tmp_path / "palli.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.isolation_level = None
    conn.execute("PRAGMA synchronous=OFF")
    try:
        ensure_catalog_schema_sync(conn)
    finally:
        conn.close()
    return PalliBrain(db_path=str(db_path))


@pytest.fixture
def sessions(tmp_path) -> Path:
    root = tmp_path / "agent_sessions"
    root.mkdir()
    return root


@pytest.fixture
def record(tmp_path) -> Path:
    return tmp_path / "record" / "memory_reach.sqlite3"


@pytest.fixture(autouse=True)
def _owner_is_robin(monkeypatch):
    async def _ids() -> frozenset[int]:
        return frozenset({ROBIN_APP, ROBIN_DISCORD})

    monkeypatch.setattr(mri, "_owner_ids", _ids)


@pytest.fixture(autouse=True)
def _fresh_module_state():
    """No back-off and no once-per-process note carried between tests."""
    mri._EMBED_PAUSE["until"] = 0.0
    mri._NOTED.clear()
    yield
    mri._EMBED_PAUSE["until"] = 0.0
    mri._NOTED.clear()


class FakeEmbed:
    """Instant, deterministic, no network: one 3-d vector per text."""

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.batches: list[int] = []
        self.gate = gate
        self.called = asyncio.Event()

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(len(texts))
        self.called.set()
        if self.gate is not None:
            await self.gate.wait()
        return [[float(len(t)), 1.0, 0.5] for t in texts]


class BrokenEmbed:
    """An embedder whose model will not load."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        raise RuntimeError("the local embedding model is not available")


class SlowBrain:
    """The real tmp brain, each connection 50 ms late (a busy database).
    ``opened``: the ``time.monotonic()`` each page read began."""

    def __init__(self, brain) -> None:
        self._brain = brain
        self.opened: list[float] = []

    async def get_connection(self):
        self.opened.append(time.monotonic())
        await asyncio.sleep(0.05)
        return await self._brain.get_connection()


async def _store(brain, *, user_id, role, content, ts, platform="discord",
                 channel_id=ROOM, username=None) -> int:
    store = ConversationHistoryStore(brain, now=lambda: ts)
    return await store.store_conversation(
        user_id=user_id, platform=platform, channel_id=channel_id, thread_id=None,
        role=role, content=content, username=username)


def _store_many(brain, rows: list[tuple[str, str, float]]) -> None:
    """Volume for the counting tests: (role, content, ts) rows for the owner
    in ROOM, the columns ``store_conversation`` writes, in one transaction
    (one store call per row costs a connection and a commit each)."""
    conn = sqlite3.connect(str(brain.db_path))
    try:
        with conn:
            conn.executemany(
                "INSERT INTO conversations (user_id, platform, channel_id, role, username,"
                " content, timestamp) VALUES (?, 'discord', ?, ?, ?, ?, ?)",
                [(ROBIN_APP, ROOM, role, "Me" if role == "assistant" else "Robin", text, ts)
                 for role, text, ts in rows])
    finally:
        conn.close()


def _user(text: str, ts: float, speaker: dict | None = None) -> AgentMessage:
    return AgentMessage(role="user", content=[TextBlock(text=text)], timestamp=_at(ts),
                        speaker=speaker)


def _palli(text: str, ts: float) -> AgentMessage:
    # Her side on disk is a LIST of parts, a tool call among them.
    return AgentMessage(role="assistant", timestamp=_at(ts), content=[
        TextBlock(text=text), ToolUseBlock(id="t1", name="web_search", input={"q": "x"})])


def _owner_speaker():
    return make_speaker("owner", platform="discord", account_id=ROBIN_DISCORD,
                        name="Robin", handle="robin")


def _guest_speaker(name: str, account: int):
    return make_speaker("guest", platform="discord", account_id=account, name=name,
                        handle=name.lower())


def _write(path: Path, msgs: list[AgentMessage], *, mode: str = "a") -> None:
    with path.open(mode, encoding="utf-8") as f:
        for m in msgs:
            f.write(_msg_to_jsonl(m) + "\n")


def _rows(record: Path) -> list[tuple]:
    conn = sqlite3.connect(str(record))
    try:
        return conn.execute(
            "SELECT source, source_ref, room, speaker, name, text FROM record ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _texts(record: Path) -> list[str]:
    return [r[5] for r in _rows(record)]


def _scalar(record: Path, sql: str, *args):
    conn = sqlite3.connect(str(record))
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def _mark(record: Path, name: str) -> dict:
    conn = mri.connect(record)
    try:
        return json.loads(mri.get_state(conn, f"file:{name}"))
    finally:
        conn.close()


async def _drain_backlog() -> None:
    tasks = list(mri._BACKLOG.values())
    if tasks:
        await asyncio.gather(*tasks)


# Room for a loaded machine where the budget is not what a test is about: the
# inline embedding then always runs, and no detached task outlives the test.
ROOMY = 30.0


async def _catch_up(brain, sessions, record, embed=None) -> dict:
    return await mri.catch_up(brain=brain, sessions_root=sessions, embed=embed or FakeEmbed(),
                              budget_s=ROOMY, path=record)


# --- which files, which rooms ------------------------------------------------

def test_only_the_chat_doors_room_files_are_read(tmp_path):
    root = tmp_path / "agent_sessions"
    (root / "tool_results").mkdir(parents=True)
    for name in ("discord_1.jsonl", "telegram_-100123.jsonl", "discord_42.jsonl",
                 "discord_1_sidecar-2.jsonl", "discord_1.jsonl.bak", "dashboard_7.jsonl",
                 "proactive_7.jsonl", "reminder_fire_7.jsonl", "workbench_7.jsonl",
                 "forge_followup_7.jsonl", "discord_voice_5.jsonl", "shot.png",
                 "discord_1.jsonl.tmp",
                 # The phone app's room file: his alone, no guests to read, and
                 # his own lines come from the database.
                 "mobile_1.jsonl"):
        (root / name).write_text("", encoding="utf-8")
    (root / "tool_results" / "discord_9.jsonl").write_text("", encoding="utf-8")
    assert [p.name for p in mri.transcript_files(root)] == [
        "discord_1.jsonl", "discord_42.jsonl", "telegram_-100123.jsonl"]
    assert mri.transcript_files(tmp_path / "nowhere") == []
    assert mri.TRANSCRIPT_RE.pattern == r"^(discord|telegram)_(-?\d+)\.jsonl$"


def test_room_keys_match_the_turns_session_keys():
    assert mri.room_key_for_file(Path("/x/discord_123.jsonl")) == "discord:123"
    assert mri.room_key_for_file(Path("telegram_-100123.jsonl")) == "telegram:-100123"
    assert mri.room_key_for_db("discord", 123, ROBIN_APP) == "discord:123"
    assert mri.room_key_for_db("telegram", -100123, ROBIN_APP) == "telegram:-100123"
    assert mri.room_key_for_db("dashboard", 0, ROBIN_APP) == f"dashboard:{ROBIN_APP}"
    assert mri.room_key_for_db("proactive", 55, ROBIN_APP) == "proactive:55"
    assert mri.room_key_for_db("mobile", 1, ROBIN_APP) == "mobile:1"
    # The scope T4 builds from a turn names the same rooms.
    assert mri.room_key_for_file(Path("discord_123.jsonl")) == session_key_for(
        "discord_text", 123, ROBIN_APP)
    assert mri.room_key_for_db("telegram", -100123, DANA) == session_key_for(
        "telegram_text", -100123, DANA)
    assert mri.room_key_for_db("dashboard", 0, ROBIN_APP) == session_key_for(
        "dashboard", 0, ROBIN_APP)
    assert mri.room_key_for_db("mobile", 1, ROBIN_APP) == session_key_for(
        "mobile_text", 1, ROBIN_APP)


# --- the database ------------------------------------------------------------

@pytest.mark.asyncio
async def test_db_rows_since_reads_who_said_it_and_skips_what_has_no_words(brain):
    owner = await _store(brain, user_id=ROBIN_APP, role="user", username="Robin",
                         content="the fence goes blue this weekend", ts=T0)
    reply = await _store(brain, user_id=ROBIN_APP, role="assistant",
                         content="Blue it is. Want me to check the forecast?", ts=T0 + 5)
    guest = await _store(brain, user_id=DANA, role="user", username="Dana",
                         content="[Dana]: count me in for the painting", ts=T0 + 9)
    to_guest = await _store(brain, user_id=DANA, role="assistant",
                            content="Thanks, Dana. Brushes are in the shed.", ts=T0 + 12)
    dash = await _store(brain, user_id=ROBIN_APP, role="user", platform="dashboard",
                        channel_id=0, content="note the paint brand", ts=T0 + 20)
    nudge = await _store(brain, user_id=ROBIN_APP, role="assistant", platform="proactive",
                         channel_id=55, content="How did the fence turn out?", ts=T0 + 30)
    await _store(brain, user_id=ROBIN_APP, role="user", platform="proactive", channel_id=55,
                 content="a proactive user row is no one's words", ts=T0 + 31)
    for placeholder in ("[image attached]", "[file attached]", "[VOICE_MESSAGE]"):
        await _store(brain, user_id=ROBIN_APP, role="user", content=placeholder, ts=T0 + 40)
    last = await _store(brain, user_id=ROBIN_APP, role="system",
                        content="delivery failed: the reply did not reach them", ts=T0 + 50)

    rows, top = await mri.db_rows_since(
        brain, last_id=0, owner_ids=frozenset({ROBIN_APP, ROBIN_DISCORD}))

    assert top == last            # the watermark passes the rows it skipped
    got = [(r.source, r.source_ref, r.room, r.speaker, r.name, r.text) for r in rows]
    assert got == [
        ("db", f"db:{owner}", "discord:1", "owner", "Robin", "the fence goes blue this weekend"),
        ("db", f"db:{reply}", "discord:1", "palli", "PaLLi",
         "Blue it is. Want me to check the forecast?"),
        ("db", f"db:{guest}", "discord:1", "guest", "Dana", "count me in for the painting"),
        ("db", f"db:{to_guest}", "discord:1", "palli", "PaLLi",
         "Thanks, Dana. Brushes are in the shed."),
        # No username given: the store files the row under "User<id>".
        ("db", f"db:{dash}", f"dashboard:{ROBIN_APP}", "owner", f"User{ROBIN_APP}",
         "note the paint brand"),
        ("db", f"db:{nudge}", "proactive:55", "palli", "PaLLi", "How did the fence turn out?"),
    ]
    assert rows[0].ts == T0 and rows[0].day == _day(T0)
    assert rows[1].ts == T0 + 5

    # Paged by id, from the watermark.
    first, top1 = await mri.db_rows_since(
        brain, last_id=0, owner_ids=frozenset({ROBIN_APP}), limit=2)
    assert [r.source_ref for r in first] == [f"db:{owner}", f"db:{reply}"] and top1 == reply
    rest, top2 = await mri.db_rows_since(
        brain, last_id=top1, owner_ids=frozenset({ROBIN_APP}), limit=2)
    assert [r.source_ref for r in rest] == [f"db:{guest}", f"db:{to_guest}"] and top2 == to_guest
    none, same = await mri.db_rows_since(brain, last_id=last, owner_ids=frozenset({ROBIN_APP}))
    assert none == [] and same == last


@pytest.mark.asyncio
async def test_a_reply_to_a_guest_is_hers_never_the_guests(brain):
    """PaLLi's replies to guests carry the guest's id in ``user_id``: the
    speaker comes from the role, never from the id alone."""
    await _store(brain, user_id=SAM, role="assistant", content="Sure, Sam.", ts=T0)
    rows, _ = await mri.db_rows_since(brain, last_id=0, owner_ids=frozenset({ROBIN_APP}))
    assert [(r.speaker, r.name) for r in rows] == [("palli", "PaLLi")]


# --- the phone app -----------------------------------------------------------
# His third private door (``mobile:1``). Its words are in the table (the
# phone's intake persists them under platform "mobile") and its room file
# holds no guest, so the record reads them from the table alone, exactly as
# it reads his Telegram private chat.

@pytest.mark.asyncio
@pytest.mark.parametrize("platform, channel_id, room", [
    ("telegram", 777, "telegram:777"),     # his private chat with her
    ("mobile", 1, "mobile:1"),             # the phone app's one conversation
])
async def test_his_private_door_words_are_his_in_that_doors_room(
        brain, platform, channel_id, room):
    his = await _store(brain, user_id=ROBIN_APP, role="user", platform=platform,
                       channel_id=channel_id, username="Robin",
                       content="remind me the ferry leaves at noon", ts=T0)
    hers = await _store(brain, user_id=ROBIN_APP, role="assistant", platform=platform,
                        channel_id=channel_id, content="Noon, noted.", ts=T0 + 5)
    # No handle on the row: the store files it under "User<id>", still his.
    bare = await _store(brain, user_id=ROBIN_APP, role="user", platform=platform,
                        channel_id=channel_id, content="and bring the blue bag", ts=T0 + 9)

    rows, _ = await mri.db_rows_since(
        brain, last_id=0, owner_ids=frozenset({ROBIN_APP, ROBIN_DISCORD}))

    assert [(r.source, r.source_ref, r.room, r.speaker, r.name, r.text) for r in rows] == [
        ("db", f"db:{his}", room, "owner", "Robin", "remind me the ferry leaves at noon"),
        ("db", f"db:{hers}", room, "palli", "PaLLi", "Noon, noted."),
        ("db", f"db:{bare}", room, "owner", f"User{ROBIN_APP}", "and bring the blue bag"),
    ]


@pytest.mark.asyncio
async def test_a_user_row_on_another_platform_is_still_no_ones_words(brain):
    """Only the doors' and the dashboard's user rows are words; her reach-outs
    and the reminders' rows stay hers, and their user rows stay out."""
    phone = await _store(brain, user_id=ROBIN_APP, role="user", platform="mobile",
                         channel_id=1, content="what did I say about the ferry", ts=T0)
    nudge = await _store(brain, user_id=ROBIN_APP, role="assistant", platform="proactive",
                         channel_id=55, content="How was the ferry?", ts=T0 + 5)
    for platform in ("proactive", "reminder_fire", "voice", "workbench"):
        await _store(brain, user_id=ROBIN_APP, role="user", platform=platform, channel_id=55,
                     content=f"a {platform} user row is no one's words", ts=T0 + 10)
    last = await _store(brain, user_id=ROBIN_APP, role="user", platform="proactive",
                        channel_id=55, content="one more, to move the watermark", ts=T0 + 20)

    rows, top = await mri.db_rows_since(
        brain, last_id=0, owner_ids=frozenset({ROBIN_APP, ROBIN_DISCORD}))

    assert top == last
    assert [(r.source_ref, r.room, r.speaker) for r in rows] == [
        (f"db:{phone}", "mobile:1", "owner"), (f"db:{nudge}", "proactive:55", "palli")]
    assert "mobile" in mri._DB_USER_PLATFORMS
    assert not {"proactive", "reminder_fire", "voice"} & set(mri._DB_USER_PLATFORMS)


@pytest.mark.asyncio
async def test_what_he_sends_from_the_phone_is_in_the_record_once_and_found_from_the_phone(
        brain, sessions, record, tmp_path, monkeypatch):
    """The real path: the phone's intake builds the Turn, the real persister
    writes his row, the room file gets his line with his speaker, and the
    catch-up keeps it once, as his, in ``mobile:1``. A search from a phone
    turn reaches it, and every other room too."""
    from ai import memory_reach_search as mrs
    from bots.mobile import MOBILE_CHANNEL_ID, MOBILE_PLATFORM, MOBILE_SURFACE
    from bots.mobile.intake import build_turn
    from core import database
    from core.turn import persistence

    monkeypatch.setattr(database, "brain", brain)
    monkeypatch.setattr(mri, "embedder_for", lambda embed=None: None)     # by words

    turn, _payload = await build_turn(
        owner_id=ROBIN_APP, client_id="0b8c6f0e-6f5e-4c58-9a35-2f1d4f0a1b11",
        text="the harbour key is under the green bucket", uploads=[],
        directory=tmp_path / "uploads", store=None)
    assert turn.surface == MOBILE_SURFACE and turn.metadata.origin == "user"
    assert turn.metadata.speaker["kind"] == "owner"

    await persistence._persist_user_side_with_content(turn, turn.content)
    await _store(brain, user_id=ROBIN_APP, role="assistant", platform=MOBILE_PLATFORM,
                 channel_id=MOBILE_CHANNEL_ID, content="Green bucket, harbour key. Noted.",
                 ts=time.time() + 1)
    await _store(brain, user_id=ROBIN_APP, role="user", platform="discord", channel_id=9,
                 username="Robin", content="the ferry leaves at nine", ts=T0)
    # Her side of the phone's room file, as the runner writes it: his line
    # carries his speaker, hers has none. Neither is a guest's.
    _write(sessions / "mobile_1.jsonl", [
        _user(turn.content, T0 + 2, turn.metadata.speaker),
        _palli("Green bucket, harbour key. Noted.", T0 + 3),
    ])

    result = await _catch_up(brain, sessions, record)

    assert result["errors"] == []
    assert result["file_rows"] == 0          # no guest in the phone's room file
    assert sorted((r[0], r[2], r[3], r[5]) for r in _rows(record)) == sorted([
        ("db", "mobile:1", "owner", "the harbour key is under the green bucket"),
        ("db", "mobile:1", "palli", "Green bucket, harbour key. Noted."),
        ("db", "discord:9", "owner", "the ferry leaves at nine"),
    ])

    scope = mrs.scope_for_turn(turn)
    assert scope == mrs.Scope(None)
    hits = await mrs.search("harbour key green bucket", scope=scope, path=record)
    his = [h for h in hits if h.speaker == "owner"]
    assert [(h.room, h.text) for h in his] == [
        ("mobile:1", "the harbour key is under the green bucket")]
    assert mrs.room_label(his[0].room) == "the phone app"
    # From the phone, his every-room reach finds the Discord line as well.
    assert "the ferry leaves at nine" in {
        h.text for h in await mrs.search("ferry nine", scope=scope, path=record)}


# --- the room files ----------------------------------------------------------

def test_room_file_rows_take_guests_lines_only(sessions):
    path = sessions / "discord_1.jsonl"
    _write(path, [
        _user("the fence goes blue this weekend", T0, _owner_speaker()),
        _palli("Blue it is.", T0 + 5),
        AgentMessage(role="tool", content=[TextBlock(text="tool output")], timestamp=_at(T0 + 6)),
        _user(guest_content("Dana", "count me in for the painting"), T0 + 9,
              _guest_speaker("Dana", DANA)),
        _user("Robin pinned a message", T0 + 10,
              make_speaker("system", platform="discord")),
        _user(guest_content("Jo", "is the ladder still in the shed?"), T0 + 11,
              make_speaker("hidden", platform="telegram", name="Jo")),
        # Before 10-08 a row had no speaker: the door's tag was the only mark.
        _user(guest_content("Sam", "the ferry leaves at noon"), T0 + 12),
        _user("an untagged line before speakers was the owner's", T0 + 13),
        _user(guest_content("Alex", "[image attached]"), T0 + 14, _guest_speaker("Alex", 44)),
    ])
    size = path.stat().st_size

    rows, offset, lineno, skipped = mri.room_file_rows(path, offset=0, lineno=0)

    assert (offset, lineno, skipped) == (size, 9, 0)
    got = [(r.source, r.source_ref, r.room, r.speaker, r.name, r.text) for r in rows]
    assert got == [
        ("room_file", "discord_1.jsonl:4", "discord:1", "guest", "Dana",
         "count me in for the painting"),
        ("room_file", "discord_1.jsonl:6", "discord:1", "guest", "Jo",
         "is the ladder still in the shed?"),
        ("room_file", "discord_1.jsonl:7", "discord:1", "guest", "Sam",
         "the ferry leaves at noon"),
    ]
    assert rows[0].ts == pytest.approx(T0 + 9) and rows[0].day == _day(T0 + 9)
    # Resuming at the end reads nothing.
    assert mri.room_file_rows(path, offset=offset, lineno=lineno) == ([], size, 9, 0)


def test_a_string_content_and_a_name_from_the_tag(sessions):
    path = sessions / "telegram_-100.jsonl"
    line = {"role": "user", "content": "[Dana]: the keys are under the mat",
            "timestamp": "2026-10-05T19:02:00+02:00"}
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    rows, *_ = mri.room_file_rows(path, offset=0, lineno=0)
    want_ts = datetime(2026, 10, 5, 17, 2, tzinfo=timezone.utc).timestamp()
    assert [(r.room, r.name, r.text, r.ts) for r in rows] == [
        ("telegram:-100", "Dana", "the keys are under the mat", want_ts)]


def test_a_bad_line_is_skipped_and_counted_and_the_file_is_never_written(sessions):
    path = sessions / "discord_1.jsonl"
    _write(path, [_user(guest_content("Dana", "first line"), T0, _guest_speaker("Dana", DANA))])
    with path.open("a", encoding="utf-8") as f:
        f.write('{"role": "user", "content": [{"type": "text", "te\n')
        f.write("[1, 2, 3]\n")
        f.write(json.dumps({"role": "user", "content": "[Sam]: no time on this one"}) + "\n")
    _write(path, [_user(guest_content("Sam", "third line"), T0 + 3, _guest_speaker("Sam", SAM))])
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns

    rows, offset, lineno, skipped = mri.room_file_rows(path, offset=0, lineno=0)

    assert [r.text for r in rows] == ["first line", "third line"]
    assert [r.source_ref for r in rows] == ["discord_1.jsonl:1", "discord_1.jsonl:5"]
    assert (offset, lineno, skipped) == (len(before), 5, 3)
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime


def test_a_half_written_last_line_waits_for_its_newline(sessions):
    path = sessions / "discord_1.jsonl"
    whole = _msg_to_jsonl(_user(guest_content("Dana", "first"), T0, _guest_speaker("Dana", DANA)))
    half = _msg_to_jsonl(_user(guest_content("Sam", "second"), T0 + 1, _guest_speaker("Sam", SAM)))
    path.write_bytes((whole + "\n" + half[:25]).encode("utf-8"))

    rows, offset, lineno, skipped = mri.room_file_rows(path, offset=0, lineno=0)
    assert [r.text for r in rows] == ["first"]
    assert (offset, lineno, skipped) == (len(whole) + 1, 1, 0)

    with path.open("a", encoding="utf-8") as f:
        f.write(half[25:] + "\n")
    rows, offset2, lineno2, skipped = mri.room_file_rows(path, offset=offset, lineno=lineno)
    assert [(r.source_ref, r.text) for r in rows] == [("discord_1.jsonl:2", "second")]
    assert (offset2, lineno2, skipped) == (path.stat().st_size, 2, 0)


# --- the dedupe, end to end --------------------------------------------------

@pytest.mark.asyncio
async def test_each_line_is_in_the_record_once_from_its_one_source(brain, sessions, record):
    path = sessions / "discord_1.jsonl"
    owner_words = "should we paint the fence blue or green"
    her_words = "Blue would match the door."
    dana_words = "green, definitely green"
    to_dana = "Green has a vote, then."
    # The owner's line and her replies are in both places; Dana's only in the file.
    await _store(brain, user_id=ROBIN_APP, role="user", username="Robin",
                 content=owner_words, ts=T0)
    await _store(brain, user_id=ROBIN_APP, role="assistant", content=her_words, ts=T0 + 5)
    await _store(brain, user_id=DANA, role="assistant", content=to_dana, ts=T0 + 15)
    _write(path, [
        _user(owner_words, T0, _owner_speaker()),
        _palli(her_words, T0 + 5),
        _user(guest_content("Dana", dana_words), T0 + 10, _guest_speaker("Dana", DANA)),
        _palli(to_dana, T0 + 15),
    ])

    result = await _catch_up(brain, sessions, record)

    assert result["errors"] == []
    assert result["added"] == 4 and result["db_rows"] == 3 and result["file_rows"] == 1
    got = sorted((r[0], r[3], r[4], r[5]) for r in _rows(record))
    assert got == sorted([
        ("db", "owner", "Robin", owner_words),
        ("db", "palli", "PaLLi", her_words),
        ("db", "palli", "PaLLi", to_dana),
        ("room_file", "guest", "Dana", dana_words),
    ])
    assert _scalar(record, "SELECT COUNT(*) FROM record_fts") == 4
    assert _scalar(record, "SELECT COUNT(*) FROM record_vec") == 4


@pytest.mark.asyncio
async def test_the_dedupe_holds_for_a_pre_10_08_tagged_row(brain, sessions, record):
    path = sessions / "discord_1.jsonl"
    await _store(brain, user_id=ROBIN_DISCORD, role="user", username="robin",
                 content="who has the ferry times", ts=T0)
    _write(path, [
        _user("who has the ferry times", T0),                        # his, untagged
        _user(guest_content("Sam", "the ferry leaves at noon"), T0 + 4),  # Sam's, tagged
    ])

    result = await _catch_up(brain, sessions, record)

    assert result["errors"] == []
    assert [(r[0], r[3], r[4], r[5]) for r in _rows(record)] == [
        ("db", "owner", "robin", "who has the ferry times"),
        ("room_file", "guest", "Sam", "the ferry leaves at noon"),
    ]


# --- catch-up by watermark ---------------------------------------------------

@pytest.mark.asyncio
async def test_catch_up_twice_indexes_nothing_the_second_time(brain, sessions, record):
    path = sessions / "discord_1.jsonl"
    await _store(brain, user_id=ROBIN_APP, role="user", content="one", ts=T0)
    top = await _store(brain, user_id=ROBIN_APP, role="assistant", content="two", ts=T0 + 1)
    _write(path, [_user(guest_content("Dana", "three"), T0 + 2, _guest_speaker("Dana", DANA))])
    embed = FakeEmbed()

    first = await _catch_up(brain, sessions, record, embed)
    second = await _catch_up(brain, sessions, record, embed)

    assert first["added"] == 3 and first["embedded"] == 3 and first["errors"] == []
    assert second["added"] == 0 and second["embedded"] == 0 and second["errors"] == []
    assert second["vectors_missing"] == 0 and second["detached"] is False
    assert embed.batches == [3]
    assert len(_rows(record)) == 3
    conn = mri.connect(record)
    try:
        assert mri.get_state(conn, "db_last_id") == str(top)
    finally:
        conn.close()
    assert _mark(record, "discord_1.jsonl") == {
        "size": path.stat().st_size, "ino": path.stat().st_ino,
        "offset": path.stat().st_size, "lineno": 1}

    # Something new in each source: only that is read.
    await _store(brain, user_id=ROBIN_APP, role="user", content="four", ts=T0 + 3)
    _write(path, [_user(guest_content("Sam", "five"), T0 + 4, _guest_speaker("Sam", SAM))])
    third = await _catch_up(brain, sessions, record, embed)
    assert third["added"] == 2 and third["db_rows"] == 1 and third["file_rows"] == 1
    assert _texts(record) == ["one", "two", "three", "four", "five"]
    assert [r[1] for r in _rows(record)][-1] == "discord_1.jsonl:2"


@pytest.mark.asyncio
async def test_catch_up_leaves_a_half_line_for_next_time(brain, sessions, record):
    path = sessions / "discord_1.jsonl"
    whole = _msg_to_jsonl(_user(guest_content("Dana", "first"), T0, _guest_speaker("Dana", DANA)))
    half = _msg_to_jsonl(_user(guest_content("Sam", "second"), T0 + 1, _guest_speaker("Sam", SAM)))
    path.write_bytes((whole + "\n" + half[:30]).encode("utf-8"))
    before = path.read_bytes()

    one = await _catch_up(brain, sessions, record)
    assert one["file_rows"] == 1 and _texts(record) == ["first"]
    assert path.read_bytes() == before                      # never rewritten
    assert _mark(record, "discord_1.jsonl")["offset"] == len(whole) + 1

    with path.open("a", encoding="utf-8") as f:
        f.write(half[30:] + "\n")
    two = await _catch_up(brain, sessions, record)
    three = await _catch_up(brain, sessions, record)
    assert (two["file_rows"], three["file_rows"]) == (1, 0)
    assert [(r[1], r[5]) for r in _rows(record)] == [
        ("discord_1.jsonl:1", "first"), ("discord_1.jsonl:2", "second")]


@pytest.mark.asyncio
async def test_catch_up_skips_a_bad_line_and_never_writes_the_file(brain, sessions, record):
    path = sessions / "discord_1.jsonl"
    _write(path, [_user(guest_content("Dana", "before"), T0, _guest_speaker("Dana", DANA))])
    with path.open("a", encoding="utf-8") as f:
        f.write("{broken\n")
    _write(path, [_user(guest_content("Dana", "after"), T0 + 2, _guest_speaker("Dana", DANA))])
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns

    result = await _catch_up(brain, sessions, record)

    assert result["skipped_lines"] == 1 and result["file_rows"] == 2
    assert _texts(record) == ["before", "after"]
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime


@pytest.mark.asyncio
async def test_a_shrunk_file_is_read_again_whole(brain, sessions, record):
    path = sessions / "discord_1.jsonl"
    _write(path, [
        _user(guest_content("Dana", "the lighthouse tour is on Saturday"), T0,
              _guest_speaker("Dana", DANA)),
        _user(guest_content("Sam", "bring the binoculars"), T0 + 1, _guest_speaker("Sam", SAM)),
        _user(guest_content("Jo", "and a thermos of soup"), T0 + 2, _guest_speaker("Jo", 55)),
    ])
    await _catch_up(brain, sessions, record)
    assert len(_rows(record)) == 3
    ino = path.stat().st_ino

    # A channel reset rewrites the file in place, smaller.
    _write(path, [_user(guest_content("Alex", "new week"), T0 + 9, _guest_speaker("Alex", 66))],
           mode="w")
    assert path.stat().st_ino == ino

    result = await _catch_up(brain, sessions, record)

    assert result["reread_files"] == 1
    assert [(r[1], r[4], r[5]) for r in _rows(record)] == [
        ("discord_1.jsonl:1", "Alex", "new week")]
    # The words and the vectors of the old lines went with them.
    assert _scalar(record,
                   "SELECT COUNT(*) FROM record_fts WHERE record_fts MATCH 'lighthouse'") == 0
    assert _scalar(record, "SELECT COUNT(*) FROM record_fts") == 1
    assert _scalar(record, "SELECT COUNT(*) FROM record_vec") == 1
    assert _mark(record, "discord_1.jsonl")["offset"] == path.stat().st_size


@pytest.mark.asyncio
async def test_a_replaced_file_is_read_again_whole(brain, sessions, record):
    path = sessions / "discord_1.jsonl"
    _write(path, [_user(guest_content("Dana", "old words"), T0, _guest_speaker("Dana", DANA))])
    await _catch_up(brain, sessions, record)

    # A write-beside-and-replace (the session's own _write_atomically), bigger.
    tmp = sessions / "discord_1.jsonl.tmp"
    _write(tmp, [
        _user(guest_content("Dana", "kept words"), T0, _guest_speaker("Dana", DANA)),
        _user(guest_content("Sam", "more words"), T0 + 1, _guest_speaker("Sam", SAM)),
    ], mode="w")
    os.replace(tmp, path)

    result = await _catch_up(brain, sessions, record)

    assert result["reread_files"] == 1
    assert _texts(record) == ["kept words", "more words"]


@pytest.mark.asyncio
async def test_a_line_rewritten_under_the_offset_is_read_again_whole(brain, sessions, record):
    """Rewritten in place and grown past the old offset before the next look:
    the offset no longer starts a line, so the file is read again whole."""
    path = sessions / "discord_1.jsonl"
    _write(path, [_user(guest_content("Dana", "abc"), T0, _guest_speaker("Dana", DANA))])
    await _catch_up(brain, sessions, record)
    _write(path, [
        _user(guest_content("Sam", "a much longer first line than before"), T0 + 1,
              _guest_speaker("Sam", SAM)),
        _user(guest_content("Jo", "and another"), T0 + 2, _guest_speaker("Jo", 55)),
    ], mode="w")

    result = await _catch_up(brain, sessions, record)

    assert result["reread_files"] == 1 and result["skipped_lines"] == 0
    assert _texts(record) == ["a much longer first line than before", "and another"]


@pytest.mark.asyncio
async def test_a_reread_file_keeps_the_vectors_of_its_unchanged_lines(brain, sessions, record):
    path = sessions / "discord_1.jsonl"
    _write(path, [
        _user(guest_content("Dana", "the lighthouse tour is on Saturday"), T0,
              _guest_speaker("Dana", DANA)),
        _user(guest_content("Sam", "bring the binoculars"), T0 + 1, _guest_speaker("Sam", SAM)),
        _user(guest_content("Jo", "and a thermos of soup"), T0 + 2, _guest_speaker("Jo", 55)),
    ])
    embed = FakeEmbed()
    await _catch_up(brain, sessions, record, embed)
    kept_vec = _scalar(record, "SELECT v.vec FROM record r JOIN record_vec v ON v.id = r.id"
                               " WHERE r.text = 'bring the binoculars'")

    # Rewritten smaller: Sam's line moves up a line, unchanged; one line is new.
    _write(path, [
        _user(guest_content("Sam", "bring the binoculars"), T0 + 1, _guest_speaker("Sam", SAM)),
        _user(guest_content("Alex", "soup!"), T0 + 9, _guest_speaker("Alex", 66)),
    ], mode="w")
    result = await _catch_up(brain, sessions, record, embed)

    assert result["reread_files"] == 1
    assert embed.batches == [3, 1]                 # only the new line was embedded
    assert _texts(record) == ["bring the binoculars", "soup!"]
    assert _scalar(record, "SELECT v.vec FROM record r JOIN record_vec v ON v.id = r.id"
                           " WHERE r.text = 'bring the binoculars'") == kept_vec
    assert _scalar(record, "SELECT COUNT(*) FROM record_vec") == 2


@pytest.mark.asyncio
async def test_an_unknown_owner_leaves_the_database_for_later(brain, sessions, record,
                                                              monkeypatch):
    """Without the owner's ids nobody's line can be told apart, so the
    database waits (its watermark unmoved) rather than file his words as a
    guest's for good; the room files still come in."""
    async def _none() -> frozenset[int]:
        return frozenset()

    monkeypatch.setattr(mri, "_owner_ids", _none)
    await _store(brain, user_id=ROBIN_APP, role="user", content="mine", ts=T0)
    _write(sessions / "discord_1.jsonl",
           [_user(guest_content("Dana", "hers"), T0 + 1, _guest_speaker("Dana", DANA))])

    result = await _catch_up(brain, sessions, record)

    assert result["db_rows"] == 0 and result["file_rows"] == 1
    assert any(e.startswith("db:") for e in result["errors"])
    conn = mri.connect(record)
    try:
        assert mri.get_state(conn, "db_last_id") is None
    finally:
        conn.close()
    with pytest.raises(RuntimeError):
        await mri.build(brain=brain, sessions_root=sessions, embed=FakeEmbed(),
                        path=record, rebuild=True)


@pytest.mark.asyncio
async def test_catch_up_never_raises_into_the_turn(sessions, record):
    class BrokenBrain:
        async def get_connection(self):
            raise sqlite3.OperationalError("database is locked")

    _write(sessions / "discord_1.jsonl",
           [_user(guest_content("Dana", "still here"), T0, _guest_speaker("Dana", DANA))])
    result = await _catch_up(BrokenBrain(), sessions, record)
    assert result["errors"] == ["db: OperationalError (database is locked)"]
    assert result["file_rows"] == 1 and _texts(record) == ["still here"]


@pytest.mark.asyncio
async def test_a_failed_step_is_logged_as_a_warning_with_the_sqlite_message(
        sessions, record, caplog):
    class BrokenBrain:
        async def get_connection(self):
            raise sqlite3.OperationalError("database is locked")

    with caplog.at_level(logging.INFO, logger="ai.memory_reach_index"):
        await _catch_up(BrokenBrain(), sessions, record)
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "database is locked" in warned[0].getMessage()


@pytest.mark.asyncio
async def test_an_unknown_owner_is_noted_once_per_process(brain, sessions, record,
                                                          monkeypatch, caplog):
    async def _none() -> frozenset[int]:
        return frozenset()

    monkeypatch.setattr(mri, "_owner_ids", _none)
    with caplog.at_level(logging.INFO, logger="ai.memory_reach_index"):
        for _ in range(3):
            result = await _catch_up(brain, sessions, record)
            assert result["errors"] == ["db: owner unknown"]
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warned) == 1 and "owner" in warned[0].getMessage()


@pytest.mark.asyncio
async def test_catch_up_stops_at_its_budget_and_the_rest_follows(brain, sessions, record,
                                                                 monkeypatch):
    """A backlog bigger than one page, and room files, with a tiny budget:
    the catch-up stops between pages and before the files, and what is
    left follows from the watermarks (the detached task, the next look)."""
    monkeypatch.setattr(mri, "_DB_PAGE", 5)
    _store_many(brain, [("user", f"backlog line {i}", T0 + i) for i in range(20)])
    for room in (1, 2, 3):
        _write(sessions / f"discord_{room}.jsonl",
               [_user(guest_content("Dana", f"room {room}"), T0, _guest_speaker("Dana", DANA))])
    slow = SlowBrain(brain)
    loop = asyncio.get_running_loop()
    # One-offs the live brain and record pay once, paid before the catch-up
    # (T5): the tmp brain's first connection builds its schema, the record's
    # first open makes the file (each up to a second on a loaded machine,
    # which left no budget for the pages this test is about).
    warm = await brain.get_connection()
    await warm.close()
    mri.connect(record).close()
    deadlines: list[float | None] = []
    real_ingest_db = mri._ingest_db

    async def _ingest_db(*args, **kwargs):
        deadlines.append(kwargs.get("deadline"))
        return await real_ingest_db(*args, **kwargs)

    monkeypatch.setattr(mri, "_ingest_db", _ingest_db)

    started = loop.time()
    first = await mri.catch_up(brain=slow, sessions_root=sessions, embed=FakeEmbed(),
                               budget_s=0.12, path=record)
    took = loop.time() - started
    pages = list(slow.opened)          # the catch-up's own; the detached task's come later

    # Stopped at its budget, by behaviour rather than the wall clock (T5: one
    # page's commit stalled up to 2.8 s on a loaded disk, and a page under way
    # is never cut): no page began after the catch-up's own deadline, the
    # files were left, and the backlog was not read to its end.
    assert len(deadlines) == 1 and deadlines[0] is not None
    assert all(t < deadlines[0] for t in pages)
    assert took < 10.0                             # a hang guard only
    assert first["complete"] is False and first["detached"] is True
    assert first["db_rows"] < 20 and first["file_rows"] == 0
    assert first["embedded"] == 0                  # too little budget left to try
    second = await _catch_up(brain, sessions, record)
    await _drain_backlog()
    assert second["complete"] is True
    assert len(_rows(record)) == 23
    assert _scalar(record, "SELECT COUNT(*) FROM record_vec") == 23
    third = await _catch_up(brain, sessions, record)
    assert third["added"] == 0 and third["complete"] is True and third["detached"] is False


@pytest.mark.asyncio
async def test_a_detached_chunk_always_moves_on(brain, sessions, record, monkeypatch):
    """However late a chunk starts, it reads one page or one file, so the
    detached catch-up can never spin in place."""
    monkeypatch.setattr(mri, "_DB_PAGE", 5)
    _store_many(brain, [("user", f"line {i}", T0 + i) for i in range(12)])
    for room in (1, 2, 3):
        _write(sessions / f"discord_{room}.jsonl",
               [_user(guest_content("Dana", f"room {room}"), T0, _guest_speaker("Dana", DANA))])
    past = time.monotonic() - 1.0
    owners = frozenset({ROBIN_APP})

    assert await mri._ingest_db(record, brain, owners, deadline=past) == (0, False)
    assert await mri._ingest_db(record, brain, owners, deadline=past,
                                at_least_one=True) == (5, False)
    assert mri._ingest_files(record, sessions, past) == (0, 0, 0, False)
    # Late every time: each call still reads the first file with new lines,
    # walking past the files already caught up.
    assert mri._ingest_files(record, sessions, past, at_least_one=True) == (1, 0, 0, False)
    assert mri._ingest_files(record, sessions, past, at_least_one=True) == (1, 0, 0, False)
    assert mri._ingest_files(record, sessions, past, at_least_one=True) == (1, 0, 0, True)
    assert mri._ingest_files(record, sessions, past, at_least_one=True) == (0, 0, 0, True)


@pytest.mark.asyncio
async def test_the_detached_catch_up_finishes_even_with_no_time_per_chunk(
        brain, sessions, record, monkeypatch):
    """A chunk's time starts inside its worker thread, and every chunk moves
    on, so the sources are read to the end however short the chunks."""
    monkeypatch.setattr(mri, "_DB_PAGE", 5)
    monkeypatch.setattr(mri, "_CHUNK_S", 0.0)
    _store_many(brain, [("user", f"line {i}", T0 + i) for i in range(12)])
    for room in (1, 2, 3, 4):
        _write(sessions / f"discord_{room}.jsonl",
               [_user(guest_content("Dana", f"room {room}"), T0, _guest_speaker("Dana", DANA))])

    totals = await asyncio.wait_for(
        mri._ingest_chunks(record, brain, frozenset({ROBIN_APP}), sessions), timeout=10)

    assert (totals["db_rows"], totals["file_rows"]) == (12, 4)
    assert len(_rows(record)) == 16


# --- meaning: inline, then detached ------------------------------------------

@pytest.mark.asyncio
async def test_catch_up_embeds_fifty_inline_and_the_rest_in_a_detached_task(
        brain, sessions, record):
    _store_many(brain, [("user", f"line number {i}", T0 + i) for i in range(60)])
    embed = FakeEmbed()

    result = await _catch_up(brain, sessions, record, embed)

    assert result["added"] == 60 and result["embedded"] == 50
    assert result["vectors_missing"] == 10 and result["detached"] is True
    assert embed.batches == [50]
    await _drain_backlog()
    assert embed.batches == [50, 10]
    assert _scalar(record, "SELECT COUNT(*) FROM record_vec") == 60


@pytest.mark.asyncio
async def test_a_slow_embedder_never_holds_the_turn_past_its_budget(brain, sessions, record):
    for i in range(3):
        await _store(brain, user_id=ROBIN_APP, role="user", content=f"slow {i}", ts=T0 + i)
    # The words first, so the budget below is the embedder's alone: on a
    # loaded machine the reading could spend all of it (T5).
    await mri._ingest_chunks(record, brain, frozenset({ROBIN_APP}), sessions)
    gate = asyncio.Event()                  # the embedder finishes only when let go
    embed = FakeEmbed(gate)
    loop = asyncio.get_running_loop()

    started = loop.time()
    result = await asyncio.wait_for(
        mri.catch_up(brain=brain, sessions_root=sessions, embed=embed, budget_s=1.0,
                     path=record), timeout=15)
    took = loop.time() - started

    # The embedder never answers here, so coming back at all means the turn
    # gave up on it; the bound says it gave up by the budget, not the 120 s
    # detached cap, with room for a loaded machine (T5: 2 s -> 6 s; the
    # guard above stays far under 120 s).
    assert took < 6.0
    assert result["added"] == 0 and result["embedded"] == 0 and result["errors"] == []
    assert result["vectors_missing"] == 3 and result["detached"] is True
    gate.set()
    await _drain_backlog()
    # The detached task made them; an inline try came first if the reading
    # left any of the budget.
    assert embed.batches in ([3], [3, 3])
    assert _scalar(record, "SELECT COUNT(*) FROM record_vec") == 3


@pytest.mark.asyncio
async def test_local_embeddings_off_means_words_only(brain, sessions, record, monkeypatch):
    from core.memory_service import memory_workspace_service

    loads: list[str] = []

    async def _load(name):
        loads.append(name)
        return None

    monkeypatch.setattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", False)
    monkeypatch.setattr(memory_workspace_service, "_ensure_local_model", _load)
    await _store(brain, user_id=ROBIN_APP, role="user", content="words only", ts=T0)

    result = await mri.catch_up(brain=brain, sessions_root=sessions, budget_s=ROOMY,
                                path=record)

    assert result["added"] == 1 and result["embedded"] == 0
    assert result["errors"] == [] and result["detached"] is False
    assert result["meaning"] == "off" and result["vectors_missing"] == 1
    built = await mri.build(brain=brain, sessions_root=sessions, path=record, rebuild=True)
    assert built["rows"] == 1 and built["vectors_missing"] == 1 and built["embedded"] == 0
    assert "embed_error" not in built
    assert loads == []                              # the model is never touched


@pytest.mark.asyncio
async def test_a_failed_embedder_backs_off_instead_of_retrying_every_search(
        brain, sessions, record):
    broken = BrokenEmbed()
    await _store(brain, user_id=ROBIN_APP, role="user", content="one", ts=T0)

    first = await _catch_up(brain, sessions, record, broken)
    assert broken.calls == 1 and first["errors"] == ["embed: RuntimeError"]
    assert first["detached"] is False

    await _store(brain, user_id=ROBIN_APP, role="user", content="two", ts=T0 + 1)
    second = await _catch_up(brain, sessions, record, broken)
    assert broken.calls == 1                        # no retry while backing off
    assert second["added"] == 1 and second["errors"] == []
    assert second["meaning"] == "backing off" and second["detached"] is False

    mri._EMBED_PAUSE["until"] = 0.0                 # the minutes have passed
    third = await _catch_up(brain, sessions, record, FakeEmbed())
    assert third["embedded"] == 2 and third["vectors_missing"] == 0


@pytest.mark.asyncio
async def test_the_record_lock_is_free_while_the_model_works(brain, sessions, record):
    await _store(brain, user_id=ROBIN_APP, role="user", content="first", ts=T0)
    gate = asyncio.Event()
    held = FakeEmbed(gate)
    working = asyncio.create_task(_catch_up(brain, sessions, record, held))
    await asyncio.wait_for(held.called.wait(), timeout=5)

    assert not mri._lock().locked()
    await _store(brain, user_id=ROBIN_APP, role="user", content="second", ts=T0 + 1)
    other = await mri.catch_up(brain=brain, sessions_root=sessions, embed=FakeEmbed(),
                               budget_s=0.3, path=record)
    assert other["busy"] is False and other["added"] == 1

    gate.set()
    await working
    await _drain_backlog()
    assert _scalar(record, "SELECT COUNT(*) FROM record_vec") == 2


def test_each_event_loop_keeps_its_own_lock():
    async def _one():
        return mri._lock()

    loop_a, loop_b = asyncio.new_event_loop(), asyncio.new_event_loop()
    try:
        a1 = loop_a.run_until_complete(_one())
        b1 = loop_b.run_until_complete(_one())
        a2 = loop_a.run_until_complete(_one())
    finally:
        loop_a.close()
        loop_b.close()
    assert a1 is a2 and a1 is not b1


@pytest.mark.asyncio
async def test_a_detached_embed_that_never_returns_is_given_up(brain, sessions, record,
                                                               monkeypatch, caplog):
    monkeypatch.setattr(mri, "_DETACHED_EMBED_S", 0.2)
    await _store(brain, user_id=ROBIN_APP, role="user", content="stuck", ts=T0)
    stuck = FakeEmbed(asyncio.Event())             # never let go

    with caplog.at_level(logging.INFO, logger="ai.memory_reach_index"):
        # Too little budget for an inline try: the detached task embeds.
        result = await mri.catch_up(brain=brain, sessions_root=sessions, embed=stuck,
                                    budget_s=0.3, path=record)
        assert result["detached"] is True
        await asyncio.wait_for(_drain_backlog(), timeout=3)

    assert stuck.batches == [1]
    assert not mri._IN_FLIGHT.get(str(record))     # the row is free again
    assert not mri._running(record)
    assert mri._EMBED_PAUSE["until"] > time.monotonic()
    assert any(r.levelno == logging.WARNING and "TimeoutError" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_a_spent_budget_still_takes_a_free_lock(brain, sessions, record):
    await _store(brain, user_id=ROBIN_APP, role="user", content="later", ts=T0)
    result = await mri.catch_up(brain=brain, sessions_root=sessions, embed=FakeEmbed(),
                                budget_s=0.0, path=record)
    assert result["busy"] is False and result["complete"] is False
    assert result["detached"] is True
    await _drain_backlog()
    assert _texts(record) == ["later"]


def test_a_vector_is_written_only_for_the_text_it_was_made_from(record):
    """The rebuild race: an id read for embedding, then its row deleted and
    the id reused by another line before the vector is written. The vector
    must not be written for the new line."""
    conn = mri.connect(record)
    try:
        mri.add_rows(conn, [mri.Row(source="room_file", source_ref="discord_1.jsonl:1",
                                    room="discord:1", ts=T0, day=_day(T0), speaker="guest",
                                    name="Dana", text="old line")], vectors=None)
        (rid, text), = mri.pending_vectors(conn, 10)
        with conn:
            conn.execute("DELETE FROM record WHERE id = ?", (rid,))
        mri.add_rows(conn, [mri.Row(source="room_file", source_ref="discord_1.jsonl:1",
                                    room="discord:1", ts=T0, day=_day(T0), speaker="guest",
                                    name="Sam", text="new line")], vectors=None)
        assert mri.pending_vectors(conn, 10)[0][0] == rid          # the id came back
        put = mri._put_vectors_checked(conn, [(rid, text, mri.encode_vector([1.0, 2.0]))])
        assert put == 0
        assert mri.pending_vectors(conn, 10) == [(rid, "new line")]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_the_default_embedder_uses_the_loaded_model_off_the_loop(monkeypatch):
    import threading

    from core.memory_service import memory_workspace_service

    calls: list[tuple] = []
    loop_thread = threading.get_ident()

    class FakeModel:
        def encode(self, texts, batch_size=32, normalize_embeddings=False):
            calls.append((list(texts), batch_size, normalize_embeddings,
                          threading.get_ident() != loop_thread))
            return [[0.5, 0.25] for _ in texts]

    async def _loaded(name):
        return FakeModel()

    monkeypatch.setattr(memory_workspace_service, "_ensure_local_model", _loaded)
    assert await mri._default_embed(["a", "b"]) == [[0.5, 0.25], [0.5, 0.25]]
    assert calls == [(["a", "b"], 64, True, True)]

    async def _missing(name):
        return None

    monkeypatch.setattr(memory_workspace_service, "_ensure_local_model", _missing)
    with pytest.raises(RuntimeError):
        await mri._default_embed(["a"])


# --- the one-time build ------------------------------------------------------

@pytest.mark.asyncio
async def test_build_fills_the_record_words_and_meaning(brain, sessions, record):
    _store_many(brain, [("user" if i % 2 == 0 else "assistant", f"message {i}", T0 + i * 86400)
                        for i in range(70)])
    _write(sessions / "discord_1.jsonl",
           [_user(guest_content("Dana", "a guest's line"), T0, _guest_speaker("Dana", DANA))])
    lines: list[str] = []
    embed = FakeEmbed()

    result = await mri.build(brain=brain, sessions_root=sessions, embed=embed, path=record,
                             progress=lines.append)

    assert result["rows"] == 71 and result["vectors_missing"] == 0
    assert result["db_rows"] == 70 and result["file_rows"] == 1
    assert result["first_day"] == _day(T0) and result["last_day"] == _day(T0 + 69 * 86400)
    assert result["rooms"] == 1 and result["seconds"] >= 0
    assert sum(embed.batches) == 71 and max(embed.batches) <= 64
    assert lines and all(isinstance(line, str) for line in lines)
    assert not mri._BACKLOG
    # Built once, then a catch-up finds nothing new.
    again = await _catch_up(brain, sessions, record, embed)
    assert again["added"] == 0


@pytest.mark.asyncio
async def test_build_rebuild_writes_beside_and_replaces_the_file_atomically(
        brain, sessions, record, monkeypatch):
    top = await _store(brain, user_id=ROBIN_APP, role="user", content="a real line", ts=T0)
    _write(sessions / "discord_1.jsonl",
           [_user(guest_content("Dana", "a guest's line"), T0, _guest_speaker("Dana", DANA))])
    # The record as it stands: a line no source holds and a watermark past it.
    conn = mri.connect(record)
    try:
        mri.add_rows(conn, [mri.Row(source="db", source_ref="db:999", room="discord:1", ts=T0,
                                    day=_day(T0), speaker="owner", name="Robin",
                                    text="a stale line")], vectors=None)
        mri.set_state(conn, "db_last_id", "999")
    finally:
        conn.close()
    building = record.with_name(record.name + ".building")
    building.write_bytes(b"left over from a build that died")
    old_ino = record.stat().st_ino
    seen: list[tuple] = []
    real_replace = os.replace

    def _replace(src, dst):
        src, dst = Path(src), Path(dst)
        # The old record is whole until the very moment it is replaced.
        assert _texts(dst) == ["a stale line"]
        header = src.read_bytes()[:100]
        seen.append((src, dst, header[18], header[19],
                     Path(str(src) + "-wal").exists(), Path(str(src) + "-shm").exists()))
        real_replace(src, dst)

    monkeypatch.setattr(mri.os, "replace", _replace)

    result = await mri.build(brain=brain, sessions_root=sessions, embed=FakeEmbed(),
                             path=record, rebuild=True)

    # Written beside, in rollback-journal mode (header bytes 18/19 = 1), no WAL left.
    assert seen == [(building, record, 1, 1, False, False)]
    assert not building.exists()
    assert record.stat().st_ino != old_ino
    assert sorted(_texts(record)) == ["a guest's line", "a real line"]
    assert result["rows"] == 2 and result["vectors_missing"] == 0
    conn = mri.connect(record)
    try:
        assert mri.get_state(conn, "db_last_id") == str(top)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_a_rebuild_is_not_undone_by_the_old_records_leftover_wal(brain, sessions, record):
    """The bot died with lines in the old record's -wal. A rebuild must not
    let SQLite replay them into the new file."""
    await _store(brain, user_id=ROBIN_APP, role="user", content="a real line", ts=T0)
    conn = mri.connect(record)
    try:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")      # the schema into the file
        mri.add_rows(conn, [mri.Row(source="db", source_ref="db:999", room="discord:1", ts=T0,
                                    day=_day(T0), speaker="owner", name="Robin",
                                    text="a stale line")], vectors=None)
        crashed = {s: Path(f"{record}{s}").read_bytes() for s in ("", "-wal", "-shm")}
    finally:
        conn.close()
    for suffix, data in crashed.items():                     # as the crash left it
        Path(f"{record}{suffix}").write_bytes(data)
    assert Path(f"{record}-wal").stat().st_size > 0

    await mri.build(brain=brain, sessions_root=sessions, embed=FakeEmbed(), path=record,
                    rebuild=True)

    assert _texts(record) == ["a real line"]
    assert not Path(f"{record}-wal").exists() and not Path(f"{record}-shm").exists()


def _crash_with_a_stale_line_in_the_wal(record: Path) -> None:
    """The record as a crash leaves it: a line in the -wal, not yet in the
    file."""
    conn = mri.connect(record)
    try:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")      # the schema into the file
        mri.add_rows(conn, [mri.Row(source="db", source_ref="db:999", room="discord:1", ts=T0,
                                    day=_day(T0), speaker="owner", name="Robin",
                                    text="a stale line")], vectors=None)
        crashed = {s: Path(f"{record}{s}").read_bytes() for s in ("", "-wal", "-shm")}
    finally:
        conn.close()
    for suffix, data in crashed.items():
        Path(f"{record}{suffix}").write_bytes(data)
    assert Path(f"{record}-wal").stat().st_size > 0


@pytest.mark.asyncio
async def test_a_rebuild_clears_a_leftover_wal_whose_record_is_gone(brain, sessions, record):
    await _store(brain, user_id=ROBIN_APP, role="user", content="a real line", ts=T0)
    _crash_with_a_stale_line_in_the_wal(record)
    record.unlink()                                          # the file went, its -wal stayed

    await mri.build(brain=brain, sessions_root=sessions, embed=FakeEmbed(), path=record,
                    rebuild=True)

    assert _texts(record) == ["a real line"]
    assert not Path(f"{record}-wal").exists() and not Path(f"{record}-shm").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("reading", [False, True], ids=["idle", "reading"])
async def test_a_rebuild_refuses_while_the_record_is_open_elsewhere(
        brain, sessions, record, monkeypatch, caplog, reading):
    """Another connection on the old record (PaLLi's own, say): the new file
    is not put in place under it. Idle, it leaves the -wal behind our close;
    reading, the checkpoint reports busy."""
    monkeypatch.setattr(mri, "_SETTLE_WAIT_S", 0.01)
    await _store(brain, user_id=ROBIN_APP, role="user", content="a real line", ts=T0)
    conn = mri.connect(record)
    mri.add_rows(conn, [mri.Row(source="db", source_ref="db:999", room="discord:1", ts=T0,
                                day=_day(T0), speaker="owner", name="Robin",
                                text="the old line")], vectors=None)
    if reading:
        conn.execute("BEGIN")
        conn.execute("SELECT COUNT(*) FROM record").fetchone()
    old_ino = record.stat().st_ino
    try:
        with caplog.at_level(logging.INFO, logger="ai.memory_reach_index"):
            with pytest.raises(RuntimeError, match="open elsewhere"):
                await mri.build(brain=brain, sessions_root=sessions, embed=FakeEmbed(),
                                path=record, rebuild=True)
    finally:
        conn.close()

    assert record.stat().st_ino == old_ino                   # the old record stands
    assert _texts(record) == ["the old line"]
    assert not record.with_name(record.name + ".building").exists()
    assert any(r.levelno == logging.WARNING and "open elsewhere" in r.getMessage()
               for r in caplog.records)


# --- the model that made the meaning (R15, T5) -------------------------------

class OtherEmbed(FakeEmbed):
    """Another model: other vectors for the same texts."""

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        await super().__call__(texts)
        return [[0.0, float(len(t)), 2.0] for t in texts]


def _vectors(record: Path) -> list[bytes]:
    conn = sqlite3.connect(str(record))
    try:
        return [v for (v,) in conn.execute("SELECT vec FROM record_vec ORDER BY id")]
    finally:
        conn.close()


def _model(record: Path) -> str | None:
    conn = mri.connect(record)
    try:
        return mri.get_state(conn, mri.EMBED_MODEL_KEY)
    finally:
        conn.close()


@pytest.fixture
def model_name(monkeypatch):
    def _set(name: str) -> None:
        monkeypatch.setattr(config, "MEMORY_LOCAL_EMBEDDINGS_MODEL", name, raising=False)

    _set("model-a")
    return _set


def test_the_model_name_is_the_one_memory_reads(model_name, monkeypatch):
    assert mri.embed_model_name() == "model-a"
    model_name("  model-b ")
    assert mri.embed_model_name() == "model-b"
    model_name("")
    assert mri.embed_model_name() == "all-MiniLM-L6-v2"


@pytest.mark.asyncio
async def test_build_names_the_model_that_made_the_meaning(brain, sessions, record, model_name):
    _store_many(brain, [("user", f"message {i}", T0 + i) for i in range(5)])

    result = await mri.build(brain=brain, sessions_root=sessions, embed=FakeEmbed(), path=record)

    assert _model(record) == "model-a" and result["embed_model"] == "model-a"


@pytest.mark.asyncio
async def test_words_only_names_no_model(brain, sessions, record, model_name, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", False, raising=False)
    _store_many(brain, [("user", f"message {i}", T0 + i) for i in range(5)])

    result = await mri.build(brain=brain, sessions_root=sessions, path=record)

    assert result["vectors_missing"] == 5 and result["embed_model"] is None
    assert _model(record) is None


@pytest.mark.asyncio
async def test_a_build_after_the_model_changed_embeds_every_line_again(
        brain, sessions, record, model_name):
    _store_many(brain, [("user", f"message {i}", T0 + i) for i in range(70)])
    await mri.build(brain=brain, sessions_root=sessions, embed=FakeEmbed(), path=record)
    before = _vectors(record)
    model_name("model-b")
    lines: list[str] = []
    other = OtherEmbed()

    result = await mri.build(brain=brain, sessions_root=sessions, embed=other, path=record,
                             progress=lines.append)

    assert sum(other.batches) == 70                          # every line, not just new ones
    after = _vectors(record)
    assert len(after) == 70 and not set(after) & set(before)
    assert _model(record) == "model-b" and result["embed_model"] == "model-b"
    assert result["vectors_missing"] == 0 and result["embedded"] == 70
    assert any("model-a" in line and "model-b" in line for line in lines)


@pytest.mark.asyncio
async def test_the_same_model_embeds_nothing_again(brain, sessions, record, model_name):
    _store_many(brain, [("user", f"message {i}", T0 + i) for i in range(5)])
    await mri.build(brain=brain, sessions_root=sessions, embed=FakeEmbed(), path=record)
    again = FakeEmbed()

    result = await mri.build(brain=brain, sessions_root=sessions, embed=again, path=record)

    assert again.batches == [] and result["embedded"] == 0


@pytest.mark.asyncio
async def test_a_catch_up_after_the_model_changed_makes_the_meaning_again(
        brain, sessions, record, model_name, caplog):
    _store_many(brain, [("user", f"line {i}", T0 + i) for i in range(10)])
    await _catch_up(brain, sessions, record)
    await _drain_backlog()
    before = _vectors(record)
    assert len(before) == 10 and _model(record) == "model-a"
    model_name("model-b")
    other = OtherEmbed()

    with caplog.at_level(logging.INFO, logger="ai.memory_reach_index"):
        await _catch_up(brain, sessions, record, other)
        await _drain_backlog()

    after = _vectors(record)
    assert len(after) == 10 and not set(after) & set(before)
    assert _model(record) == "model-b" and sum(other.batches) == 10
    assert any(r.levelno == logging.WARNING and "model-a" in r.getMessage()
               and "model-b" in r.getMessage() for r in caplog.records)


def test_a_vector_from_a_model_the_record_no_longer_names_is_not_written(record, model_name):
    conn = mri.connect(record)
    try:
        mri.add_rows(conn, [mri.Row(source="db", source_ref="db:1", room="discord:1", ts=T0,
                                    day=_day(T0), speaker="owner", name="Robin",
                                    text="a line")], vectors=None)
        mri.set_state(conn, mri.EMBED_MODEL_KEY, "model-b")
        (rid, text), = mri.pending_vectors(conn, 10)

        stale = mri._put_vectors_checked(conn, [(rid, text, mri.encode_vector([1.0]))],
                                         model="model-a")
        fresh = mri._put_vectors_checked(conn, [(rid, text, mri.encode_vector([2.0]))],
                                         model="model-b")

        assert (stale, fresh) == (0, 1)
        assert mri.get_state(conn, mri.EMBED_MODEL_KEY) == "model-b"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_a_refused_rebuild_is_its_own_error(brain, sessions, record, monkeypatch):
    """The ctl tells "the record is in use" from every other failure by type."""
    monkeypatch.setattr(mri, "_SETTLE_WAIT_S", 0.01)
    await _store(brain, user_id=ROBIN_APP, role="user", content="a real line", ts=T0)
    held = mri.connect(record)
    try:
        with pytest.raises(mri.RecordInUse):
            await mri.build(brain=brain, sessions_root=sessions, embed=FakeEmbed(),
                            path=record, rebuild=True)
    finally:
        held.close()
    assert issubclass(mri.RecordInUse, RuntimeError)


# --- spoken rows keep their own room (W2, fix round 1) ---------------------
# A Discord voice channel has a text chat with the same channel id. The voice
# runtime files the owner's spoken words as platform "discord" with that id,
# and PaLLi's spoken replies follow them there with no mark of their own. A
# friend typing in that text chat is scoped to discord:<id>, so spoken rows
# get a room of their own, discord_voice:<id>: never a chat room's key, still
# inside the owner's every-room scope.

VOICE_CH = 4040


async def _store_marked(brain, *, user_id, role, content, ts, metadata=None,
                        channel_id=VOICE_CH, username=None) -> int:
    store = ConversationHistoryStore(brain, now=lambda: ts)
    return await store.store_conversation(
        user_id=user_id, platform="discord", channel_id=channel_id, thread_id=None,
        role=role, content=content, username=username, metadata=metadata)


async def _voice_channel_rows(brain) -> dict[str, int]:
    """The owner speaks in the voice channel and PaLLi answers aloud; then the
    owner and PaLLi trade lines in that channel's text chat."""
    return {
        # The voice runtime's own row of his words (voice_session).
        "spoken": await _store_marked(
            brain, user_id=ROBIN_DISCORD, role="user", ts=T0, username="Robin",
            content="the spare key is under the blue pot",
            metadata={"username": "Robin", "voice_message": True, "voice_session": True}),
        # Dispatch's row of the same turn (core.turn.persistence: its surface).
        "spoken_turn": await _store_marked(
            brain, user_id=ROBIN_APP, role="user", ts=T0 + 1, username="Robin",
            content="and the alarm code is the old one",
            metadata={"surface": "discord_voice"}),
        # Her spoken reply: no mark of its own.
        "said_aloud": await _store_marked(
            brain, user_id=ROBIN_APP, role="assistant", ts=T0 + 5,
            content="Got it: the blue pot, and the old alarm code."),
        # His typed line in the voice channel's text chat (a chat row).
        "typed": await _store_marked(
            brain, user_id=ROBIN_APP, role="user", ts=T0 + 60, username="Robin",
            content="pizza in the voice chat later?", metadata={"is_dm": False}),
        "typed_reply": await _store_marked(
            brain, user_id=ROBIN_APP, role="assistant", ts=T0 + 65,
            content="Pizza sounds good."),
    }


def test_a_spoken_row_is_told_by_the_marks_the_proactive_pick_skips():
    assert mri.is_spoken({"voice_session": True}) and mri.is_spoken('{"voice_session": true}')
    assert mri.is_spoken({"surface": "discord_voice"}) and mri.is_spoken({"surface": "voice_local"})
    for not_spoken in (None, "", "not json", "[1]", {"is_dm": False}, {"surface": "discord_text"},
                       {"voice_message": True}, {"voice_session": False}):
        assert not mri.is_spoken(not_spoken), not_spoken
    assert mri.room_key_for_db("discord", VOICE_CH, ROBIN_APP, spoken=True) == (
        f"discord_voice:{VOICE_CH}")
    assert mri.room_key_for_db("discord", VOICE_CH, ROBIN_APP) == f"discord:{VOICE_CH}"


@pytest.mark.asyncio
async def test_spoken_rows_and_her_spoken_replies_get_the_voice_room(brain):
    ids = await _voice_channel_rows(brain)
    rows, _ = await mri.db_rows_since(
        brain, last_id=0, owner_ids=frozenset({ROBIN_APP, ROBIN_DISCORD}))

    room_of = {r.source_ref: r.room for r in rows}
    voice, text = f"discord_voice:{VOICE_CH}", f"discord:{VOICE_CH}"
    assert room_of == {f"db:{ids['spoken']}": voice, f"db:{ids['spoken_turn']}": voice,
                       f"db:{ids['said_aloud']}": voice,
                       f"db:{ids['typed']}": text, f"db:{ids['typed_reply']}": text}


@pytest.mark.asyncio
async def test_a_friend_in_the_voice_channels_text_chat_never_reaches_a_spoken_row(
        brain, sessions, record, monkeypatch):
    from ai import memory_reach_search as mrs
    from core.turn.types import Turn, TurnMetadata

    await _voice_channel_rows(brain)
    await _catch_up(brain, sessions, record)
    monkeypatch.setattr(mri, "embedder_for", lambda embed=None: None)     # by words

    friend = Turn(surface="discord_text", owner_id=DANA, content="[Dana]: where's the spare key?",
                  metadata=TurnMetadata(origin="guest", channel_id=VOICE_CH),
                  received_at=datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc))
    scope = mrs.scope_for_turn(friend)
    assert scope == mrs.Scope(f"discord:{VOICE_CH}")
    for query in ("spare key blue pot", "alarm code", "blue pot old alarm code"):
        assert await mrs.search(query, scope=scope, path=record) == []
    assert {h.text for h in await mrs.search("pizza", scope=scope, path=record)} == {
        "pizza in the voice chat later?", "Pizza sounds good."}

    async def no_catch_up(**_kw):
        return {}

    monkeypatch.setattr(mri, "catch_up", no_catch_up)
    assert await mrs.room_section_for(friend, text=friend.content, shown_since_ts=None,
                                      path=record) == ""

    # The owner's every-room search still finds what he said aloud.
    everywhere = await mrs.search("spare key blue pot", scope=mrs.Scope(None), path=record)
    assert everywhere and everywhere[0].text == "the spare key is under the blue pot"
    assert everywhere[0].room == f"discord_voice:{VOICE_CH}"
    assert mrs.room_label(everywhere[0].room) == f"the Discord voice channel {VOICE_CH}"


def test_voice_sessions_write_no_room_file_the_record_reads():
    """Spoken turns keep their own session files (``discord_voice:<owner>``,
    ``voice:<owner>``), which the record's room-file pattern never matches."""
    from core.agent.session import _sanitize_session_key

    for surface in ("discord_voice", "voice_local"):
        key = session_key_for(surface, VOICE_CH, ROBIN_APP)
        assert not mri.TRANSCRIPT_RE.match(f"{_sanitize_session_key(key)}.jsonl"), key

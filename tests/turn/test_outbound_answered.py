"""Was a reach-out answered? (Batch 6, task 6; item 3.9 of the fix plan.)

Her own reach-outs, the arbiter's say and a workbench share, are written to a
small append-only book, ``data/reachouts/reachouts.jsonl``, folded on read
like the fears book. The owner's next message on the SAME door (platform and
channel both) inside six hours marks every reach-out still unanswered there.
Nothing else she sends is a reach-out: her hands' sends, forge follow-ups and
mid-task speech write nothing, and reminders never pass this module at all.

The book belongs to the arbiter: while the arbiter is off nothing is written
and nothing is marked. The file work runs off the event loop (DEFECT §9).

The default test world is arbiter-OFF (tests/conftest.py); tests that need
the book open turn it on through ``ai.arbiter._companion``.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import time
import types
from datetime import datetime, timedelta, timezone

import pytest

import ai.arbiter as arbiter
from core.turn import outbound
from core.turn.types import Turn, TurnMetadata


def _arbiter_on(monkeypatch):
    comp = types.SimpleNamespace(arbiter=True, present_moment=True)
    monkeypatch.setattr(arbiter, "_companion", lambda: comp)
    return comp


def _events():
    path = outbound._reachouts_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _row(rid):
    return next(r for r in outbound.read_reachouts() if r["id"] == rid)


@pytest.fixture
def outbound_store(monkeypatch, tmp_path):
    """The brief's store, built around the real book on disk."""
    monkeypatch.setattr(outbound, "REACHOUTS_DIR", tmp_path / "reachouts")

    class _Store:
        async def record(self, *, owner_id, platform, channel_id, text, origin,
                         at, medium="text"):
            return await asyncio.to_thread(
                outbound.record_reachout, owner_id=owner_id, platform=platform,
                channel_id=channel_id, text=text, origin=origin, at=at,
                medium=medium,
            )

        async def mark_answered(self, *, owner_id, platform, channel_id, at):
            return await asyncio.to_thread(
                outbound.mark_answered, owner_id=owner_id, platform=platform,
                channel_id=channel_id, at=at,
            )

        @property
        def rows(self):
            return outbound.read_reachouts()

    return _Store()


# ── the book: same door, inside six hours ────────────────────────────────

@pytest.mark.asyncio
async def test_a_reply_on_the_same_door_marks_the_reachout_answered(outbound_store):
    await outbound_store.record(owner_id=1, platform="discord", channel_id=7, text="hey", origin="arbiter", at=0.0)
    await outbound_store.mark_answered(owner_id=1, platform="discord", channel_id=7, at=600.0)
    assert outbound_store.rows[-1]["answered_at"] == 600.0
    await outbound_store.record(owner_id=1, platform="discord", channel_id=7, text="hey again", origin="arbiter", at=1000.0)
    await outbound_store.mark_answered(owner_id=1, platform="telegram", channel_id=7, at=1100.0)   # a different door
    assert outbound_store.rows[-1]["answered_at"] is None


@pytest.mark.asyncio
async def test_another_room_on_the_same_platform_is_another_door(outbound_store):
    await outbound_store.record(owner_id=1, platform="discord", channel_id=7,
                                text="hey", origin="arbiter", at=0.0)
    await outbound_store.mark_answered(owner_id=1, platform="discord", channel_id=8, at=60.0)
    assert outbound_store.rows[-1]["answered_at"] is None


@pytest.mark.asyncio
async def test_a_message_after_six_hours_does_not_answer_it(outbound_store):
    await outbound_store.record(owner_id=1, platform="discord", channel_id=7,
                                text="hey", origin="arbiter", at=0.0)
    marked = await outbound_store.mark_answered(
        owner_id=1, platform="discord", channel_id=7,
        at=outbound.ANSWER_WINDOW_S + 1.0)
    assert marked == 0
    assert outbound_store.rows[-1]["answered_at"] is None
    assert outbound.ANSWER_WINDOW_S == 21600.0


@pytest.mark.asyncio
async def test_a_message_from_before_the_reachout_does_not_answer_it(outbound_store):
    await outbound_store.record(owner_id=1, platform="discord", channel_id=7,
                                text="hey", origin="arbiter", at=1000.0)
    await outbound_store.mark_answered(owner_id=1, platform="discord", channel_id=7, at=900.0)
    assert outbound_store.rows[-1]["answered_at"] is None


@pytest.mark.asyncio
async def test_every_unanswered_reachout_on_the_door_is_marked(outbound_store):
    await outbound_store.record(owner_id=1, platform="discord", channel_id=7,
                                text="first", origin="arbiter", at=0.0)
    await outbound_store.record(owner_id=1, platform="discord", channel_id=7,
                                text="second", origin="workbench_share", at=100.0)
    marked = await outbound_store.mark_answered(
        owner_id=1, platform="discord", channel_id=7, at=200.0)
    assert marked == 2
    assert [r["answered_at"] for r in outbound_store.rows] == [200.0, 200.0]


@pytest.mark.asyncio
async def test_marking_again_changes_nothing(outbound_store):
    """The attachment path stores the same message a second time."""
    await outbound_store.record(owner_id=1, platform="discord", channel_id=7,
                                text="hey", origin="arbiter", at=0.0)
    assert await outbound_store.mark_answered(
        owner_id=1, platform="discord", channel_id=7, at=600.0) == 1
    assert await outbound_store.mark_answered(
        owner_id=1, platform="discord", channel_id=7, at=700.0) == 0
    assert outbound_store.rows[-1]["answered_at"] == 600.0
    assert [e["op"] for e in _events()] == ["sent", "answered"]


@pytest.mark.asyncio
async def test_someone_elses_message_marks_nothing(outbound_store):
    await outbound_store.record(owner_id=1, platform="discord", channel_id=7,
                                text="hey", origin="arbiter", at=0.0)
    await outbound_store.mark_answered(owner_id=2, platform="discord", channel_id=7, at=60.0)
    assert outbound_store.rows[-1]["answered_at"] is None


def test_the_events_have_the_ruled_shape(outbound_store):
    long_text = "word " * 40
    assert outbound.record_reachout(owner_id=1, platform="Discord", channel_id=7,
                                    text=long_text, origin="arbiter", at=5.0)
    assert outbound.mark_answered(owner_id=1, platform="discord", channel_id=7, at=65.0) == 1
    sent, answered = _events()
    assert set(sent) == {"op", "id", "at", "owner_id", "platform", "channel_id",
                         "origin", "medium", "head"}
    assert sent["op"] == "sent" and sent["at"] == 5.0
    assert sent["platform"] == "discord" and sent["channel_id"] == 7
    assert sent["origin"] == "arbiter" and sent["medium"] == "text"
    assert sent["head"] == " ".join(long_text.split())[:80]
    assert answered == {"op": "answered", "id": sent["id"], "at": 65.0}


def test_only_her_own_reachouts_are_written(outbound_store):
    for origin in ("her_hands", "forge", "duplex_chime", "duplex_ask",
                   "duplex_pause_expired", "reminder"):
        assert outbound.record_reachout(owner_id=1, platform="discord", channel_id=7,
                                        text="x", origin=origin, at=1.0) is False
    assert _events() == []


def test_mark_answered_never_raises(outbound_store, monkeypatch, tmp_path):
    path = outbound._reachouts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe not json")
    assert outbound.mark_answered(owner_id=1, platform="discord", channel_id=7, at=1.0) == 0

    blocked = tmp_path / "a-file"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setattr(outbound, "REACHOUTS_DIR", blocked)
    assert outbound.mark_answered(owner_id=1, platform="discord", channel_id=7, at=1.0) == 0


def test_a_damaged_line_is_skipped_not_the_book(outbound_store):
    outbound.record_reachout(owner_id=1, platform="discord", channel_id=7,
                             text="kept", origin="arbiter", at=1.0)
    with outbound._reachouts_path().open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert [r["head"] for r in outbound.read_reachouts()] == ["kept"]


def test_one_bad_byte_spoils_only_its_own_line(outbound_store):
    """Fix round 1 (M1): a torn write can leave a byte that is not UTF-8.
    It spoils its own line, never the book — the reach-outs on either side
    of it are still listed and still marked."""
    outbound.record_reachout(owner_id=1, platform="discord", channel_id=7,
                             text="before", origin="arbiter", at=1.0)
    with outbound._reachouts_path().open("ab") as fh:
        fh.write(b'{"op": "sent", "id": "torn\xff\n')
    outbound.record_reachout(owner_id=1, platform="discord", channel_id=7,
                             text="after", origin="arbiter", at=2.0)

    assert [r["head"] for r in outbound.recent_reachouts(limit=5)] == ["after", "before"]
    assert outbound.mark_answered(owner_id=1, platform="discord", channel_id=7,
                                  at=60.0) == 2
    assert [r["answered_at"] for r in outbound.read_reachouts()] == [60.0, 60.0]


def test_answer_state_says_what_is_known(outbound_store):
    now = 100_000.0
    window = outbound.ANSWER_WINDOW_S
    assert outbound.answer_state({"at": now - 60, "answered_at": now - 30}, now=now) == "answered"
    assert outbound.answer_state({"at": now - 60, "answered_at": None}, now=now) == "no answer yet"
    assert outbound.answer_state({"at": now - window - 1, "answered_at": None},
                                 now=now) == "unanswered"


def test_recent_reachouts_are_newest_first_and_can_leave_out_voice(outbound_store):
    for i in range(7):
        outbound.record_reachout(owner_id=1, platform="discord", channel_id=7,
                                 text=f"say {i}", origin="arbiter", at=float(i))
    outbound.record_reachout(owner_id=1, platform="discord", channel_id=7,
                             text="said aloud", origin="arbiter", at=10.0,
                             medium="voice")
    assert [r["head"] for r in outbound.recent_reachouts(limit=5)] == [
        "said aloud", "say 6", "say 5", "say 4", "say 3"]
    assert [r["head"] for r in outbound.recent_reachouts(limit=5, medium="text")] == [
        "say 6", "say 5", "say 4", "say 3", "say 2"]


# ── the writer: the custody bookkeeping every organic send passes ─────────

class _FakeRuntime:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_envelope(self, *, platform, surface_id, envelope):
        if self.fail:
            raise RuntimeError("the door is down")
        self.sent.append((platform, surface_id, envelope.text))


@pytest.fixture
def custody(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setattr("core.channel_runtime.channel_runtime", runtime)

    async def _fake_persist(*, turn, response_text):
        return None

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _fake_persist
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    return runtime


@pytest.mark.asyncio
async def test_an_arbiter_send_writes_one_sent_event(monkeypatch, custody):
    _arbiter_on(monkeypatch)
    before = time.time()
    ok = await outbound.record_outbound(
        owner_id=1, channel_id=123, text="  the page you asked about is finished  ",
        origin="arbiter", extra={"arbiter": True},
    )
    assert ok is True
    [event] = _events()
    assert event["op"] == "sent" and event["origin"] == "arbiter"
    assert event["owner_id"] == 1 and event["platform"] == "discord"
    assert event["channel_id"] == 123 and event["medium"] == "text"
    assert event["head"] == "the page you asked about is finished"
    assert before <= event["at"] <= time.time()


@pytest.mark.asyncio
async def test_a_share_is_a_reachout_too(monkeypatch, custody):
    _arbiter_on(monkeypatch)
    await outbound.record_outbound(owner_id=1, channel_id=123, text="made a thing",
                                   origin="workbench_share")
    assert [e["origin"] for e in _events()] == ["workbench_share"]


@pytest.mark.asyncio
async def test_a_telegram_reachout_is_filed_under_its_door(monkeypatch, custody):
    _arbiter_on(monkeypatch)
    await outbound.record_outbound(owner_id=1, channel_id=424242, text="made a thing",
                                   origin="arbiter", platform="telegram")
    [event] = _events()
    assert (event["platform"], event["channel_id"]) == ("telegram", 424242)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin, extra", [
    ("her_hands", {"third_party": True, "recipient": "a friend"}),
    ("her_hands", {"speak_line_fallback": True}),
    ("forge", {}),
    ("duplex_chime", {"mid_task": True}),
    ("duplex_ask", {"mid_task": True}),
    ("duplex_pause_expired", {"mid_task": False}),
])
async def test_sends_she_did_not_start_are_never_recorded(monkeypatch, custody, origin, extra):
    _arbiter_on(monkeypatch)
    ok = await outbound.record_outbound(owner_id=1, channel_id=555, text="hi there",
                                        origin=origin, extra=extra)
    assert ok is True and custody.sent
    assert not outbound._reachouts_path().exists()


@pytest.mark.asyncio
async def test_the_book_stays_shut_while_the_arbiter_is_off(custody):
    # the default test world: arbiter off
    await outbound.record_outbound(owner_id=1, channel_id=123, text="hey", origin="arbiter")
    assert custody.sent
    assert not outbound._reachouts_path().exists()
    assert not outbound._reachouts_path().parent.exists()


@pytest.mark.asyncio
async def test_a_send_that_failed_is_never_written(monkeypatch, custody):
    _arbiter_on(monkeypatch)
    custody.fail = True
    with pytest.raises(RuntimeError):
        await outbound.record_outbound(owner_id=1, channel_id=123, text="hey",
                                       origin="arbiter")
    assert not outbound._reachouts_path().exists()


@pytest.mark.asyncio
async def test_a_book_that_cannot_be_written_never_unsends(monkeypatch, custody, tmp_path):
    _arbiter_on(monkeypatch)
    blocked = tmp_path / "a-file"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setattr(outbound, "REACHOUTS_DIR", blocked)
    ok = await outbound.record_outbound(owner_id=1, channel_id=123, text="hey",
                                        origin="arbiter")
    assert ok is True and custody.sent


@pytest.mark.asyncio
async def test_the_book_is_written_off_the_event_loop(monkeypatch, custody):
    _arbiter_on(monkeypatch)
    loop_thread = threading.get_ident()
    seen = []

    def _spy(**kw):
        seen.append((threading.get_ident(), kw["origin"], kw["medium"]))
        return True

    monkeypatch.setattr(outbound, "record_reachout", _spy)
    await outbound.record_outbound(owner_id=1, channel_id=123, text="hey", origin="arbiter")
    assert len(seen) == 1
    assert seen[0][0] != loop_thread
    assert seen[0][1:] == ("arbiter", "text")


@pytest.mark.asyncio
async def test_a_spoken_reachout_is_recorded_as_voice(monkeypatch, tmp_path):
    """Spoken aloud, it is on record as ``voice`` and left out of the judge's
    list: the owner's spoken answer never passes persistence.py and is filed
    under the voice channel, so it could only ever read as unanswered (ruling
    R45)."""
    _arbiter_on(monkeypatch)

    class _Voice:
        async def say_text(self, *, runtime_session_key, text):
            return True

    import voice.control as _voice_control

    monkeypatch.setattr(_voice_control, "voice_session_control_service", _Voice())

    async def _fake_persist(*, turn, response_text):
        return None

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _fake_persist
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))

    await outbound.record_outbound_spoken(
        owner_id=1, channel_id=123, runtime_session_key="vs-1",
        text="the page is finished", origin="arbiter",
    )
    [event] = _events()
    assert event["medium"] == "voice"
    assert (event["platform"], event["channel_id"]) == ("discord", 123)
    assert outbound.recent_reachouts(limit=5, medium="text") == []


# ── the hook: the owner's message, where it is stored ─────────────────────

@pytest.fixture
def stored(monkeypatch):
    """The owner's message goes through the real persistence helper; only the
    conversations store is a stand-in."""
    import core.conversation_history_store as chs

    rows = []

    class _Store:
        def __init__(self, brain):
            pass

        async def store_conversation(self, **kw):
            rows.append(kw)

    monkeypatch.setattr(chs, "ConversationHistoryStore", _Store)
    return rows


async def _owner_says(*, surface="discord_text", channel_id=123, owner_id=1,
                      origin="user", text="yes, go on", **turn_kw):
    """``turn_kw`` reaches the Turn itself (``received_at``, its arrival)."""
    import core.turn.persistence as per

    turn = Turn(surface=surface, owner_id=owner_id, content=text,
                metadata=TurnMetadata(origin=origin, channel_id=channel_id),
                **turn_kw)
    await per._persist_user_side_with_content(turn, text)
    return turn


def _sent_a_while_ago(*, platform="discord", channel_id=123, ago=600.0):
    outbound.record_reachout(owner_id=1, platform=platform, channel_id=channel_id,
                             text="a reach-out", origin="arbiter",
                             at=time.time() - ago)
    return outbound.read_reachouts()[-1]["id"]


def _naive_utc(epoch: float) -> datetime:
    """An arrival in the Turn's own convention: naive UTC
    (core/turn/types.py's default is datetime.utcnow)."""
    return datetime(1970, 1, 1) + timedelta(seconds=epoch)


@pytest.fixture
def clock_away_from_utc():
    """The host's clock five hours behind UTC for one test, so a naive-UTC
    arrival read as local time comes out five hours wrong even on a host
    that runs on UTC."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "EST5"
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


@pytest.mark.asyncio
async def test_the_owners_message_on_the_same_door_answers_it(monkeypatch, stored):
    _arbiter_on(monkeypatch)
    rid = _sent_a_while_ago()
    turn = await _owner_says()
    assert stored and stored[0]["role"] == "user"
    assert _row(rid)["answered_at"] == turn.received_at.replace(
        tzinfo=timezone.utc).timestamp()


@pytest.mark.asyncio
async def test_the_answer_is_timed_by_the_messages_arrival(
        monkeypatch, stored, clock_away_from_utc):
    """Fix round 1 (M2): the answer is stamped with the moment the message
    ARRIVED (the turn's naive-UTC ``received_at``, read as UTC whatever the
    host's zone), not the moment it was stored."""
    _arbiter_on(monkeypatch)
    arrived = float(int(time.time()) - 30)
    rid = _sent_a_while_ago(ago=600.0)
    await _owner_says(received_at=_naive_utc(arrived))
    assert _row(rid)["answered_at"] == arrived


@pytest.mark.asyncio
async def test_a_reachout_sent_after_the_message_arrived_is_not_answered_by_it(
        monkeypatch, stored):
    """Fix round 1 (M2): the attachment paths store the owner's message
    late — after the vision read, or after the whole run. A reach-out that
    went out in that gap came AFTER the message, so the message is not a
    reply to it."""
    _arbiter_on(monkeypatch)
    arrived = time.time() - 30
    rid = _sent_a_while_ago(ago=10.0)             # out after the arrival
    await _owner_says(text="[image attached — a picture of a desk]",
                      received_at=_naive_utc(arrived))
    assert _row(rid)["answered_at"] is None
    assert [e["op"] for e in _events()] == ["sent"]


@pytest.mark.asyncio
async def test_a_turn_with_no_arrival_time_is_timed_by_its_store(monkeypatch, stored):
    _arbiter_on(monkeypatch)
    rid = _sent_a_while_ago()
    before = time.time()
    await _owner_says(received_at=None)
    assert before <= _row(rid)["answered_at"] <= time.time()


@pytest.mark.asyncio
async def test_a_telegram_message_answers_a_telegram_reachout(monkeypatch, stored):
    _arbiter_on(monkeypatch)
    on_telegram = _sent_a_while_ago(platform="telegram", channel_id=424242)
    on_discord = _sent_a_while_ago(platform="discord", channel_id=424242)
    await _owner_says(surface="telegram_text", channel_id=424242)
    assert _row(on_telegram)["answered_at"] is not None
    assert _row(on_discord)["answered_at"] is None


@pytest.mark.asyncio
async def test_the_hook_does_nothing_while_the_arbiter_is_off(stored):
    await _owner_says()                           # no book yet: none is made
    assert not outbound._reachouts_path().parent.exists()

    rid = _sent_a_while_ago()
    await _owner_says()
    assert _row(rid)["answered_at"] is None
    assert stored                                 # the message itself is stored


@pytest.mark.asyncio
async def test_a_guests_message_in_the_same_room_answers_nothing(monkeypatch, stored):
    """A guest never reaches this helper (both doors route non-owners away
    first, pinned by the guest-message test in tests/bots/test_discord_client.py
    — a guest gets a plain reply and never touches the owner's lane — and
    dispatch persists only the owner's origins). Were one to arrive, the
    guest's own id is not the owner's, and nothing is marked."""
    _arbiter_on(monkeypatch)
    rid = _sent_a_while_ago()
    await _owner_says(owner_id=12345, origin="guest", text="[a guest]: hello")
    assert _row(rid)["answered_at"] is None


@pytest.mark.asyncio
async def test_the_same_message_stored_twice_is_one_answer(monkeypatch, stored):
    """The attachment paths store the same turn a second time with the
    richer text; both stores carry the same arrival."""
    import core.turn.persistence as per

    _arbiter_on(monkeypatch)
    rid = _sent_a_while_ago()
    turn = await _owner_says(text="[image attached]")
    first = _row(rid)["answered_at"]
    await per._persist_user_side_with_content(
        turn, "[image attached — a picture of a desk]")
    assert _row(rid)["answered_at"] == first
    assert [e["op"] for e in _events()] == ["sent", "answered"]


@pytest.mark.asyncio
async def test_a_spoken_answer_is_another_door(monkeypatch, stored):
    """A Discord voice turn is filed under the voice channel, so it never
    matches the text chat the reach-out went to (ruling R45: a follow-up)."""
    _arbiter_on(monkeypatch)
    rid = _sent_a_while_ago()
    await _owner_says(surface="discord_voice", channel_id=777)
    assert _row(rid)["answered_at"] is None


@pytest.mark.asyncio
async def test_a_failing_mark_never_reaches_the_turn(monkeypatch, stored):
    _arbiter_on(monkeypatch)

    def _boom(**kw):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(outbound, "mark_answered", _boom)
    await _owner_says()                           # does not raise
    assert stored


@pytest.mark.asyncio
async def test_the_hook_runs_off_the_event_loop(monkeypatch, stored):
    _arbiter_on(monkeypatch)
    loop_thread = threading.get_ident()
    seen = []

    def _spy(**kw):
        seen.append((threading.get_ident(), kw))
        return 0

    monkeypatch.setattr(outbound, "mark_answered", _spy)
    await _owner_says(channel_id=321)
    [(thread, kw)] = seen
    assert thread != loop_thread
    assert (kw["owner_id"], kw["platform"], kw["channel_id"]) == (1, "discord", 321)


def test_the_signatures_the_fakes_pin_are_unchanged():
    """About thirty tests fake these two with a fixed shape (ruling R44)."""
    import core.turn.persistence as per

    assert list(inspect.signature(per._persist_user_side_with_content).parameters) == [
        "turn", "content"]
    assert list(inspect.signature(per._persist_assistant_side_to_history).parameters) == [
        "turn", "response_text"]

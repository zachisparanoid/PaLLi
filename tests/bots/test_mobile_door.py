"""The mobile door's owner lane (spec §6): the shared lane
(bots/common/owner_lane.py) with the phone's hooks. A burst is one turn; with
converse_while_working off a new message cancels the reply in flight; with it
on, a message while she works goes to the steering fork and her work goes on;
a failed turn is a plain notice; the door's start registers it and takes it
down."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
import pytest_asyncio

from bots.common import steering as steering_fork
from bots.mobile import client as door_mod
from bots.mobile import intake
from bots.mobile.client import MobileDoor
from core.agent import steering as core_steering
from core.mobile_events_store import MobileEventsStore
from core.turn.types import TurnResult


@pytest_asyncio.fixture
async def store(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "door.sqlite3"))
    await brain.initialize()
    try:
        yield MobileEventsStore(brain)
    finally:
        await brain.close()


@pytest.fixture
def flag(monkeypatch):
    """companion.converse_while_working, pinned per test (the shipped yaml has it on)."""
    def set_flag(on: bool) -> None:
        monkeypatch.setattr(steering_fork, "_flag_on", lambda: on)
    return set_flag


class FakeHub:
    def __init__(self) -> None:
        self.frames: list = []

    async def publish(self, event, *, owner_id):
        self.frames.append(("event", event["kind"], event.get("text")))
        return 1

    async def set_typing(self, on, *, owner_id):
        self.frames.append(("typing", on))


async def _record(turn, *, sessions_root, transcript=True):
    return None


def _ok(text):
    return TurnResult(responded=True, response_text=text, decision=None, reflection_applied=False,
                      delegation_tier_used=0)


async def _turn(store, text, n):
    turn, _ = await intake.build_turn(owner_id=1, client_id=f"9f1b2c3d-0000-4000-8000-00000000000{n}",
                                      text=text, uploads=[], directory=Path("/tmp/unused"), store=store)
    return turn


async def _settled():
    """A quiet half second, for the things that must NOT happen."""
    for _ in range(50):
        await asyncio.sleep(0.01)


async def _until(predicate, timeout=15.0):
    """Wait for something that must happen. A delivery appends to a real
    sqlite timeline (a connection per call, a couple hundred ms on a loaded
    machine), so a fixed pause is a flaky way to wait for one."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)


def _said(hub, text):
    return lambda: ("event", "message", text) in hub.frames


@pytest.mark.asyncio
async def test_a_burst_is_one_turn_with_typing_while_she_thinks(store, flag):
    flag(False)
    seen = []

    async def dispatch(turn):
        seen.append(turn)
        return _ok("both heard")

    hub = FakeHub()
    door = MobileDoor(owner_id=1, store=store, hub=hub, dispatch=dispatch, record=_record,
                      settle=0.05, typing_window=0.0, max_hold=1.0)
    await door.accept(await _turn(store, "first thought", 1))
    await door.accept(await _turn(store, "second thought", 2))
    await _until(lambda: ("event", "message", "both heard") in hub.frames)
    await _settled()                                      # a late frame would show in the exact list below

    (turn,) = seen
    assert turn.surface == "mobile_text" and turn.metadata.channel_id == 1
    assert turn.content == "first thought\nsecond thought"
    assert turn.metadata.user_side_prerecorded is True and turn.metadata.is_dm is True
    assert turn.metadata.speaker["kind"] == "owner" and turn.metadata.speaker["platform"] == "mobile"
    assert hub.frames == [("typing", True), ("typing", False), ("event", "message", "both heard")]


@pytest.mark.asyncio
async def test_a_new_message_cancels_the_reply_in_flight(store, flag):
    flag(False)
    started = asyncio.Event()
    calls = []

    async def dispatch(turn):
        calls.append(turn.content)
        return _ok(f"answer to {turn.content}")

    async def slow_deliver(owner_id, result, *, store, hub):
        await hub.publish({"kind": "message", "text": "first beat"}, owner_id=owner_id)
        if "one" in result.response_text:
            started.set()
            await asyncio.sleep(3600)                     # the pause before beat two
        await hub.publish({"kind": "message", "text": result.response_text}, owner_id=owner_id)

    hub = FakeHub()
    door = MobileDoor(owner_id=1, store=store, hub=hub, dispatch=dispatch, deliver=slow_deliver,
                      record=_record, settle=0.0, typing_window=0.0, max_hold=1.0)
    await door.accept(await _turn(store, "one", 1))
    await asyncio.wait_for(started.wait(), 15)
    await door.accept(await _turn(store, "two", 2))
    await _until(lambda: ("event", "message", "answer to two") in hub.frames)
    await _settled()

    assert calls == ["one", "two"]
    texts = [f[2] for f in hub.frames if f[0] == "event"]
    assert texts == ["first beat", "first beat", "answer to two"]   # "answer to one" never went out


@pytest.mark.asyncio
async def test_with_converse_while_working_on_her_work_goes_on_and_the_message_goes_to_the_fork(
        store, flag, monkeypatch):
    flag(True)
    working = asyncio.Event()
    release = asyncio.Event()
    handed = []

    async def dispatch(turn):
        working.set()
        await release.wait()
        return _ok("the ferry times, done")

    async def handle_mid_task(turn, *, session_key, is_owner, send, run_in_flight, dispatch=None,
                              sessions_root=None, launch_sidecar=None):
        handed.append((turn.content, session_key, is_owner))
        return True

    monkeypatch.setattr(core_steering, "is_working", lambda key: key == "mobile:1")
    monkeypatch.setattr(steering_fork, "route_mid_run", lambda **kw: "steer")
    monkeypatch.setattr(steering_fork, "handle_mid_task", handle_mid_task)

    hub = FakeHub()
    door = MobileDoor(owner_id=1, store=store, hub=hub, dispatch=dispatch, record=_record,
                      settle=0.0, typing_window=0.0, max_hold=1.0)
    await door.accept(await _turn(store, "research the ferry times", 1))
    await asyncio.wait_for(working.wait(), 15)
    await door.accept(await _turn(store, "and the way back", 2))
    await _until(lambda: handed)
    assert handed == [("and the way back", "mobile:1", True)]

    release.set()
    await _until(_said(hub, "the ferry times, done"))                  # never cancelled


@pytest.mark.asyncio
async def test_the_lane_and_the_runner_name_one_conversation(store):
    """The lane keys its own bookkeeping f"{door}:{chat_id}" and the run is keyed
    by session_key_for(surface, channel_id, owner_id): both are mobile:1, the
    door's one conversation, whoever the owner id is."""
    from bots.mobile import MOBILE_CHANNEL_ID, MOBILE_PLATFORM
    from core.agent.runner import _derive_session_key
    from core.session_keys import session_key_for

    assert session_key_for("mobile_text", 1, 7) == "mobile:1"
    assert session_key_for("mobile_text", MOBILE_CHANNEL_ID, 1) == f"{MOBILE_PLATFORM}:{MOBILE_CHANNEL_ID}"
    assert _derive_session_key(await _turn(store, "hello", 1)) == "mobile:1"


@pytest.mark.asyncio
async def test_her_typing_shows_on_the_phone_while_her_voice_is_made(store, flag, monkeypatch):
    flag(False)
    hub = FakeHub()

    async def plan_voice(platform, channel_id, result, *, owner_id=0, typing=None):
        async with typing():                              # the synthesis, as Telegram's is wrapped
            hub.frames.append(("synthesizing",))
        return None

    real_deliver = door_mod.deliver_result

    async def deliver_with_a_voice_plan(owner_id, result, **kw):
        return await real_deliver(owner_id, result, plan_voice=plan_voice, **kw)

    monkeypatch.setattr(door_mod, "deliver_result", deliver_with_a_voice_plan)

    async def dispatch(turn):
        return _ok("said aloud")

    door = MobileDoor(owner_id=1, store=store, hub=hub, dispatch=dispatch, record=_record,
                      settle=0.0, typing_window=0.0, max_hold=1.0)
    await door.accept(await _turn(store, "tell me", 1))
    await _until(_said(hub, "said aloud"))
    await _settled()
    assert hub.frames == [
        ("typing", True), ("typing", False),                          # she thinks
        ("typing", True), ("synthesizing",), ("typing", False),       # her voice is made
        ("event", "message", "said aloud"),
    ]


@pytest.mark.asyncio
async def test_a_turn_that_failed_with_nothing_to_say_is_a_plain_notice(store, flag, caplog):
    flag(False)
    caplog.set_level(logging.WARNING, logger=door_mod.logger.name)

    async def dispatch(turn):
        return TurnResult(responded=False, response_text="", decision=None, reflection_applied=False,
                          delegation_tier_used=0, error="provider down: key sk-secret rejected")

    hub = FakeHub()
    door = MobileDoor(owner_id=1, store=store, hub=hub, dispatch=dispatch, record=_record,
                      settle=0.0, typing_window=0.0, max_hold=1.0)
    await door.accept(await _turn(store, "hello?", 1))
    await _until(lambda: any(f[:2] == ("event", "notice") for f in hub.frames))
    await _settled()
    notices = [f for f in hub.frames if f[0] == "event" and f[1] == "notice"]
    assert [n[2] for n in notices] == [door_mod.TROUBLE_NOTICE]          # a plain line, nothing of the provider's
    assert "provider down: key sk-secret rejected" in caplog.text        # the detail is in the log


@pytest.mark.asyncio
async def test_a_delivery_that_raised_is_a_plain_notice_and_the_detail_is_logged(store, flag, caplog):
    flag(False)
    caplog.set_level(logging.ERROR, logger=door_mod.logger.name)

    async def dispatch(turn):
        return _ok("never delivered")

    async def broken_deliver(owner_id, result, *, store, hub):
        raise RuntimeError("disk full at /srv/palli/outbox")

    hub = FakeHub()
    door = MobileDoor(owner_id=1, store=store, hub=hub, dispatch=dispatch, deliver=broken_deliver,
                      record=_record, settle=0.0, typing_window=0.0, max_hold=1.0)
    await door.accept(await _turn(store, "hello?", 1))
    await _until(lambda: any(f[:2] == ("event", "notice") for f in hub.frames))
    await _settled()
    notices = [f for f in hub.frames if f[0] == "event" and f[1] == "notice"]
    assert [n[2] for n in notices] == [door_mod.TROUBLE_NOTICE]
    assert "disk full at /srv/palli/outbox" in caplog.text


@pytest.mark.asyncio
async def test_the_lane_reads_who_sent_the_burst_not_who_the_door_hopes(store, monkeypatch):
    door = MobileDoor(owner_id=1, store=store, hub=FakeHub(), dispatch=None, record=_record)
    seen = {}

    async def arrive(chat_id, turn, *, author_id, has_attachment, resolve):
        seen["author"] = author_id

    monkeypatch.setattr(door._lane, "arrive", arrive)
    stranger, _ = await intake.build_turn(owner_id=2, client_id="9f1b2c3d-0000-4000-8000-0000000000b2",
                                          text="let me in", uploads=[], directory=Path("/tmp/unused"),
                                          store=store)
    await door.accept(stranger)
    assert seen["author"] == 2                                   # the turn's owner, not a constant
    assert door.owner_id == 1
    assert await door._owner_alone(1, {1}) is True
    for authors in ({2}, {1, 2}, set()):
        assert await door._owner_alone(1, authors) is False


@pytest.mark.asyncio
async def test_typing_on_the_phone_holds_the_burst(store, flag):
    flag(False)
    seen = []

    async def dispatch(turn):
        seen.append(turn.content)
        return _ok("ok")

    door = MobileDoor(owner_id=1, store=store, hub=FakeHub(), dispatch=dispatch, record=_record,
                      settle=0.0, typing_window=0.3, max_hold=2.0)
    await door.accept(await _turn(store, "wait for it", 1))
    door.note_typing()                                  # he is typing his next thought
    await asyncio.sleep(0.1)
    assert seen == []                                   # still typing
    await asyncio.sleep(0.4)
    assert seen == ["wait for it"]


@pytest.mark.asyncio
async def test_the_start_registers_the_door_and_takes_it_down(store, monkeypatch):
    from core import owner_notice
    from core.channel_runtime import channel_runtime

    async def refs():
        return {"app_user_id": 1}

    monkeypatch.setattr(door_mod, "get_local_owner_refs", refs)
    monkeypatch.setattr(door_mod, "events_store", lambda: store)
    monkeypatch.setattr(channel_runtime, "_adapters", {})
    monkeypatch.setattr(owner_notice, "_senders", {})

    task = asyncio.create_task(door_mod.start_mobile_door())
    await _until(lambda: "mobile" in channel_runtime._adapters and "mobile" in owner_notice.registered_doors())
    assert "mobile" in channel_runtime._adapters
    assert "mobile" in owner_notice.registered_doors()
    assert door_mod.active_door() is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "mobile" not in channel_runtime._adapters
    assert "mobile" not in owner_notice.registered_doors()
    assert door_mod.active_door() is None


def test_an_upload_name_cannot_leave_the_inbox(tmp_path):
    path = intake.save_upload(intake.Upload("files", "../../etc/passwd", "text/plain", b"x"), directory=tmp_path)
    assert path.parent == tmp_path and path.name.endswith("passwd")
    assert path.read_bytes() == b"x"


def test_a_send_is_checked_before_anything_is_saved():
    ok_id = "9f1b2c3d-0000-4000-8000-000000000001"
    intake.check_send(ok_id, "hi", [])
    for client_id, text, uploads, status in [
        ("not-a-uuid", "hi", [], 422),
        (ok_id, "   ", [], 422),
        (ok_id, "", [intake.Upload("voice", "a.m4a", "audio/mp4", b"1"),
                     intake.Upload("voice", "b.m4a", "audio/mp4", b"2")], 422),
        (ok_id, "", [intake.Upload("files", "big.bin", "application/octet-stream",
                                   b"0" * (intake.MAX_PART_BYTES + 1))], 413),
    ]:
        with pytest.raises(intake.SendRefused) as refused:
            intake.check_send(client_id, text, uploads)
        assert refused.value.status == status

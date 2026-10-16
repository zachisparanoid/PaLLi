"""Her reply on the mobile door, as timeline events (spec §4, §6)."""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from bots.common.voice_note import VoiceReply
from bots.mobile import reply
from core.mobile_events_store import MobileEventsStore
from core.turn.types import Beat, CadencedReply, TurnResult


@pytest_asyncio.fixture
async def store(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "reply.sqlite3"))
    await brain.initialize()
    try:
        yield MobileEventsStore(brain)
    finally:
        await brain.close()


class FakeHub:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, event, *, owner_id):
        self.published.append(event)
        return 1


def _result(text="", *, beats=None, attachments=(), quiet=False, voice_notes=()):
    cadenced = CadencedReply(beats=tuple(beats), total_text=text) if beats else None
    return TurnResult(responded=True, response_text=text, decision=None, reflection_applied=False,
                      delegation_tier_used=0, cadenced_reply=cadenced, attachments=tuple(attachments),
                      quiet=quiet, voice_notes=tuple(voice_notes))


async def _no_voice(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_beats_become_events_in_order_after_their_pauses(store, tmp_path):
    hub, paused = FakeHub(), []

    async def sleep(seconds):
        paused.append(seconds)

    result = _result("one\ntwo", beats=[Beat("one"), Beat("two", 1.5)])
    count = await reply.deliver_result(1, result, store=store, hub=hub, directory=tmp_path,
                                       sleep=sleep, plan_voice=_no_voice)
    assert count == 2 and paused == [1.5]
    assert [(e["kind"], e["text"]) for e in hub.published] == [("message", "one"), ("message", "two")]


@pytest.mark.asyncio
async def test_quiet_writes_nothing(store, tmp_path):
    hub = FakeHub()
    count = await reply.deliver_result(1, _result("", quiet=True), store=store, hub=hub,
                                       directory=tmp_path, plan_voice=_no_voice)
    assert count == 0 and hub.published == []


@pytest.mark.asyncio
async def test_a_file_she_made_goes_out_by_id_and_a_missing_one_says_so(store, tmp_path):
    made = tmp_path / "plot.png"
    made.write_bytes(b"\x89PNG")
    hub = FakeHub()
    await reply.deliver_result(1, _result("here", attachments=[str(made), str(tmp_path / "gone.pdf")]),
                               store=store, hub=hub, directory=tmp_path, plan_voice=_no_voice)
    kinds = [e["kind"] for e in hub.published]
    assert kinds == ["message", "file", "notice"]
    file_event = hub.published[1]
    assert file_event["file"]["filename"] == "plot.png" and file_event["file"]["content_type"] == "image/png"
    assert str(tmp_path) not in str(file_event)                     # no path crosses the API
    assert hub.published[2]["level"] == "trouble"
    assert hub.published[2]["text"] == "I made gone.pdf, but it is not on my machine any more."


@pytest.mark.asyncio
async def test_a_voice_reply_is_a_voice_note_carrying_her_words(store, tmp_path):
    async def voice(platform, channel_id, result, *, owner_id=0, typing=None):
        assert (platform, channel_id) == ("mobile", 1)
        return VoiceReply(text="", notes=(b"OggS-fake",))

    hub = FakeHub()
    await reply.deliver_result(1, _result("I'll say it aloud"), store=store, hub=hub,
                               directory=tmp_path, plan_voice=voice)
    (event,) = hub.published
    assert event["kind"] == "voice_note" and event["text"] == "I'll say it aloud"
    assert event["file"]["content_type"] == "audio/ogg"
    found = await store.file(owner_id=1, file_id=event["file"]["id"])
    assert open(found["path"], "rb").read() == b"OggS-fake"


@pytest.mark.asyncio
async def test_the_door_s_typing_reaches_the_voice_plan_and_turns_on_then_off(store, tmp_path):
    seen = []

    class Typing:
        async def __aenter__(self):
            seen.append("on")

        async def __aexit__(self, *exc):
            seen.append("off")
            return False

    async def voice(platform, channel_id, result, *, owner_id=0, typing=None):
        async with typing():                                          # the speech being made
            seen.append("synthesizing")
        return None

    hub = FakeHub()
    await reply.deliver_result(1, _result("hello"), store=store, hub=hub, directory=tmp_path,
                               plan_voice=voice, typing=Typing)
    assert seen == ["on", "synthesizing", "off"]
    assert [e["text"] for e in hub.published] == ["hello"]


@pytest.mark.asyncio
async def test_without_a_typing_factory_the_voice_plan_is_called_as_before(store, tmp_path):
    calls = []

    async def voice(platform, channel_id, result, **kwargs):
        calls.append(kwargs)
        return None

    await reply.deliver_result(1, _result("hello"), store=store, hub=FakeHub(), directory=tmp_path,
                               plan_voice=voice)
    assert calls == [{"owner_id": 1}]


@pytest.mark.asyncio
async def test_a_barge_in_keeps_the_beats_already_sent_and_drops_the_rest(store, tmp_path):
    async def sleep(seconds):
        raise asyncio.CancelledError

    hub = FakeHub()
    with pytest.raises(asyncio.CancelledError):
        await reply.deliver_result(1, _result("a\nb", beats=[Beat("a"), Beat("b", 2.0)]), store=store,
                                   hub=hub, directory=tmp_path, sleep=sleep, plan_voice=_no_voice)
    assert [e["text"] for e in hub.published] == ["a"]


@pytest.mark.asyncio
async def test_a_voice_plan_that_fails_still_delivers_her_words_as_text(store, tmp_path):
    async def broken_voice(*args, **kwargs):
        raise RuntimeError("tts is down")

    hub = FakeHub()
    count = await reply.deliver_result(1, _result("one\ntwo", beats=[Beat("one"), Beat("two")]),
                                       store=store, hub=hub, directory=tmp_path, plan_voice=broken_voice)
    assert count == 2
    assert [(e["kind"], e["text"]) for e in hub.published] == [("message", "one"), ("message", "two")]


@pytest.mark.asyncio
async def test_a_voice_plan_cancelled_by_a_barge_in_still_propagates(store, tmp_path):
    async def cancelled_voice(*args, **kwargs):
        raise asyncio.CancelledError

    hub = FakeHub()
    with pytest.raises(asyncio.CancelledError):
        await reply.deliver_result(1, _result("hello"), store=store, hub=hub, directory=tmp_path,
                                   plan_voice=cancelled_voice)
    assert hub.published == []


@pytest.mark.asyncio
async def test_a_push_that_fails_after_the_append_still_returns_the_event(store):
    class BrokenHub:
        async def publish(self, event, *, owner_id):
            raise ConnectionError("socket gone")

    event = await reply.emit_text(1, "still in the timeline", store=store, hub=BrokenHub())
    page, _ = await store.after(owner_id=1, after=0, limit=10)
    assert [e["id"] for e in page] == [event["id"]]
    assert page[0]["text"] == "still in the timeline"


@pytest.mark.asyncio
async def test_a_failed_append_still_raises(store):
    class BrokenStore:
        async def append(self, **kwargs):
            raise OSError("disk full")

    hub = FakeHub()
    with pytest.raises(OSError):
        await reply.emit_text(1, "lost", store=BrokenStore(), hub=hub)
    assert hub.published == []


@pytest.mark.asyncio
async def test_a_barge_in_during_the_append_leaves_no_committed_row_unpublished(store):
    entered, release, pushed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class GatedStore:
        async def append(self, **kwargs):
            entered.set()
            await release.wait()
            return await store.append(**kwargs)

    class SignalHub(FakeHub):
        async def publish(self, event, *, owner_id):
            await super().publish(event, owner_id=owner_id)
            pushed.set()
            return 1

    hub = SignalHub()
    task = asyncio.ensure_future(reply.emit_text(1, "mid-append", store=GatedStore(), hub=hub))
    await asyncio.wait_for(entered.wait(), 15)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    await asyncio.wait_for(pushed.wait(), 15)                 # the shielded unit finished on its own
    page, _ = await store.after(owner_id=1, after=0, limit=10)
    assert [e["text"] for e in page] == ["mid-append"]
    assert [e["id"] for e in hub.published] == [page[0]["id"]]

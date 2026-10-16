"""The mobile door's channel adapter: an append IS the delivery (spec §4)."""
from __future__ import annotations

import inspect

import pytest
import pytest_asyncio

from bots.mobile.adapter import MobileChannelAdapter
from core.channel_runtime import ChannelAttachment, ChannelDeliveryEnvelope
from core.mobile_events_store import MobileEventsStore


@pytest_asyncio.fixture
async def store(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "adapter.sqlite3"))
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
        return 0                                   # no phone open: still delivered


@pytest.mark.asyncio
async def test_a_reach_out_lands_in_the_timeline_even_with_no_phone_open(store):
    hub = FakeHub()
    adapter = MobileChannelAdapter(owner_id=1, store=store, hub=hub, enabled=lambda: True)
    out = await adapter.send_envelope(surface_id="1", envelope=ChannelDeliveryEnvelope(text="thinking of you"))
    assert out["sent"] is True
    page, _ = await store.after(owner_id=1, after=0, limit=10)
    assert [(e["kind"], e["text"], e["reminder"]) for e in page] == [("message", "thinking of you", False)]


@pytest.mark.asyncio
async def test_a_reminder_envelope_is_marked_and_a_file_is_sent_by_id(store, tmp_path):
    made = tmp_path / "list.txt"
    made.write_text("eggs")
    hub = FakeHub()
    adapter = MobileChannelAdapter(owner_id=1, store=store, hub=hub, enabled=lambda: True)
    await adapter.send_envelope(surface_id="1", envelope=ChannelDeliveryEnvelope(
        text="call the co-op", metadata={"reminder": True},
        attachments=[ChannelAttachment(kind="file", path=str(made))]))
    assert [e["kind"] for e in hub.published] == ["message", "file"]
    assert hub.published[0]["reminder"] is True


@pytest.mark.asyncio
async def test_nothing_to_send_is_not_a_failed_send_and_a_wrong_chat_raises(store):
    adapter = MobileChannelAdapter(owner_id=1, store=store, hub=FakeHub(), enabled=lambda: True)
    assert (await adapter.send_envelope(surface_id="1", envelope=ChannelDeliveryEnvelope(text="  ")))["sent"] is False
    with pytest.raises(RuntimeError):
        await adapter.send_envelope(surface_id="2", envelope=ChannelDeliveryEnvelope(text="hi"))


@pytest.mark.asyncio
async def test_a_failed_append_raises_so_custody_never_records_a_send(store):
    class BrokenStore:
        async def append(self, **kwargs):
            raise OSError("disk full")

    adapter = MobileChannelAdapter(owner_id=1, store=BrokenStore(), hub=FakeHub(), enabled=lambda: True)
    with pytest.raises(RuntimeError, match="nothing was delivered"):
        await adapter.send_envelope(surface_id="1", envelope=ChannelDeliveryEnvelope(text="hi"))


@pytest.mark.asyncio
async def test_a_file_that_fails_after_the_text_landed_is_told_as_trouble(store, tmp_path):
    """Review M9 (ledger T4 L73): the text is in the timeline, the file's emit
    raises — the owner hears the file did not come through (not that it is
    gone: it is still on the machine), and the send still says what it
    delivered."""
    made = tmp_path / "plot.png"
    made.write_bytes(b"png")

    class FileRefused:
        def __getattr__(self, name):
            return getattr(store, name)

        async def register_file(self, **kwargs):
            raise OSError("disk full")

    adapter = MobileChannelAdapter(owner_id=1, store=FileRefused(), hub=FakeHub(), enabled=lambda: True)
    out = await adapter.send_envelope(surface_id="1", envelope=ChannelDeliveryEnvelope(
        text="here is the plot", attachments=[ChannelAttachment(kind="file", path=str(made))]))
    assert out == {"sent": True, "chat_id": 1, "events": 1}
    page, _ = await store.after(owner_id=1, after=0, limit=10)
    assert [(e["kind"], e.get("level"), e["text"]) for e in page] == [
        ("message", None, "here is the plot"),
        ("notice", "trouble", "I made plot.png, but it didn't come through."),
    ]


@pytest.mark.asyncio
async def test_a_trouble_notice_that_fails_too_never_raises(store, tmp_path):
    made = tmp_path / "plot.png"
    made.write_bytes(b"png")

    class TextOnly:
        def __init__(self):
            self.appended = 0

        async def append(self, **kwargs):
            self.appended += 1
            if self.appended > 1:
                raise OSError("disk full")
            return await store.append(**kwargs)

        async def register_file(self, **kwargs):
            raise OSError("disk full")

    adapter = MobileChannelAdapter(owner_id=1, store=TextOnly(), hub=FakeHub(), enabled=lambda: True)
    out = await adapter.send_envelope(surface_id="1", envelope=ChannelDeliveryEnvelope(
        text="here is the plot", attachments=[ChannelAttachment(kind="file", path=str(made))]))
    assert out["sent"] is True and out["events"] == 1


def _phones(monkeypatch, count: int) -> None:
    import core.mobile_auth as mobile_auth

    class _Devices:
        async def count_devices(self, *, owner_id=None):
            return count

    monkeypatch.setattr(mobile_auth, "device_store", lambda: _Devices())


def _door_up(monkeypatch, adapter) -> None:
    """``adapter`` is the mobile door channel_runtime knows (and only it)."""
    from core.channel_runtime import channel_runtime

    monkeypatch.setattr(channel_runtime, "_adapters", {"mobile": adapter})


@pytest.mark.asyncio
async def test_readiness_privacy_and_owner_notices(store, monkeypatch):
    switch = {"on": False}
    hub = FakeHub()
    adapter = MobileChannelAdapter(owner_id=1, store=store, hub=hub, enabled=lambda: switch["on"])
    _door_up(monkeypatch, adapter)
    _phones(monkeypatch, 1)
    assert adapter.is_ready() is False
    switch["on"] = True
    assert adapter.is_ready() is True
    assert await adapter.is_owner_private_chat("1") is True
    assert await adapter.is_owner_private_chat("2") is False
    assert await adapter.send_owner_notice("You have spent $4.10 today.") is True
    assert hub.published[-1]["kind"] == "notice" and hub.published[-1]["level"] == "info"


@pytest.mark.asyncio
@pytest.mark.parametrize("door_on, paired", [
    (True, 0),      # the door is up, but no phone is paired
    (False, 1),     # a phone is paired, but the door is off
])
async def test_an_owner_notice_with_no_reachable_phone_goes_to_the_next_door(
        store, monkeypatch, door_on, paired):
    """Review I1: with policy.proactive.door "mobile" and no phone that can
    hear it, the notice is not "sent" into a timeline nobody reads — the
    door says False, core asks Discord, and the timeline stays empty."""
    from core import doors, owner_notice

    hub = FakeHub()
    adapter = MobileChannelAdapter(owner_id=1, store=store, hub=hub, enabled=lambda: door_on)
    _door_up(monkeypatch, adapter)
    _phones(monkeypatch, paired)
    monkeypatch.setattr(doors, "proactive_door_setting", lambda: "mobile")
    monkeypatch.setattr(owner_notice, "_senders", {})
    told: list[str] = []

    async def discord(text):
        told.append(text)
        return True

    owner_notice.register_owner_notice_sender("mobile", adapter.send_owner_notice)
    owner_notice.register_owner_notice_sender("discord", discord)

    assert await owner_notice.send_owner_notice("You have spent $4.10 today.") == "discord"
    assert told == ["You have spent $4.10 today."]
    page, _ = await store.after(owner_id=1, after=0, limit=10)
    assert page == [] and hub.published == []


def test_a_reminder_envelope_says_it_is_one():
    from core.reminders import binding

    assert 'metadata={"reminder": True}' in inspect.getsource(binding.deliver_reminder)

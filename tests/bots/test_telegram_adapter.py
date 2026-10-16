"""Everything that reaches OUT reaches Telegram too (telegram door, task 2).

Reminders, the arbiter's reach-outs, her hands, the delegation bridge — none
of them know what Discord is. They all end at
``channel_runtime.send_envelope(platform=…, surface_id=…)``. Registering one
adapter under ``"telegram"`` is therefore the entire outbound half of this
door: a reminder set from a Telegram chat fires in that chat with no new
code in the scheduler.
"""
from __future__ import annotations

import pytest

from core.channel_runtime import (
    ChannelAttachment,
    ChannelDeliveryEnvelope,
    ChannelRuntimeRegistry,
)


class FakeBot:
    def __init__(self):
        self.messages: list[dict] = []
        self.documents: list[dict] = []

    async def send_message(self, *, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text})

    async def send_document(self, *, chat_id, document, filename=None, **kw):
        self.documents.append({"chat_id": chat_id, "filename": filename})

    async def send_chat_action(self, *, chat_id, action, **kw):
        pass


class BlockedBot(FakeBot):
    """The live failure this fix exists for: *Forbidden: bot was blocked by
    the user* / *chat not found*. Every send raises; nothing lands."""

    async def send_message(self, *, chat_id, text, **kw):
        raise RuntimeError("Forbidden: bot was blocked by the user")


class RetryAfter(Exception):
    """Telegram's flood-control error BY SHAPE — a class of that name
    carrying ``retry_after``, which is all bots/telegram/reply.py needs and
    exactly what the real exception is. The library is never imported here."""

    def __init__(self, retry_after: float = 0.0) -> None:
        super().__init__("flood control")
        self.retry_after = retry_after


class FloodedBot(FakeBot):
    """Flood control that does not clear: reply.py honours a RetryAfter
    ONCE, and the second refusal is a send that did not happen."""

    def __init__(self):
        super().__init__()
        self.attempts = 0

    async def send_message(self, *, chat_id, text, **kw):
        self.attempts += 1
        raise RetryAfter(0.0)


@pytest.mark.asyncio
async def test_an_envelope_reaches_the_chat_it_names():
    from bots.telegram.adapter import TelegramChannelAdapter

    bot = FakeBot()
    registry = ChannelRuntimeRegistry()
    await registry.register_adapter("telegram", TelegramChannelAdapter(bot),
                                    metadata={"label": "Telegram"})

    out = await registry.send_envelope(
        platform="telegram", surface_id="424242",
        envelope=ChannelDeliveryEnvelope(text="the kettle's boiled"))

    assert bot.messages == [{"chat_id": 424242, "text": "the kettle's boiled"}]
    assert out == {"sent": True, "chat_id": 424242}


@pytest.mark.asyncio
async def test_a_negative_group_id_survives_the_adapter():
    from bots.telegram.adapter import TelegramChannelAdapter

    bot = FakeBot()
    registry = ChannelRuntimeRegistry()
    await registry.register_adapter("telegram", TelegramChannelAdapter(bot))

    await registry.send_envelope(
        platform="telegram", surface_id="-1001234567890",
        envelope=ChannelDeliveryEnvelope(text="in the shop group"))

    assert bot.messages[0]["chat_id"] == -1001234567890


@pytest.mark.asyncio
async def test_an_envelopes_attachments_go_with_it(tmp_path):
    from bots.telegram.adapter import TelegramChannelAdapter

    path = tmp_path / "chart.png"
    path.write_bytes(b"\x89PNG")
    bot = FakeBot()
    adapter = TelegramChannelAdapter(bot)

    await adapter.send_envelope(
        surface_id="424242",
        envelope=ChannelDeliveryEnvelope(
            text="the chart",
            attachments=[ChannelAttachment(kind="file", path=str(path))]))

    assert bot.documents and bot.documents[0]["filename"] == "chart.png"


@pytest.mark.asyncio
async def test_a_surface_id_that_is_not_a_chat_id_is_an_error_not_a_guess():
    from bots.telegram.adapter import TelegramChannelAdapter

    adapter = TelegramChannelAdapter(FakeBot())
    with pytest.raises(RuntimeError, match="surface_id"):
        await adapter.send_envelope(
            surface_id="not-a-chat",
            envelope=ChannelDeliveryEnvelope(text="hello"))


@pytest.mark.asyncio
async def test_the_global_registry_shows_the_door_open_then_shut():
    from bots.telegram.adapter import TelegramChannelAdapter
    from core.channel_runtime import channel_runtime

    bot = FakeBot()
    await channel_runtime.register_adapter(
        "telegram", TelegramChannelAdapter(bot), metadata={"label": "Telegram"})
    try:
        assert "telegram" in channel_runtime.snapshot()["connected_platforms"]
        await channel_runtime.send_envelope(
            platform="telegram", surface_id="424242",
            envelope=ChannelDeliveryEnvelope(text="still here"))
        assert bot.messages[-1]["text"] == "still here"
    finally:
        await channel_runtime.unregister_adapter("telegram", reason="stopped")

    snapshot = channel_runtime.snapshot()
    assert "telegram" not in snapshot["connected_platforms"]
    entry = [p for p in snapshot["platforms"] if p["platform"] == "telegram"][0]
    assert entry["status"] == "disconnected"


# ── a send that never landed (final wave, I1 + M8) ────────────────────────
@pytest.mark.asyncio
async def test_a_send_that_never_landed_is_an_error_not_a_delivery():
    """``send_turn_reply`` swallows its send failures on purpose — one
    unreachable chat must not close the door on a REPLY. But everything that
    reaches out without a turn (reminders, reach-outs, the delegation
    bridge) reads this adapter's answer as custody: reported delivered, a
    reminder is marked sent, transcribed as said, and never dead-lettered.
    So when nothing landed, the adapter raises — and names what killed it."""
    from bots.telegram.adapter import TelegramChannelAdapter

    adapter = TelegramChannelAdapter(BlockedBot())

    with pytest.raises(RuntimeError) as excinfo:
        await adapter.send_envelope(
            surface_id="424242",
            envelope=ChannelDeliveryEnvelope(text="the kettle's boiled"))

    assert "nothing was delivered to chat 424242" in str(excinfo.value)
    assert "RuntimeError" in str(excinfo.value)          # what killed it


@pytest.mark.asyncio
async def test_flood_control_that_never_clears_raises_too():
    from bots.telegram.adapter import TelegramChannelAdapter

    bot = FloodedBot()
    adapter = TelegramChannelAdapter(bot)

    with pytest.raises(RuntimeError) as excinfo:
        await adapter.send_envelope(
            surface_id="424242",
            envelope=ChannelDeliveryEnvelope(text="the kettle's boiled"))

    assert bot.attempts == 2                             # honoured once, no loop
    assert "RetryAfter" in str(excinfo.value), str(excinfo.value)


@pytest.mark.asyncio
async def test_an_envelope_that_is_only_an_attachment_reports_itself_sent(tmp_path):
    """M8: ``sent`` was ``bool(text)``, so a reminder whose whole body is a
    file came back sent=False after the file had gone — the same untruth
    pointing the other way."""
    from bots.telegram.adapter import TelegramChannelAdapter

    path = tmp_path / "chart.png"
    path.write_bytes(b"\x89PNG")
    bot = FakeBot()
    adapter = TelegramChannelAdapter(bot)

    out = await adapter.send_envelope(
        surface_id="424242",
        envelope=ChannelDeliveryEnvelope(
            attachments=[ChannelAttachment(kind="file", path=str(path))]))

    assert bot.documents and bot.documents[0]["filename"] == "chart.png"
    assert out == {"sent": True, "chat_id": 424242}


@pytest.mark.asyncio
async def test_an_empty_envelope_sends_nothing_and_claims_nothing():
    """Nothing to send is not a failed send: no raise, and sent=False."""
    from bots.telegram.adapter import TelegramChannelAdapter

    bot = FakeBot()
    out = await TelegramChannelAdapter(bot).send_envelope(
        surface_id="424242", envelope=ChannelDeliveryEnvelope(text="   "))

    assert bot.messages == [] and out == {"sent": False, "chat_id": 424242}

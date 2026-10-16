"""Whose words these are, on the second door (telegram door, task 2).

``conversations.username`` is read back into every rendered history row.
Persistence took it from ``turn.metadata.discord_username``, which a
Telegram turn has no business carrying — so without the fallback, a whole
door's worth of his messages would persist with a NULL name and come back
anonymous. Telegram's handle rides in ``metadata.extra`` (no schema churn,
task 1's ruling); this is the one place that reads it.

Discord rows are unchanged: that is the regression guard here.
"""
from __future__ import annotations

import pytest

from core.turn.types import Turn, TurnMetadata


@pytest.fixture
def written(monkeypatch):
    import core.conversation_history_store as chs

    rows: list[dict] = []

    class _Store:
        def __init__(self, brain):
            pass

        async def store_conversation(self, **kw):
            rows.append(kw)

    monkeypatch.setattr(chs, "ConversationHistoryStore", _Store)
    return rows


@pytest.mark.asyncio
async def test_a_telegram_turn_persists_its_own_handle(written):
    import core.turn.persistence as per

    turn = Turn(
        surface="telegram_text", owner_id=1, content="hi",
        metadata=TurnMetadata(channel_id=424242,
                              extra={"telegram_username": "dana_example"}))
    await per._persist_user_side_with_content(turn, "hi")

    assert written[0]["platform"] == "telegram"
    assert written[0]["channel_id"] == 424242
    assert written[0]["username"] == "dana_example"


@pytest.mark.asyncio
async def test_a_discord_turns_username_is_untouched(written):
    import core.turn.persistence as per

    turn = Turn(
        surface="discord_text", owner_id=1, content="hi",
        metadata=TurnMetadata(channel_id=67890, discord_username="dana_example"))
    await per._persist_user_side_with_content(turn, "hi")

    assert written[0]["platform"] == "discord"
    assert written[0]["username"] == "dana_example"


@pytest.mark.asyncio
async def test_a_turn_with_no_name_anywhere_persists_none(written):
    import core.turn.persistence as per

    turn = Turn(surface="telegram_text", owner_id=1, content="hi",
                metadata=TurnMetadata(channel_id=424242))
    await per._persist_user_side_with_content(turn, "hi")

    assert written[0]["username"] is None

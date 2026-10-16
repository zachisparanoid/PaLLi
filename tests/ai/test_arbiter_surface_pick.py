"""The arbiter reaches for the door he last used (telegram door, task 2).

Task 1 taught ``core.turn.proactive`` to look across doors and gave
``record_outbound`` a ``platform``. The arbiter still asked for a bare
Discord channel id, so every reach-out she decided to make went out of
Discord whatever door the conversation had been on — a reply in a room he
had not opened for a week.

``_pick_channel`` becomes ``_pick_surface``: the pair ``(platform,
surface_id)``, carried all the way to custody. One rule stays Discord's:
the SPOKEN leg is a Discord voice session, so it is never attempted for
another door — a Telegram reach-out is text, always.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_pick_surface_asks_the_cross_door_picker(monkeypatch):
    import ai.arbiter as arbiter
    import core.turn.proactive as proactive

    async def _picked(*, owner_id, **kw):
        assert owner_id == 7
        return ("telegram", "424242")

    monkeypatch.setattr(proactive, "_pick_proactive_surface", _picked)

    assert await arbiter._pick_surface(owner_id=7) == ("telegram", "424242")


@pytest.mark.asyncio
async def test_no_eligible_door_is_none_never_a_guess(monkeypatch):
    import ai.arbiter as arbiter
    import core.turn.proactive as proactive

    async def _none(*, owner_id, **kw):
        return None

    monkeypatch.setattr(proactive, "_pick_proactive_surface", _none)

    assert await arbiter._pick_surface(owner_id=7) is None


@pytest.mark.asyncio
async def test_a_telegram_release_goes_out_the_telegram_door(monkeypatch):
    import ai.arbiter as arbiter

    sent: list[dict] = []

    async def _record(**kw):
        sent.append(kw)
        return True

    monkeypatch.setattr(arbiter, "_record_outbound", _record)

    out = await arbiter._release_outbound(
        owner_user_id=1, channel_id=424242, text="the kettle's boiled",
        origin="arbiter", extra={}, vm=None, platform="telegram")

    assert out["medium"] == "text"
    assert sent[0]["platform"] == "telegram"
    assert sent[0]["channel_id"] == 424242
    assert sent[0]["surface_id"] == "424242"


@pytest.mark.asyncio
async def test_the_spoken_leg_is_never_tried_off_discord(monkeypatch):
    """A voice moment only exists on Discord. Reaching for it from another
    door would record a spoken line into a session that does not exist."""
    import ai.arbiter as arbiter

    spoken: list[dict] = []
    sent: list[dict] = []

    async def _record_spoken(**kw):
        spoken.append(kw)
        return True

    async def _record(**kw):
        sent.append(kw)
        return True

    monkeypatch.setattr(arbiter, "_record_outbound_spoken", _record_spoken)
    monkeypatch.setattr(arbiter, "_record_outbound", _record)

    out = await arbiter._release_outbound(
        owner_user_id=1, channel_id=424242, text="found it", origin="arbiter",
        extra={}, vm={"runtime_session_key": "voice:sess"}, platform="telegram")

    assert spoken == []
    assert out["medium"] == "text"
    assert sent[0]["platform"] == "telegram"


@pytest.mark.asyncio
async def test_the_discord_spoken_leg_still_works(monkeypatch):
    """The regression guard: step 32's medium rule is untouched on Discord."""
    import ai.arbiter as arbiter

    spoken: list[dict] = []

    async def _record_spoken(**kw):
        spoken.append(kw)
        return True

    monkeypatch.setattr(arbiter, "_record_outbound_spoken", _record_spoken)

    out = await arbiter._release_outbound(
        owner_user_id=1, channel_id=42, text="found it", origin="arbiter",
        extra={}, vm={"runtime_session_key": "voice:sess"}, platform="discord")

    assert out["medium"] == "voice"
    assert spoken[0]["channel_id"] == 42

"""Her mid-task word learns which door it goes out of (telegram door, task 1).

``chime_in`` / ``ask_him`` ride ``duplex.speak_mid_task`` → the arbiter's
ONE release leg → ``record_outbound``. Every step of that chain said
"discord". A chime from a Telegram run would have been delivered to a
Discord channel whose id happens to match the Telegram chat's.

The spoken medium is a Discord voice session (step 32), so it stays where
it is: only a Discord-door act can be released through it.
"""
from __future__ import annotations

import asyncio

import pytest

from core.agent.tools import ToolContext


def _ctx(surface="discord_text", channel_id=555):
    return ToolContext(
        turn_owner_id=1, is_owner=True, surface=surface, channel_id=channel_id,
    )


# ── speak_mid_task ──────────────────────────────────────────────────────

def test_the_default_still_releases_on_discord(monkeypatch):
    from core.agent import duplex as dx
    import ai.arbiter as arb

    seen: dict = {}

    async def fake_probe():
        return None

    async def fake_release(**kw):
        seen.update(kw)
        return {"medium": "text"}

    monkeypatch.setattr(arb, "_probe_voice_moment", fake_probe)
    monkeypatch.setattr(arb, "_release_outbound", fake_release)

    out = asyncio.run(dx.speak_mid_task(
        owner_user_id=1, channel_id=555, text="hey", origin="duplex_chime",
    ))

    assert out == {"medium": "text"}
    assert seen["platform"] == "discord"
    assert seen["extra"] == {"mid_task": True}


def test_a_telegram_act_releases_on_telegram(monkeypatch):
    from core.agent import duplex as dx
    import ai.arbiter as arb

    seen: dict = {}

    async def fake_release(**kw):
        seen.update(kw)
        return {"medium": "text"}

    monkeypatch.setattr(arb, "_release_outbound", fake_release)

    asyncio.run(dx.speak_mid_task(
        owner_user_id=1, channel_id=424242, text="hey", origin="duplex_chime",
        platform="telegram",
    ))

    assert seen["platform"] == "telegram"
    assert seen["channel_id"] == 424242


def test_a_telegram_act_never_takes_the_discord_voice_leg(monkeypatch):
    """A live Discord voice moment is not a reason to speak a Telegram
    chime aloud into a room the message was never addressed to."""
    from core.agent import duplex as dx
    import ai.arbiter as arb

    probed: list = []
    seen: dict = {}

    async def fake_probe():
        probed.append(True)
        return {"runtime_session_key": "vs-1"}

    async def fake_release(**kw):
        seen.update(kw)
        return {"medium": "text"}

    monkeypatch.setattr(arb, "_probe_voice_moment", fake_probe)
    monkeypatch.setattr(arb, "_release_outbound", fake_release)

    asyncio.run(dx.speak_mid_task(
        owner_user_id=1, channel_id=424242, text="hey", origin="duplex_chime",
        platform="telegram",
    ))

    assert probed == []
    assert seen["vm"] is None


def test_a_discord_act_still_probes_the_voice_moment(monkeypatch):
    from core.agent import duplex as dx
    import ai.arbiter as arb

    probed: list = []
    seen: dict = {}

    async def fake_probe():
        probed.append(True)
        return {"runtime_session_key": "vs-1"}

    async def fake_release(**kw):
        seen.update(kw)
        return {"medium": "voice"}

    monkeypatch.setattr(arb, "_probe_voice_moment", fake_probe)
    monkeypatch.setattr(arb, "_release_outbound", fake_release)

    asyncio.run(dx.speak_mid_task(
        owner_user_id=1, channel_id=555, text="hey", origin="duplex_chime",
    ))

    assert probed == [True]
    assert seen["vm"] == {"runtime_session_key": "vs-1"}


def test_a_mid_task_word_on_a_shared_call_keeps_its_medium(monkeypatch):
    """The owner's 10-13 rule (a say or share for him alone is spoken only
    when the call is him and her alone) is for her own messages to him. A
    chime or an ask belongs to its job and goes back where the job is, so
    its medium rule is unchanged: a live voice moment still speaks it."""
    from core.agent import duplex as dx
    import ai.arbiter as arb

    spoken: list = []
    sent: list = []

    async def fake_probe():
        return {"runtime_session_key": "vs-1", "alone_with_her": False}

    async def fake_spoken(**kw):
        spoken.append(kw)
        return True

    async def fake_text(**kw):
        sent.append(kw)
        return True

    monkeypatch.setattr(arb, "_probe_voice_moment", fake_probe)
    monkeypatch.setattr(arb, "_record_outbound_spoken", fake_spoken)
    monkeypatch.setattr(arb, "_record_outbound", fake_text)

    out = asyncio.run(dx.speak_mid_task(
        owner_user_id=1, channel_id=555, text="found it", origin="duplex_chime",
    ))

    assert out == {"medium": "voice"}
    assert spoken[0]["runtime_session_key"] == "vs-1" and sent == []


# ── the release leg ─────────────────────────────────────────────────────

def test_the_release_leg_hands_the_platform_to_custody(monkeypatch):
    import ai.arbiter as arb

    sent: list = []

    async def fake_record(**kw):
        sent.append(kw)
        return True

    monkeypatch.setattr(arb, "_record_outbound", fake_record)

    asyncio.run(arb._release_outbound(
        owner_user_id=1, channel_id=424242, text="hey", origin="duplex_chime",
        extra={}, vm=None, platform="telegram",
    ))

    assert sent[0]["platform"] == "telegram"


def test_the_release_leg_defaults_to_discord(monkeypatch):
    """Every existing caller passes no platform and must stay identical."""
    import ai.arbiter as arb

    sent: list = []

    async def fake_record(**kw):
        sent.append(kw)
        return True

    monkeypatch.setattr(arb, "_record_outbound", fake_record)

    out = asyncio.run(arb._release_outbound(
        owner_user_id=1, channel_id=123, text="hey", origin="arbiter",
        extra={}, vm=None,
    ))

    assert out == {"medium": "text"}
    assert sent[0]["platform"] == "discord"


# ── the two hands ───────────────────────────────────────────────────────

def test_chime_in_from_discord_says_discord(monkeypatch):
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import ChimeInTool

    seen: dict = {}

    async def fake_speak(**kw):
        seen.update(kw)
        return {"medium": "text"}

    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    asyncio.run(ChimeInTool().execute({"text": "found it"}, _ctx()))

    # Byte-identical: the Discord call is the four arguments it has always
    # been. "discord" is speak_mid_task's default and a call site does not
    # name a default (tests/core/test_duplex_hands.py:26 pins this shape).
    assert set(seen) == {"owner_user_id", "channel_id", "text", "origin"}
    assert seen["channel_id"] == 555


def test_chime_in_from_telegram_says_telegram(monkeypatch):
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import ChimeInTool

    seen: dict = {}

    async def fake_speak(**kw):
        seen.update(kw)
        return {"medium": "text"}

    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    asyncio.run(ChimeInTool().execute(
        {"text": "found it"}, _ctx(surface="telegram_text", channel_id=424242),
    ))

    assert seen["platform"] == "telegram"
    assert seen["channel_id"] == 424242


def test_a_surfaceless_context_still_says_discord(monkeypatch):
    """Nothing in the tree builds a surfaceless duplex ctx today, but the
    fallback is the old behaviour, never None."""
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import ChimeInTool

    seen: dict = {}

    async def fake_speak(**kw):
        seen.update(kw)
        return {"medium": "text"}

    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    asyncio.run(ChimeInTool().execute({"text": "found it"}, _ctx(surface="")))

    assert "platform" not in seen        # the default, unnamed


def test_ask_him_from_telegram_says_telegram(monkeypatch):
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import AskHimTool

    seen: dict = {}

    async def fake_speak(**kw):
        seen.update(kw)
        return {"medium": "text"}

    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    asyncio.run(AskHimTool().execute(
        {"question": "can you clear the captcha?"},
        _ctx(surface="telegram_text", channel_id=424242),
    ))

    assert seen["platform"] == "telegram"


@pytest.mark.parametrize(
    "surface,channel_id,expected",
    [
        ("discord_text", 555, "task:discord:555"),
        ("telegram_text", 424242, "task:telegram:424242"),
    ],
)
def test_a_parked_question_names_the_door_it_was_asked_on(
    monkeypatch, surface, channel_id, expected,
):
    """The parked FOLLOWUP's source_id IS the session key; a Telegram
    question parked under discord:<chat> resurfaces in the wrong room."""
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import AskHimTool
    import core.database as db_mod

    async def fake_speak(**kw):
        return {"medium": "text"}

    parked: list = []

    class _FakeBrain:
        async def add_open_thread(self, **kw):
            parked.append(kw)

    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
    monkeypatch.setattr(db_mod, "brain", _FakeBrain())

    asyncio.run(AskHimTool().execute(
        {"question": "can you clear the captcha?"}, _ctx(surface, channel_id),
    ))

    assert parked and parked[0]["source_id"] == expected

"""The session key learns a second word (telegram door, task 1).

``_derive_session_key`` decides which transcript file a turn reads and
writes. A Telegram turn must land on ``telegram:<chat_id>`` — its own
file — rather than on the generic ``telegram_text:<owner_id>`` fallback,
so two chats never share one memory.

``ai.callbacks_lookup.session_key_for`` is a byte-equal MIRROR of the
runner's derivation (pinned by tests/ai/test_callbacks_lookup.py). It
learns the same branch in the same position here, or the two drift.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import ai.callbacks_lookup as lk
from core.agent.runner import _derive_session_key


def _turn(surface: str, channel_id: int = 0, owner_id: int = 1, extra=None):
    return SimpleNamespace(
        surface=surface,
        owner_id=owner_id,
        metadata=SimpleNamespace(channel_id=channel_id, extra=dict(extra or {})),
    )


def test_a_discord_turn_still_keys_on_its_channel():
    assert _derive_session_key(_turn("discord_text", 424242)) == "discord:424242"


def test_a_telegram_turn_keys_on_its_chat():
    assert _derive_session_key(_turn("telegram_text", 424242)) == "telegram:424242"


def test_two_telegram_chats_never_share_a_key():
    assert _derive_session_key(_turn("telegram_text", 777)) != _derive_session_key(
        _turn("telegram_text", 778)
    )


def test_a_telegram_turn_with_no_chat_id_falls_through_like_discord_does():
    """Both doors share the shape: no address → the generic owner key."""
    assert _derive_session_key(_turn("telegram_text", 0, owner_id=7)) == "telegram_text:7"
    assert _derive_session_key(_turn("discord_text", 0, owner_id=7)) == "discord_text:7"


def test_the_sidecar_override_still_wins_on_either_door():
    for surface in ("discord_text", "telegram_text"):
        turn = _turn(surface, 424242, extra={"session_key_override": "child:key"})
        assert _derive_session_key(turn) == "child:key"


def test_the_other_surfaces_are_unchanged():
    assert _derive_session_key(_turn("voice_local", 0, owner_id=3)) == "voice:3"
    assert _derive_session_key(_turn("dashboard", 0, owner_id=3)) == "dashboard:3"
    assert _derive_session_key(_turn("proactive", 0, owner_id=3)) == "proactive:3"
    assert _derive_session_key(_turn("weird", 0, owner_id=3)) == "weird:3"


@pytest.mark.parametrize(
    "surface,channel_id,owner_id",
    [
        ("discord_text", 424242, 1),
        ("telegram_text", 424242, 1),
        ("telegram_text", 0, 7),
        ("discord_voice", 42, 1),
        ("voice_local", 0, 1),
        ("dashboard", 0, 7),
        ("proactive", 0, 1),
        ("weird", 0, 3),
    ],
)
def test_the_callbacks_mirror_agrees_on_every_door(surface, channel_id, owner_id):
    assert lk.session_key_for(
        surface=surface, channel_id=channel_id, owner_id=owner_id
    ) == _derive_session_key(_turn(surface, channel_id, owner_id))


def test_the_mirrors_session_path_sanitizes_a_telegram_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    assert lk.session_path_for(
        surface="telegram_text", channel_id=424242, owner_id=1
    ) == tmp_path / "telegram_424242.jsonl"


# ── Batch 8, task 1 (B8-1, rulings R3/R7/R11): the keys built in one place ──

def test_the_room_key_is_the_chat_doors_transcript_key():
    from core.session_keys import room_key

    assert room_key("discord", 424242) == "discord:424242" == _derive_session_key(_turn("discord_text", 424242))
    assert room_key("telegram", -100777) == "telegram:-100777" == _derive_session_key(
        _turn("telegram_text", -100777))


def test_a_guest_run_key_hangs_off_the_room_key():
    from core.session_keys import guest_run_key, room_key

    assert guest_run_key(room_key("discord", 555), 42) == "discord:555:guest:42"
    assert guest_run_key("telegram:-100777", 9) == "telegram:-100777:guest:9"
    # Never the room's own key, never another guest's.
    assert guest_run_key("discord:555", 42) not in ("discord:555", guest_run_key("discord:555", 43))


@pytest.mark.parametrize(
    "surface,channel_id,owner_id",
    [
        ("discord_text", 424242, 1), ("telegram_text", 424242, 1), ("telegram_text", 0, 7),
        ("discord_voice", 42, 1), ("voice_local", 0, 1), ("dashboard", 0, 7),
        ("proactive", 0, 1), ("weird", 0, 3), ("reminder_fire", 0, 1), ("workbench", 0, 1),
    ],
)
def test_the_runner_derives_its_key_through_the_one_helper(surface, channel_id, owner_id):
    from core.session_keys import session_key_for

    assert _derive_session_key(_turn(surface, channel_id, owner_id)) == session_key_for(
        surface, channel_id, owner_id)


def test_the_not_delivered_mark_finds_the_room_file_through_the_helper():
    """core/turn/persistence.py builds the room key with room_key(), not a
    literal copy of its shape."""
    import inspect

    import core.turn.persistence as persistence

    src = inspect.getsource(persistence.persist_delivery_failure)
    assert "room_key(" in src and 'f"{platform}:{int(channel_id)}"' not in src

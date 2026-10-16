"""The phone app is a third private door (spec §7, §8): every door list
names it, the R16 rule holds for it (it is his alone, never a group), and the
owner's reach-out door setting routes to it."""
from __future__ import annotations

import sqlite3
from typing import get_args
from unittest.mock import MagicMock, patch

import pytest

from core import doors, owner_notice
from core.session_keys import session_key_for
from core.turn import proactive
from core.turn.recall import _platform_for_surface, platform_for_surface

PRIVATE = '{"is_dm": true}'


GROUP = '{"is_dm": false}'


def _db(tmp_path, rows):
    """``rows``: ``(platform, channel_id, ts)``, marked private, or
    ``(platform, channel_id, ts, metadata)``."""
    db_path = tmp_path / "conversations.sqlite3"
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY, user_id INTEGER, platform TEXT, "
        "channel_id INTEGER, timestamp REAL, role TEXT, content TEXT, metadata TEXT)")
    for platform, channel_id, ts, *mark in rows:
        con.execute(
            "INSERT INTO conversations (user_id, platform, channel_id, timestamp, role, content, metadata) "
            "VALUES (?, ?, ?, ?, 'user', 'said something', ?)",
            (1, platform, channel_id, ts, mark[0] if mark else PRIVATE))
    con.commit()
    con.close()
    return db_path


def _cfg(db_path):
    cfg = MagicMock()
    cfg.DATABASE_PATH = str(db_path)
    cfg.PROACTIVE_ALLOWED_CHANNEL_IDS = []
    cfg.PROACTIVE_ALLOWED_GUILD_IDS = []
    return cfg


@pytest.fixture(autouse=True)
def _no_doors_registered(monkeypatch):
    from core.channel_runtime import channel_runtime

    monkeypatch.setattr(channel_runtime, "_adapters", {})


class _Door:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def send_envelope(self, *, surface_id, envelope):
        return {"sent": True}

    def is_ready(self) -> bool:
        return self.ready


def _register(monkeypatch, platform: str, *, ready: bool = True) -> None:
    from core.channel_runtime import channel_runtime

    monkeypatch.setitem(channel_runtime._adapters, platform, _Door(ready))


def _prefer(monkeypatch, door: str) -> None:
    monkeypatch.setattr(doors, "proactive_door_setting", lambda: door)


def _phones(monkeypatch, count: int) -> None:
    import core.mobile_auth as mobile_auth

    class _Devices:
        async def count_devices(self, *, owner_id=None):
            return count

    monkeypatch.setattr(mobile_auth, "device_store", lambda: _Devices())


def test_the_phone_app_is_a_chat_surface_with_its_own_room():
    from core.turn.persistence import _CHAT_SURFACES
    from core.turn.types import SurfaceName

    assert "mobile_text" in get_args(SurfaceName)
    assert _platform_for_surface("mobile_text") == "mobile"
    assert platform_for_surface("mobile_text") == "mobile"
    assert session_key_for("mobile_text", 1, 7) == "mobile:1"
    assert "mobile_text" in _CHAT_SURFACES          # its rows carry the is_dm mark
    assert doors.CHAT_DOORS == ("discord", "telegram", "mobile")
    assert doors.WEB_DOOR not in doors.CHAT_DOORS


@pytest.mark.asyncio
async def test_the_phone_app_is_private_with_him_and_never_a_group():
    assert await doors.is_private_chat("mobile", 1, marked_private=False, marked_group=False) is True
    assert await doors.is_private_chat("mobile", 0, marked_private=True, marked_group=False) is False
    assert await doors.is_private_chat("mobile", 1, marked_private=True, marked_group=True) is False


@pytest.mark.asyncio
async def test_last_used_picks_the_phone_when_he_wrote_there_last(tmp_path, monkeypatch):
    """The regression guard for review I1: a phone is paired and the door is
    up, so his newest row, on the phone, still wins."""
    _prefer(monkeypatch, "last_used")
    _register(monkeypatch, "mobile")
    _phones(monkeypatch, 1)
    db_path = _db(tmp_path, [("telegram", 424242, 1000.0), ("mobile", 1, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("mobile", "1")


@pytest.mark.asyncio
@pytest.mark.parametrize("registered, ready, paired", [
    (True, True, 0),      # every phone is unpaired (a phone swap)
    (True, False, 1),     # the door says it is not ready
    (False, True, 1),     # the door is not up
])
async def test_last_used_skips_a_phone_that_cannot_hear_her(
        tmp_path, monkeypatch, registered, ready, paired):
    """Review I1 (ledger T3 L63, T8 L115): his newest row is on the phone, but
    no phone can hear her, so the scan falls through to the private chat he
    used before it."""
    _prefer(monkeypatch, "last_used")
    if registered:
        _register(monkeypatch, "mobile", ready=ready)
    _phones(monkeypatch, paired)
    db_path = _db(tmp_path, [("telegram", 424242, 1000.0), ("mobile", 1, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("telegram", "424242")


@pytest.mark.asyncio
async def test_a_preferred_phone_wins_once_one_is_paired(tmp_path, monkeypatch):
    _prefer(monkeypatch, "mobile")
    _register(monkeypatch, "mobile")
    _phones(monkeypatch, 1)
    db_path = _db(tmp_path, [("telegram", 424242, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("mobile", "1")


@pytest.mark.asyncio
@pytest.mark.parametrize("registered, ready, paired", [
    (False, True, 1),     # the door is not up
    (True, False, 1),     # the door says it is not ready
    (True, True, 0),      # no phone is paired
])
async def test_a_preferred_door_that_is_closed_falls_back_to_last_used(
        tmp_path, monkeypatch, registered, ready, paired):
    _prefer(monkeypatch, "mobile")
    if registered:
        _register(monkeypatch, "mobile", ready=ready)
    _phones(monkeypatch, paired)
    db_path = _db(tmp_path, [("telegram", 424242, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("telegram", "424242")


@pytest.mark.asyncio
async def test_a_preferred_telegram_takes_his_private_chat_there_over_a_newer_discord(tmp_path, monkeypatch):
    _prefer(monkeypatch, "telegram")
    _register(monkeypatch, "telegram")
    db_path = _db(tmp_path, [("telegram", 424242, 1000.0), ("discord", 111, 2000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("telegram", "424242")


@pytest.mark.asyncio
async def test_a_preferred_telegram_that_is_a_group_is_never_her_destination(tmp_path, monkeypatch):
    """R16 on the preference road (ledger T3 L60): he prefers Telegram, but
    his Telegram chat with her is a group his friends can read; his Discord
    DM is private, so she goes there."""
    _prefer(monkeypatch, "telegram")
    _register(monkeypatch, "telegram")
    _register(monkeypatch, "discord")
    db_path = _db(tmp_path, [("discord", 111, 1000.0), ("telegram", -100424242, 2000.0, GROUP)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1) == ("discord", "111")


@pytest.mark.asyncio
async def test_a_preference_outside_the_doors_asked_about_is_ignored(tmp_path, monkeypatch):
    _prefer(monkeypatch, "mobile")
    _register(monkeypatch, "mobile")
    _phones(monkeypatch, 1)
    db_path = _db(tmp_path, [("discord", 111, 1000.0)])
    with patch("core.config.config", _cfg(db_path)):
        assert await proactive._pick_proactive_surface(owner_id=1, platforms=("discord",)) == ("discord", "111")


@pytest.mark.asyncio
async def test_owner_notices_ask_the_preferred_door_first(monkeypatch):
    monkeypatch.setattr(owner_notice, "_senders", {})
    asked: list[str] = []

    def _door(name):
        async def send(text):
            asked.append(name)
            return True
        return send

    for name in ("mobile", "telegram", "discord"):
        owner_notice.register_owner_notice_sender(name, _door(name))

    assert owner_notice.NOTICE_ORDER == ("discord", "telegram", "mobile")
    _prefer(monkeypatch, "last_used")
    assert await owner_notice.send_owner_notice("one") == "discord"
    _prefer(monkeypatch, "mobile")
    assert await owner_notice.send_owner_notice("two") == "mobile"
    assert await owner_notice.send_owner_notice("three", order=("telegram",)) == "telegram"


def test_the_setting_defaults_to_last_used_and_names_only_doors():
    from pydantic import ValidationError

    from core.settings_schema import ChannelsConfig, ProactivePolicy

    assert ProactivePolicy(max_per_day=3, min_gap_hours=2.0).door == "last_used"
    assert ProactivePolicy(max_per_day=3, min_gap_hours=2.0, door="mobile").door == "mobile"
    with pytest.raises(ValidationError):
        ProactivePolicy(max_per_day=3, min_gap_hours=2.0, door="dashboard")
    assert ChannelsConfig().mobile.enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("surface, platform", [
    ("telegram_text", "telegram"), ("mobile_text", "mobile"), ("discord_text", "discord"),
    ("discord_voice", "discord"), ("", "discord"),
])
async def test_a_forge_follow_up_goes_back_out_of_the_askers_door(monkeypatch, surface, platform):
    import ai.forge as forge

    sent: dict = {}

    async def refs():
        return {"app_user_id": 42}

    async def dispatch(turn):
        return MagicMock(response_text="here it is")

    async def record(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(forge, "_get_owner_refs", refs)
    monkeypatch.setattr(forge, "_dispatch", dispatch)
    monkeypatch.setattr(forge, "_record_outbound", record)
    monkeypatch.setattr(forge, "_followup_turn", lambda content, owner_id, channel_id: object())

    await forge._followup(green=True, name="t", original_ask="a", origin_channel_id=777, surface=surface)
    assert sent["platform"] == platform and sent["channel_id"] == 777

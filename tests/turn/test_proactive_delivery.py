"""Tests for proactive Discord delivery added in core.turn.proactive."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_pick_channel_picks_most_recent(tmp_path):
    """Multiple discord DMs → pick the most recent channel. (Since 10-13 only
    a private chat with him is picked; each row here is marked a DM.)"""
    from core.turn import proactive

    db_path = tmp_path / "test.sqlite3"
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id INTEGER, platform TEXT, channel_id INTEGER,
            timestamp REAL, role TEXT, content TEXT, metadata TEXT
        )
    """)
    con.execute(
        "INSERT INTO conversations (user_id, platform, channel_id, timestamp, role, content, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, '{\"is_dm\": true}')",
        (1, "discord", 111, 1000.0, "user", "old"),
    )
    con.execute(
        "INSERT INTO conversations (user_id, platform, channel_id, timestamp, role, content, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, '{\"is_dm\": true}')",
        (1, "discord", 222, 2000.0, "user", "newer"),
    )
    con.commit()
    con.close()

    fake_cfg = MagicMock()
    fake_cfg.DATABASE_PATH = str(db_path)
    fake_cfg.PROACTIVE_ALLOWED_CHANNEL_IDS = []
    fake_cfg.PROACTIVE_ALLOWED_GUILD_IDS = []

    with patch("core.config.config", fake_cfg):
        result = await proactive._pick_proactive_channel_id(owner_id=1)
    assert result == 222


@pytest.mark.asyncio
async def test_pick_channel_respects_allowlist(tmp_path):
    """If PROACTIVE_ALLOWED_CHANNEL_IDS is set, pick from that list only.
    (Each row is marked a DM: since 10-13 only a private chat is picked.)"""
    from core.turn import proactive

    db_path = tmp_path / "test.sqlite3"
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id INTEGER, platform TEXT, channel_id INTEGER,
            timestamp REAL, role TEXT, content TEXT, metadata TEXT
        )
    """)
    con.execute(
        "INSERT INTO conversations (user_id, platform, channel_id, timestamp, role, content, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, '{\"is_dm\": true}')",
        (1, "discord", 111, 1000.0, "user", "in-list"),
    )
    con.execute(
        "INSERT INTO conversations (user_id, platform, channel_id, timestamp, role, content, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, '{\"is_dm\": true}')",
        (1, "discord", 999, 2000.0, "user", "newer-but-not-in-list"),
    )
    con.commit()
    con.close()

    fake_cfg = MagicMock()
    fake_cfg.DATABASE_PATH = str(db_path)
    fake_cfg.PROACTIVE_ALLOWED_CHANNEL_IDS = [111]  # only 111 is allowed
    fake_cfg.PROACTIVE_ALLOWED_GUILD_IDS = []

    with patch("core.config.config", fake_cfg):
        result = await proactive._pick_proactive_channel_id(owner_id=1)
    assert result == 111  # picked the older but allowed channel, not the newer disallowed one


@pytest.mark.asyncio
async def test_pick_channel_returns_none_when_no_history(tmp_path):
    from core.turn import proactive

    db_path = tmp_path / "test.sqlite3"
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id INTEGER, platform TEXT, channel_id INTEGER,
            timestamp REAL, role TEXT, content TEXT, metadata TEXT
        )
    """)
    con.commit()
    con.close()

    fake_cfg = MagicMock()
    fake_cfg.DATABASE_PATH = str(db_path)
    fake_cfg.PROACTIVE_ALLOWED_CHANNEL_IDS = []
    fake_cfg.PROACTIVE_ALLOWED_GUILD_IDS = []

    with patch("core.config.config", fake_cfg):
        result = await proactive._pick_proactive_channel_id(owner_id=1)
    assert result is None


@pytest.mark.asyncio
async def test_deliver_to_discord_calls_channel_runtime(tmp_path):
    """When a proactive turn produces a reply, channel_runtime.send_envelope
    is called with the picked channel."""
    from core.turn import proactive

    fake_result = MagicMock()
    fake_result.responded = True
    fake_result.response_text = "Hey, just thinking about you"
    fake_result.attachments = ()

    fake_envelope_send = AsyncMock(return_value={"sent": True})

    with patch.object(
        proactive, "_pick_proactive_channel_id", AsyncMock(return_value=222),
    ), patch(
        "core.channel_runtime.channel_runtime.send_envelope", fake_envelope_send,
    ):
        await proactive._deliver_proactive_to_discord(owner_id=1, result=fake_result)

    fake_envelope_send.assert_awaited_once()
    call_args = fake_envelope_send.await_args
    assert call_args.kwargs["platform"] == "discord"
    assert call_args.kwargs["surface_id"] == "222"


@pytest.mark.asyncio
async def test_deliver_skipped_when_no_channel(tmp_path):
    """When channel selection returns None, send_envelope is NOT called."""
    from core.turn import proactive

    fake_result = MagicMock()
    fake_result.responded = True
    fake_result.response_text = "test"
    fake_result.attachments = ()

    fake_envelope_send = AsyncMock()

    with patch.object(
        proactive, "_pick_proactive_channel_id", AsyncMock(return_value=None),
    ), patch(
        "core.channel_runtime.channel_runtime.send_envelope", fake_envelope_send,
    ):
        await proactive._deliver_proactive_to_discord(owner_id=1, result=fake_result)

    fake_envelope_send.assert_not_called()

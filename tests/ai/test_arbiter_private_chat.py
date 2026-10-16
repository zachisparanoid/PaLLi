"""Her reach-outs and shares go to a private chat with him (the owner, 10-13).

The arbiter's say (her check-ins, follow-ups, tonight's pick, a parked ask
coming back) and its share lane (her work, done) are messages for the owner
alone. They take "the private chat he used last" — a Discord DM or a Telegram
private chat — never the newest chat he typed in when that is a server
channel or a group. On 10-12 at 08:10 one went to a server channel his
friends could see. No private chat means she holds; she never falls back to
a group.

Every gate is open here, as in tests/ai/test_arbiter.py, except the pick:
it runs for real, on a real temporary database.
"""
from __future__ import annotations

import sqlite3
import time
import types

import pytest

import ai.arbiter as arbiter
from tests.ai.test_arbiter import _Comp, _say

HIS_DM = 5001
SERVER_CHANNEL = 9004
HIS_TELEGRAM = 424242
TELEGRAM_GROUP = -100555
PRIVATE = '{"is_dm": true}'
GROUP = '{"is_dm": false}'


def _his_rows(tmp_path, rows):
    path = tmp_path / "conversations.sqlite3"
    con = sqlite3.connect(str(path))
    con.execute(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            user_id INTEGER, platform TEXT, channel_id INTEGER,
            timestamp REAL, role TEXT, content TEXT, metadata TEXT
        )
        """
    )
    for platform, channel_id, ts, metadata in rows:
        con.execute(
            "INSERT INTO conversations "
            "(user_id, platform, channel_id, timestamp, role, content, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, platform, channel_id, ts, "user", "said something", metadata),
        )
    con.commit()
    con.close()
    return path


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """The arbiter on and every moment gate open; the send recorded, not
    made. ``_pick_surface`` is left alone: it asks core.turn.proactive."""
    from core.channel_runtime import channel_runtime
    from core.config import config

    monkeypatch.setattr(arbiter, "ARBITER_DATA_DIR", tmp_path / "arbiter")
    comp = _Comp()
    monkeypatch.setattr(arbiter, "_companion", lambda: comp)

    async def _fresh_ok(**kw):
        return (True, "fresh (fixture)")

    async def _last_msg(*, owner_id):
        return time.time() - 3600.0

    sent = []

    async def _record(**kw):
        sent.append(kw)
        return True

    async def _mark(*, thread_id):
        return None

    async def _actives(*, owner_user_id):
        return []

    monkeypatch.setattr(arbiter, "_freshness_check", _fresh_ok)
    monkeypatch.setattr(arbiter, "_last_user_message_at", _last_msg)
    monkeypatch.setattr(arbiter, "_record_outbound", _record)
    monkeypatch.setattr(arbiter, "_mark_surfaced", _mark)
    monkeypatch.setattr(arbiter, "_list_active", _actives)
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: False)
    monkeypatch.setattr(config, "PROACTIVE_ALLOWED_CHANNEL_IDS", [], raising=False)
    monkeypatch.setattr(channel_runtime, "_adapters", {})

    def _rows(rows):
        monkeypatch.setattr(config, "DATABASE_PATH", str(_his_rows(tmp_path, rows)))

    return types.SimpleNamespace(comp=comp, sent=sent, rows=_rows)


@pytest.mark.asyncio
async def test_her_say_reaches_his_dm_not_the_newer_server_channel(wired):
    wired.rows([("discord", HIS_DM, 1000.0, PRIVATE),
                ("discord", SERVER_CHANNEL, 2000.0, GROUP)])

    out = await arbiter.consider_release(impulse=_say(), owner_user_id=1, arbiter_live=True)

    assert out["disposition"] == "sent"
    assert len(wired.sent) == 1
    assert (wired.sent[0]["platform"], wired.sent[0]["channel_id"]) == ("discord", HIS_DM)


@pytest.mark.asyncio
async def test_her_share_reaches_his_telegram_chat_not_the_newer_group(wired):
    wired.rows([("telegram", HIS_TELEGRAM, 1000.0, PRIVATE),
                ("discord", SERVER_CHANNEL, 2000.0, GROUP),
                ("telegram", TELEGRAM_GROUP, 3000.0, GROUP)])

    out = await arbiter.deliver_share(text="the lake page has a sky-history now",
                                      owner_user_id=1)

    assert out["disposition"] == "sent"
    assert len(wired.sent) == 1
    assert (wired.sent[0]["platform"], wired.sent[0]["channel_id"]) == ("telegram", HIS_TELEGRAM)


@pytest.mark.asyncio
async def test_with_only_groups_she_holds_and_sends_nothing(wired):
    wired.rows([("discord", SERVER_CHANNEL, 1000.0, GROUP),
                ("telegram", TELEGRAM_GROUP, 2000.0, GROUP)])

    say = await arbiter.consider_release(impulse=_say(), owner_user_id=1, arbiter_live=True)
    share = await arbiter.deliver_share(text="made a thing", owner_user_id=1)

    assert say == {"disposition": "held_no_channel"}
    assert share == {"disposition": "held_no_channel"}
    assert wired.sent == []

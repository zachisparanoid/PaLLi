"""Custody primitive (arbiter spec §5): send → DB row → discord transcript,
in that order; bookkeeping failures are swallowed; a send failure raises
before anything is recorded."""
import json

import pytest

from core.turn import outbound


class _FakeRuntime:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_envelope(self, *, platform, surface_id, envelope):
        if self.fail:
            raise RuntimeError("discord down")
        self.sent.append((platform, surface_id, envelope.text))


@pytest.mark.asyncio
async def test_record_outbound_full_custody(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setattr("core.channel_runtime.channel_runtime", runtime)
    persisted = {}

    async def _fake_persist(*, turn, response_text):
        persisted["turn"] = turn
        persisted["text"] = response_text

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _fake_persist
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))

    ok = await outbound.record_outbound(
        owner_id=1, channel_id=123, text="  hey — the ferret verdict?  ",
        origin="arbiter", extra={"thread_id": "abc"},
    )
    assert ok is True
    assert runtime.sent == [("discord", "123", "hey — the ferret verdict?")]
    assert persisted["text"] == "hey — the ferret verdict?"
    assert persisted["turn"].surface == "discord_text"
    assert persisted["turn"].metadata.channel_id == 123
    assert persisted["turn"].metadata.extra["self_initiated"] is True
    assert persisted["turn"].metadata.extra["thread_id"] == "abc"
    assert persisted["turn"].metadata.origin == "arbiter"
    lines = (tmp_path / "discord_123.jsonl").read_text().strip().splitlines()
    roles = [json.loads(l)["role"] for l in lines if l.strip()]
    assert roles[-1] == "assistant"


@pytest.mark.asyncio
async def test_record_outbound_send_failure_records_nothing(monkeypatch, tmp_path):
    runtime = _FakeRuntime(fail=True)
    monkeypatch.setattr("core.channel_runtime.channel_runtime", runtime)
    called = []

    async def _fake_persist(*, turn, response_text):
        called.append(1)

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _fake_persist
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError):
        await outbound.record_outbound(owner_id=1, channel_id=123, text="hi")
    assert called == []
    assert not (tmp_path / "discord_123.jsonl").exists()


@pytest.mark.asyncio
async def test_record_outbound_bookkeeping_failures_swallowed(monkeypatch, tmp_path):
    runtime = _FakeRuntime()
    monkeypatch.setattr("core.channel_runtime.channel_runtime", runtime)

    async def _boom(*, turn, response_text):
        raise RuntimeError("db locked")

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _boom
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    ok = await outbound.record_outbound(owner_id=1, channel_id=9, text="hello")
    assert ok is True                      # send happened; custody hiccup logged
    assert len(runtime.sent) == 1


@pytest.mark.asyncio
async def test_record_outbound_empty_text_or_channel_is_noop(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setattr("core.channel_runtime.channel_runtime", runtime)
    assert await outbound.record_outbound(owner_id=1, channel_id=0, text="hi") is False
    assert await outbound.record_outbound(owner_id=1, channel_id=5, text="  ") is False
    assert runtime.sent == []


@pytest.mark.asyncio
async def test_mid_task_act_keeps_db_custody_but_never_writes_the_transcript(monkeypatch, tmp_path):
    """Step 27 (2026-08-28): a chime_in / ask_him rides this primitive from
    INSIDE a run — the tool_use and its result already are the transcript
    record, and the run holds the session. A second writer here landed her
    words between an open tool_use and its result (12:59 live). Mid-task:
    send + DB row + deed, no transcript line."""
    runtime = _FakeRuntime()
    monkeypatch.setattr("core.channel_runtime.channel_runtime", runtime)
    persisted = {}

    async def _fake_persist(*, turn, response_text):
        persisted["text"] = response_text

    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", _fake_persist
    )
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    ok = await outbound.record_outbound(
        owner_id=1, channel_id=123, text="found the part number — a 9225",
        origin="duplex_chime", extra={"mid_task": True},
    )
    assert ok is True
    assert runtime.sent == [("discord", "123", "found the part number — a 9225")]
    assert persisted["text"] == "found the part number — a 9225"
    assert not (tmp_path / "discord_123.jsonl").exists()

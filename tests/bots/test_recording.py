# tests/bots/test_recording.py
from __future__ import annotations
import asyncio
from types import SimpleNamespace
from pathlib import Path
from tests.agent.conftest import load_module

def test_record_user_fragment_writes_transcript(tmp_path, monkeypatch):
    mod = load_module("bots/discord/recording.py", "recording_under_test")
    import core.agent.session as sess_mod

    # Stub the DB persist (Task covers transcript here; DB is a detached task).
    async def fake_db(turn, content): fake_db.calls.append(content)
    fake_db.calls = []
    monkeypatch.setattr(mod, "_persist_user_side_with_content", fake_db)

    turn = SimpleNamespace(
        surface="discord_text", owner_id=1, content="hey palli",
        images=[], metadata=SimpleNamespace(channel_id=555, origin="user", discord_username="robin_example"),
    )
    asyncio.run(mod.record_user_fragment(turn, sessions_root=tmp_path))

    s = asyncio.run(sess_mod.Session.open_or_create("discord:555", root=tmp_path))
    texts = [b.text for m in s.messages if m.role == "user"
             for b in m.content if isinstance(b, sess_mod.TextBlock)]
    assert texts == ["hey palli"]


def _run_and_drain(mod, turn, tmp_path):
    """Run record_user_fragment, then await any detached DB-persist task it
    scheduled (held in the module's _BACKGROUND_PERSIST_TASKS set) so the
    fake persist has actually run before we assert on it."""
    async def go():
        await mod.record_user_fragment(turn, sessions_root=tmp_path)
        pending = list(mod._BACKGROUND_PERSIST_TASKS)
        if pending:
            await asyncio.gather(*pending)
    asyncio.run(go())


def test_record_user_fragment_text_fragment_schedules_db_persist(tmp_path, monkeypatch):
    """A normal text fragment DOES schedule the detached DB persist."""
    mod = load_module("bots/discord/recording.py", "recording_under_test")

    async def fake_db(turn, content): fake_db.calls.append(content)
    fake_db.calls = []
    monkeypatch.setattr(mod, "_persist_user_side_with_content", fake_db)

    turn = SimpleNamespace(
        surface="discord_text", owner_id=1, content="hey palli",
        images=[], metadata=SimpleNamespace(channel_id=555, origin="user", discord_username="robin_example"),
    )
    _run_and_drain(mod, turn, tmp_path)
    assert fake_db.calls == ["hey palli"]


def test_record_user_fragment_image_placeholder_skips_db_persist(tmp_path, monkeypatch):
    """A bare '[image attached]' fragment must NOT schedule the DB persist (the
    coalesced turn's enrich writes the rich row; recall has no dedup so a bare
    placeholder row here would double it) — but the shielded transcript append
    still happens (barge-in loss protection)."""
    mod = load_module("bots/discord/recording.py", "recording_under_test")
    import core.agent.session as sess_mod

    async def fake_db(turn, content): fake_db.calls.append(content)
    fake_db.calls = []
    monkeypatch.setattr(mod, "_persist_user_side_with_content", fake_db)

    turn = SimpleNamespace(
        surface="discord_text", owner_id=1, content="[image attached]",
        images=[object()], metadata=SimpleNamespace(channel_id=777, origin="user", discord_username="robin_example"),
    )
    _run_and_drain(mod, turn, tmp_path)

    # DB persist skipped for the bare placeholder...
    assert fake_db.calls == []
    # ...but the transcript append still landed.
    s = asyncio.run(sess_mod.Session.open_or_create("discord:777", root=tmp_path))
    texts = [b.text for m in s.messages if m.role == "user"
             for b in m.content if isinstance(b, sess_mod.TextBlock)]
    assert texts == ["[image attached]"]


def test_record_user_fragment_file_placeholder_skips_db_persist(tmp_path, monkeypatch):
    """Important-2 regression fix (2026-07-07 final review): a bare
    '[file attached]' fragment must NOT schedule the DB persist either — same
    reasoning as the image placeholder above (the coalesced turn's enrich in
    runner.run_turn persists a RICH row with the file analysis baked in, and
    recall has no dedup, so a bare placeholder row here would double it). The
    shielded transcript append still happens (barge-in loss protection)."""
    mod = load_module("bots/discord/recording.py", "recording_under_test")
    import core.agent.session as sess_mod

    async def fake_db(turn, content): fake_db.calls.append(content)
    fake_db.calls = []
    monkeypatch.setattr(mod, "_persist_user_side_with_content", fake_db)

    turn = SimpleNamespace(
        surface="discord_text", owner_id=1, content="[file attached]",
        images=[], metadata=SimpleNamespace(channel_id=888, origin="user", discord_username="robin_example"),
    )
    _run_and_drain(mod, turn, tmp_path)

    # DB persist skipped for the bare placeholder...
    assert fake_db.calls == []
    # ...but the transcript append still landed.
    s = asyncio.run(sess_mod.Session.open_or_create("discord:888", root=tmp_path))
    texts = [b.text for m in s.messages if m.role == "user"
             for b in m.content if isinstance(b, sess_mod.TextBlock)]
    assert texts == ["[file attached]"]

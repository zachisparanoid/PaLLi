"""One thing on her mind: the record that she said it (Batch 6, task 1;
item 3.2; rulings R7, R9, R12).

After a reply, reflect asks the model one yes/no question: did she bring
tonight's pick up as her own thing? A clean yes stamps the pick file and,
for a stake, the ledger; anything else leaves the band in place. The check
is its own small call on its own lane, outside the plan extraction, and it
runs only where the two of them talk.
"""
from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

import core.turn.reflect as reflect
from ai import stake_ledger, tonight
from core.database import PalliBrain
from core.turn.reflect import apply_reflection

PICK_TEXT = "I keep thinking about the concert on Friday."
REPLY = "Before you go — I keep thinking about the concert on Friday. Did the box office ever call back?"


class _FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def chat_completion(self, **kw):
        self.calls.append(kw)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _ctx(surface: str = "dashboard"):
    return SimpleNamespace(
        turn=SimpleNamespace(owner_id=1, surface=surface, content="what's new with you?",
                             metadata=SimpleNamespace()),
    )


@pytest.fixture
def reflect_ctx():
    """A turn on a surface where the two of them talk ("local" is not one)."""
    return _ctx("dashboard")


async def _write_pick(monkeypatch, tmp_path, *, source="stake", source_id="42", null=False):
    """Tonight's file in this test's folder, with the organ on."""
    monkeypatch.setattr(tonight, "TONIGHT_DIR", tmp_path / "tonight")
    monkeypatch.setattr(tonight, "tonight_enabled", lambda: True)
    reply = '{"pick": null}' if null else json.dumps({"pick": {
        "source": source, "source_id": source_id, "text": PICK_TEXT, "why": "it moved this week"}})

    async def llm(prompt):
        return reply

    now = time.time()
    stake_id = int(source_id) if source == "stake" else 42
    await tonight.pick_tonight(
        1, llm_call=llm, now=now,
        stakes=[{"id": stake_id, "kind": "hope", "stake": "the concert goes well"}],
        journal_pages=[{"id": "2026-09-27", "text": "a page"}], daydreams=[],
        recent_conversation="")
    return tonight.day_for(now)


class _Ledger:
    """Stands in for the two ledger stamps and records what they were given."""

    def __init__(self, monkeypatch):
        self.voiced, self.surfaced = [], []

        async def mark_stake_said(stake_id, *, brain_client=None, now=None):
            self.voiced.append(stake_id)
            return True

        async def record_surfaced(stake_id, *, brain_client=None):
            self.surfaced.append(stake_id)

        monkeypatch.setattr(stake_ledger, "mark_stake_said", mark_stake_said)
        monkeypatch.setattr(stake_ledger, "record_surfaced", record_surfaced)


async def _reflect(ctx, fake, *, reply=REPLY, plan=None):
    extract = AsyncMock(return_value=plan)
    with patch.object(reflect, "llm", fake), \
         patch.object(reflect, "_resolve_reflect_model", lambda: "fake-model"), \
         patch.object(reflect, "_extract_plan", extract):
        await apply_reflection(ctx, reply)
    return extract


# ── the brief's two tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_said_yes_marks_the_pick_and_the_stake(monkeypatch, tmp_path, reflect_ctx):
    day = await _write_pick(monkeypatch, tmp_path)
    ledger = _Ledger(monkeypatch)
    fake = _FakeLLM('{"said": true}')
    before = time.time()
    await _reflect(reflect_ctx, fake)
    said_at = tonight.load_pick(day).said_at
    assert said_at is not None and before <= said_at <= time.time()
    assert ledger.voiced == [42]
    assert ledger.surfaced == [42]
    # the band is gone until the next night
    assert tonight.render_band(tonight.load_current()) == ""


@pytest.mark.asyncio
async def test_said_no_leaves_the_band(monkeypatch, tmp_path, reflect_ctx):
    day = await _write_pick(monkeypatch, tmp_path)
    ledger = _Ledger(monkeypatch)
    fake = _FakeLLM('{"said": false}')
    await _reflect(reflect_ctx, fake)
    assert len(fake.calls) == 1
    assert tonight.load_pick(day).said_at is None
    assert ledger.voiced == [] and ledger.surfaced == []
    assert tonight.render_band(tonight.load_current()).startswith("## One thing on my mind")


# ── what the check is handed, and on which lane ─────────────────────────

@pytest.mark.asyncio
async def test_the_check_is_one_small_call_on_its_own_lane(monkeypatch, tmp_path, reflect_ctx):
    await _write_pick(monkeypatch, tmp_path)
    _Ledger(monkeypatch)
    fake = _FakeLLM('{"said": false}')
    await _reflect(reflect_ctx, fake)
    call, = fake.calls
    assert call["log_context"] == "reflect_turn_tonight_said"
    assert call["model"] == "fake-model"
    assert call["temperature"] <= 0.2
    assert call["max_tokens"] <= 400
    (message,) = call["messages"]
    assert PICK_TEXT in message["content"] and REPLY in message["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ['{"said": "yes"}', "yes, she did", "", None, '{"said": 1}'])
async def test_anything_but_a_clean_yes_stamps_nothing(monkeypatch, tmp_path, reflect_ctx, reply):
    day = await _write_pick(monkeypatch, tmp_path)
    ledger = _Ledger(monkeypatch)
    await _reflect(reflect_ctx, _FakeLLM(reply))
    assert tonight.load_pick(day).said_at is None
    assert ledger.voiced == []


@pytest.mark.asyncio
async def test_a_journal_pick_is_recorded_in_the_file_alone(monkeypatch, tmp_path, reflect_ctx):
    day = await _write_pick(monkeypatch, tmp_path, source="journal", source_id="2026-09-27")
    ledger = _Ledger(monkeypatch)
    await _reflect(reflect_ctx, _FakeLLM('{"said": true}'))
    assert tonight.load_pick(day).said_at is not None
    assert ledger.voiced == [] and ledger.surfaced == []


# ── when the check does not run at all ──────────────────────────────────

@pytest.mark.asyncio
async def test_the_flag_off_asks_nothing(monkeypatch, tmp_path, reflect_ctx):
    day = await _write_pick(monkeypatch, tmp_path)
    monkeypatch.setattr(tonight, "tonight_enabled", lambda: False)
    ledger = _Ledger(monkeypatch)
    fake = _FakeLLM('{"said": true}')
    await _reflect(reflect_ctx, fake)
    assert fake.calls == []
    assert tonight.load_pick(day).said_at is None and ledger.voiced == []


@pytest.mark.asyncio
async def test_no_pick_tonight_asks_nothing(monkeypatch, tmp_path, reflect_ctx):
    monkeypatch.setattr(tonight, "TONIGHT_DIR", tmp_path / "tonight")
    monkeypatch.setattr(tonight, "tonight_enabled", lambda: True)
    fake = _FakeLLM('{"said": true}')
    await _reflect(reflect_ctx, fake)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_null_night_asks_nothing(monkeypatch, tmp_path, reflect_ctx):
    await _write_pick(monkeypatch, tmp_path, null=True)
    fake = _FakeLLM('{"said": true}')
    await _reflect(reflect_ctx, fake)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_pick_already_said_is_never_asked_about_again(monkeypatch, tmp_path, reflect_ctx):
    day = await _write_pick(monkeypatch, tmp_path)
    tonight.mark_said(day, at=123.0)
    ledger = _Ledger(monkeypatch)
    fake = _FakeLLM('{"said": true}')
    await _reflect(reflect_ctx, fake)
    assert fake.calls == []
    assert tonight.load_pick(day).said_at == 123.0 and ledger.voiced == []


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["reminder_fire", "proactive", "workbench", "local", ""])
async def test_a_turn_that_is_not_a_conversation_asks_nothing(monkeypatch, tmp_path, surface):
    """Reflect also runs after a reminder and after a reach-out. The band is
    not shown there, so nothing said there can be the pick."""
    day = await _write_pick(monkeypatch, tmp_path)
    ledger = _Ledger(monkeypatch)
    fake = _FakeLLM('{"said": true}')
    await _reflect(_ctx(surface), fake)
    assert fake.calls == []
    assert tonight.load_pick(day).said_at is None and ledger.voiced == []


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["reminder_fire", "proactive"])
async def test_a_skipped_check_never_logs_under_the_callbacks_name(
        monkeypatch, tmp_path, caplog, surface):
    """Fix round 1 (ruling R61b): with a pick unsaid, every reminder and
    every reach-out used to log "callback filing skipped: surface … is not
    a conversation" — another organ's line. The said-check's own gate is
    quiet: nothing at INFO or above, and nothing that names callbacks."""
    await _write_pick(monkeypatch, tmp_path)
    _Ledger(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        await _reflect(_ctx(surface), _FakeLLM('{"said": true}'))
    said = [r for r in caplog.records if r.name in ("core.turn.reflect", "ai.tonight")]
    assert not any("callback" in r.getMessage().lower() for r in said), \
        [r.getMessage() for r in said]
    assert not any(r.levelno >= logging.INFO and "tonight" in r.getMessage().lower()
                   for r in said), [r.getMessage() for r in said]


def test_the_callbacks_own_gate_still_says_its_line(caplog):
    """The callbacks organ's gate is as it was: it still names itself when
    it refuses a surface."""
    with caplog.at_level(logging.INFO, logger="core.turn.reflect"):
        assert reflect._callback_surface_ok(_ctx("reminder_fire").turn) is False
        assert reflect._callback_surface_ok(_ctx("dashboard").turn) is True
    assert [r.getMessage() for r in caplog.records] == [
        "apply_reflection: callback filing skipped: surface 'reminder_fire' is not a conversation",
    ]


def test_reflect_gained_no_import_for_the_gate():
    """reflect.py sits at the cap of 25 in-repo imports (rule 6)."""
    from tests.architecture.conftest import collect_repo_modules, in_repo_imports

    module, = [m for m in collect_repo_modules() if m.rel_path == "core/turn/reflect.py"]
    assert len(in_repo_imports(module)) <= 25


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["discord_text", "discord_voice", "voice_local",
                                     "dashboard", "telegram_text"])
async def test_every_door_where_they_talk_is_checked(monkeypatch, tmp_path, surface):
    day = await _write_pick(monkeypatch, tmp_path)
    _Ledger(monkeypatch)
    await _reflect(_ctx(surface), _FakeLLM('{"said": true}'))
    assert tonight.load_pick(day).said_at is not None


# ── where it sits in the reflection ─────────────────────────────────────

@pytest.mark.asyncio
async def test_the_stamp_does_not_wait_on_the_plan(monkeypatch, tmp_path, reflect_ctx):
    """A reflection whose plan fails, or comes back empty, still records
    that she said it."""
    day = await _write_pick(monkeypatch, tmp_path)
    _Ledger(monkeypatch)
    fake = _FakeLLM('{"said": true}')
    with patch.object(reflect, "llm", fake), \
         patch.object(reflect, "_resolve_reflect_model", lambda: "fake-model"), \
         patch.object(reflect, "_extract_plan", AsyncMock(side_effect=RuntimeError("plan died"))):
        assert await apply_reflection(reflect_ctx, REPLY) is False
    assert tonight.load_pick(day).said_at is not None


@pytest.mark.asyncio
async def test_a_failed_check_never_costs_the_reflection(monkeypatch, tmp_path, reflect_ctx, caplog):
    day = await _write_pick(monkeypatch, tmp_path)
    ledger = _Ledger(monkeypatch)
    fake = _FakeLLM(RuntimeError("provider down"))
    with caplog.at_level(logging.WARNING):
        extract = await _reflect(reflect_ctx, fake)
    extract.assert_awaited_once()
    assert tonight.load_pick(day).said_at is None and ledger.voiced == []
    # and the failure is said out loud, once, under this organ's name
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert [(r.name, r.getMessage()) for r in warnings] == [
        ("ai.tonight", "tonight: the said-check failed (provider down) — the pick stays unsaid"),
    ]


@pytest.mark.asyncio
async def test_a_broken_store_never_costs_the_reflection(monkeypatch, tmp_path, reflect_ctx):
    await _write_pick(monkeypatch, tmp_path)

    def boom(*a, **kw):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(tonight, "load_current", boom)
    fake = _FakeLLM('{"said": true}')
    extract = await _reflect(reflect_ctx, fake)
    extract.assert_awaited_once()
    assert fake.calls == []


@pytest.mark.asyncio
async def test_the_plan_extraction_still_makes_exactly_one_call(monkeypatch, tmp_path):
    """The said-check is its own step: with the organ on and a pick unsaid,
    the plan extraction is still one call on the reflect lane."""
    from datetime import datetime

    await _write_pick(monkeypatch, tmp_path)
    ctx = SimpleNamespace(
        compiled_identity=None,
        turn=SimpleNamespace(owner_id=1, surface="dashboard", content="hey",
                             received_at=datetime(2026, 9, 29, 20, 0)),
        unfulfilled_first_contact_targets=[], turn_read=None)
    fake = _FakeLLM(None)
    with patch.object(reflect.prompt_loader, "load_prompt",
                      return_value="reflect on: {user_message} / {assistant_reply}"), \
         patch.object(reflect, "brain",
                      SimpleNamespace(list_active_open_threads=AsyncMock(return_value=[]))), \
         patch.object(reflect, "_resolve_reflect_model", lambda: "fake-model"), \
         patch.object(reflect, "llm", fake):
        await reflect._extract_plan(ctx, "a reply")
    assert [c["log_context"] for c in fake.calls] == ["reflect_turn"]


# ── the real ledger, end to end (ruling R7) ─────────────────────────────

@pytest_asyncio.fixture
async def ledger_db(tmp_path):
    db = PalliBrain(str(tmp_path / "stakes.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_yes_fills_voiced_at_on_a_real_hope(monkeypatch, tmp_path, reflect_ctx, ledger_db):
    """Nothing is stubbed between the yes and the row: a mock-only test
    would pass while the ledger stayed at "0 ever recorded as said"."""
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": "hope", "stake": "the concert goes well", "horizon": None}],
        source_channel_id="t", brain_client=ledger_db)
    hope, = await stake_ledger.open_stakes(limit=5, brain_client=ledger_db)
    assert hope.voiced_at is None
    monkeypatch.setattr("core.database.brain", ledger_db)
    day = await _write_pick(monkeypatch, tmp_path, source_id=str(hope.id))
    await _reflect(reflect_ctx, _FakeLLM('{"said": true}'))
    row, = await stake_ledger.open_stakes(limit=5, brain_client=ledger_db)
    assert row.voiced_at is not None and row.voiced_at == tonight.load_pick(day).said_at
    assert row.last_surfaced_at is not None

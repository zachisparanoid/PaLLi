"""The nightly rhythm refresh has its own switch (owner call 7, 2026-09-28).

``NightCycleEngine._refresh_owner_rhythm`` used to ride on
``arbiter_learned_quiet`` — the learned-quiet-hours master toggle the owner
turned OFF on 2026-08-25. The rhythm file (her statistical picture of when
he is around) went stale on 2026-08-25, and the surprise count fell from 22
(Aug) to 1 (Sep): the surprise organ reads the file for its expectations
with no staleness check, so a frozen file made its timing line wrong, not
absent. Now ``companion.rhythm_refresh`` (default on) gates the refresh
alone; quiet hours stay off and nothing else changes.

Also pins ruling R6: ``ai.owner_rhythm.refresh_rhythm_file`` already
replaces the file whole (write ``owner_rhythm.json.tmp``, then rename) —
a refresh that fails at the rename leaves the previous file byte-identical,
and one that succeeds leaves no temp file behind.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import types
from datetime import datetime
from pathlib import Path

import pytest

import ai.night_cycle as nc
import ai.owner_rhythm as owner_rhythm

_NOW = datetime(2026, 9, 28, 4, 0, 0).timestamp()   # the morning of the change

# The learner's knobs as the night hook forwards them (spec defaults).
_KNOBS = dict(
    rhythm_lookback_days=56,
    rhythm_quiet_max_active_ratio=0.08,
    rhythm_min_observed_weekdays=15,
    rhythm_min_observed_weekend_days=6,
)


def _engine(tmp_path):
    return nc.NightCycleEngine(state_path=tmp_path / "state.json")


def _companion(monkeypatch, **flags):
    comp = types.SimpleNamespace(**_KNOBS, **flags)
    monkeypatch.setattr(nc, "companion_settings", lambda: comp)
    return comp


def _record_refreshes(monkeypatch):
    """A call-recorder OUTSIDE the hook's crash-guard — a raising mock would
    be swallowed and prove nothing."""
    calls = []
    monkeypatch.setattr(owner_rhythm, "refresh_rhythm_file", lambda **kw: calls.append(kw))
    return calls


def _empty_conversations_db(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY, role TEXT,"
        " timestamp REAL, content TEXT)"
    )
    conn.commit()
    conn.close()
    return path


# ── the switch ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rhythm_refresh_runs_with_quiet_hours_off(monkeypatch, tmp_path):
    """Owner call 7: quiet hours OFF and the refresh switch ON => the
    learner runs, with the knobs forwarded as before."""
    _companion(monkeypatch, arbiter_learned_quiet=False, rhythm_refresh=True)
    calls = _record_refreshes(monkeypatch)

    await _engine(tmp_path)._refresh_owner_rhythm()

    assert len(calls) == 1
    assert calls[0]["lookback_days"] == 56
    assert calls[0]["max_active_ratio"] == 0.08
    assert calls[0]["min_weekday_days"] == 15
    assert calls[0]["min_weekend_days"] == 6


@pytest.mark.asyncio
async def test_rhythm_refresh_off_is_a_no_op(monkeypatch, tmp_path):
    """The switch OFF => the learner is neither imported nor run."""
    _companion(monkeypatch, arbiter_learned_quiet=False, rhythm_refresh=False)
    calls = _record_refreshes(monkeypatch)

    await _engine(tmp_path)._refresh_owner_rhythm()

    assert calls == []


@pytest.mark.asyncio
async def test_rhythm_refresh_defaults_on_when_the_setting_is_absent(monkeypatch, tmp_path):
    """The hook's fallback mirrors the schema default (on): a companion
    object without the key still refreshes — the crash-guard test in
    test_night_cycle.py builds exactly such an object and must keep
    reaching the learner."""
    _companion(monkeypatch, arbiter_learned_quiet=False)   # no rhythm_refresh
    calls = _record_refreshes(monkeypatch)

    await _engine(tmp_path)._refresh_owner_rhythm()

    assert len(calls) == 1


# ── ruling R6: the file is replaced whole, never half-written ───────────

@pytest.mark.asyncio
async def test_the_night_hook_writes_the_file_and_leaves_no_temp(monkeypatch, tmp_path):
    """End to end with the real writer: quiet hours off, switch on => a
    fresh rhythm file on disk and no ``.json.tmp`` beside it."""
    _companion(monkeypatch, arbiter_learned_quiet=False, rhythm_refresh=True)
    out = tmp_path / "rhythm" / "owner_rhythm.json"
    monkeypatch.setattr(owner_rhythm, "RHYTHM_FILE", out)
    monkeypatch.setattr(
        owner_rhythm, "DB_FILE", _empty_conversations_db(tmp_path / "t.sqlite3")
    )

    await _engine(tmp_path)._refresh_owner_rhythm()

    written = json.loads(out.read_text(encoding="utf-8"))
    assert set(written["buckets"]) == {
        f"{kind}-{h:02d}" for kind in ("weekday", "weekend") for h in range(24)
    }
    assert sorted(p.name for p in out.parent.iterdir()) == ["owner_rhythm.json"]


def test_a_failed_rename_leaves_the_previous_file_byte_identical(monkeypatch, tmp_path):
    """The write goes to a temp name and the rename is the only step that
    touches the target: when the rename fails, yesterday's file is still
    there byte for byte, and nothing half-written sits under its name."""
    out = tmp_path / "rhythm" / "owner_rhythm.json"
    out.parent.mkdir(parents=True)
    before = b'{"generated_at": "2026-08-25T04:00:00", "buckets": {"weekday-09": {"quiet": true}}}'
    out.write_bytes(before)
    db = _empty_conversations_db(tmp_path / "t.sqlite3")

    real_replace = Path.replace

    def _refuse_the_target(self, target):
        if Path(target) == out:
            raise OSError("rename refused")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _refuse_the_target)
    with pytest.raises(OSError, match="rename refused"):
        owner_rhythm.refresh_rhythm_file(now=_NOW, db_path=db, out_path=out)

    assert out.read_bytes() == before
    assert {p.name for p in out.parent.iterdir()} <= {
        "owner_rhythm.json", "owner_rhythm.json.tmp"
    }


# ── fix round 1 (review finding 2): hermetic by construction ────────────
# The seeded test settings (tests/conftest.py, from the example yaml) carry
# no rhythm_refresh row, so the schema default turns the refresh ON for
# every test that reaches the night pass — the loop tests in
# test_night_cycle.py among them. Before the conftest redirect of
# owner_rhythm.DB_FILE / RHYTHM_FILE, only the live-DB audit backstop stood
# between such a test and the owner's database + rhythm file, and the crash
# guard swallowed its refusal. These two run the hook with the REAL seeded
# settings, no companion stub — the loop tests' world.


def _dir_state(path: Path):
    if not path.exists():
        return None
    return sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in path.iterdir())


@pytest.mark.asyncio
async def test_the_night_hook_reads_only_a_db_under_tmp(monkeypatch, tmp_path):
    """With the seeded settings (switch on by default) the writer is handed
    the conftest-redirected DB, under tmp — never data/. The recorder
    raises after recording, so nothing is written anywhere."""
    assert nc.companion_settings().rhythm_refresh is True

    seen = {}

    def _record_then_stop(db_path, **kw):
        seen["db_path"] = Path(db_path)
        raise RuntimeError("recorded")

    monkeypatch.setattr(owner_rhythm, "gather_his_timestamps", _record_then_stop)
    await _engine(tmp_path)._refresh_owner_rhythm()

    assert seen["db_path"].resolve().is_relative_to(tmp_path.resolve())
    assert owner_rhythm.RHYTHM_FILE.resolve().is_relative_to(tmp_path.resolve())
    assert not seen["db_path"].resolve().is_relative_to(Path("data").resolve())


@pytest.mark.asyncio
async def test_the_night_hook_fails_closed_without_a_tmp_db(tmp_path, caplog):
    """No stubs at all: the redirected DB does not exist, the read-only open
    fails, the crash guard swallows it — no DB is created, no rhythm file or
    temp is written under tmp, data/rhythm is exactly as it was, and it is
    NOT the live-DB backstop that stopped it."""
    assert nc.companion_settings().rhythm_refresh is True
    live_rhythm_dir = Path("data/rhythm")
    before = _dir_state(live_rhythm_dir)

    caplog.set_level(logging.WARNING, logger="ai.night_cycle")
    await _engine(tmp_path)._refresh_owner_rhythm()

    assert "owner_rhythm: refresh failed (ignored)" in caplog.text
    assert "BLOCKED by tests/conftest.py" not in caplog.text
    assert not owner_rhythm.DB_FILE.exists()
    assert not owner_rhythm.RHYTHM_FILE.exists()
    assert not owner_rhythm.RHYTHM_FILE.with_suffix(".json.tmp").exists()
    assert _dir_state(live_rhythm_dir) == before

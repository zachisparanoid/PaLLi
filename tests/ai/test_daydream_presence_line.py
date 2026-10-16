"""The daydream's machine line obeys the two-hour rule (Batch 3 item 1.5,
ruling R17, 2026-09-28).

``ai.daydream._presence_line`` fed the daydream prompt "their machine is
idle." straight from the snapshot's ``machine_state`` with no freshness
check, so the frozen snapshot behind the present-moment incident (newest real
reading 2026-08-18, "Machine: idle." for six weeks after it) reached her
daydreams too. The gate is now the present-moment gather's own
``_machine_line`` rule, imported: a stale or absent reading says the line's
existing no-signal words, "no signal.", and a fresh one keeps its old words."""
from __future__ import annotations

import asyncio
import time

import ai.daydream as dd

_SIX_WEEKS_S = 42 * 24 * 3600


def _presence_with(monkeypatch, snap):
    from core import home_presence as hp

    async def fake_refs():
        return {"app_user_id": 1}

    async def fake_snapshot(*, owner_user_id):
        return snap

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setattr(hp.home_presence_service, "get_context_snapshot", fake_snapshot)
    return asyncio.run(dd._presence_line())


def test_a_six_week_old_reading_is_no_signal_not_idle(monkeypatch):
    now = time.time()
    line = _presence_with(monkeypatch, {
        "machine_state": "idle",
        "last_machine_active_at": now - _SIX_WEEKS_S,
        # another source stamped the snapshot a minute ago; it is not a
        # machine reading and must not revive the old state
        "last_event_at": now - 60,
    })
    assert line == "no signal."
    assert "idle" not in line


def test_a_fresh_poller_reading_keeps_the_old_words(monkeypatch):
    line = _presence_with(monkeypatch, {
        "machine_state": "idle",
        "source_health": {"machine": {"status": "ready", "updated_at": time.time() - 60}},
    })
    assert line == "their machine is idle."


def test_no_machine_state_is_no_signal(monkeypatch):
    line = _presence_with(monkeypatch, {"machine_state": None, "owner_presence_state": "home"})
    assert line == "no signal."


def test_the_gate_is_the_present_moment_rule_imported_not_copied(monkeypatch):
    """Whatever the present-moment helper decides, the daydream follows: a
    fresh reading goes silent when the helper says "", and a stale one speaks
    when the helper says it knows."""
    import ai.present_moment_gather as pmg

    fresh = {
        "machine_state": "idle",
        "source_health": {"machine": {"status": "ready", "updated_at": time.time() - 60}},
    }
    monkeypatch.setattr(pmg, "_machine_line", lambda snap, *, now: "")
    assert _presence_with(monkeypatch, fresh) == "no signal."

    stale = {"machine_state": "idle", "last_machine_active_at": time.time() - _SIX_WEEKS_S}
    monkeypatch.setattr(pmg, "_machine_line", lambda snap, *, now: "Machine: idle.")
    assert _presence_with(monkeypatch, stale) == "their machine is idle."

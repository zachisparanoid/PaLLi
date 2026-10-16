"""The moment's presence sense (2026-08-18): with machine presence disabled the
snapshot carries no machine_state, and the sense must say NOTHING — not
"Machine: unknown.", which is a sentence for her to wonder about."""
from __future__ import annotations

import asyncio
import time


def _run(coro):
    return asyncio.run(coro)


def _house_sense_off(monkeypatch):
    """Tests pin their own flags (the house law). Left unpinned, these three
    read the LIVE device book — data/lan_presence.json has existed since her
    2026-08-24 17:31 boot sweep — and her real house walked into the
    expected strings ("the house right now: 20 devices online")."""
    from types import SimpleNamespace
    from core import lan_presence as lp

    monkeypatch.setattr(lp, "companion_settings", lambda: SimpleNamespace(lan_presence=False, lan_devices={}))


def test_presence_sense_is_silent_when_machine_state_is_unknown(monkeypatch):
    from ai import present_moment_gather as pmg
    from core import home_presence as hp

    async def fake_refs():
        return {"app_user_id": 1}

    async def fake_snapshot(*, owner_user_id):
        return {"machine_state": None, "owner_presence_state": "home"}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setattr(hp.home_presence_service, "get_context_snapshot", fake_snapshot)
    _house_sense_off(monkeypatch)
    assert _run(pmg._presence()) == ""


def test_presence_sense_speaks_when_a_real_state_exists(monkeypatch):
    from ai import present_moment_gather as pmg
    from core import home_presence as hp

    async def fake_refs():
        return {"app_user_id": 1}

    async def fake_snapshot(*, owner_user_id):
        # Re-pinned 2026-09-27 (Batch 3 task 5): a real state is one somebody
        # read recently — the poller's own health stamp is what makes it real.
        # Without one the line is silence, so a fresh reading keeps this
        # test's premise.
        return {"machine_state": "active",
                "source_health": {"machine": {"status": "ready", "updated_at": time.time() - 60}}}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setattr(hp.home_presence_service, "get_context_snapshot", fake_snapshot)
    _house_sense_off(monkeypatch)
    assert _run(pmg._presence()) == "Machine: active."


def test_presence_sense_carries_discord_line_when_known(monkeypatch):
    """The first real signal (2026-08-18): Discord status rides the moment's
    presence sense, beside (or instead of) the machine line."""
    from ai import present_moment_gather as pmg
    from core import home_presence as hp
    from core import owner_presence as op

    op.reset_for_tests()
    async def fake_refs():
        return {"app_user_id": 1}
    async def fake_snapshot(*, owner_user_id):
        return {"machine_state": None}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setattr(hp.home_presence_service, "get_context_snapshot", fake_snapshot)
    _house_sense_off(monkeypatch)
    op.record_discord_presence(status="online", activity="Star Citizen", at=1000.0)
    try:
        assert _run(pmg._presence()) == "Discord: online — playing Star Citizen"
    finally:
        op.reset_for_tests()


# ── step 23 (2026-08-24): "the house right now" beside the Discord line ──

def _lan_engine(tmp_path, now):
    from core import lan_presence as lp

    lp.save_book(tmp_path / "lan_presence.json", {
        "aa:bb:cc:dd:ee:01": {"ip": "192.0.2.1", "hostname": "_gateway", "first_seen": now - 5 * 86400,
                              "last_seen": now - 30, "online": True, "online_since": now - 5 * 86400},
    })
    return lp.LanPresenceEngine(book_path=tmp_path / "lan_presence.json", clock=lambda: now)


def test_presence_sense_carries_the_house_line_when_on(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ai import present_moment_gather as pmg
    from core import home_presence as hp
    from core import lan_presence as lp
    from core import owner_presence as op

    op.reset_for_tests()
    now = 1_700_000_000.0

    async def fake_refs():
        return {"app_user_id": 1}

    async def fake_snapshot(*, owner_user_id):
        return {"machine_state": None}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setattr(hp.home_presence_service, "get_context_snapshot", fake_snapshot)
    monkeypatch.setattr(lp, "lan_presence_engine", _lan_engine(tmp_path, now))
    monkeypatch.setattr(lp, "companion_settings", lambda: SimpleNamespace(lan_presence=True, lan_devices={}))
    op.record_discord_presence(status="online", activity="", at=1000.0)
    try:
        assert _run(pmg._presence()).startswith("Discord: online\nthe house right now: 1 device online")
    finally:
        op.reset_for_tests()


def test_presence_sense_is_byte_identical_when_the_house_sense_is_off(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ai import present_moment_gather as pmg
    from core import home_presence as hp
    from core import lan_presence as lp
    from core import owner_presence as op

    op.reset_for_tests()

    async def fake_refs():
        return {"app_user_id": 1}

    async def fake_snapshot(*, owner_user_id):
        return {"machine_state": None}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setattr(hp.home_presence_service, "get_context_snapshot", fake_snapshot)
    monkeypatch.setattr(lp, "lan_presence_engine", _lan_engine(tmp_path, 1_700_000_000.0))
    monkeypatch.setattr(lp, "companion_settings", lambda: SimpleNamespace(lan_presence=False, lan_devices={}))
    assert _run(pmg._presence()) == ""                      # today's exact bytes


# ── Batch 3 task 5 (2026-09-27): "Machine: idle" only when somebody knows ──
# The newest real machine reading was 2026-08-18, and the line printed on
# every request for six weeks anyway — a stale fact read as a present one.
# A reading is fresh when the newest of the machine poller's health stamp
# (source_health.machine.updated_at, status "ready") and last_machine_active_at
# is under two hours old. last_event_at is NOT a machine reading: any source
# stamps it, and a Home Assistant event must not revive a six-week-old state.

def _ready(at):
    return {"machine": {"status": "ready", "updated_at": at}}


def test_machine_line_only_when_fresh():
    from ai.present_moment_gather import _machine_line

    now = time.time()
    assert _machine_line({"machine_state": "idle", "last_machine_active_at": now - 600}, now=now) == "Machine: idle."
    assert _machine_line({"machine_state": "idle", "last_machine_active_at": now - 5 * 24 * 3600}, now=now) == ""
    assert _machine_line({"machine_state": "unknown"}, now=now) == ""
    assert _machine_line({"machine_state": "active", "source_health": _ready(now - 100)}, now=now) == "Machine: active."


def test_machine_line_reads_the_poller_not_the_event_book():
    from ai.present_moment_gather import _machine_line

    now = 1_700_000_000.0
    # (i) a non-machine event alone never revives a stale state
    assert _machine_line({"machine_state": "idle", "last_event_at": now - 100}, now=now) == ""
    # (ii) a healthy poller with an unchanged state still prints: he has sat at the
    # keyboard for three hours, so last_machine_active_at never moved
    assert _machine_line({"machine_state": "active", "last_machine_active_at": now - 3 * 3600,
                          "source_health": _ready(now - 30)}, now=now) == "Machine: active."
    # (iii) a health entry that is not "ready" does not count, however fresh
    assert _machine_line({"machine_state": "idle",
                          "source_health": {"machine": {"status": "unavailable", "updated_at": now - 30}}},
                         now=now) == ""
    assert _machine_line({"machine_state": "idle",
                          "source_health": {"machine": {"status": "degraded", "updated_at": now - 30}}},
                         now=now) == ""
    # the newest of the two stamps decides, either way round
    assert _machine_line({"machine_state": "idle", "last_machine_active_at": now - 3 * 86400,
                          "source_health": _ready(now - 300)}, now=now) == "Machine: idle."
    assert _machine_line({"machine_state": "idle", "last_machine_active_at": now - 300,
                          "source_health": _ready(now - 3 * 86400)}, now=now) == "Machine: idle."
    # another source's health is not the machine's
    assert _machine_line({"machine_state": "idle",
                          "source_health": {"home_assistant": {"status": "ready", "updated_at": now - 30}}},
                         now=now) == ""
    # two hours exactly is still within; a second past is not; max_age is honoured
    assert _machine_line({"machine_state": "idle", "source_health": _ready(now - 7200)}, now=now) == "Machine: idle."
    assert _machine_line({"machine_state": "idle", "source_health": _ready(now - 7201)}, now=now) == ""
    assert _machine_line({"machine_state": "idle", "source_health": _ready(now - 100)}, now=now, max_age=60) == ""


def test_machine_line_treats_unreadable_stamps_and_states_as_absent():
    from ai.present_moment_gather import _machine_line

    now = 1_700_000_000.0
    assert _machine_line({"machine_state": "idle"}, now=now) == ""
    assert _machine_line({"machine_state": "idle", "last_machine_active_at": None, "source_health": {}}, now=now) == ""
    assert _machine_line({"machine_state": "idle", "last_machine_active_at": "yesterday"}, now=now) == ""
    assert _machine_line({"machine_state": "idle", "last_machine_active_at": str(now - 10)}, now=now) == ""
    assert _machine_line({"machine_state": "idle", "source_health": _ready(None)}, now=now) == ""
    assert _machine_line({"machine_state": "idle", "source_health": _ready(str(now - 10))}, now=now) == ""
    assert _machine_line({"machine_state": "idle", "source_health": {"machine": "ready"}}, now=now) == ""
    assert _machine_line({"machine_state": "idle", "source_health": None}, now=now) == ""
    assert _machine_line({"machine_state": None, "source_health": _ready(now - 10)}, now=now) == ""
    assert _machine_line({"machine_state": "", "last_machine_active_at": now - 10}, now=now) == ""
    assert _machine_line(None, now=now) == ""


def test_presence_sense_is_silent_when_the_machine_reading_is_stale(monkeypatch):
    """The incident, through the sense itself: state "idle", the poller last
    healthy and the machine last active on 2026-08-18, reached every
    present-moment request six weeks later — and a fresh event from another
    source must not bring it back."""
    from ai import present_moment_gather as pmg
    from core import home_presence as hp

    async def fake_refs():
        return {"app_user_id": 1}

    async def fake_snapshot(*, owner_user_id):
        return {"machine_state": "idle", "last_machine_active_at": 1_755_500_000.0,
                "last_event_at": time.time() - 30,
                "source_health": {"machine": {"status": "ready", "message": "ok", "updated_at": 1_755_500_000.0},
                                  "home_assistant": {"status": "ready", "message": "ok", "updated_at": time.time() - 30}}}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setattr(hp.home_presence_service, "get_context_snapshot", fake_snapshot)
    _house_sense_off(monkeypatch)
    assert _run(pmg._presence()) == ""

"""Present Moment — what wakes her, and what is skipped as nothing new
(Batch 6, task 4, item 3.6).

On the live install about a third of her moments were woken by nothing but
a device joining or leaving the wifi, and the moments they produced
restated the one before. Two code gates, one knob
(``companion.device_wake_seconds``, default 3600, 0 = both off):

- the wake gate, in ``_ingest``: a device event sets the wake at most once
  in that many seconds; a later one joins the bundle without waking. Every
  other kind wakes exactly as before, inside that hour too.
- the skip, in ``_loop_once``: a bundle holding device kinds only, fired
  while her last written moment is younger than the knob, is dropped as
  nothing new. The skip is the gate that limits anything: an un-woken
  event still fires at the next heartbeat deadline.

Hermetic the way ``test_present_moment_engine.py`` is: a fake clock stands
in for the module's ``time``, tier 2 is a recorder, no test calls
``start()``. The fixtures are local copies of that file's, with the
settings stand-in carrying the knob.
"""
from __future__ import annotations

import logging
import time as _real_time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import ai.present_moment as pm
from ai.present_moment import PresentMomentEngine

EPOCH = 1_800_000_000.0  # fixed, arbitrary; only its stability matters
HOUR = 3600.0


class FakeClock:
    """Controllable stand-in for a module's ``time`` import."""

    def __init__(self, now: float) -> None:
        self.now = float(now)

    def time(self) -> float:
        return self.now

    def localtime(self, ts=None):
        return _real_time.localtime(self.now if ts is None else ts)

    def strftime(self, fmt, t=None):
        return _real_time.strftime(fmt, self.localtime() if t is None else t)

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _set_knob(monkeypatch, name: str, value) -> None:
    from core.config import config

    monkeypatch.setattr(config, name, value, raising=False)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock(EPOCH)
    monkeypatch.setattr(pm, "time", fake)
    return fake


@pytest.fixture(autouse=True)
def companion(monkeypatch):
    """The settings the engine reads: the organ on, the knob at its default.
    A test changes a row by setting the attribute on what this returns."""
    import core.turn.companion_flags as flags

    rows = SimpleNamespace(present_moment=True, device_wake_seconds=3600)
    monkeypatch.setattr(flags, "companion_settings", lambda: rows)
    return rows


@pytest.fixture
def engine(clock):
    eng = PresentMomentEngine()
    eng.is_running = True  # start() is never called: no background task
    eng._budget_day = clock.strftime("%Y-%m-%d")
    eng._last_heartbeat_at = clock.now
    return eng


@pytest.fixture
def ran(engine, monkeypatch):
    """Tier 2 and the residual jobs replaced by recorders."""
    seen = SimpleNamespace(cycles=[], residuals=0)

    async def fake_cycle(woke_on=None):
        seen.cycles.append([e["kind"] for e in (woke_on or [])])
        return {}

    async def fake_residuals():
        seen.residuals += 1

    monkeypatch.setattr(engine, "run_cycle_once", fake_cycle)
    monkeypatch.setattr(engine, "_run_outreach_residuals", fake_residuals)
    return seen


def _last_moment(monkeypatch, *, age, meta=None):
    """Her last written moment as the store would report it; age None = no
    moment on disk."""
    import ai.present_moment_store as store

    reads = []

    def fake_read(**kw):
        reads.append(kw)
        if age is None:
            return None
        entry = store.MomentEntry(
            text="the house is quiet", written_at=EPOCH - age, meta=dict(meta or {})
        )
        return entry, float(age), False

    monkeypatch.setattr(store, "read_current", fake_read)
    return reads


# ── the brief's three ──────────────────────────────────────────────────


def test_device_churn_wakes_at_most_once_an_hour(engine):
    engine._ingest("device_arrived", {}, now=0.0)
    assert engine.woke_count == 1
    engine._ingest("device_left", {}, now=600.0)
    assert engine.woke_count == 1  # joins the bundle, no new wake
    engine._ingest("device_arrived", {}, now=3700.0)
    assert engine.woke_count == 2


def test_a_message_always_wakes(engine):
    engine._ingest("device_arrived", {}, now=0.0)
    engine._ingest("owner_message", {}, now=60.0)
    assert engine.woke_count == 2


def test_device_only_bundle_within_an_hour_is_nothing_new(engine):
    # her last moment was written at 1000.0; it is 1500.0 now
    assert engine.should_skip(
        bundle_kinds={"device_left"}, now=1500.0, last_moment_age=500.0
    ) is True
    assert engine.should_skip(
        bundle_kinds={"device_left", "initiative_sent"}, now=1500.0, last_moment_age=500.0
    ) is False
    assert engine.should_skip(
        bundle_kinds={"device_left", "owner_message"}, now=1500.0, last_moment_age=500.0
    ) is False


# ── the wake gate ──────────────────────────────────────────────────────


def test_an_unwoken_device_event_joins_the_bundle(engine):
    engine._ingest("device_arrived", {"device": "pixel"}, now=0.0)
    engine._wake.clear()  # the loop took the first wake
    engine._ingest("device_left", {"device": "pixel"}, now=600.0)
    assert not engine._wake.is_set()
    assert [(e["kind"], e["at"], e["device"]) for e in engine._pending] == [
        ("device_arrived", 0.0, "pixel"),
        ("device_left", 600.0, "pixel"),
    ]


def test_the_hour_is_counted_from_the_device_event_that_woke(engine):
    engine._ingest("device_arrived", {}, now=0.0)
    engine._ingest("device_left", {}, now=3599.0)  # un-woken: starts no new hour
    assert engine.woke_count == 1
    engine._ingest("device_arrived", {}, now=3600.0)
    assert engine.woke_count == 2
    engine._ingest("device_left", {}, now=7199.0)
    assert engine.woke_count == 2


@pytest.mark.parametrize(
    "kind",
    [
        "owner_message", "turn_completed", "turn_failed", "initiative_sent",
        "owner_arrived", "owner_online", "owner_joined_voice",
        "voice_channel_joined", "machine_active", "machine_unlocked",
        "privacy_shared", "monologue_thought", "task_problem",
    ],
)
def test_no_other_kind_is_held_back_inside_the_hour(engine, kind):
    engine._ingest("device_arrived", {}, now=0.0)
    engine._ingest("device_left", {}, now=30.0)  # un-woken
    engine._wake.clear()
    engine._ingest(kind, {}, now=60.0)
    assert engine._wake.is_set()
    assert engine.woke_count == 2
    assert len(engine._pending) == 3


def test_a_message_does_not_start_or_spend_the_devices_hour(engine):
    engine._ingest("owner_message", {}, now=0.0)
    engine._ingest("device_arrived", {}, now=10.0)  # the hour's first device wake
    assert engine.woke_count == 2
    engine._ingest("owner_message", {}, now=20.0)
    engine._ingest("device_left", {}, now=30.0)
    assert engine.woke_count == 3


def test_a_long_absence_arrival_is_still_promoted_inside_the_hour(engine, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_ARRIVAL_ABSENCE_SECONDS", 1800)
    engine._ingest("device_arrived", {}, now=0.0)
    engine._ingest("owner_arrived", {"absence_s": 5400.0}, now=60.0)
    assert engine._pending[-1]["kind"] == "owner_arrived_after_absence"
    assert engine.woke_count == 2


def test_without_a_given_time_an_event_is_stamped_from_the_clock(engine, clock):
    engine._ingest("device_arrived", {})
    clock.advance(600.0)
    engine._ingest("device_left", {})
    clock.advance(3000.0)
    engine._ingest("device_arrived", {})
    assert [e["at"] for e in engine._pending] == [EPOCH, EPOCH + 600.0, EPOCH + 3600.0]
    assert engine.woke_count == 2


def test_a_new_engine_has_no_hour_running(engine):
    """A restart forgets the wake gate's hour; the skip does not depend on it."""
    engine._ingest("device_arrived", {}, now=0.0)
    fresh = PresentMomentEngine()
    fresh.is_running = True
    assert fresh.woke_count == 0
    fresh._ingest("device_left", {}, now=600.0)
    assert fresh.woke_count == 1


def test_knob_zero_turns_the_wake_gate_off(engine, companion):
    companion.device_wake_seconds = 0
    for at in (0.0, 1.0, 2.0):
        engine._ingest("device_arrived", {}, now=at)
    assert engine.woke_count == 3


def test_with_the_organ_off_every_device_event_wakes_as_before(engine, companion):
    companion.present_moment = False
    engine._ingest("device_arrived", {}, now=0.0)
    engine._ingest("device_left", {}, now=600.0)
    assert engine.woke_count == 2


def test_a_stopped_engine_counts_no_wake(engine):
    engine.is_running = False
    engine._ingest("owner_message", {}, now=0.0)
    assert engine.woke_count == 0 and len(engine._pending) == 0


# ── the knob ───────────────────────────────────────────────────────────


def test_the_knob_is_a_companion_row_of_one_hour():
    from core.settings_schema import CompanionConfig

    assert CompanionConfig.model_fields["device_wake_seconds"].default == 3600
    assert CompanionConfig().device_wake_seconds == 3600
    assert CompanionConfig(device_wake_seconds=0).device_wake_seconds == 0
    with pytest.raises(ValidationError):
        CompanionConfig(device_wake_seconds=-1)


def test_unreadable_settings_mean_one_hour(engine, monkeypatch):
    import core.turn.companion_flags as flags

    def broken():
        raise RuntimeError("settings unreadable")

    for unreadable in (lambda: None, broken, lambda: SimpleNamespace(present_moment=True)):
        monkeypatch.setattr(flags, "companion_settings", unreadable)
        assert engine._device_wake_seconds() == 3600


def test_the_engine_reads_the_knob_live(engine, companion):
    companion.device_wake_seconds = 120
    assert engine._device_wake_seconds() == 120
    assert engine.should_skip(bundle_kinds={"device_left"}, now=0.0, last_moment_age=119.0) is True
    assert engine.should_skip(bundle_kinds={"device_left"}, now=0.0, last_moment_age=120.0) is False


# ── should_skip ────────────────────────────────────────────────────────


def test_a_moment_one_hour_old_is_not_skipped(engine):
    both = {"device_arrived", "device_left"}
    assert engine.should_skip(bundle_kinds=both, now=0.0, last_moment_age=3599.0) is True
    assert engine.should_skip(bundle_kinds=both, now=0.0, last_moment_age=3600.0) is False


def test_an_empty_bundle_is_not_skipped(engine):
    assert engine.should_skip(bundle_kinds=set(), now=0.0, last_moment_age=10.0) is False


def test_knob_zero_turns_the_skip_off(engine, companion):
    companion.device_wake_seconds = 0
    assert engine.should_skip(bundle_kinds={"device_left"}, now=0.0, last_moment_age=10.0) is False


def test_with_no_age_given_the_age_is_read_from_disk(engine, monkeypatch):
    reads = _last_moment(monkeypatch, age=500.0)
    assert engine.should_skip(bundle_kinds={"device_left"}, now=1500.0) is True
    assert reads == [{"now": 1500.0}]


def test_no_moment_on_disk_means_no_skip(engine, monkeypatch):
    _last_moment(monkeypatch, age=None)
    assert engine.should_skip(bundle_kinds={"device_left"}, now=1500.0) is False


def test_a_failed_read_means_no_skip(engine, monkeypatch):
    import ai.present_moment_store as store

    def broken(**kw):
        raise OSError("disk gone")

    monkeypatch.setattr(store, "read_current", broken)
    assert engine.should_skip(bundle_kinds={"device_left"}, now=1500.0) is False


def test_a_bundle_with_other_news_never_reads_the_disk(engine, monkeypatch):
    reads = _last_moment(monkeypatch, age=500.0)
    assert engine.should_skip(bundle_kinds={"device_left", "owner_message"}, now=1500.0) is False
    assert reads == []


# ── the skip in the loop ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_churn_only_cycle_is_skipped_as_nothing_new(
    engine, ran, clock, monkeypatch, caplog
):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    _last_moment(monkeypatch, age=900.0)
    engine.last_cycle_at = clock.now - 900.0
    engine.cycles_today = 7
    engine._ingest("device_arrived", {"device": "pixel"})
    engine._ingest("device_left", {"device": "pixel"})

    with caplog.at_level(logging.INFO, logger="ai.present_moment"):
        await engine._loop_once()

    assert ran.cycles == []  # no cycle, so no model call
    assert len(engine._pending) == 0  # dropped, not put back
    assert engine.last_cycle_at == clock.now - 900.0
    assert engine.cycles_today == 7
    assert ran.residuals == 1
    lines = [r.getMessage() for r in caplog.records if "nothing_new" in r.getMessage()]
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_the_skip_comes_before_the_daily_cap(engine, ran, clock, monkeypatch, caplog):
    """At the cap a bundle is held and fired later. A churn-only bundle is
    not held: it is dropped, and it spends nothing."""
    _set_knob(monkeypatch, "PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 2)
    _last_moment(monkeypatch, age=900.0)
    engine.cycles_today = 2
    engine.last_cycle_at = clock.now - 900.0
    engine._ingest("device_left", {})

    with caplog.at_level(logging.DEBUG, logger="ai.present_moment"):
        await engine._loop_once()

    assert ran.cycles == []
    assert len(engine._pending) == 0
    assert engine.cycles_today == 2
    assert "cap" not in caplog.text


@pytest.mark.asyncio
async def test_a_message_beside_the_churn_fires_the_whole_bundle(
    engine, ran, clock, monkeypatch
):
    _last_moment(monkeypatch, age=900.0)
    engine.last_cycle_at = clock.now - 900.0
    engine._ingest("device_arrived", {})
    engine._ingest("device_left", {})  # un-woken
    engine._ingest("owner_message", {})

    await engine._loop_once()

    assert ran.cycles == [["device_arrived", "device_left", "owner_message"]]
    assert ran.residuals == 1


@pytest.mark.asyncio
async def test_a_reminder_send_beside_the_churn_fires(engine, ran, clock, monkeypatch):
    _last_moment(monkeypatch, age=900.0)
    engine.last_cycle_at = clock.now - 900.0
    engine._ingest("device_left", {})
    engine._ingest("initiative_sent", {})

    await engine._loop_once()

    assert ran.cycles == [["device_left", "initiative_sent"]]


@pytest.mark.asyncio
async def test_a_priority_wake_beside_the_churn_fires_inside_the_gap(
    engine, ran, clock, monkeypatch
):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    _last_moment(monkeypatch, age=10.0)
    engine.last_cycle_at = clock.now - 10.0
    engine._ingest("device_left", {})
    engine._ingest("owner_joined_voice", {})

    await engine._loop_once()

    assert ran.cycles == [["device_left", "owner_joined_voice"]]


@pytest.mark.asyncio
async def test_churn_fires_once_her_last_moment_is_an_hour_old(
    engine, ran, clock, monkeypatch, caplog
):
    _last_moment(monkeypatch, age=HOUR)
    engine.last_cycle_at = clock.now - HOUR
    engine._ingest("device_arrived", {"device": "pixel"})

    with caplog.at_level(logging.INFO, logger="ai.present_moment"):
        await engine._loop_once()

    assert ran.cycles == [["device_arrived"]]
    assert ran.residuals == 1
    assert "nothing_new" not in caplog.text


@pytest.mark.asyncio
async def test_with_no_moment_on_disk_churn_fires(engine, ran):
    """The suite's moment folder is an empty tmp folder: the real read."""
    engine._ingest("device_arrived", {})
    await engine._loop_once()
    assert ran.cycles == [["device_arrived"]]


@pytest.mark.asyncio
async def test_the_age_is_read_from_disk_so_it_outlives_a_restart(clock, monkeypatch):
    """The real store, in the suite's tmp folder: a moment written before
    the restart, and an engine built after it that knows nothing else."""
    import ai.present_moment_store as store

    monkeypatch.setattr(store, "time", clock)
    store.write_moment(text="the house is quiet", meta={})
    clock.advance(900.0)

    restarted = PresentMomentEngine()
    restarted.is_running = True
    restarted._budget_day = clock.strftime("%Y-%m-%d")
    restarted._last_heartbeat_at = clock.now
    assert restarted.last_cycle_at is None
    did = []

    async def fake_cycle(woke_on=None):
        did.append("cycle")
        return {}

    async def fake_residuals():
        did.append("residuals")

    monkeypatch.setattr(restarted, "run_cycle_once", fake_cycle)
    monkeypatch.setattr(restarted, "_run_outreach_residuals", fake_residuals)
    restarted._ingest("device_left", {})

    await restarted._loop_once()

    assert did == ["residuals"]
    assert len(restarted._pending) == 0

    clock.advance(HOUR)  # her last moment is now older than the hour
    restarted._ingest("device_arrived", {})
    await restarted._loop_once()
    assert did == ["residuals", "cycle", "residuals"]


@pytest.mark.asyncio
async def test_a_skipped_cycle_never_reaches_the_gather_or_the_model(
    engine, clock, monkeypatch
):
    """Tier 2 left real: a skip must end the pass before any of it runs."""
    import ai.llm_client as llm_client
    import ai.present_moment_gather as gather_mod

    reached = []

    async def fake_gather(*, owner_user_id):
        reached.append("gather")
        raise AssertionError("a skipped cycle gathered")

    async def fake_chat(*args, **kwargs):
        reached.append("model")
        return ""

    async def fake_residuals():
        reached.append("residuals")

    monkeypatch.setattr(gather_mod, "gather_moment_inputs", fake_gather)
    monkeypatch.setattr(llm_client.llm, "chat_completion", fake_chat)
    monkeypatch.setattr(engine, "_run_outreach_residuals", fake_residuals)
    _last_moment(monkeypatch, age=900.0)
    engine.last_cycle_at = clock.now - 900.0
    engine._ingest("device_left", {})

    await engine._loop_once()

    assert reached == ["residuals"]


@pytest.mark.asyncio
async def test_knob_zero_fires_churn_as_before(engine, ran, clock, companion, monkeypatch):
    companion.device_wake_seconds = 0
    reads = _last_moment(monkeypatch, age=60.0)
    engine.last_cycle_at = clock.now - 900.0
    engine._ingest("device_left", {})

    await engine._loop_once()

    assert ran.cycles == [["device_left"]]
    assert reads == []  # the skip is off: it does not even look


@pytest.mark.asyncio
async def test_churn_inside_the_gap_is_deferred_then_skipped(
    engine, ran, clock, monkeypatch
):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    engine.last_cycle_at = clock.now - 10.0
    engine._ingest("device_left", {})
    await engine._loop_once()
    assert ran.cycles == [] and len(engine._pending) == 1  # deferred, as any ordinary wake
    assert ran.residuals == 0

    clock.advance(700.0)
    _last_moment(monkeypatch, age=710.0)
    engine._wake.set()  # stand in for the deferred deadline, no real sleep
    await engine._loop_once()

    assert ran.cycles == [] and len(engine._pending) == 0
    assert engine._deferred_wake_at is None
    assert ran.residuals == 1


# ── after a skip the heartbeat checks still run ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "before, after, expected",
    [
        ({"machine_state": "active", "conversation_bucket": 0},
         {"machine_state": "active", "conversation_bucket": 1}, "silence_bucket_crossed"),
        ({"machine_state": "active", "conversation_bucket": 0},
         {"machine_state": "idle", "conversation_bucket": 0}, "presence_drift"),
    ],
)
async def test_a_skip_does_not_blind_the_heartbeat_diff(
    engine, ran, clock, monkeypatch, before, after, expected
):
    _set_knob(monkeypatch, "PRESENT_MOMENT_HEARTBEAT_SECONDS", 1200)
    _set_knob(monkeypatch, "PRESENT_MOMENT_SENTINEL_HOURS", 12)
    _last_moment(monkeypatch, age=1200.0, meta={"tier1_snapshot": before})

    async def snap(now):
        return dict(after)

    monkeypatch.setattr(engine, "_tier1_snapshot", snap)
    engine.last_cycle_at = clock.now - 1200.0
    engine._last_heartbeat_at = clock.now - 1200.0  # the heartbeat is due
    engine._ingest("device_left", {})  # the churn that would have hidden it

    await engine._loop_once()

    assert ran.cycles == [[expected]]  # the check's own bundle, no device in it
    assert len(engine._pending) == 0
    assert engine._last_heartbeat_at == clock.now
    assert ran.residuals == 1


@pytest.mark.asyncio
async def test_a_skip_does_not_blind_the_sentinel(engine, ran, clock, companion, monkeypatch):
    """Only a knob longer than the sentinel can meet it; the sentinel still
    fires, and through the daily cap."""
    _set_knob(monkeypatch, "PRESENT_MOMENT_HEARTBEAT_SECONDS", 1200)
    _set_knob(monkeypatch, "PRESENT_MOMENT_SENTINEL_HOURS", 12)
    _set_knob(monkeypatch, "PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 1)
    companion.device_wake_seconds = 24 * 3600
    _last_moment(monkeypatch, age=13 * HOUR)
    engine.cycles_today = 99
    engine._last_heartbeat_at = clock.now - 1200.0
    engine._ingest("device_left", {})

    await engine._loop_once()

    assert ran.cycles == [["sentinel"]]
    assert ran.residuals == 1


@pytest.mark.asyncio
async def test_a_skip_with_a_quiet_heartbeat_ends_the_pass(engine, ran, clock, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_HEARTBEAT_SECONDS", 1200)
    same = {"machine_state": "active", "conversation_bucket": 0}
    _last_moment(monkeypatch, age=1200.0, meta={"tier1_snapshot": same})

    async def snap(now):
        return dict(same)

    monkeypatch.setattr(engine, "_tier1_snapshot", snap)
    engine.last_cycle_at = clock.now - 1200.0
    engine._last_heartbeat_at = clock.now - 1200.0
    engine._ingest("device_left", {})

    await engine._loop_once()

    assert ran.cycles == []
    assert ran.residuals == 1
    # the loop is off its 1 s floor (the 07-24 spin)
    assert engine._next_deadline(clock.now) == pytest.approx(1200.0)


@pytest.mark.asyncio
async def test_a_skip_before_the_heartbeat_is_due_leaves_its_deadline_alone(
    engine, ran, clock, monkeypatch
):
    _set_knob(monkeypatch, "PRESENT_MOMENT_HEARTBEAT_SECONDS", 1200)
    reads = _last_moment(monkeypatch, age=900.0)
    engine.last_cycle_at = clock.now - 900.0
    engine._last_heartbeat_at = clock.now - 500.0
    engine._ingest("device_left", {})

    await engine._loop_once()

    assert ran.cycles == []
    assert len(reads) == 1  # the skip's read; the heartbeat diff did not run
    assert engine._last_heartbeat_at == clock.now - 500.0
    assert engine._next_deadline(clock.now) == pytest.approx(700.0)


@pytest.mark.asyncio
async def test_a_check_held_at_the_cap_after_a_skip_still_runs_the_residuals(
    engine, ran, clock, monkeypatch
):
    _set_knob(monkeypatch, "PRESENT_MOMENT_HEARTBEAT_SECONDS", 1200)
    _set_knob(monkeypatch, "PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 2)
    before = {"machine_state": "active", "conversation_bucket": 0}
    _last_moment(monkeypatch, age=1200.0, meta={"tier1_snapshot": before})

    async def snap(now):
        return {"machine_state": "idle", "conversation_bucket": 0}

    monkeypatch.setattr(engine, "_tier1_snapshot", snap)
    engine.cycles_today = 2
    engine.last_cycle_at = clock.now - 1200.0
    engine._last_heartbeat_at = clock.now - 1200.0
    engine._ingest("device_left", {})

    await engine._loop_once()

    assert ran.cycles == []
    assert [e["kind"] for e in engine._pending] == ["presence_drift"]  # held; the device is gone
    assert ran.residuals == 1


# ── flag off ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_with_the_organ_off_the_loop_clears_pending_as_before(
    engine, ran, companion, monkeypatch, caplog
):
    companion.present_moment = False
    reads = _last_moment(monkeypatch, age=900.0)
    engine._ingest("device_left", {})

    with caplog.at_level(logging.DEBUG, logger="ai.present_moment"):
        await engine._loop_once()

    assert ran.cycles == []
    assert len(engine._pending) == 0
    assert ran.residuals == 1
    assert reads == []
    assert "nothing_new" not in caplog.text

"""Present Moment engine — tier-1 wake logic and tier-2 parse fallbacks.

Hermetic by construction: a fake clock replaces ``ai.present_moment.time``,
every cross-module dependency is monkeypatched at its SOURCE module (the
engine imports them lazily inside methods, so source-attribute patches
intercept), and ``llm.chat_completion`` is always a canned async stub —
zero live model calls, zero DB reads, zero writes outside ``tmp_path``.

No test calls ``start()``: there is no background task and no real sleep.
``_loop_once`` waits on ``self._wake``, so every test sets that event (or
lets ``_ingest`` set it) before driving a tick.
"""
from __future__ import annotations

import time as _real_time
from types import SimpleNamespace

import pytest

import ai.present_moment as pm
from ai.present_moment import PresentMomentEngine, _elapsed_bucket, notice

EPOCH = 1_800_000_000.0  # fixed, arbitrary; only its stability matters


class FakeClock:
    """Controllable stand-in for the module's ``time`` import."""

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
def _organ_on(monkeypatch):
    """_enabled() reads companion_settings() directly (fail-closed)."""
    import core.turn.companion_flags as flags

    monkeypatch.setattr(
        flags, "companion_settings", lambda: SimpleNamespace(present_moment=True)
    )


@pytest.fixture
def engine(clock):
    eng = PresentMomentEngine()
    eng.is_running = True  # start() is never called: no background task
    # KNOWN TRAP: with _budget_day == "" the first _loop_once takes the
    # day-rollover branch and zeroes cycles_today, silently defeating any
    # cap assertion. start() normally sets this; we set it by hand.
    eng._budget_day = clock.strftime("%Y-%m-%d")
    eng._last_heartbeat_at = clock.now
    return eng


@pytest.fixture
def cycles(engine, monkeypatch):
    """Replace tier 2 with a recorder so wake-logic tests stay tier-1 only."""
    recorded = []

    async def fake_cycle(woke_on=None):
        recorded.append(list(woke_on or []))
        return {}

    monkeypatch.setattr(engine, "run_cycle_once", fake_cycle)
    return recorded


# ── notice(): the caller-safety contract ───────────────────────────────


def test_notice_is_noop_when_engine_is_down(clock, monkeypatch):
    # FORCE the down state, never assume it: notice() reaches the
    # process-wide singleton, and tests/core/test_main.py's startup test
    # runs the app's real on_startup handlers — which call the real
    # present_moment_engine.start() (is_running = True) and never stop it.
    # Asserting is_running here instead of setting it made this test pass
    # only on alphabetical collection order (tests/ai/ before tests/core/).
    monkeypatch.setattr(pm.present_moment_engine, "is_running", False)
    pm.present_moment_engine._pending.clear()
    notice("turn_failed", count=1)  # must not raise
    assert len(pm.present_moment_engine._pending) == 0  # hook was a no-op


def test_notice_never_raises_when_ingest_explodes(monkeypatch):
    def boom(kind, fields):
        raise RuntimeError("ingest exploded")

    monkeypatch.setattr(pm.present_moment_engine, "_ingest", boom)
    assert notice("owner_arrived", absence_s=99.0) is None


# ── priority promotion at the arrival knob ─────────────────────────────


def test_arrival_promoted_at_absence_knob(engine, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_ARRIVAL_ABSENCE_SECONDS", 1800)
    engine._ingest("owner_arrived", {"absence_s": 1800.0})
    assert [e["kind"] for e in engine._pending] == ["owner_arrived_after_absence"]


def test_arrival_below_knob_is_not_promoted(engine, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_ARRIVAL_ABSENCE_SECONDS", 1800)
    engine._ingest("owner_arrived", {"absence_s": 1799.0})
    assert [e["kind"] for e in engine._pending] == ["owner_arrived"]


def test_arrival_without_absence_field_is_not_promoted(engine):
    engine._ingest("owner_arrived", {})
    assert [e["kind"] for e in engine._pending] == ["owner_arrived"]


# ── the deferring gap: suppressed wakes defer, never cancel ────────────


@pytest.mark.asyncio
async def test_priority_event_fires_inside_the_gap(engine, cycles, clock, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    engine.last_cycle_at = clock.now - 10.0  # deep inside the gap
    engine._ingest("turn_failed", {})
    await engine._loop_once()
    assert [e["kind"] for e in cycles[0]] == ["turn_failed"]
    assert len(engine._pending) == 0


@pytest.mark.asyncio
async def test_promoted_arrival_fires_inside_the_gap(engine, cycles, clock, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    _set_knob(monkeypatch, "PRESENT_MOMENT_ARRIVAL_ABSENCE_SECONDS", 1800)
    engine.last_cycle_at = clock.now - 10.0
    engine._ingest("owner_arrived", {"absence_s": 5400.0})
    await engine._loop_once()
    assert [e["kind"] for e in cycles[0]] == ["owner_arrived_after_absence"]


@pytest.mark.asyncio
async def test_non_priority_inside_gap_defers_without_losing_events(
    engine, cycles, clock, monkeypatch
):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    engine.last_cycle_at = clock.now - 10.0
    engine._ingest("message_received", {})
    engine._ingest("desktop_focus_changed", {})
    await engine._loop_once()
    assert cycles == []  # suppressed
    assert len(engine._pending) == 2  # but NOT dropped
    assert engine._deferred_wake_at == pytest.approx(engine.last_cycle_at + 600.0)


@pytest.mark.asyncio
async def test_deferred_bundle_fires_coalesced_after_the_gap(
    engine, cycles, clock, monkeypatch
):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    engine.last_cycle_at = clock.now - 10.0
    engine._ingest("message_received", {})
    await engine._loop_once()
    assert cycles == []

    clock.advance(700.0)  # gap expiry
    engine._ingest("desktop_focus_changed", {})  # arrives while deferred
    await engine._loop_once()

    assert [e["kind"] for e in cycles[0]] == ["message_received", "desktop_focus_changed"]
    assert engine._deferred_wake_at is None
    assert len(engine._pending) == 0


def test_next_deadline_prefers_deferred_wake_and_floors_at_one(engine, clock, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_HEARTBEAT_SECONDS", 1200)
    engine._last_heartbeat_at = clock.now
    engine._deferred_wake_at = clock.now + 30.0
    assert engine._next_deadline(clock.now) == pytest.approx(30.0)
    engine._deferred_wake_at = clock.now - 500.0  # overdue
    assert engine._next_deadline(clock.now) == 1.0


# ── the daily cap: lossless hold, sentinel exemption, rollover ──────────


@pytest.mark.asyncio
async def test_daily_cap_holds_the_bundle_losslessly(engine, cycles, clock, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 2)
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    engine.cycles_today = 2
    engine.last_cycle_at = clock.now - 1000.0  # past the gap: would fire
    engine._ingest("message_received", {})
    engine._ingest("turn_failed", {})
    await engine._loop_once()
    assert cycles == []
    assert {e["kind"] for e in engine._pending} == {"message_received", "turn_failed"}


@pytest.mark.asyncio
async def test_sentinel_fires_through_the_daily_cap(engine, cycles, clock, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 1)
    _set_knob(monkeypatch, "PRESENT_MOMENT_HEARTBEAT_SECONDS", 1200)
    engine.cycles_today = 99
    engine._last_heartbeat_at = clock.now - 5000.0

    async def fake_diff(now):
        return {"kind": "sentinel", "at": now}

    monkeypatch.setattr(engine, "_heartbeat_diff", fake_diff)
    engine._wake.set()  # stand in for the loop's timeout, no real sleep
    await engine._loop_once()
    assert [e["kind"] for e in cycles[0]] == ["sentinel"]


@pytest.mark.asyncio
async def test_held_bundle_fires_after_day_rollover(engine, cycles, clock, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 1)
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    engine.cycles_today = 1
    engine.last_cycle_at = clock.now - 1000.0
    engine._ingest("message_received", {})
    await engine._loop_once()
    assert cycles == [] and len(engine._pending) == 1

    clock.advance(86400.0)  # next local day
    engine._wake.set()
    await engine._loop_once()

    assert engine._budget_day == clock.strftime("%Y-%m-%d")
    assert engine.cycles_today == 0
    assert [e["kind"] for e in cycles[0]] == ["message_received"]


# ── the heartbeat diff: bucket crossing + sentinel ──────────────────────


def test_elapsed_bucket_edges():
    assert _elapsed_bucket(0.0) == 0
    assert _elapsed_bucket(3599.0) == 0
    assert _elapsed_bucket(3600.0) == 1
    assert _elapsed_bucket(3 * 3600.0) == 2
    assert _elapsed_bucket(6 * 3600.0) == 3


def _patch_read_current(monkeypatch, value):
    import ai.present_moment_store as store

    monkeypatch.setattr(store, "read_current", lambda **kw: value)


def _moment(meta):
    from ai.present_moment_store import MomentEntry

    return MomentEntry(text="a written moment", written_at=EPOCH - 60.0, meta=meta)


@pytest.mark.asyncio
async def test_heartbeat_first_moment_when_store_is_empty(engine, clock, monkeypatch):
    _patch_read_current(monkeypatch, None)
    diff = await engine._heartbeat_diff(clock.now)
    assert diff["kind"] == "heartbeat_first_moment"


@pytest.mark.asyncio
async def test_heartbeat_sentinel_when_moment_older_than_sentinel_hours(
    engine, clock, monkeypatch
):
    _set_knob(monkeypatch, "PRESENT_MOMENT_SENTINEL_HOURS", 12)
    _patch_read_current(monkeypatch, (_moment({}), 13 * 3600.0, True))
    diff = await engine._heartbeat_diff(clock.now)
    assert diff["kind"] == "sentinel"


@pytest.mark.asyncio
async def test_heartbeat_silence_bucket_crossed(engine, clock, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_SENTINEL_HOURS", 12)
    prev = {"machine_state": "active", "conversation_bucket": 0}
    _patch_read_current(monkeypatch, (_moment({"tier1_snapshot": prev}), 600.0, False))

    async def snap(now):
        return {"machine_state": "active", "conversation_bucket": 1}

    monkeypatch.setattr(engine, "_tier1_snapshot", snap)
    diff = await engine._heartbeat_diff(clock.now)
    assert diff["kind"] == "silence_bucket_crossed"
    assert diff["conversation_bucket"] == 1


@pytest.mark.asyncio
async def test_heartbeat_none_when_nothing_changed(engine, clock, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_SENTINEL_HOURS", 12)
    same = {"machine_state": "active", "conversation_bucket": 2}
    _patch_read_current(monkeypatch, (_moment({"tier1_snapshot": same}), 600.0, False))

    async def snap(now):
        return dict(same)

    monkeypatch.setattr(engine, "_tier1_snapshot", snap)
    assert await engine._heartbeat_diff(clock.now) is None


# ── tier 2: JSON parse, raw-text fallback, impulse normalization ────────


@pytest.fixture
def cycle_env(monkeypatch, tmp_path, clock, engine):
    """Every tier-2 dependency stubbed; writes forced into tmp_path."""
    import ai.llm_client as llm_client
    import ai.llm_routing as llm_routing
    import ai.present_moment_gather as gather_mod
    import ai.present_moment_store as store
    import ai.prompt_loader as prompt_mod
    import core.local_mode as local_mode

    state = SimpleNamespace(responses=[], writes=[], current=None, calls=0)

    async def fake_refs():
        return {"palli_user_id": 1, "app_user_id": 1}

    monkeypatch.setattr(local_mode, "get_local_owner_refs", fake_refs)

    async def fake_gather(*, owner_user_id):
        return gather_mod.GatherInputs(
            senses_block="S",
            bases_block="B",
            monologue_block="M",
            reflection_block="R",
            sense_health={"conversation": "ok", "activity": "timeout"},
        )

    monkeypatch.setattr(gather_mod, "gather_moment_inputs", fake_gather)
    monkeypatch.setattr(llm_routing, "get_task_model", lambda task, fallback=None: "test-model")
    monkeypatch.setattr(
        prompt_mod.prompt_loader,
        "load_prompt",
        lambda name: (
            "{current_time}|{previous_moment_block}|{events_block}"
            "|{senses_block}|{bases_block}|{monologue_block}|{reflection_block}"
        ),
    )

    async def fake_chat(*args, **kwargs):
        state.calls += 1
        return state.responses.pop(0) if state.responses else ""

    monkeypatch.setattr(llm_client.llm, "chat_completion", fake_chat)

    real_write = store.write_moment

    def fake_write(*, text, meta, root=None):
        state.writes.append({"text": text, "meta": meta})
        return real_write(text=text, meta=meta, root=tmp_path)

    monkeypatch.setattr(store, "write_moment", fake_write)
    monkeypatch.setattr(store, "read_current", lambda **kw: state.current)
    monkeypatch.setattr(store, "count_today", lambda **kw: len(state.writes))

    async def snap(now):
        return {"machine_state": "active", "conversation_bucket": 0}

    monkeypatch.setattr(engine, "_tier1_snapshot", snap)
    return state


@pytest.mark.asyncio
async def test_cycle_writes_moment_from_valid_json(engine, cycle_env, monkeypatch):
    _set_knob(monkeypatch, "PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 120)
    long_why = ("he is heads-down   and  interrupting would cost him " * 10).strip()
    cycle_env.responses = [
        '{"moment": "The house is quiet and he is working.", '
        '"impulse": {"kind": "hold", "why": "' + long_why + '"}}'
    ]
    result = await engine.run_cycle_once(
        woke_on=[{"kind": "turn_failed"}, {"kind": "turn_failed"}]
    )

    assert cycle_env.calls == 1  # parsed first try, no re-roll
    written = cycle_env.writes[0]
    assert written["text"] == "The house is quiet and he is working."
    impulse = written["meta"]["impulse"]
    assert impulse["kind"] == "hold"
    assert len(impulse["why"]) == 200 and "  " not in impulse["why"]  # collapsed + clamped
    assert written["meta"]["woke_on"] == [{"kind": "turn_failed", "count": 2}]
    assert written["meta"]["budget"]["cycles_today"] == 1
    assert written["meta"]["sense_health"]["activity"] == "timeout"
    assert engine.cycles_today == 1 and engine.last_error == ""
    assert result["impulse"]["kind"] == "hold"


@pytest.mark.asyncio
async def test_cycle_rerolls_once_on_malformed_json_then_parses(engine, cycle_env):
    cycle_env.responses = [
        '{"moment": "truncated mid-thoug',
        '{"moment": "second try landed", "impulse": {"kind": "none", "why": ""}}',
    ]
    await engine.run_cycle_once()
    assert cycle_env.calls == 2
    assert cycle_env.writes[0]["text"] == "second try landed"


@pytest.mark.asyncio
async def test_cycle_falls_back_to_raw_text_when_response_is_not_json(engine, cycle_env):
    cycle_env.responses = ["  I am here. Nothing is wrong.  ", "I am here. Nothing is wrong."]
    await engine.run_cycle_once()
    assert cycle_env.calls == 2  # both attempts unparseable
    assert cycle_env.writes[0]["text"] == "I am here. Nothing is wrong."
    assert cycle_env.writes[0]["meta"]["impulse"]["kind"] == "none"


@pytest.mark.asyncio
async def test_cycle_normalizes_malformed_impulse_to_none(engine, cycle_env):
    cycle_env.responses = [
        '{"moment": "a", "impulse": "just a string"}',
        '{"moment": "b", "impulse": {"kind": "shout", "why": "x"}}',
    ]
    await engine.run_cycle_once()
    await engine.run_cycle_once()
    for written in cycle_env.writes:
        assert written["meta"]["impulse"]["kind"] == "none"
        assert "malformed impulse" in written["meta"]["impulse"]["why"]


@pytest.mark.asyncio
async def test_cycle_writes_nothing_when_appraisal_stays_empty(engine, cycle_env, clock):
    cycle_env.responses = ["", ""]
    result = await engine.run_cycle_once()
    assert cycle_env.calls == 2
    assert cycle_env.writes == []
    assert "empty appraisal" in result["error"]
    assert engine.cycles_today == 0  # success-only, mirrors count_today()
    assert engine.last_cycle_at == clock.now  # attempt throttle still advances


# ── 2026-07-26 polish (step 02): the two owner-approved guards ──────────


def test_prompt_format_guard_matches_engine_contract():
    """The LIVE template must carry exactly the placeholders run_cycle_once
    renders — a drifted prompt fails here instead of KeyError-ing a live
    cycle. Also guards the two-lock cap statement and the humanized events
    shape (the mechanics-echo fix): no raw enum kind ever reaches her."""
    import string

    from ai.present_moment import _EVENT_PHRASES, _humanize_events
    from ai.prompt_loader import prompt_loader

    template = prompt_loader.load_prompt("present_moment_appraisal")
    assert template, "live prompt file missing"

    fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    assert fields == {
        "current_time", "previous_moment_block", "events_block",
        "senses_block", "bases_block", "monologue_block", "reflection_block",
    }
    assert "1,200" in template or "1200" in template  # two-lock cap statement

    rendered = _humanize_events(
        [{"kind": k} for k in _EVENT_PHRASES] + [{"kind": "brand_new_kind"}]
    )
    for raw_kind in _EVENT_PHRASES:
        assert raw_kind not in rendered  # no enum shape reaches her
    assert "brand_new_kind" not in rendered  # unknown kinds degrade too
    assert "brand new kind" in rendered


@pytest.mark.asyncio
async def test_carryover_branch_renders_previous_moment(engine, cycle_env, monkeypatch):
    """Regenerate-with-carryover (spec §2): a previous moment must enter the
    prompt with its age framing and land in meta as prev_moment_at; the
    first-moment sentinel must appear only when the store is empty."""
    import ai.llm_client as llm_client
    import ai.present_moment_store as store

    captured = []

    async def capturing_chat(messages, **kwargs):
        captured.append(messages[0]["content"])
        return '{"moment": "carried forward", "impulse": {"kind": "none", "why": ""}}'

    monkeypatch.setattr(llm_client.llm, "chat_completion", capturing_chat)

    prev = store.MomentEntry(
        text="the quiet felt like decompression", written_at=EPOCH - 600, meta={}
    )
    cycle_env.current = (prev, 600.0, False)
    await engine.run_cycle_once(woke_on=[{"kind": "owner_message"}])

    assert "the quiet felt like decompression" in captured[-1]
    assert "(none — this is your first moment)" not in captured[-1]
    assert cycle_env.writes[-1]["meta"]["prev_moment_at"] == EPOCH - 600

    cycle_env.current = None
    await engine.run_cycle_once(woke_on=[{"kind": "owner_message"}])
    assert "(none — this is your first moment)" in captured[-1]
    assert cycle_env.writes[-1]["meta"]["prev_moment_at"] is None


@pytest.mark.asyncio
async def test_cap_zero_means_unlimited_not_stopped(engine, cycles, clock, monkeypatch):
    """0 = unlimited (owner convention; 07c Task 3, built 2026-08-06). The old
    `cycles_today >= cap` read 0 as 'never fire' — her present STOPPED
    entirely, the exact opposite of the convention."""
    _set_knob(monkeypatch, "PRESENT_MOMENT_MAX_CYCLES_PER_DAY", 0)
    _set_knob(monkeypatch, "PRESENT_MOMENT_MIN_GAP_SECONDS", 600)
    engine.cycles_today = 500
    engine.last_cycle_at = clock.now - 1000.0
    engine._ingest("message_received", {})
    await engine._loop_once()
    assert len(cycles) == 1, "cap 0 must mean unlimited, never 'present stopped'"


def test_owner_online_and_offline_have_lived_phrases_and_promote_long_absence():
    """Discord presence wakes (2026-08-18): the kinds read like life, not
    telemetry, and a return after >= the arrival-absence knob promotes to the
    priority arrival wake exactly like the old machine arrival did."""
    import ai.present_moment as pm

    assert "owner_online" in pm._EVENT_PHRASES and "owner_offline" in pm._EVENT_PHRASES
    for k in ("owner_online", "owner_offline"):
        assert "_" not in pm._EVENT_PHRASES[k]
    text = pm._humanize_events([{"kind": "owner_online", "count": 1, "absence_s": 7200}])
    assert "owner_online" not in text and "away" in text

    eng = pm.PresentMomentEngine()
    eng.is_running = True
    eng._ingest("owner_online", {"absence_s": 5400})
    assert eng._pending[-1]["kind"] == "owner_arrived_after_absence"
    eng._ingest("owner_online", {"absence_s": 60})
    assert eng._pending[-1]["kind"] == "owner_online"


# ── step 23 (2026-08-24): the moment hears the door — ordinary wakes ────

def test_the_moment_hears_the_door_as_ordinary_wakes():
    import ai.present_moment as pm

    assert "device_arrived" in pm._EVENT_PHRASES and "device_left" in pm._EVENT_PHRASES
    assert "device_arrived" not in pm._PRIORITY_KINDS and "device_left" not in pm._PRIORITY_KINDS
    for k in ("device_arrived", "device_left"):
        assert "_" not in pm._EVENT_PHRASES[k]


def test_device_wakes_name_the_device_and_the_absence():
    import ai.present_moment as pm

    text = pm._humanize_events([{"kind": "device_arrived", "device": "Sam's phone", "absence_s": 3600.0}])
    assert text.startswith("- Sam's phone") and "came home" in text
    assert "after about 60 minutes away" in text
    text2 = pm._humanize_events([{"kind": "device_left", "device": "…:2c:ba"}])
    assert "…:2c:ba" in text2 and "left" in text2
    bare = pm._humanize_events([{"kind": "device_arrived"}])          # structure only: the phrase alone
    assert bare == "- " + pm._EVENT_PHRASES["device_arrived"]


def test_several_devices_in_one_bundle_are_listed_not_counted():
    import ai.present_moment as pm

    text = pm._humanize_events([
        {"kind": "device_arrived", "device": "Sam's phone"},
        {"kind": "device_arrived", "device": "pixel"},
    ])
    assert "Sam's phone" in text and "pixel" in text and "times" not in text

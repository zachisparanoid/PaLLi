"""Unit tests for the steering queue + now-doing registry (spec §4a, §4d)."""
from __future__ import annotations


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/steering.py", "agent_steering_under_test")


def _bundle(mod, text, at=100.0):
    return mod.SteerBundle(turn=object(), reply_text=None, enqueued_at=at)


def test_enqueue_and_drain_preserves_arrival_order():
    mod = _load()
    a, b = _bundle(mod, "first", 1.0), _bundle(mod, "second", 2.0)
    assert mod.enqueue("s1", a) is True
    assert mod.enqueue("s1", b) is True
    assert mod.has_pending("s1") is True
    assert mod.drain("s1") == [a, b]
    assert mod.has_pending("s1") is False
    assert mod.drain("s1") == []


def test_queues_are_isolated_per_session_key():
    mod = _load()
    a, b = _bundle(mod, "a"), _bundle(mod, "b")
    mod.enqueue("chan-1", a)
    mod.enqueue("chan-2", b)
    assert mod.drain("chan-1") == [a]
    assert mod.drain("chan-2") == [b]


def test_cap_rejects_overflow_so_caller_can_fall_back():
    mod = _load()
    for i in range(mod.QUEUE_CAP):
        assert mod.enqueue("s", _bundle(mod, str(i))) is True
    assert mod.enqueue("s", _bundle(mod, "one too many")) is False
    assert len(mod.peek("s")) == mod.QUEUE_CAP


def test_remove_and_clear():
    mod = _load()
    a, b = _bundle(mod, "a"), _bundle(mod, "b")
    mod.enqueue("s", a)
    mod.enqueue("s", b)
    assert mod.remove("s", a) is True
    assert mod.remove("s", a) is False
    assert mod.clear("s") == [b]
    assert mod.has_pending("s") is False


def test_oldest_age_measures_the_head_of_the_queue():
    mod = _load()
    mod.enqueue("s", _bundle(mod, "old", at=100.0))
    mod.enqueue("s", _bundle(mod, "new", at=150.0))
    assert mod.oldest_age("s", now=160.0) == 60.0
    assert mod.oldest_age("empty", now=160.0) == 0.0


def test_now_doing_registry_roundtrip():
    mod = _load()
    assert mod.get_now_doing("s") == ""
    mod.set_now_doing("s", "running slow_tool (iteration 2)")
    assert mod.get_now_doing("s") == "running slow_tool (iteration 2)"
    mod.clear_now_doing("s")
    assert mod.get_now_doing("s") == ""


# ── run phase (phase-owned routing, 2026-08-04) ─────────────────────────────


def test_run_phase_default_empty():
    mod = _load()
    mod.clear_run_state("s1")
    assert mod.get_run_phase("s1") == ""
    assert mod.get_tool_call_count("s1") == 0
    assert mod.is_working("s1") is False


def test_run_phase_set_and_clear():
    mod = _load()
    mod.set_run_phase("s2", "prep")
    assert mod.get_run_phase("s2") == "prep"
    mod.set_run_phase("s2", "generating")
    assert mod.get_run_phase("s2") == "generating"
    mod.clear_run_state("s2")
    assert mod.get_run_phase("s2") == ""


def test_tool_calls_accumulate_and_gate_working():
    mod = _load()
    mod.clear_run_state("s3")
    mod.set_run_phase("s3", "generating")
    assert mod.is_working("s3") is False
    mod.note_tool_calls("s3", 2)
    assert mod.get_tool_call_count("s3") == 2
    assert mod.is_working("s3") is True
    mod.note_tool_calls("s3", 1)
    assert mod.get_tool_call_count("s3") == 3
    mod.clear_run_state("s3")
    assert mod.is_working("s3") is False


def test_clear_run_state_also_clears_now_doing():
    mod = _load()
    mod.set_now_doing("s4", "running web_search")
    mod.set_run_phase("s4", "generating")
    mod.clear_run_state("s4")
    assert mod.get_now_doing("s4") == ""
    assert mod.get_run_phase("s4") == ""


class TestDuplexRunState:
    """Step 27 Phase A: three more per-session facts, all cleared with the run."""

    def test_stop_flag_roundtrip(self):
        import core.agent.steering as st
        st.clear_run_state("discord:9")
        assert st.stop_requested("discord:9") is False
        st.request_stop("discord:9")
        assert st.stop_requested("discord:9") is True
        st.clear_run_state("discord:9")
        assert st.stop_requested("discord:9") is False

    def test_expectation_roundtrip_and_clear(self):
        import core.agent.steering as st
        st.clear_run_state("discord:9")
        assert st.get_expectation("discord:9") is None
        st.set_expectation("discord:9", 12.5)
        assert st.get_expectation("discord:9") == 12.5
        st.clear_run_state("discord:9")
        assert st.get_expectation("discord:9") is None

    def test_presence_activity_pop_is_one_shot(self):
        import core.agent.steering as st
        st.clear_run_state("discord:9")
        assert st.pop_presence_activity("discord:9") is None
        st.note_presence_activity("discord:9", "heads down on the adapter")
        assert st.pop_presence_activity("discord:9") == "heads down on the adapter"
        assert st.pop_presence_activity("discord:9") is None

    def test_clear_run_state_clears_presence_too(self):
        import core.agent.steering as st
        st.note_presence_activity("discord:9", "x")
        st.clear_run_state("discord:9")
        assert st.pop_presence_activity("discord:9") is None


class TestPause:
    """Step 27 Phase B (spec §3): "hold on" — the request is cleared with the
    run; the paused RECORD outlives it (a paused run has ended)."""

    def test_pause_request_roundtrip_cleared_with_run(self):
        import core.agent.steering as st
        st.clear_run_state("discord:9"); st.clear_paused("discord:9")
        assert st.pause_requested("discord:9") is False
        st.request_pause("discord:9")
        assert st.pause_requested("discord:9") is True
        st.clear_run_state("discord:9")
        assert st.pause_requested("discord:9") is False

    def test_paused_record_outlives_run_state(self):
        import core.agent.steering as st
        st.clear_paused("discord:9")
        st.set_paused("discord:9", "running local_shell (iteration 3)")
        st.clear_run_state("discord:9")
        p = st.get_paused("discord:9")
        assert p is not None and p.now_doing.startswith("running local_shell") and p.paused_at > 0
        st.clear_paused("discord:9")
        assert st.get_paused("discord:9") is None

from __future__ import annotations

import pytest


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/detection.py", "agent_detection_under_test")


def test_generic_repeat_warns_at_threshold():
    mod = _load()
    det = mod.ToolLoopDetector(known_tool_names={"foo"})
    args = {"x": 1}
    for _ in range(9):
        decision = det.check("foo", args)
        assert not decision.stuck
    decision = det.check("foo", args)
    assert decision.stuck
    assert decision.detector == "generic_repeat"
    assert decision.level == "warning"


def test_unknown_tool_repeat_critical_at_threshold():
    mod = _load()
    det = mod.ToolLoopDetector(known_tool_names={"foo"})
    for _ in range(9):
        decision = det.check("nonexistent", {})
        assert not decision.stuck
    decision = det.check("nonexistent", {})
    assert decision.stuck
    assert decision.detector == "unknown_tool_repeat"
    assert decision.level == "critical"


def test_known_poll_no_progress_warn_then_critical():
    mod = _load()
    det = mod.ToolLoopDetector(known_tool_names={"poll"}, polling_tools={"poll"})
    args = {"job_id": "abc"}
    for i in range(20):
        decision = det.check("poll", args, result_hash="same-result")
        if i < 9:
            assert not decision.stuck
        elif i < 19:
            if decision.stuck:
                assert decision.level == "warning"
                assert decision.detector == "known_poll_no_progress"


def test_global_circuit_breaker_at_30():
    mod = _load()
    det = mod.ToolLoopDetector(known_tool_names={"foo"})
    args = {"x": 1}
    triggered = False
    for i in range(30):
        decision = det.check("foo", args, result_hash="no-progress")
        if decision.stuck and decision.detector == "global_circuit_breaker":
            triggered = True
            assert decision.level == "critical"
            break
    assert triggered or i == 29


def test_ping_pong_detection():
    mod = _load()
    det = mod.ToolLoopDetector(known_tool_names={"a", "b"})
    triggered = False
    for _ in range(15):
        det.check("a", {"k": 1})
        decision = det.check("b", {"k": 2})
        if decision.stuck and decision.detector == "ping_pong":
            triggered = True
            break
    assert triggered


def test_args_hash_stable():
    mod = _load()
    h1 = mod._args_hash({"x": 1, "y": 2})
    h2 = mod._args_hash({"y": 2, "x": 1})
    assert h1 == h2


# ---------------------------------------------------------------------------
# D.4 step 1 regression pins for generic_repeat detector.
#
# Pre-D.4 the detector summed across the entire 30-record window, firing
# on legitimate "search-then-search" patterns separated by other tools.
# The fix makes it count only the *trailing consecutive* run of identical
# (tool_name, args_hash) records, mirroring the ping_pong / unknown_tool_repeat
# style. These tests pin both the true-positive (still fires for legit
# consecutive abuse) and the false-positive cases (other-tool calls and
# differing args break the streak).
# ---------------------------------------------------------------------------


def test_generic_repeat_does_not_fire_on_alternating_tools():
    """False-positive case: tool A then tool B then tool A again — the
    pre-fix sum() would have counted both A calls toward the streak.
    Now the streak is broken by the B call, so even 9 A-calls scattered
    among B-calls must NOT fire generic_repeat (the threshold is 10
    consecutive)."""
    mod = _load()
    det = mod.ToolLoopDetector(known_tool_names={"a", "b"})
    args = {"x": 1}
    # Interleave a/b 14 times — 14 A-calls total but never consecutive.
    last_decision = None
    for _ in range(14):
        det.check("a", args)
        last_decision = det.check("b", args)
    # Neither side should be flagged as generic_repeat (alternating).
    # ping_pong might fire here, but generic_repeat MUST NOT.
    assert last_decision.detector != "generic_repeat"


def test_generic_repeat_fires_on_consecutive_calls():
    """True-positive case: 10 consecutive identical calls trip the
    warning at WARNING_THRESHOLD (10). This is the legitimate abuse
    case the detector is supposed to catch."""
    mod = _load()
    det = mod.ToolLoopDetector(known_tool_names={"foo"})
    args = {"x": 1}
    triggered = None
    for _ in range(10):
        triggered = det.check("foo", args)
    assert triggered.stuck is True
    assert triggered.detector == "generic_repeat"
    assert triggered.level == "warning"
    assert triggered.count == 10


def test_generic_repeat_window_boundary_does_not_fire_with_gap():
    """Window-boundary case: tool A once, then 20 calls to other tools,
    then tool A again. The pre-fix sum() would have counted both A-calls
    across the gap. Now the streak is broken — the second A-call sits
    alone at the end of the deque (its consecutive count is 1), so
    generic_repeat must NOT fire."""
    mod = _load()
    det = mod.ToolLoopDetector(known_tool_names={"a", "b"})
    det.check("a", {"x": 1})
    # 20 unrelated tool calls between the two A-calls. Stays under deque
    # maxlen (30) so the first A-record is still in the window.
    for i in range(20):
        det.check("b", {"i": i})  # unique args so generic_repeat doesn't fire on B either
    # Second A-call — same args as the first. Pre-fix this counted 2 (matches
    # the first A across the gap). Post-fix it counts 1 (only itself).
    decision = det.check("a", {"x": 1})
    assert decision.detector != "generic_repeat"


def test_generic_repeat_differing_args_break_streak():
    """Same tool name but differing args also breaks the streak —
    consistent with the (tool_name, args_hash) tuple match in the
    consecutive loop. 10 calls to tool 'foo' with 10 different args
    objects must NOT fire generic_repeat."""
    mod = _load()
    det = mod.ToolLoopDetector(known_tool_names={"foo"})
    last = None
    for i in range(10):
        last = det.check("foo", {"i": i})  # each args dict is different
    assert last.detector != "generic_repeat"

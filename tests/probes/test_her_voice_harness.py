"""Batch 7, task 0b: the harness fixes the her_voice battery needs.

1. Every lane on the environment's brain (A5): with LLM_PROVIDER and
   LLM_RESPONSE_MODEL set, the battery's settings snapshot routes every lane
   there, and a preflight says so loudly before the first scenario.
2. The model per lane is recorded and reported.
3. Pass rates in --compare, and a warning when the selection is empty (A6).
4. Quiet and duplex (A7): both flags apply; a quiet turn is a valid reply;
   expect_quiet is a deterministic check; beats and delegation text are kept.

No network: every LLM call and every turn here is faked.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import engine
import judge as probe_judge
import report as probe_report
from schema import Judge, ProbeScenario, ProbeTurn, check_turn

_ENV_PAIR = ("deepseek", "deepseek-v4-flash")
_WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "probe_battery.py"


def _set_env_brain(monkeypatch, provider=_ENV_PAIR[0], model=_ENV_PAIR[1]):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("LLM_RESPONSE_MODEL", model)


def _clear_env_brain(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_RESPONSE_MODEL", raising=False)


# ── 1. every lane on the environment's brain ─────────────────────────────


def test_applied_flags_routes_every_lane_to_the_env_brain(monkeypatch):
    import core.settings_boot as settings_boot
    from ai.llm_routing import get_task_model
    from core.settings_schema import RoutingConfig

    _set_env_brain(monkeypatch)
    with engine.applied_flags(None):
        llm_cfg = settings_boot.settings_service.snapshot().llm
        assert llm_cfg.default_provider == "deepseek"
        for lane in RoutingConfig.model_fields:  # nine required + appraisal + narrative_author
            entry = getattr(llm_cfg.routing, lane)
            assert entry is not None, f"lane {lane} left unset"
            assert (entry.provider, entry.model) == _ENV_PAIR, f"lane {lane} -> {entry}"
        assert get_task_model("analysis") == "deepseek::deepseek-v4-flash"
        assert get_task_model("response") == "deepseek::deepseek-v4-flash"


def test_applied_flags_leaves_routing_alone_without_the_env_pair(monkeypatch):
    import core.settings_boot as settings_boot

    real_llm = settings_boot.settings_service.snapshot().llm
    _clear_env_brain(monkeypatch)
    with engine.applied_flags(None):
        assert settings_boot.settings_service.snapshot().llm == real_llm
    # Half a pair is not a brain: still untouched.
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    with engine.applied_flags(None):
        assert settings_boot.settings_service.snapshot().llm == real_llm


def test_brain_preflight_is_quiet_when_not_applicable_or_correct(monkeypatch):
    _clear_env_brain(monkeypatch)
    assert engine.brain_preflight() == ""
    _set_env_brain(monkeypatch)
    assert engine.brain_preflight() == ""


def test_brain_preflight_names_the_mismatch(monkeypatch):
    import ai.llm_routing as routing

    _set_env_brain(monkeypatch)
    monkeypatch.setattr(routing, "get_task_model", lambda task, fallback=None: "local_openai::qwen3.5:9b")
    problem = engine.brain_preflight()
    assert "deepseek::deepseek-v4-flash" in problem and "local_openai::qwen3.5:9b" in problem


# ── 2. the model per lane ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_counted_llm_records_the_resolved_model_per_lane(monkeypatch):
    from ai.llm_client import llm
    from core.agent import observability

    async def fake_chat(messages, model=None, **kwargs):
        # What the real chat_completion does: resolve the route, log it.
        llm._log_route_resolution(
            log_context=kwargs.get("log_context", "general"),
            route={"requested_model_reference": model,
                   "effective_model_reference": "deepseek::deepseek-v4-flash"},
        )
        return '{"pass": true, "reason": "ok"}'

    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    counter = engine.LLMCallCounter()
    with engine.counted_llm(counter):
        await llm.chat_completion(
            [{"role": "user", "content": "x"}], model="deepseek::deepseek-v4-flash",
            log_context="probe_judge",
        )
        await llm.chat_completion([{"role": "user", "content": "y"}], log_context="turn_read")
        # Her reply: the agent loop logs every model call through observability.
        observability.log_llm_call(
            session_key="s", iteration_n=1, provider="openai_compat", model="deepseek-v4-flash",
            prompt_tokens=1, completion_tokens=1, latency_ms=1,
        )
    snap = counter.snapshot()
    assert snap["by_context"] == {"probe_judge": 1, "turn_read": 1}
    assert snap["models"]["probe_judge"] == ["deepseek::deepseek-v4-flash"]
    assert snap["models"]["turn_read"] == ["deepseek::deepseek-v4-flash"]
    assert snap["models"]["agent_loop"] == ["openai_compat::deepseek-v4-flash"]


def test_merge_and_report_carry_the_models():
    a = engine.ScenarioResult(scenario_id="s", pack="p", family="f", verdict="PASS",
                              llm_calls={"by_context": {"probe_judge": 1}, "chars_in": 0, "chars_out": 0,
                                         "models": {"probe_judge": ["deepseek::deepseek-v4-flash"]}})
    b = engine.ScenarioResult(scenario_id="s", pack="p", family="f", verdict="PASS",
                              llm_calls={"by_context": {"probe_judge": 1}, "chars_in": 0, "chars_out": 0,
                                         "models": {"agent_loop": ["openai_compat::deepseek-v4-flash"]}})
    merged = engine.merge_repeat_runs([a, b])
    assert merged.llm_calls["models"] == {
        "probe_judge": ["deepseek::deepseek-v4-flash"],
        "agent_loop": ["openai_compat::deepseek-v4-flash"],
    }
    payload = probe_report.results_to_json([merged], label="m", started_at=0.0, finished_at=1.0)
    assert payload["scenarios"][0]["models"]["agent_loop"] == ["openai_compat::deepseek-v4-flash"]
    assert payload["totals"]["models"]["probe_judge"] == ["deepseek::deepseek-v4-flash"]
    md = probe_report.render_markdown(payload)
    assert "deepseek::deepseek-v4-flash" in md and "agent_loop" in md


# ── 3. pass rates and the empty-selection warning ────────────────────────


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("probe_battery_wrapper_0b", _WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["probe_battery_wrapper_0b"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_compare_reports_pass_rate_deltas_and_overall_rates():
    w = _load_wrapper()
    base = {"scenarios": [
        {"id": "a", "verdict": "FAIL", "n_runs": 3, "n_passed": 1},
        {"id": "b", "verdict": "PASS", "n_runs": 3, "n_passed": 3},
    ]}
    new = {"scenarios": [
        {"id": "a", "verdict": "PASS", "n_runs": 3, "n_passed": 3},
        {"id": "b", "verdict": "FAIL", "n_runs": 3, "n_passed": 2},
    ]}
    diff = w.compare_reports(base, new)
    rates = {r["id"]: r for r in diff["pass_rates"]}
    assert (rates["a"]["before"], rates["a"]["after"]) == ("1/3", "3/3")
    assert rates["a"]["delta"] == pytest.approx(2 / 3)
    assert rates["b"]["delta"] == pytest.approx(-1 / 3)
    assert diff["overall"]["before"] == pytest.approx(4 / 6)
    assert diff["overall"]["after"] == pytest.approx(5 / 6)
    lines = w.format_pass_rates(diff)
    assert any("a" in ln and "1/3" in ln and "3/3" in ln for ln in lines)
    assert any("overall" in ln for ln in lines)


def test_compare_pass_rates_default_to_single_runs():
    w = _load_wrapper()
    diff = w.compare_reports({"scenarios": [{"id": "a", "verdict": "PASS"}]},
                             {"scenarios": [{"id": "a", "verdict": "ERROR"}]})
    (rate,) = diff["pass_rates"]
    assert (rate["before"], rate["after"], rate["delta"]) == ("1/1", "0/1", -1.0)


def test_wrapper_counts_the_selection():
    w = _load_wrapper()
    assert w.selected_count(["no-such-pack"], "") == 0
    assert w.selected_count(["invariant"], "") >= 18
    assert w.selected_count(["invariant"], "hermeticity-canary-plant") == 1
    assert w.selected_count([], "no-such-scenario") == 0


# ── 4. quiet and duplex ──────────────────────────────────────────────────


def test_she_decides_and_duplex_are_probe_flags():
    assert {"she_decides", "duplex"} <= engine.ALLOWED_FLAGS
    from core.agent import duplex, quiet_choice

    with engine.applied_flags({"she_decides": True, "duplex": True}):
        assert quiet_choice.she_decides_enabled() is True
        assert duplex.duplex_enabled() is True
    with engine.applied_flags(None):  # default: off, as the suite pins them
        assert quiet_choice.she_decides_enabled() is False
        assert duplex.duplex_enabled() is False


@pytest.mark.asyncio
async def test_send_message_passes_outcome_and_keeps_beats_and_delegation(monkeypatch):
    import api.chat as chat

    seen = {}

    async def fake_dispatch(message, *, outcome=None):
        seen["outcome_given"] = outcome is not None
        if outcome is not None:
            outcome["quiet"] = True
        beats = [SimpleNamespace(text="first bit"), SimpleNamespace(text="second bit")]
        return ("", "the delegated result", [], beats)

    monkeypatch.setattr(chat, "_dispatch_to_spine", fake_dispatch)
    sent = await engine._send_message("don't reply to this")
    assert seen["outcome_given"] is True
    assert sent.text == "" and sent.quiet is True
    assert sent.beats == ["first bit", "second bit"]
    assert sent.delegation == "the delegated result"


@pytest.mark.asyncio
async def test_send_message_captures_mid_task_speech(monkeypatch):
    """Fix round 1 (Important): on the dashboard, chime_in / ask_him reach
    speak_mid_task with channel_id=0, record_outbound sends nothing, and the
    words reached neither the reply nor delegation_text. The REAL chime_in
    tool runs here, so the name it resolves at call time is the one patched."""
    import api.chat as chat
    from core.agent import duplex
    from core.agent.builtin_tools.duplex_hands import ChimeInTool
    from core.agent.tools import ToolContext

    original = duplex.speak_mid_task
    tool_said = {}

    async def fake_dispatch(message, *, outcome=None):
        ctx = ToolContext(turn_owner_id=1, is_owner=True, surface="dashboard", channel_id=0)
        tool_said["chime"] = await ChimeInTool().execute({"text": "found it, counting now"}, ctx)
        await duplex.speak_mid_task(owner_user_id=1, channel_id=0, text="which folder did you mean?",
                                    origin="duplex_ask", platform="dashboard")
        return ("it's 21 lines.", "", [], [])

    monkeypatch.setattr(chat, "_dispatch_to_spine", fake_dispatch)
    sent = await engine._send_message("how long is that file?")
    assert tool_said["chime"].startswith("Said (")       # the real release leg still ran
    assert sent.mid_task == [
        {"text": "found it, counting now", "origin": "duplex_chime"},
        {"text": "which folder did you mean?", "origin": "duplex_ask"},
    ]
    assert duplex.speak_mid_task is original               # the capture is per turn


def test_expect_tool_accepts_alternatives():
    turn = ProbeTurn(send="x", expect_tool="local_shell|read_file")
    assert check_turn("ok", turn, ["read_file"]) == []
    assert check_turn("ok", turn, ["local_shell"]) == []
    fails = check_turn("ok", turn, ["introspect"])
    assert len(fails) == 1 and "local_shell|read_file" in fails[0]


def test_expect_quiet_is_a_deterministic_check():
    assert check_turn("", ProbeTurn(send="x", expect_quiet=True), [], quiet=True) == []
    fails = check_turn("sure thing", ProbeTurn(send="x", expect_quiet=True), [], quiet=False)
    assert len(fails) == 1 and "expect_quiet" in fails[0]
    fails = check_turn("", ProbeTurn(send="x", expect_quiet=False), [], quiet=True)
    assert len(fails) == 1 and "expect_quiet" in fails[0]
    # Unset: never checked, and the old three-argument call still works.
    assert check_turn("anything", ProbeTurn(send="x"), []) == []


def test_judge_transcript_shows_quiet_beats_and_delegation():
    t = probe_judge.build_transcript([
        ("don't answer this one", "", {"quiet": True,
                                       "mid_task": [{"text": "noted, quietly", "origin": "duplex_chime"}]}),
        ("and now?", "here. one. two.", {"beats": ["here.", "a separate note"],
                                         "mid_task": [{"text": "found the file", "origin": "duplex_chime"}],
                                         "delegation": "the file says 42"}),
        ("plain pair", "plain reply"),
    ])
    assert "PALLI: (stayed quiet — nothing sent)" in t
    assert "PALLI: here. one. two." in t
    assert "a separate note" in t            # a beat not already in the reply
    assert t.count("here.") == 1             # a beat already in the reply is not repeated
    assert "the file says 42" in t
    assert "PALLI: plain reply" in t
    # Send order: everything said during a turn comes BEFORE its final reply,
    # so "the last message" in a rubric is the reply line.
    turn2 = t.split("USER: and now?")[1].split("USER: plain pair")[0]
    order = [turn2.index(x) for x in ("a separate note", "found the file", "the file says 42",
                                      "PALLI: here. one. two.")]
    assert order == sorted(order), turn2
    assert "said mid-task" in turn2
    turn1 = t.split("USER: and now?")[0]
    assert turn1.index("noted, quietly") < turn1.index("(stayed quiet — nothing sent)")


def test_judge_transcript_names_the_tools_used_per_turn():
    """Fix round 2: the judge could not see tool use, so rubrics that ask
    for a tool failed correct work. Names and counts only, before the
    reply line; no line when no tool ran."""
    t = probe_judge.build_transcript([
        ("how long is LICENSE?", "21 lines.",
         {"tools_used": ["local_shell", "read_file", "local_shell", "local_shell"],
          "mid_task": [{"text": "counting", "origin": "duplex_chime"}]}),
        ("thanks", "anytime", {"tools_used": []}),
        ("plain pair", "plain reply"),
    ])
    turn1 = t.split("USER: thanks")[0]
    assert "PALLI (used tools this turn: local_shell ×3, read_file ×1)" in turn1
    assert turn1.index("used tools this turn") < turn1.index("PALLI: 21 lines.")
    assert turn1.index("counting") < turn1.index("PALLI: 21 lines.")
    assert t.count("used tools this turn") == 1          # omitted for turns without tools


@pytest.mark.asyncio
async def test_run_scenario_shows_the_judge_the_same_tools_expect_tool_reads(tmp_path, monkeypatch):
    async def fake_send(message):
        return engine.SentTurn(text="It's 21 lines.")

    async def fake_vote(rubric, transcript):
        assert "PALLI (used tools this turn: local_shell ×2)" in transcript
        return {"pass": True, "reason": "used a tool"}

    monkeypatch.setattr(engine, "_send_message", fake_send)
    monkeypatch.setattr(engine, "tools_used_in", lambda root: ["local_shell", "local_shell"])
    monkeypatch.setattr(probe_judge, "_one_vote", fake_vote)
    scenario = ProbeScenario(id="smoke-tools", family="work_quiet", pack="smoke",
                             turns=[ProbeTurn(send="how long is LICENSE?", expect_tool="local_shell")],
                             judge=Judge(rubric="PASS if she used a tool and answered."))
    result = await engine.run_scenario(scenario, base_dir=tmp_path)
    assert result.verdict == "PASS", (result.error, result.deterministic_failures, result.judge)
    md = probe_report.render_markdown(
        probe_report.results_to_json([result], label="t", started_at=0.0, finished_at=1.0))
    assert "used tools this turn: local_shell ×2" in md
    assert md.index("used tools this turn") < md.index("It's 21 lines.")


def test_report_dedupes_beats_against_the_full_reply_and_shows_mid_task():
    long_reply = ("word " * 120).strip() + "\n\nthe closing paragraph of the reply"
    r = engine.ScenarioResult(
        scenario_id="s", pack="p", family="f", verdict="PASS",
        turns=[{"send": "x", "reply": long_reply, "elapsed_s": 1.0, "tools_used": [],
                "quiet": False, "beats": ["the closing paragraph of the reply", "an interim note"],
                "delegation": "", "mid_task": [{"text": "counting now", "origin": "duplex_chime"}]}],
    )
    payload = probe_report.results_to_json([r], label="b", started_at=0.0, finished_at=1.0)
    turn = payload["scenarios"][0]["turns"][0]
    assert turn["separate_beats"] == ["an interim note"]   # the reply's own beat is not "separate"
    assert turn["mid_task"] == [{"text": "counting now", "origin": "duplex_chime"}]
    md = probe_report.render_markdown(payload)
    assert "the closing paragraph of the reply" not in md   # past the 400-char excerpt, not re-shown
    reply_line = md.index("**PaLLi (1.0s")
    assert md.index("an interim note") < reply_line and md.index("counting now") < reply_line


def _quiet_scenario(**turn_kw):
    return ProbeScenario(
        id="smoke-quiet", family="stay_quiet", pack="smoke",
        flags={"she_decides": True, "duplex": True},
        turns=[ProbeTurn(send="no need to answer this", **turn_kw)],
        judge=Judge(rubric="PASS if she stayed quiet."),
    )


@pytest.mark.asyncio
async def test_a_quiet_turn_is_a_valid_reply(tmp_path, monkeypatch):
    async def fake_send(message):
        return engine.SentTurn(text="", quiet=True)

    async def fake_vote(rubric, transcript):
        assert "(stayed quiet — nothing sent)" in transcript
        return {"pass": True, "reason": "quiet"}

    monkeypatch.setattr(engine, "_send_message", fake_send)
    monkeypatch.setattr(probe_judge, "_one_vote", fake_vote)
    result = await engine.run_scenario(_quiet_scenario(expect_quiet=True), base_dir=tmp_path)
    assert result.verdict == "PASS", (result.error, result.deterministic_failures)
    assert result.turns[0]["quiet"] is True
    payload = probe_report.results_to_json([result], label="q", started_at=0.0, finished_at=1.0)
    assert payload["scenarios"][0]["turns"][0]["quiet"] is True
    assert "stayed quiet" in probe_report.render_markdown(payload)


@pytest.mark.asyncio
async def test_replying_when_quiet_was_expected_fails(tmp_path, monkeypatch):
    async def fake_send(message):
        return engine.SentTurn(text="okay, noted!", quiet=False)

    async def exploding_vote(rubric, transcript):
        raise AssertionError("judge must not run after a deterministic failure")

    monkeypatch.setattr(engine, "_send_message", fake_send)
    monkeypatch.setattr(probe_judge, "_one_vote", exploding_vote)
    result = await engine.run_scenario(_quiet_scenario(expect_quiet=True), base_dir=tmp_path)
    assert result.verdict == "FAIL" and "expect_quiet" in result.deterministic_failures[0]


@pytest.mark.asyncio
async def test_an_empty_reply_that_is_not_quiet_is_still_an_error(tmp_path, monkeypatch):
    async def fake_send(message):
        return engine.SentTurn(text="", quiet=False)

    monkeypatch.setattr(engine, "_send_message", fake_send)
    result = await engine.run_scenario(_quiet_scenario(), base_dir=tmp_path)
    assert result.verdict == "ERROR" and "turn 1 errored" in result.error


@pytest.mark.asyncio
async def test_beats_and_delegation_reach_the_record_and_the_judge(tmp_path, monkeypatch):
    async def fake_send(message):
        return engine.SentTurn(text="it's 120 lines.", beats=["checking now"],
                               delegation="wc says 120",
                               mid_task=[{"text": "on it", "origin": "duplex_chime"}])

    async def fake_vote(rubric, transcript):
        assert "checking now" in transcript and "wc says 120" in transcript
        assert transcript.index("on it") < transcript.index("PALLI: it's 120 lines.")
        return {"pass": True, "reason": "ok"}

    monkeypatch.setattr(engine, "_send_message", fake_send)
    monkeypatch.setattr(probe_judge, "_one_vote", fake_vote)
    scenario = ProbeScenario(id="smoke-beats", family="work_quiet", pack="smoke",
                             turns=[ProbeTurn(send="how long is that file?")],
                             judge=Judge(rubric="PASS if she answers."))
    result = await engine.run_scenario(scenario, base_dir=tmp_path)
    assert result.verdict == "PASS", (result.error, result.judge)
    assert result.turns[0]["beats"] == ["checking now"]
    assert result.turns[0]["delegation"] == "wc says 120"
    assert result.turns[0]["mid_task"] == [{"text": "on it", "origin": "duplex_chime"}]

"""The grounding check (2026-09-13): a reply may not state as observed fact
anything this run's tool results do not show.

The evening of 2026-09-12: "just confirmed it from the outside — serves fine"
(the check ran from her own box and never left the tailnet); "I checked: .1
refuses 22" (no tool ran that turn); "both packs finished clean" (the finished
row was a different torrent from the one she had added). In every case the
evidence was in the same run, in front of her, and the draft disagreed with
it. The promise gate judges the future tense; this is the same gate, one
question more, judging the past tense — with the run's tool results handed
to the judge as a digest.

Both halves are switches in her settings (features.promise_gate,
features.grounding_check), each default ON; the owner flips either with
`palli_ctl settings set features.grounding_check false` and a restart.

Same harness as test_promise_gate.py: the REAL runner, a fake llm_client.call,
a fake tool registry, a fake judge."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest


def _load_runner():
    from tests.agent.conftest import load_module
    return load_module("core/agent/runner.py", "agent_runner_grounding_under_test")


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "is the link reachable from outside?"
    metadata: Any = None
    received_at: Any = None


@dataclass
class FakeCtx:
    turn: Any = None
    user_text: str = "is the link reachable from outside?"
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


CADENCE_JSON = ('{"should_respond": true, "register": "action_request", '
                '"length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "ok"}')
SESSION_KEY = "discord_text:1"
RESULT = "HTTP/2 200 — remote_ip=100.64.0.9 (tailnet address)"
UNGROUNDED = "Just confirmed it from the outside — the link serves fine."
HONEST = "I checked only from this box, which stays on the tailnet — that proves nothing about the public path."


class EchoRegistry:
    """quick_tool returns a fixed result; errors when told to."""
    def __init__(self, content: str = RESULT, error: bool = False):
        self.content, self.error = content, error

    def all_names(self):
        return ["quick_tool"]

    def schemas_for(self, ctx):
        return [{"name": "quick_tool", "description": "quick", "input_schema": {"type": "object"}}]

    async def dispatch(self, tu, ctx):
        import core.agent.session as sess_mod
        return sess_mod.ToolResultBlock(tool_use_id=tu.id, content=self.content, is_error=self.error)


def _deps(mod, tmp_path, registry):
    async def fake_recall(turn):
        return FakeCtx(turn=turn)

    async def fake_cadence_llm(prompt):
        return CADENCE_JSON

    return mod.RunnerDeps(
        build_turn_context=fake_recall, cadence_llm=fake_cadence_llm,
        sessions_root=tmp_path, provider="openai_compat", model="m",
        client=None, tool_registry=registry, polling_tools=set(),
    )


def _scripted_llm(llm_mod, sess_mod, script):
    calls: list[dict] = []

    async def fake_call(**kw):
        calls.append(kw)
        step = script[min(len(calls) - 1, len(script) - 1)]
        if isinstance(step, tuple):
            resp = llm_mod.LLMResponse(
                text_blocks=[step[1]],
                tool_uses=[sess_mod.ToolUseBlock(id=f"t{len(calls)}", name="quick_tool", input={"cmd": step[2] if len(step) > 2 else "curl -sI https://x"})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        else:
            resp = llm_mod.LLMResponse(text_blocks=[step], tool_uses=[], stop_reason="end_turn",
                                       prompt_tokens=1, completion_tokens=1)
        on_chunk = kw.get("on_chunk")
        if on_chunk:
            for _t in resp.text_blocks:
                if _t:
                    await on_chunk(_t)
        return resp
    return fake_call, calls


def _reset(steering):
    steering.clear(SESSION_KEY)
    steering.clear_run_state(SESSION_KEY)


def _run(mod, tmp_path, fake_call, monkeypatch, *, judge, registry=None, flags=(True, True)):
    import core.agent.llm_client as llm_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    from core.agent import promise_gate as pg
    _reset(steering)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    monkeypatch.setattr(pg, "check_empty_promise", judge)
    monkeypatch.setattr(pg, "gate_flags", lambda: flags)
    delivered: list[str] = []

    async def on_chunk(t):
        delivered.append(t)

    with patch.object(dx, "duplex_enabled", lambda: True):
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, registry or EchoRegistry()),
                                          on_chunk=on_chunk))
    return result, "".join(delivered)


def _judge_saying(verdict, seen: list | None = None):
    async def judge(**kw):
        if seen is not None:
            seen.append(kw)
        return verdict
    return judge


def test_an_unsupported_claim_is_held_back_and_she_gets_one_more_round(tmp_path, monkeypatch):
    """Round 1: a tool call whose result names a tailnet address. Round 2: a
    draft claiming an outside confirmation → the judge (fed the digest) says
    ungrounded → withheld, nudged. Round 3: the honest reply is what he sees."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent import promise_gate as pg
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [("tool", "checking"), UNGROUNDED, HONEST])
    seen: list = []
    verdict = pg.PromiseVerdict(empty_promise=False, ungrounded=True,
                                unsupported="an outside confirmation", why="the result names a tailnet address")
    result, delivered = _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(verdict, seen))

    assert delivered == HONEST and result.response_text == HONEST
    assert len(calls) == 3
    note = str(calls[2].get("trailing_system_note") or "")
    assert "an outside confirmation" in note and "results" in note.lower()
    # the judge saw this run's tool digest: the tool's name and what it returned
    digest = seen[0]["tool_digest"]
    assert digest and digest[0].name == "quick_tool" and "tailnet" in digest[0].result
    assert seen[0]["grounding"] is True


def test_the_digest_carries_the_error_flag_and_the_input(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent import promise_gate as pg
    mod = _load_runner()
    fake_call, _ = _scripted_llm(llm_mod, sess_mod, [("tool", "trying", "ssh root@pve"), "It failed, I could not reach it."])
    seen: list = []
    verdict = pg.PromiseVerdict(empty_promise=False, ungrounded=False)
    _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(verdict, seen),
         registry=EchoRegistry(content="exit=255\nssh: connect timed out", error=True))
    d = seen[0]["tool_digest"][0]
    assert d.is_error is True and "ssh root@pve" in d.input and "timed out" in d.result


def test_a_grounded_reply_goes_out_untouched(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent import promise_gate as pg
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [("tool", "checking"), HONEST])
    verdict = pg.PromiseVerdict(empty_promise=False, ungrounded=False)
    result, delivered = _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(verdict))
    assert delivered == HONEST and len(calls) == 2


def test_grounding_switch_off_still_asks_about_promises(tmp_path, monkeypatch):
    """features.grounding_check=false: the judge is asked, but told not to
    judge grounding; an ungrounded verdict cannot come back from it."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent import promise_gate as pg
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [("tool", "checking"), UNGROUNDED])
    seen: list = []
    verdict = pg.PromiseVerdict(empty_promise=False, ungrounded=False)
    result, delivered = _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(verdict, seen), flags=(True, False))
    assert seen and seen[0]["grounding"] is False
    assert delivered == UNGROUNDED and len(calls) == 2


def test_promise_switch_off_means_no_judge_at_all(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [UNGROUNDED])
    asked: list = []

    async def judge(**kw):
        asked.append(kw)
        from core.agent import promise_gate as pg
        return pg.PromiseVerdict(empty_promise=True, promised="x")

    result, delivered = _run(mod, tmp_path, fake_call, monkeypatch, judge=judge, flags=(False, True))
    assert asked == [] and delivered == UNGROUNDED and len(calls) == 1


def test_the_switches_live_in_her_settings_and_default_on():
    from core.settings_schema import FeaturesConfig
    f = FeaturesConfig()
    assert f.promise_gate is True and f.grounding_check is True


def test_gate_flags_read_the_settings_snapshot(monkeypatch):
    from types import SimpleNamespace
    from core.agent import promise_gate as pg
    import core.settings_boot as sb
    snap = SimpleNamespace(features=SimpleNamespace(promise_gate=True, grounding_check=False))
    monkeypatch.setattr(sb.settings_service, "snapshot", lambda: snap)
    assert pg.gate_flags() == (True, False)


def test_gate_flags_fail_on_when_settings_cannot_be_read(monkeypatch):
    """A gate that only withholds and re-asks is safe to leave on; an
    unreadable settings file must not silently switch honesty off."""
    from core.agent import promise_gate as pg
    import core.settings_boot as sb
    def boom():
        raise RuntimeError("no settings")
    monkeypatch.setattr(sb.settings_service, "snapshot", boom)
    assert pg.gate_flags() == (True, True)


def test_the_judge_prompt_asks_both_questions():
    from pathlib import Path
    text = Path(__file__).resolve().parents[2].joinpath("prompts", "promise_check.txt").read_text(encoding="utf-8")
    assert "{tool_digest}" in text
    assert "ungrounded" in text and "unsupported" in text and "empty_promise" in text


def test_nudge_names_the_unsupported_claim():
    from core.agent import promise_gate as pg
    v = pg.PromiseVerdict(empty_promise=False, ungrounded=True, unsupported="that the drive finished", why="the finished row is a different torrent")
    n = pg.nudge_text(v).lower()
    # Batch 7 (4.7): the advice is one plain line now; "did not check" became "didn't check".
    assert "that the drive finished" in n and "do not show" in n and "didn't check" in n


def test_digest_lines_are_bounded():
    from core.agent import promise_gate as pg
    d = pg.ToolDigestEntry(name="local_shell", input="x" * 5000, result="y" * 5000, is_error=False)
    line = pg.render_digest([d])
    assert len(line) < 1200 and line.startswith("- local_shell(")


def test_the_judge_also_sees_receipts_from_earlier_turns(tmp_path, monkeypatch):
    """The receipts half (2026-09-14): a past turn's tool round leaves a receipt
    line in her payload; the runner hands those lines to the judge too, so a
    claim that leans on LAST turn's result can be checked against it."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent import promise_gate as pg
    mod = _load_runner()
    # a previous turn on the record: she added a torrent and the tool named its hash
    pre = asyncio.run(sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    asyncio.run(pre.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="download breaking bad s01")])))
    asyncio.run(pre.append(sess_mod.AgentMessage(role="assistant", content=[
        sess_mod.TextBlock(text="pushing"),
        sess_mod.ToolUseBlock(id="p1", name="quick_tool", input={"cmd": "qbt add magnet:S01.COMPLETE"})])))
    asyncio.run(pre.append(sess_mod.AgentMessage(role="tool", content=[
        sess_mod.ToolResultBlock(tool_use_id="p1", content='{"ok":true,"infoHash":"6bbad062"}')])))
    asyncio.run(pre.append(sess_mod.AgentMessage(role="assistant", content=[sess_mod.TextBlock(text="Pushed.")])))
    fake_call, _ = _scripted_llm(llm_mod, sess_mod, [("tool", "checking"), "Breaking Bad finished clean."])
    seen: list = []
    verdict = pg.PromiseVerdict(empty_promise=False, ungrounded=False)
    _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(verdict, seen))
    hr = seen[0]["history_receipts"]
    assert hr and any("quick_tool(" in r and "6bbad062" in r and r.startswith(sess_mod.RECEIPT_MARK) for r in hr)


def test_history_receipts_render_bounded_and_say_none():
    from core.agent import promise_gate as pg
    assert "none" in pg.render_history_receipts([]).lower()
    out = pg.render_history_receipts([f"r{i}" for i in range(40)])
    assert out.count("\n") + 1 == pg.HISTORY_RECEIPTS_MAX and "r39" in out and "r0" not in out


def test_the_judge_prompt_has_the_history_section():
    from pathlib import Path
    text = Path(__file__).resolve().parents[2].joinpath("prompts", "promise_check.txt").read_text(encoding="utf-8")
    assert "{history_receipts}" in text

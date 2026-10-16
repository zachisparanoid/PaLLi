"""The single judged LLM call for mid-task messages (spec §3, §8)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _no_voice_page(monkeypatch):
    """Batch 7's final review (M6): this file never reads the identity tree.
    On the owner's checkout the conftest's identity copy holds her real voice
    page, which the judges' reader would otherwise feed into the fake model's
    prompt; the reader is stubbed to "" so every judge here opens on the
    neutral line. The reader itself is tested in test_her_voice_judges.py."""
    from core.agent import steering_reply

    monkeypatch.setattr(steering_reply, "voice_identity_text", lambda: "")


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "did you see the game last night"
    metadata: Any = None


def _patch_llm(monkeypatch, response):
    import ai.llm_client as llm_mod

    async def fake_chat_completion(messages, **kw):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(llm_mod.llm, "chat_completion", fake_chat_completion)


def test_judgment_parses_reply_and_stop_verdict(monkeypatch):
    from core.agent import steering_reply

    _patch_llm(monkeypatch, '{"stop_run": false, "reply": "ha, caught the last quarter"}')
    out = asyncio.run(steering_reply.judge_mid_task(
        FakeTurn(), session_key="discord_text:1"))
    assert out.stop_run is False
    assert out.reply == "ha, caught the last quarter"


def test_judgment_can_choose_to_hold(monkeypatch):
    from core.agent import steering_reply

    _patch_llm(monkeypatch, '{"stop_run": false, "reply": null}')
    out = asyncio.run(steering_reply.judge_mid_task(
        FakeTurn(), session_key="discord_text:1"))
    assert out.stop_run is False
    assert out.reply is None


def test_judgment_reports_a_natural_language_stop(monkeypatch):
    from core.agent import steering_reply

    _patch_llm(monkeypatch, '{"stop_run": true, "reply": "yep, dropping it"}')
    out = asyncio.run(steering_reply.judge_mid_task(
        FakeTurn(content="no wait, wrong one"), session_key="discord_text:1"))
    assert out.stop_run is True


def test_judgment_fails_open_to_steer_on_llm_error(monkeypatch):
    """Spec §8: a failed judgment must never stop the run or lose the message —
    no reply is sent, the caller still enqueues."""
    from core.agent import steering_reply

    _patch_llm(monkeypatch, RuntimeError("provider down"))
    out = asyncio.run(steering_reply.judge_mid_task(
        FakeTurn(), session_key="discord_text:1"))
    assert out.stop_run is False
    assert out.reply is None


def test_judgment_fails_open_on_unparseable_output(monkeypatch):
    from core.agent import steering_reply

    _patch_llm(monkeypatch, "not json at all")
    out = asyncio.run(steering_reply.judge_mid_task(
        FakeTurn(), session_key="discord_text:1"))
    assert out.stop_run is False
    assert out.reply is None


# ── supersede judge (phase-owned routing, 2026-08-04) ───────────────────────
# One binary for a mid-GENERATION arrival: does it change what she is saying
# right now? Every failure path is False — fail-open to finish-then-follow-up;
# a judgment failure must never cost a completion or strand a message.


def test_judge_supersede_true_on_supersede_json(monkeypatch):
    from core.agent import steering_reply

    _patch_llm(
        monkeypatch,
        '{"supersede": true, "reason": "changes what she should say"}')
    turn = FakeTurn(content="wait, actually make it about the gist lane instead")
    assert asyncio.run(steering_reply.judge_supersede(
        turn, session_key="discord:9")) is True


def test_judge_supersede_false_on_finish_json(monkeypatch):
    from core.agent import steering_reply

    _patch_llm(monkeypatch, '{"supersede": false, "reason": "a follow-up"}')
    turn = FakeTurn(content="also grab milk")
    assert asyncio.run(steering_reply.judge_supersede(
        turn, session_key="discord:9")) is False


def test_judge_supersede_fails_open_false(monkeypatch):
    from core.agent import steering_reply

    _patch_llm(monkeypatch, RuntimeError("provider down"))
    turn = FakeTurn(content="also grab milk")
    assert asyncio.run(steering_reply.judge_supersede(
        turn, session_key="discord:9")) is False


def test_judge_supersede_empty_content_fails_open_false(monkeypatch):
    from core.agent import steering_reply

    _patch_llm(monkeypatch, "")
    turn = FakeTurn(content="hm")
    assert asyncio.run(steering_reply.judge_supersede(
        turn, session_key="discord:9")) is False


# ── PaLLi's own words (the owner, 2026-10-12) ─────────────────────────────
# The 08-18 busy-ack ban and its denylist came from replies the owner read as
# filler. On 10-12 the owner gave the choice back: "palli is its own entity
# and should be able to converse and acknowledge with whatever language it
# sees best fit." Whatever PaLLi writes goes out as written.

def test_a_short_acknowledgement_goes_out_as_written(monkeypatch):
    import json
    from core.agent import steering_reply

    for words in ("On it.", "give me a sec", "Working on it — give me a sec."):
        _patch_llm(monkeypatch, '{"action": "fold_in", "reply": %s}' % json.dumps(words))
        out = asyncio.run(steering_reply.judge_mid_task(
            FakeTurn(), session_key="discord_text:1"))
        assert out.reply == words
    assert not hasattr(steering_reply, "_is_pure_busy_ack")


def test_judgment_prompt_leaves_the_words_and_the_choice_to_her():
    from pathlib import Path
    from scripts.prompt_size_report import find_scripted_quotes

    text = Path("prompts/steering_judgment.txt").read_text(encoding="utf-8")
    for gone in ("busy-ack", "give me a sec", "Saying nothing is a real choice",
                 "elbow-deep", "the way a person would", "the current task"):
        assert gone not in text, gone
    say = text.split("2. What do you say right now", 1)[1].split("Reply with JSON only:", 1)[0]
    assert find_scripted_quotes(say) == []          # no lines to copy
    for slot in ("{identity}", "{now_doing}", "{recent}", "{message}"):
        assert slot in text
    assert '"full_turn": true' in text


def test_the_judgment_prompt_names_the_work_and_forbids_the_generic_task():
    """02:49 08-23: with no real work under way the judge was told she was
    "in the middle of doing something" and she invented "the current task".
    The prompt names what she is doing ({now_doing}) and forbids the generic
    phrase; the routing (client) keeps her out of this lane when there is no
    work at all. The owner, 10-12: the phrase ban went with the other bans;
    she now reads the work as it stands (core/agent/work_progress), so what
    she names is the real thing."""
    from ai.prompt_loader import prompt_loader
    text = prompt_loader.load_prompt("steering_judgment") or ""
    assert "Where it stands:\n{now_doing}" in text


class TestPhaseBActions:
    """Step 27 Phase B (spec §3): the judge names what his words mean for the work."""

    def _judge_with(self, monkeypatch, raw_json, **kw):
        import asyncio
        import core.agent.steering_reply as sr
        from ai.llm_client import llm

        async def fake_chat(*a, **k):
            return raw_json
        monkeypatch.setattr(llm, "chat_completion", fake_chat)
        turn = type("T", (), {"content": "hold on a sec, I need to think"})()
        return asyncio.run(sr.judge_mid_task(turn, session_key="discord:1", **kw))

    def test_default_action_is_fold_in(self, monkeypatch):
        j = self._judge_with(monkeypatch, '{"stop_run": false, "reply": null, "full_turn": false}')
        assert j.action == "fold_in" and j.stop_run is False

    def test_pause_and_new_task_parse(self, monkeypatch):
        assert self._judge_with(monkeypatch, '{"action": "pause", "reply": null}').action == "pause"
        assert self._judge_with(monkeypatch, '{"action": "new_task", "reply": null}').action == "new_task"

    def test_stop_action_sets_stop_run_and_legacy_stop_run_maps_to_stop(self, monkeypatch):
        j = self._judge_with(monkeypatch, '{"action": "stop", "reply": null}')
        assert j.stop_run is True and j.action == "stop"
        j2 = self._judge_with(monkeypatch, '{"stop_run": true, "reply": null}')
        assert j2.action == "stop"

    def test_unknown_action_falls_back_to_fold_in(self, monkeypatch):
        assert self._judge_with(monkeypatch, '{"action": "dance", "reply": null}').action == "fold_in"

    def test_resume_only_legal_when_paused(self, monkeypatch):
        assert self._judge_with(monkeypatch, '{"action": "resume", "reply": null}').action == "fold_in"
        j = self._judge_with(monkeypatch, '{"action": "resume", "reply": null}', paused_doing="the adapter")
        assert j.action == "resume"

    def test_prompt_carries_the_five_actions_the_paused_marker_and_the_sidecar_rule(self, monkeypatch):
        import asyncio
        import core.agent.steering_reply as sr
        from ai.llm_client import llm
        seen = {}

        async def fake_chat(messages, **k):
            seen["prompt"] = messages[0]["content"]
            return '{"action": "resume", "reply": null}'
        monkeypatch.setattr(llm, "chat_completion", fake_chat)
        turn = type("T", (), {"content": "go on"})()
        asyncio.run(sr.judge_mid_task(turn, session_key="discord:1", paused_doing="the adapter"))
        # The owner, 10-12: the sidecar rule is "reply" null (the code sends no quick reply
        # beside a full turn); the "no holding line" wording went with the phrase bans.
        for word in ("fold_in", "pause", "stop", "new_task", "resume", "PAUSED", 'set "reply" to null'):
            assert word in seen["prompt"], word


def test_judgment_sees_the_work_and_the_recent_conversation(monkeypatch):
    """The owner, 10-12: PaLLi's mid-task words rest on the work as it stands
    and on what was just said (the {recent} slot used to be always empty)."""
    import ai.llm_client as llm_mod
    import core.agent.steering as steering
    from core.agent import steering_reply
    from core.agent.work_progress import WorkProgress
    seen = {}

    async def fake_chat_completion(messages, **kw):
        seen["prompt"] = messages[0]["content"]
        return '{"action": "fold_in", "reply": null}'

    monkeypatch.setattr(llm_mod.llm, "chat_completion", fake_chat_completion)
    steering.set_work_progress("discord_text:1", WorkProgress(
        asked="clear the old backups", started_at=0.0, step=2,
        done="- local_shell(ls /backups) → four folders",
        recent="them: can you clear the old backups?"))
    try:
        asyncio.run(steering_reply.judge_mid_task(FakeTurn(), session_key="discord_text:1"))
    finally:
        steering.clear_now_doing("discord_text:1")
    assert "What they asked: clear the old backups" in seen["prompt"]
    assert "- local_shell(ls /backups) → four folders" in seen["prompt"]
    assert "them: can you clear the old backups?" in seen["prompt"]

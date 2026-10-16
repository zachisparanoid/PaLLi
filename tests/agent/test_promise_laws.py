"""The laws behind the promise gate (2026-09-11), pinned as text: the duplex
law forbids an empty promise; the presence tool is signage for running work
only; the repair block corrects on contradiction, not on challenge; the
reflect lane files a promise she made as a thread she owes; the judge lane is
registered as a prompt, as a no-think lane, and as a SNAPPY lane."""
from __future__ import annotations


def test_the_duplex_law_forbids_an_empty_promise():
    from unittest.mock import patch
    from core.agent import duplex as dx
    with patch.object(dx, "duplex_enabled", lambda: True):
        low = dx.render_duplex_block().lower()
    # Batch 7 (4.3): said as her, without the ban; the promise gate is the
    # code that holds an empty promise back (same flag as this block).
    assert "i say something is happening only if i started it this turn" in low
    assert "i do it now" in low
    assert "say what i need, or say i can't" in low


def test_set_presence_is_signage_for_running_work_only():
    from core.agent.builtin_tools.her_hands import SetPresenceTool
    desc = SetPresenceTool.description.lower()
    assert "never for waiting on them" in desc
    assert "stand-in for doing the thing" in desc


def test_the_repair_block_corrects_on_contradiction_not_on_challenge(monkeypatch):
    from core.agent import system_prompt as sp
    monkeypatch.setattr("core.turn.companion_flags.faculty_enabled", lambda name: True, raising=False)
    text = sp._repair_block().lower()
    assert "not because i was questioned" in text
    assert "is not a contradiction" in text
    assert "the mistakes i own are the ones i actually made" in text


def test_reflect_files_her_own_promise_as_a_thread_she_owes():
    from pathlib import Path
    text = Path(__file__).resolve().parents[2].joinpath("prompts", "turn_reflect.txt").read_text(encoding="utf-8")
    assert "PaLLi owes:" in text


def test_the_judge_prompt_is_registered_and_present():
    from pathlib import Path
    from ai.prompt_loader import EXPECTED_PROMPTS
    assert "promise_check" in EXPECTED_PROMPTS
    p = Path(__file__).resolve().parents[2].joinpath("prompts", "promise_check.txt")
    assert p.is_file() and "empty_promise" in p.read_text(encoding="utf-8")


def test_the_judge_lane_never_thinks_and_stays_snappy():
    from ai.llm_openai_compatible import _NO_THINK_LANE_PREFIXES
    assert "promise_check" in _NO_THINK_LANE_PREFIXES
    from ai.llm_openai_compatible import OpenAICompatibleProvider as P
    assert P.request_timeout_for_context("promise_check") == float(P.REQUEST_TIMEOUT_SECONDS)

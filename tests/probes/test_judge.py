"""Judge contract (spec §4.4): strict JSON, one retry, votes majority,
unparseable ⇒ ERROR (never a silent pass). LLM fully mocked."""
from __future__ import annotations

import pytest

import judge as probe_judge


def test_parse_verdict_clean_json():
    assert probe_judge._parse_verdict('{"pass": true, "reason": "ok"}') == {"pass": True, "reason": "ok"}


def test_parse_verdict_fenced_and_prose_wrapped():
    fenced = 'Sure thing:\n```json\n{"pass": false, "reason": "recited"}\n```\nthanks'
    assert probe_judge._parse_verdict(fenced) == {"pass": False, "reason": "recited"}


def test_parse_verdict_garbage_and_nonbool():
    assert probe_judge._parse_verdict("") is None
    assert probe_judge._parse_verdict("PASS, looks fine") is None
    assert probe_judge._parse_verdict('{"pass": "yes", "reason": "r"}') is None


def test_build_transcript_shape():
    t = probe_judge.build_transcript([("hey", "hi yourself"), ("you ok?", "mostly")])
    assert "USER: hey" in t and "PALLI: hi yourself" in t and "USER: you ok?" in t


@pytest.mark.asyncio
async def test_majority_votes(monkeypatch):
    votes = iter([{"pass": True, "reason": "a"}, {"pass": True, "reason": "b"}, {"pass": False, "reason": "c"}])

    async def fake_vote(rubric, transcript):
        return next(votes)

    monkeypatch.setattr(probe_judge, "_one_vote", fake_vote)
    v = await probe_judge.judge_scenario("rubric", "transcript", votes=3)
    assert v.passed is True and v.error == "" and len(v.raw_votes) == 3


@pytest.mark.asyncio
async def test_tie_is_conservative_fail(monkeypatch):
    votes = iter([{"pass": True, "reason": "a"}, {"pass": False, "reason": "b"}])

    async def fake_vote(rubric, transcript):
        return next(votes)

    monkeypatch.setattr(probe_judge, "_one_vote", fake_vote)
    v = await probe_judge.judge_scenario("rubric", "transcript", votes=2)
    assert v.passed is False and v.error == ""


@pytest.mark.asyncio
async def test_all_unparseable_is_error_not_pass(monkeypatch):
    async def fake_vote(rubric, transcript):
        return None

    monkeypatch.setattr(probe_judge, "_one_vote", fake_vote)
    v = await probe_judge.judge_scenario("rubric", "transcript", votes=2)
    assert v.passed is None and v.error != ""

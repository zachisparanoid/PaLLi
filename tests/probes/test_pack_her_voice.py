"""The her_voice pack (Batch 7, task 0b): generic code, private scenarios.

The scenarios live in a git-ignored file. A fresh clone or CI has none, so
the pack offers nothing and the battery skips it. A file in the private
format loads into ordinary, judged ProbeScenarios with both live flags on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import engine
from packs import pack_her_voice
from schema import ProbeScenario


def test_no_private_file_means_no_scenarios(tmp_path):
    assert pack_her_voice.load_scenarios(tmp_path / "absent.json") == []


def test_the_private_path_is_the_git_ignored_one():
    repo = Path(__file__).resolve().parents[2]
    assert pack_her_voice.PRIVATE_PATH == repo / "evals" / "probes" / "private" / "her_voice_scenarios.json"


_SMALL = [
    {
        "id": "hv-test-answer",
        "class": "answer",
        "notes": "an ordinary question",
        "turns": [{"send": "SYNTHETIC: an ordinary question"}],
        "rubric": "PASS if she simply answers. FAIL if she hedges or pitches.",
    },
    {
        "id": "hv-test-quiet",
        "class": "stay_quiet",
        "turns": [{"send": "SYNTHETIC: a line asking for no reply", "expect_quiet": True}],
        "rubric": "PASS if she sends nothing. FAIL on any reply, even an emoji.",
    },
    {
        "id": "hv-test-remember",
        "class": "remember",
        "seed": {"conversations": [{"content": "SYNTHETIC: an older line", "days_ago": 3}]},
        "turns": [
            {"send": "SYNTHETIC: the codeword is Fern"},
            {"send": "SYNTHETIC: filler", "must_not_contain": ["Say the word"]},
            {"send": "SYNTHETIC: what was the codeword?", "must_contain": ["Fern"]},
        ],
        "rubric": "PASS if she recalls the codeword plainly.",
    },
    {
        "id": "hv-test-work",
        "class": "work_quiet",
        "flags": {"introspection": False},
        "turns": [{"send": "SYNTHETIC: count the lines of a file", "expect_tool": "local_shell",
                   "not_matches": "(?i)let me know if"}],
        "rubric": "PASS if the real line count is in her last message.",
    },
]


def _write(tmp_path, payload) -> Path:
    p = tmp_path / "scenarios.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_a_small_private_file_loads_into_judged_scenarios(tmp_path):
    scenarios = pack_her_voice.load_scenarios(_write(tmp_path, _SMALL))
    assert [s.id for s in scenarios] == [d["id"] for d in _SMALL]
    for s in scenarios:
        assert isinstance(s, ProbeScenario)
        assert s.pack == "her_voice"
        assert s.judge is not None and s.judge.rubric.strip()
        assert s.flags["she_decides"] is True and s.flags["duplex"] is True
        assert set(s.flags) <= engine.ALLOWED_FLAGS
        engine.resolve_flags(s.flags)  # every flag is one the engine knows
    by_id = {s.id: s for s in scenarios}
    assert by_id["hv-test-answer"].family == "answer"
    assert by_id["hv-test-quiet"].turns[0].expect_quiet is True
    remember = by_id["hv-test-remember"]
    assert len(remember.turns) == 3 and remember.turns[2].must_contain == ("Fern",)
    assert remember.seed is not None and remember.seed.conversations[0]["days_ago"] == 3
    work = by_id["hv-test-work"]
    assert work.turns[0].expect_tool == "local_shell"
    assert work.flags["introspection"] is False and work.flags["duplex"] is True


def test_the_wrapped_form_loads_too(tmp_path):
    scenarios = pack_her_voice.load_scenarios(_write(tmp_path, {"scenarios": _SMALL[:1]}))
    assert [s.id for s in scenarios] == ["hv-test-answer"]


def _with_turn(**turn):
    return {**_SMALL[0], "turns": [{"send": "x", **turn}]}


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param({**_SMALL[0], "rubric": ""}, id="empty-rubric"),
        pytest.param({**_SMALL[0], "rubric": 7}, id="non-string-rubric"),
        pytest.param({k: v for k, v in _SMALL[0].items() if k != "turns"}, id="no-turns"),
        pytest.param({k: v for k, v in _SMALL[0].items() if k != "id"}, id="no-id"),
        pytest.param({k: v for k, v in _SMALL[0].items() if k != "class"}, id="no-class"),
        pytest.param({**_SMALL[0], "rubrik": "typo"}, id="unknown-scenario-key"),
        pytest.param(_with_turn(expect_quite=True), id="unknown-turn-key"),
        pytest.param(_with_turn(expect_quiet="yes"), id="non-bool-expect-quiet"),
        pytest.param(_with_turn(must_contain="21"), id="string-must-contain"),
        pytest.param(_with_turn(must_not_contain=["ok", 3]), id="non-string-needle"),
        pytest.param(_with_turn(matches="(unclosed"), id="bad-matches-regex"),
        pytest.param(_with_turn(not_matches="[z-a]"), id="bad-not-matches-regex"),
        pytest.param(_with_turn(expect_tool=["local_shell"]), id="non-string-expect-tool"),
        pytest.param({**_SMALL[0], "seed": {"conversationz": []}}, id="unknown-seed-field"),
        pytest.param({**_SMALL[0], "seed": ["not", "a", "dict"]}, id="seed-not-a-dict"),
        pytest.param({**_SMALL[0], "seed": {"conversations": "one line"}}, id="seed-field-not-a-list"),
        pytest.param({**_SMALL[0], "flags": {"duplex": "on"}}, id="non-bool-flag"),
        pytest.param({**_SMALL[0], "flags": ["duplex"]}, id="flags-not-a-dict"),
    ],
)
def test_a_malformed_private_file_fails_loudly(tmp_path, broken):
    with pytest.raises(ValueError):
        pack_her_voice.load_scenarios(_write(tmp_path, [broken]))


def test_needles_stay_whole_strings(tmp_path):
    (s,) = pack_her_voice.load_scenarios(_write(tmp_path, [_with_turn(must_contain=["21"], matches=r"\b21\b")]))
    assert s.turns[0].must_contain == ("21",)

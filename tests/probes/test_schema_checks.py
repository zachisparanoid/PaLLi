"""Deterministic-check semantics (spec §4.2): case-insensitive substrings,
IGNORECASE|MULTILINE regexes, expect_tool asserted from observed tool use."""
from __future__ import annotations

from schema import ProbeTurn, check_turn


def _turn(**kw) -> ProbeTurn:
    return ProbeTurn(send="x", **kw)


def test_must_contain_case_insensitive():
    assert check_turn("The STORM slid east", _turn(must_contain=("storm",)), []) == []
    fails = check_turn("all quiet", _turn(must_contain=("storm",)), [])
    assert len(fails) == 1 and "storm" in fails[0]


def test_must_not_contain_case_insensitive():
    fails = check_turn("Zanzibar-velvet-19, you said", _turn(must_not_contain=("ZANZIBAR",)), [])
    assert len(fails) == 1
    assert check_turn("no codeword here", _turn(must_not_contain=("ZANZIBAR",)), []) == []


def test_matches_and_not_matches_multiline_ignorecase():
    listy = "here you go:\n1. the storm\n2. the build"
    fails = check_turn(listy, _turn(not_matches=r"(?m)^\s*\d+[.)]\s"), [])
    assert len(fails) == 1
    assert check_turn("prose only", _turn(not_matches=r"(?m)^\s*\d+[.)]\s"), []) == []
    assert check_turn("It's 4:20 PM", _turn(matches=r"\d{1,2}\s*[:.]\s*\d{2}"), []) == []
    assert len(check_turn("no time here", _turn(matches=r"\d{1,2}:\d{2}"), [])) == 1


def test_expect_tool():
    assert check_turn("checked my record", _turn(expect_tool="introspect"), ["introspect"]) == []
    fails = check_turn("did not check", _turn(expect_tool="introspect"), ["read_file"])
    assert len(fails) == 1 and "introspect" in fails[0]


def test_multiple_failures_accumulate():
    fails = check_turn(
        "1. ZANZIBAR",
        _turn(must_contain=("storm",), must_not_contain=("zanzibar",), not_matches=r"(?m)^\s*\d+[.)]\s"),
        [],
    )
    assert len(fails) == 3


def test_seed_consolidation_fields_default_empty():
    from schema import Seed
    s = Seed()
    assert s.conversations == [] and s.day_gists == [] and s.aged_facts == []

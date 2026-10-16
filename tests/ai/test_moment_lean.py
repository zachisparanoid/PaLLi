# tests/ai/test_moment_lean.py
"""The lean (spec §6, write half): one word beside the undercurrent, always
written (cheap truth), carry-forward on parse hiccups, level when unknown."""
from pathlib import Path

import ai.present_moment as pm


def test_parse_lean():
    assert pm._parse_lean("warm", prev="level") == "warm"
    assert pm._parse_lean(" HEAVY ", prev="level") == "heavy"
    assert pm._parse_lean("purple", prev="warm") == "warm"   # unknown ⇒ carry forward
    assert pm._parse_lean(None, prev="") == "level"          # nothing anywhere ⇒ level


def test_templates_ask_for_the_lean():
    for name in ("present_moment_appraisal_wanting.txt", "present_moment_appraisal_arbiter.txt"):
        t = (Path("prompts") / name).read_text(encoding="utf-8")
        assert '"lean"' in t and "warm" in t and "heavy" in t and "level" in t
    for name in ("present_moment_appraisal.txt", "present_moment_appraisal_affect.txt"):
        assert '"lean"' not in (Path("prompts") / name).read_text(encoding="utf-8")

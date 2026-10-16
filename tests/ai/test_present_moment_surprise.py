# tests/ai/test_present_moment_surprise.py
"""Surprise in the appraisal (spec §3): parse fail-safe (absent/garbled ⇒
none; nothing else affected), meta carries it only when real, a REAL one
opens the salience window, the templates carry the slot with zero-byte
collapse, and sensitivity "surprise" survives the impulse parse."""
from pathlib import Path

import ai.present_moment as pm


def test_parse_surprise_shapes():
    assert pm._parse_surprise(None) is None
    assert pm._parse_surprise("nah") is None
    assert pm._parse_surprise({"about": "them", "line": "x", "weight": "real"}) is None  # bad about
    assert pm._parse_surprise({"about": "him", "line": "", "weight": "real"}) is None    # empty line
    out = pm._parse_surprise({"about": "him", "line": "  up at nine,  never up at nine ", "weight": "real"})
    assert out == {"about": "him", "line": "up at nine, never up at nine", "weight": "real"}
    assert pm._parse_surprise({"about": "me", "line": "x", "weight": "odd"})["weight"] == "slight"  # unknown ⇒ slight


def test_impulse_parse_keeps_surprise_sensitivity():
    imp = pm._parse_impulse({"kind": "say", "why": "w", "framing": "you're up at nine",
                             "sensitivity": "surprise"})
    assert imp["sensitivity"] == "surprise"


def test_arbiter_sensitivities_tuple():
    import ai.arbiter as arb
    assert arb._SENSITIVITIES == ("routine", "care", "surprise")


def test_templates_carry_the_slot_with_zero_byte_collapse():
    for name in ("present_moment_appraisal_wanting.txt", "present_moment_appraisal_arbiter.txt"):
        t = Path("prompts") / name
        text = t.read_text(encoding="utf-8")
        assert "{expectations_block}" in text, name
        # zero-byte collapse: the slot is immediately followed by existing
        # text on the same line (no orphan blank line when it renders "")
        idx = text.index("{expectations_block}")
        assert text[idx + len("{expectations_block}")] not in ("\n",), name
    for name in ("present_moment_appraisal.txt", "present_moment_appraisal_affect.txt"):
        assert "{expectations_block}" not in (Path("prompts") / name).read_text(encoding="utf-8"), name

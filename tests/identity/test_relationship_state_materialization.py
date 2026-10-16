from __future__ import annotations

from pathlib import Path


def test_relationship_state_template_has_calibration_sections() -> None:
    text = Path("identity/templates/RELATIONSHIP_STATE.md").read_text(encoding="utf-8")

    assert "Familiarity Level" in text
    # 8ed4de2 unified identity voice to first/second person:
    # "User Tone Preferences" became "Their Tone Preferences".
    assert "Their Tone Preferences" in text
    assert "Known Boundaries" in text
    assert "Successful Collaboration Patterns" in text


def test_personality_profile_template_has_taste_and_restraint_section() -> None:
    text = Path("identity/templates/PERSONALITY_PROFILE.md").read_text(encoding="utf-8")

    assert "Taste and Restraint" in text

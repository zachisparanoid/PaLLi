"""The Great Excision's seven inert feature flags must stay deleted.

The Great Excision (2026-07-09) deleted the features these flags gated —
the bounded_reflection engine and the second-brain faculty layer — but
left the flags themselves in place. They survived because
``FeaturesConfig`` is ``extra="forbid"`` and the owner's data/palli.yaml
still carried the keys, so dropping a field alone would have failed
settings validation at boot. Removing the schema field and the YAML key
together (2026-07-16) closes that out.

This file pins two things that pull in opposite directions:

  1. The seven inert flags are gone from BOTH ``FeaturesConfig`` and the
     ``features:`` block of data/palli.yaml. Re-introducing one revives
     DB-HIGH-1 — the bug where core/config.py, the Pydantic defaults, and
     the YAML each claimed a different value for the same flag.

  2. ``relationship_calibration`` existed TWICE. Only the inert
     ``features:`` twin was deleted. ``CompanionConfig.relationship_calibration``
     is LIVE and must survive: two readers depend on it —
     ``core/turn/reflect.py`` (momentum update, via ``faculty_enabled``)
     and ``core/turn/recall.py`` (posture render, via direct attribute
     access). See the fail-open note on the ``faculty_enabled`` test below
     for why this needs a real guard rather than a smoke assertion.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core.settings_schema import CompanionConfig, FeaturesConfig
from core.turn.companion_flags import faculty_enabled


_REPO_YAML = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"

# The INERT FLAGS block deleted from core/settings_schema.py (was ~170-181).
_DELETED_INERT_FLAGS = (
    "bounded_reflection",
    "interaction_mode_router",
    "pragmatic_perception",
    "emotional_state",
    "topic_boundaries",
    "relationship_calibration",
    "delivery_planner",
)


def _yaml_block(name: str) -> dict:
    return yaml.safe_load(_REPO_YAML.read_text(encoding="utf-8")).get(name) or {}


@pytest.mark.parametrize("flag", _DELETED_INERT_FLAGS)
def test_inert_flag_removed_from_features_config(flag: str) -> None:
    """The Pydantic field is gone — nothing reads it, so nothing declares it."""
    assert flag not in FeaturesConfig.model_fields, (
        f"features.{flag} was deleted in the 2026-07-16 inert-flag cut; "
        "re-declaring it revives the three-way disagreement of DB-HIGH-1"
    )


@pytest.mark.parametrize("flag", _DELETED_INERT_FLAGS)
def test_inert_flag_removed_from_palli_yaml_features(flag: str) -> None:
    """The YAML key is gone too.

    This is the half that bites: ``FeaturesConfig`` is ``extra="forbid"``,
    so a YAML key with no matching field is a ValidationError at boot —
    not a warning, not a default. Field and key move together or not at all.
    """
    assert flag not in _yaml_block("features"), (
        f"data/palli.yaml features.{flag} must be deleted alongside the "
        "schema field; FeaturesConfig is extra=forbid, so an orphaned key "
        "fails settings validation at startup"
    )


def test_relationship_calibration_survives_on_companion_config() -> None:
    """The LIVE twin stays. Only the inert features: twin was cut."""
    assert "relationship_calibration" in CompanionConfig.model_fields
    assert CompanionConfig().relationship_calibration is True


def test_palli_yaml_companion_keeps_relationship_calibration() -> None:
    """The owner's YAML keeps the companion key that drives the live flag."""
    assert _yaml_block("companion").get("relationship_calibration") is True


def test_faculty_enabled_relationship_calibration_reads_companion_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``faculty_enabled`` resolves CompanionConfig — and RESPECTS a False.

    The fail-open guard. ``companion_flags.faculty_enabled`` ends in
    ``bool(getattr(companion, name, True))``. If the field were ever
    deleted from ``CompanionConfig``, that getattr would silently fall
    back to its ``True`` default and every read would look healthy — so
    an ``assert faculty_enabled(...) is True`` smoke test would pass over
    the bug without a murmur.

    Driving the flag to False is what makes this test discriminating: a
    False can only survive the read if the field is genuinely declared on
    CompanionConfig and genuinely consulted. Deleting the field breaks
    this test two ways over — ``CompanionConfig(relationship_calibration=False)``
    raises ValidationError under ``extra="forbid"``, and were that ever
    loosened, the fail-open True would fail the assertion instead.
    """
    from core import settings_boot

    companion = CompanionConfig(relationship_calibration=False)
    monkeypatch.setattr(
        settings_boot.settings_service,
        "snapshot",
        lambda: SimpleNamespace(companion=companion),
    )

    assert faculty_enabled("relationship_calibration") is False

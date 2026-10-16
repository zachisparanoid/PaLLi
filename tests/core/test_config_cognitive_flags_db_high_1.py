"""DB-HIGH-1 closure test — the cognitive ENABLE_* flags stay off Config.

Phase E.5 Wave 4 step 2 (2026-05-13): the cognitive feature flags
(interaction_mode_router, pragmatic_perception, emotional_state,
topic_boundaries, relationship_calibration, delivery_planner) used to
exist in three places that disagreed:

  1. core/config.py — env-var reads with default `False`
  2. core/settings_schema.py — Pydantic defaults `False`
  3. data/palli.yaml — YAML values `True`

Spine code never actually consumed `config.ENABLE_<FLAG>`, so the True
intent in palli.yaml was decorative — the cognitive classifier modules
ran unconditionally regardless of any flag. Wave 4 deleted the env layer
and made `settings_service.snapshot().features` canonical.

The 2026-07-16 inert-flag cut finished the job from the other end: the
Great Excision had already deleted the features those six flags gated, so
the flags were removed from `FeaturesConfig` and from the `features:`
block of data/palli.yaml outright. With no field and no key, the
three-way disagreement is not merely fixed but structurally impossible,
and the two tests here that pinned "the YAML True reaches the spine" were
deleted with their subject — asserting the load path surfaces a flag that
no longer exists tests nothing. See
tests/core/test_legacy_inert_flags_deleted.py, which pins the removal and
guards the one twin that survived
(`CompanionConfig.relationship_calibration`, which is live).

What still needs a guard, and is all that remains here: the dead
`config.ENABLE_<FLAG>` attributes must stay off the Config singleton, so
nobody re-introduces a False-defaulting env layer under the deleted names.
"""

from __future__ import annotations

import pytest

from core.config import config


# NB: ENABLE_BOUNDED_REFLECTION is deliberately absent from this list. It
# is still live on Config and feeds core/identity/compiler.py's effective
# permissions — a different flag that happens to share a name with the
# excised features.bounded_reflection. Adding it here would be wrong.
_DEAD_ENV_DRIVEN_ATTRS = (
    "ENABLE_INTERACTION_MODE_ROUTER",
    "ENABLE_PRAGMATIC_PERCEPTION",
    "ENABLE_EMOTIONAL_STATE",
    "ENABLE_TOPIC_BOUNDARIES",
    "ENABLE_RELATIONSHIP_CALIBRATION",
    "ENABLE_DELIVERY_PLANNER",
)


@pytest.mark.parametrize("attr_name", _DEAD_ENV_DRIVEN_ATTRS)
def test_cognitive_flag_no_longer_on_config_singleton(attr_name: str) -> None:
    """The Config class no longer carries the env-driven cognitive flag."""
    assert not hasattr(config, attr_name), (
        f"{attr_name} should have been removed from core.config.Config "
        "in E.5 Wave 4 step 2; the faculty it gated was excised entirely "
        "in 2026-07-09 and its settings flag removed 2026-07-16"
    )

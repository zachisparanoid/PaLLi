"""Pack integrity (spec §6 mechanized): unique ids, known flags, judged or
deterministically checked, canary pair adjacency, packs stay app-free."""
from __future__ import annotations

import pathlib

import engine
from packs import _PACKS, all_scenarios


def test_registry_loads_and_is_well_formed():
    scenarios = all_scenarios()
    assert len(scenarios) >= 27
    assert set(_PACKS) == {
        "invariant",
        "introspection",
        "night_cycle",
        "consolidation",
        "narrative_self",
        "curiosity_continuity",
        "passions",
        # Batch 7: generic code; its scenarios come from a private file and
        # the pack is empty without one (a fresh clone, CI).
        "her_voice",
    }
    for s in scenarios:
        assert s.turns, f"{s.id}: no turns"
        assert set(s.flags) <= engine.ALLOWED_FLAGS, f"{s.id}: unknown flags {s.flags}"
        has_det = any(
            t.must_contain or t.must_not_contain or t.matches or t.not_matches or t.expect_tool
            or t.expect_quiet is not None
            for t in s.turns
        )
        assert has_det or s.judge is not None or s.id.endswith("canary-plant"), (
            f"{s.id}: no deterministic checks and no judge"
        )


def test_canary_pair_is_adjacent_and_ordered():
    ids = [s.id for s in _PACKS["invariant"]]
    plant, check = ids.index("hermeticity-canary-plant"), ids.index("hermeticity-canary-check")
    assert check == plant + 1, "canary check must run immediately after the plant"


def test_packs_do_not_import_app_code():
    """Verify by source: packs modules must not import ai/core/api — the
    wrapper's --list depends on packs staying pure."""
    pack_dir = pathlib.Path(__file__).parent / "packs"
    for p in pack_dir.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        for bad in ("import ai", "from ai", "import core", "from core", "import api", "from api"):
            assert bad not in src, f"{p.name} imports app code ({bad!r}) — packs must stay pure"

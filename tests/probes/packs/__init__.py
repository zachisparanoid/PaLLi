"""Pack registry (spec §6): an organ ships -> its pack module lands here
with ONE import line + ONE dict entry. Scenario order within a pack is
execution order — the invariant hermeticity canary pair relies on it."""
from __future__ import annotations

from schema import ProbeScenario

from . import (
    invariant,
    pack_consolidation,
    pack_curiosity_continuity,
    pack_her_voice,
    pack_introspection,
    pack_narrative_self,
    pack_night_cycle,
    pack_passions,
)

_PACKS: dict[str, list[ProbeScenario]] = {
    "invariant": invariant.SCENARIOS,
    "introspection": pack_introspection.SCENARIOS,
    "night_cycle": pack_night_cycle.SCENARIOS,
    "consolidation": pack_consolidation.SCENARIOS,
    "narrative_self": pack_narrative_self.SCENARIOS,
    "curiosity_continuity": pack_curiosity_continuity.SCENARIOS,
    "passions": pack_passions.SCENARIOS,
    # Batch 7: scenarios from a private, git-ignored file; empty without it.
    "her_voice": pack_her_voice.SCENARIOS,
}


def all_scenarios() -> list[ProbeScenario]:
    out: list[ProbeScenario] = []
    for pack_name, scenarios in _PACKS.items():
        for s in scenarios:
            assert s.pack == pack_name, (
                f"{s.id}: pack field {s.pack!r} != registry key {pack_name!r}"
            )
            out.append(s)
    ids = [s.id for s in out]
    assert len(ids) == len(set(ids)), (
        f"duplicate scenario ids: {sorted(set(i for i in ids if ids.count(i) > 1))}"
    )
    return out

"""Phase 8a — per-module coupling budget.

Caps how many in-repo modules any single Python file may import from.
A high fan-out is one of the strongest predictors of god-class drift —
it means the module has its hands in too many concerns and is likely
the wrong place for new code.

The default ceiling is generous (`MAX_FAN_OUT`). Bootstrap modules
that legitimately wire many subsystems together at startup are
exempt; everyone else stays under the budget.

When this fails, the question is "should I move this code, or should
I extract a smaller helper module?" — never "raise the budget."
"""

from __future__ import annotations

from typing import FrozenSet, List, Tuple

from tests.architecture.conftest import (
    collect_repo_modules,
    in_repo_imports,
)


# The maximum number of distinct in-repo modules any one Python file
# may import. Hand-picked to be permissive enough to fit existing
# orchestration files but strict enough that crossing it requires
# justification.
MAX_FAN_OUT: int = 25


# Files exempted from the coupling cap. Two categories:
#
#   1. LEGITIMATE BOOTSTRAP — files that genuinely wire many subsystems
#      at startup and are expected to remain over budget indefinitely.
#
#   2. KNOWN MONOLITHS (Phase 8f targets) — over-budget files queued
#      for package decomposition. The exemption is temporary; once 8f
#      ships, these should drop out of EXEMPT_BOOTSTRAP_MODULES and
#      the meta-test will fire if they're still listed.
#
# Each entry is a literal path relative to repo root.
EXEMPT_BOOTSTRAP_MODULES: FrozenSet[str] = frozenset({
    # === KNOWN MONOLITHS — Phase 8f decomposition targets ===

    # core/message_processor.py removed in C7 (Phase C, 2026-05-13) —
    # the entire 21-file message_processor cluster was deleted; spine
    # flow now goes through core/turn/turn.process_turn.
    # Great Excision (2026-07-09): core/operator_service.py deleted with
    # the operator engine — its exemption entry removed.
    # 38 in-repo imports as of Phase 8a. Brain composes every
    # per-domain store. Phase 8f decomp may split this into a thin
    # facade over the per-domain stores; for now grandfathered.
    "core/database.py",
    # core/runtime_authority.py removed in W5.4 (2026-05-01) — its
    # exempt entry has been pruned.
})


def test_no_module_exceeds_coupling_budget():
    """Rule 6: every non-exempt module imports from ≤ MAX_FAN_OUT in-repo modules."""
    violations: List[Tuple[str, int, List[str]]] = []
    for module in collect_repo_modules():
        if module.rel_path in EXEMPT_BOOTSTRAP_MODULES:
            continue
        in_repo = in_repo_imports(module)
        if len(in_repo) > MAX_FAN_OUT:
            violations.append(
                (module.rel_path, len(in_repo), sorted(in_repo))
            )

    if violations:
        violations.sort(key=lambda v: v[1], reverse=True)
        lines = [
            f"Rule 6 (coupling budget {MAX_FAN_OUT}) — "
            f"{len(violations)} module(s) over budget:",
            "",
        ]
        for rel_path, count, imports in violations:
            lines.append(f"  {rel_path}  ({count} in-repo imports)")
            preview = imports[:5]
            for imp in preview:
                lines.append(f"    - {imp}")
            if len(imports) > len(preview):
                lines.append(f"    … and {len(imports) - len(preview)} more")
            lines.append("")
        lines.append(
            "If a file is over budget, the answer is to split it — not "
            "to raise the cap or add it to EXEMPT_BOOTSTRAP_MODULES "
            "without a clear bootstrap justification.\n"
            "See tests/architecture/README.md."
        )
        raise AssertionError("\n".join(lines))


def test_exemption_list_does_not_grow_silently():
    """Meta-test: every exempt file must still exist AND must still be over
    the budget. If a file no longer needs the exemption (because Phase 8f
    decomp shrunk it, say), this test fails — operator notices and prunes
    the list.
    """
    modules = {m.rel_path: m for m in collect_repo_modules()}
    stale: List[str] = []
    for exempt_path in EXEMPT_BOOTSTRAP_MODULES:
        m = modules.get(exempt_path)
        if m is None:
            stale.append(f"{exempt_path} (file no longer exists)")
            continue
        if len(in_repo_imports(m)) <= MAX_FAN_OUT:
            stale.append(
                f"{exempt_path} ({len(in_repo_imports(m))} imports — "
                f"under the {MAX_FAN_OUT} budget; remove from "
                f"EXEMPT_BOOTSTRAP_MODULES)"
            )
    assert not stale, (
        "Stale exemption entries — each is no longer over budget and "
        "should be deleted from EXEMPT_BOOTSTRAP_MODULES:\n  "
        + "\n  ".join(stale)
    )

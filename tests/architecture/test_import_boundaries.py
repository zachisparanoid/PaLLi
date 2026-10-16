"""Phase 8a — top-level import-boundary rules.

The layering this file enforces:

    bots/ → core/, ai/, voice/        (surface adapters depend on core)
    core/ → ai/                       (core depends on AI as a service layer)
    ai/   → voice.types only          (AI is platform-agnostic)
    voice/→ core/, ai/                (voice surface uses core+ai)

Crossings in the WRONG direction (e.g. core importing from bots) are the
architectural drift this test guards against. When this fails, do NOT
silence it — either move the imported symbol to a more appropriate
module, or invert the dependency.

Read `tests/architecture/README.md` for the full reasoning behind each
rule and the operator playbook for fixing violations.
"""

from __future__ import annotations

from typing import FrozenSet

from tests.architecture.conftest import (
    collect_repo_modules,
    imports_into_tree,
)


# Files that historically violate the rules and are explicitly
# grandfathered. Adding to this list is a deliberate act of debt; the
# CI signal is "do not grow this list." Each entry should carry a
# comment explaining why it exists.
GRANDFATHERED_CORE_TO_BOTS: FrozenSet[str] = frozenset({
    # plugin_runtime registers each surface adapter at startup; the
    # imports are bootstrap-time only, not hot-path coupling. Future
    # cleanup (likely as part of Phase 8e doctor migration or a
    # dedicated plugin-system refactor) should invert these via a
    # capability registry.
    "core/plugin_runtime.py",
    # ambient_service uses Discord-specific bot helpers to deliver
    # arrival messages. Folding the delivery surface behind
    # channel_runtime is the right fix; defer to Phase 8e or later.
    "core/ambient_service.py",
})


def _violations_to_message(rule: str, violations: list[tuple[str, str]]) -> str:
    """Render a friendly multi-line failure message from a violation list."""
    if not violations:
        return ""
    lines = [
        f"{rule} — {len(violations)} violation(s):",
        "",
    ]
    for rel_path, imported in violations:
        lines.append(f"  {rel_path} imports {imported!r}")
    lines.append("")
    lines.append("See tests/architecture/README.md for guidance.")
    return "\n".join(lines)


def test_core_does_not_import_bots():
    """Rule 1: core/ MUST NOT depend on bots/ (with grandfathered exemptions).

    Core is the platform-agnostic logic layer. Surface adapters (bots/)
    depend on core; not the other way around. New core → bots imports
    fail this test. Existing ones are listed in
    `GRANDFATHERED_CORE_TO_BOTS` for separate cleanup.
    """
    modules = collect_repo_modules()
    violations: list[tuple[str, str]] = []
    for m in modules:
        if m.tree != "core":
            continue
        if m.rel_path in GRANDFATHERED_CORE_TO_BOTS:
            continue
        for imp in imports_into_tree(m, "bots"):
            violations.append((m.rel_path, imp))
    assert not violations, _violations_to_message(
        "Rule 1 (core ↛ bots)", violations,
    )


def test_ai_does_not_import_bots():
    """Rule 2: ai/ MUST NOT depend on bots/.

    AI logic is platform-agnostic. Surface knowledge belongs in core
    or in the surface adapter itself.
    """
    modules = collect_repo_modules()
    violations: list[tuple[str, str]] = []
    for m in modules:
        if m.tree != "ai":
            continue
        for imp in imports_into_tree(m, "bots"):
            violations.append((m.rel_path, imp))
    assert not violations, _violations_to_message(
        "Rule 2 (ai ↛ bots)", violations,
    )


def test_ai_does_not_import_voice_runtime():
    """Rule 3: ai/ MUST NOT depend on voice/ EXCEPT voice.types.

    voice.types is a leaf module of pure type definitions
    (VoiceReplyPosture, VoiceSocialContext, etc.). It is shared between
    AI and voice as the canonical type vocabulary; both layers may
    depend on it. All OTHER voice.* imports from AI represent runtime
    coupling and should be inverted.
    """
    modules = collect_repo_modules()
    violations: list[tuple[str, str]] = []
    for m in modules:
        if m.tree != "ai":
            continue
        for imp in imports_into_tree(m, "voice"):
            if imp == "voice.types":
                continue
            violations.append((m.rel_path, imp))
    assert not violations, _violations_to_message(
        "Rule 3 (ai ↛ voice runtime; voice.types is permitted)",
        violations,
    )


def test_voice_does_not_import_bots():
    """Rule 4: voice/ MUST NOT depend on bots/.

    Voice is a surface, like bots. Surfaces don't share each other's
    implementations.
    """
    modules = collect_repo_modules()
    violations: list[tuple[str, str]] = []
    for m in modules:
        if m.tree != "voice":
            continue
        for imp in imports_into_tree(m, "bots"):
            violations.append((m.rel_path, imp))
    assert not violations, _violations_to_message(
        "Rule 4 (voice ↛ bots)", violations,
    )


def test_grandfather_list_does_not_grow_silently():
    """Meta-test: every entry in the grandfather list must correspond to
    a real file that still violates the rule. If we fix one of the
    grandfathered violations and forget to delete the entry, this test
    fails — the operator notices and shrinks the list.
    """
    modules = {m.rel_path: m for m in collect_repo_modules()}
    stale: list[str] = []
    for grandfathered_path in GRANDFATHERED_CORE_TO_BOTS:
        m = modules.get(grandfathered_path)
        if m is None:
            stale.append(f"{grandfathered_path} (file no longer exists)")
            continue
        if not imports_into_tree(m, "bots"):
            stale.append(f"{grandfathered_path} (no longer imports bots — remove from grandfather list)")
    assert not stale, (
        "Stale grandfather entries — each is no longer a real violation "
        "and should be deleted from GRANDFATHERED_CORE_TO_BOTS:\n  "
        + "\n  ".join(stale)
    )

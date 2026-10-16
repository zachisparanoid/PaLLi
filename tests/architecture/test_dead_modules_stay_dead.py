"""Batch 8 (2026-10-01): the dead code deleted before strangers stays deleted.

Task 6 retired the prompts nothing used and their last callers; Task 7 took
out the mood sorter (ai/tonal_register, ai/relationship_calibration and the
text-side planner halves of ai/reply_posture and ai/interaction_strategy),
the empty Phase 10 plugin registry, and the Windows desktop app adapters.
Nothing in the running code reached any of it.

This canary keeps it that way, in the shape of its two older siblings
(tests/test_legacy_classifier_modules_deleted.py: the import must fail;
tests/architecture/test_w0_chime_in_removed.py: the file and the settings
field are gone) plus an AST walk over every shipped code root. The walk
reads with utf-8-sig, so a file that starts with a byte-order mark
(ai/llm_client.py) is read, not skipped, and it catches imports inside
functions too. A name brought back in any form fails here.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every module these two tasks deleted.
DELETED_MODULES: tuple[str, ...] = (
    # Task 6: the last callers of retired prompts.
    "ai.llm_decision_execution",
    "core.message.web_search",
    # Task 7: the mood sorter.
    "ai.tonal_register",
    "ai.relationship_calibration",
    # Task 7: the empty plugin registry.
    "core.plugin_registry",
    "core.plugin_registry_boot",
    "core.plugin_manifest_schema",
    # Task 7: the Windows app adapters.
    "core.app_adapters",
)

# Whole packages deleted, every submodule with them. Only "no source left" is
# checked for the package itself: an untracked __pycache__/ left behind in an
# old checkout would still import as an empty namespace package.
DELETED_PACKAGES: tuple[str, ...] = ("core.message",)

# Modules cut to the half voice uses: these names went with the other half.
DELETED_NAMES: dict[str, tuple[str, ...]] = {
    "ai.reply_posture": (
        "plan_reply_posture",
        "ReplyPosturePlan",
        "ReplyDeliveryTexture",
        "ReplySupportSequence",
        "extract_voice_turn_identity_signals",
    ),
    "ai.interaction_strategy": (
        "plan_interaction_strategy",
        "apply_strategy_restraint",
        "summarize_strategy_for_prompt",
        "serialize_interaction_strategy",
    ),
}

# What voice still uses from the half-kept modules — the cut must not reach it.
KEPT_NAMES: dict[str, tuple[str, ...]] = {
    "ai.reply_posture": ("classify_reply_signals", "choose_reply_posture"),
    "ai.interaction_strategy": ("InteractionStrategy", "strategy_allows_outward_action"),
}

# Every root a released copy ships code from.
SCAN_ROOTS: tuple[str, ...] = (
    "ai", "core", "api", "bots", "voice", "scripts", "dashboards", "palli_config",
)
SCAN_FILES: tuple[str, ...] = ("main.py",)


def _module_path(module: str) -> Path:
    return REPO_ROOT.joinpath(*module.split("."))


def _is_deleted(dotted: str) -> bool:
    return any(
        dotted == name or dotted.startswith(name + ".")
        for name in DELETED_MODULES + DELETED_PACKAGES
    )


def _code_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        files.extend(
            p for p in sorted((REPO_ROOT / root).rglob("*.py"))
            if "__pycache__" not in p.parts
        )
    files.extend(REPO_ROOT / rel for rel in SCAN_FILES)
    return files


def _offences(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    rel = path.relative_to(REPO_ROOT).as_posix()
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_deleted(alias.name):
                    found.append(f"{rel}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _is_deleted(node.module):
                found.append(f"{rel}:{node.lineno} from {node.module} import ...")
                continue
            for alias in node.names:
                if _is_deleted(f"{node.module}.{alias.name}"):
                    found.append(f"{rel}:{node.lineno} from {node.module} import {alias.name}")
                if alias.name in DELETED_NAMES.get(node.module, ()):
                    found.append(f"{rel}:{node.lineno} from {node.module} import {alias.name}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # importlib.import_module("core.app_adapters") and the like.
            if _is_deleted(node.value):
                found.append(f"{rel}:{node.lineno} names {node.value!r}")
    return found


@pytest.mark.parametrize("module", DELETED_MODULES)
def test_deleted_module_cannot_be_imported(module: str) -> None:
    with pytest.raises(ImportError):
        importlib.import_module(module)


@pytest.mark.parametrize("module", DELETED_MODULES)
def test_deleted_module_file_is_gone(module: str) -> None:
    path = _module_path(module)
    assert not path.with_suffix(".py").exists(), f"{path.with_suffix('.py')} should stay deleted"
    assert not (path / "__init__.py").exists(), f"{path}/ should stay deleted"


@pytest.mark.parametrize("package", DELETED_PACKAGES)
def test_deleted_package_has_no_source_left(package: str) -> None:
    path = _module_path(package)
    left = sorted(p.relative_to(REPO_ROOT).as_posix() for p in path.rglob("*.py")) if path.is_dir() else []
    assert not left, f"{package} should stay deleted, found {left}"


@pytest.mark.parametrize("module", sorted(DELETED_NAMES))
def test_half_kept_module_lost_only_its_dead_half(module: str) -> None:
    loaded = importlib.import_module(module)
    back = [name for name in DELETED_NAMES[module] if hasattr(loaded, name)]
    assert not back, f"{module} has {back} again; they were deleted in Batch 8"
    missing = [name for name in KEPT_NAMES[module] if not hasattr(loaded, name)]
    assert not missing, f"{module} lost {missing}, which voice still uses"


def test_no_shipped_code_reaches_a_deleted_module() -> None:
    files = _code_files()
    assert len(files) > 300, f"the scan went blind: {len(files)} files"
    assert REPO_ROOT / "ai" / "llm_client.py" in files  # the BOM file is read too
    offences = [hit for path in files for hit in _offences(path)]
    assert not offences, (
        "code reaches a module or name deleted in Batch 8 — bring it back on "
        "purpose (and take it off this list) or drop the reference: "
        + "; ".join(offences)
    )


def test_settings_schema_has_no_tonal_register_block() -> None:
    """The tonal_register: settings block went with the mood sorter. The
    schema forbids unknown keys, so both tracked yamls dropped it too."""
    from core.settings_schema import PalliSettings

    assert "tonal_register" not in PalliSettings.model_fields
    for rel in ("data/palli.example.yaml", "data/palli.yaml"):
        path = REPO_ROOT / rel
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not any(
                line.startswith("tonal_register:") for line in text.splitlines()
            ), f"{rel} still has a tonal_register: block"

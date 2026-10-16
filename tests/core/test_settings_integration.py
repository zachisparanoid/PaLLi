"""Phase 2+3 integration test.

Proves that propose → apply → routing reads new value — the full
settings-to-runtime pipeline works end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.settings_service import SettingsService


def test_propose_apply_changes_routing(tmp_path: Path):
    """After apply, get_task_model returns the new model."""
    import core.settings_boot as boot_mod
    from core.settings_schema import PalliSettings
    from tests.core.conftest import write_hermetic_settings_yaml

    # Hermetic yaml: response → openai/gpt-5-mini. The live
    # data/palli.yaml routing is owner-tuned and must not be pinned here.
    target = write_hermetic_settings_yaml(tmp_path / "palli.yaml")

    # Boot with the temp copy; restore the original singleton afterwards
    # so the temp-yaml service does not leak into later tests.
    original_service = boot_mod.settings_service
    original_booted = boot_mod._booted
    boot_mod._booted = False
    svc = SettingsService(yaml_path=target)
    boot_mod.settings_service = svc
    svc.load()
    boot_mod._booted = True

    try:
        # Confirm initial routing reads from YAML
        from ai.llm_routing import get_task_model

        initial_model = get_task_model("response")
        assert initial_model is not None
        assert "gpt-5-mini" in initial_model

        # Propose a change: switch response to gpt-4o
        proposal = svc.propose(
            {"llm.routing.response": {"provider": "openai", "model": "gpt-4o"}},
            reason="integration test",
        )
        assert proposal.change_id

        # Apply the change
        result = svc.apply(proposal.change_id)
        assert result.applied

        # Routing should now reflect the change
        new_model = get_task_model("response")
        assert new_model is not None
        assert "gpt-4o" in new_model
        assert "gpt-5-mini" not in new_model
    finally:
        # Clean up
        boot_mod.settings_service = original_service
        boot_mod._booted = original_booted


def test_rollback_restores_routing(tmp_path: Path):
    """After rollback, get_task_model returns the original model."""
    import core.settings_boot as boot_mod

    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    target = tmp_path / "palli.yaml"
    target.write_bytes(repo_yaml.read_bytes())

    original_service = boot_mod.settings_service
    original_booted = boot_mod._booted
    boot_mod._booted = False
    svc = SettingsService(yaml_path=target)
    boot_mod.settings_service = svc
    svc.load()
    boot_mod._booted = True

    try:
        from ai.llm_routing import get_task_model

        original_model = get_task_model("response")

        proposal = svc.propose(
            {"llm.routing.response": {"provider": "openai", "model": "gpt-4o"}},
            reason="rollback test",
        )
        result = svc.apply(proposal.change_id)
        svc.rollback(result.rollback_token)

        restored_model = get_task_model("response")
        assert restored_model == original_model
    finally:
        boot_mod.settings_service = original_service
        boot_mod._booted = original_booted

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.settings_service import SettingsChanged


@pytest.mark.asyncio
async def test_rebuild_providers_calls_replace_registry():
    """_rebuild_providers calls llm.replace_provider_registry."""
    from core.settings_boot import _rebuild_providers
    from core.settings_schema import PalliSettings

    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    import yaml

    raw = yaml.safe_load(repo_yaml.read_text(encoding="utf-8"))
    snapshot = PalliSettings.model_validate(raw)

    mock_llm = AsyncMock()

    with patch("core.settings_boot.llm", mock_llm, create=True):
        # Patch the import inside _rebuild_providers
        with patch("ai.llm_client.llm", mock_llm):
            event = SettingsChanged(
                change_id="chg_test",
                diff=[{"path": "llm.routing.response", "old": {}, "new": {}}],
                old=snapshot,
                new=snapshot,
            )
            await _rebuild_providers(event)

    mock_llm.replace_provider_registry.assert_called_once()


def test_on_settings_changed_exists():
    """The listener function exists and is callable."""
    from core.settings_boot import _on_settings_changed
    assert callable(_on_settings_changed)

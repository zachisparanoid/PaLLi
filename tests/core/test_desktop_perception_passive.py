from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.desktop_perception import DesktopPerceptionService


@pytest.mark.asyncio
async def test_get_passive_snapshot_returns_focused_window_and_health():
    service = DesktopPerceptionService()

    with patch(
        "core.desktop_perception.host_platform_service.focused_window_snapshot",
        return_value={"title": "PaLLi - inner_monologue.py - VS Code", "app": "Code"},
    ):
        with patch.object(
            service, "get_adapter_health", new=AsyncMock(return_value={"status": "ready"})
        ):
            with patch.object(service, "_capture_backend", return_value="native"):
                snapshot = await service.get_passive_snapshot()

    assert snapshot["focused_window"]["title"] == "PaLLi - inner_monologue.py - VS Code"
    assert snapshot["focused_window"]["app"] == "Code"
    assert snapshot["adapter_health"]["status"] == "ready"
    assert snapshot["capture_backend"] == "native"


@pytest.mark.asyncio
async def test_get_passive_snapshot_handles_no_focused_window():
    service = DesktopPerceptionService()

    with patch(
        "core.desktop_perception.host_platform_service.focused_window_snapshot",
        return_value=None,
    ):
        with patch.object(
            service, "get_adapter_health", new=AsyncMock(return_value={"status": "ready"})
        ):
            with patch.object(service, "_capture_backend", return_value="native"):
                snapshot = await service.get_passive_snapshot()

    assert snapshot["focused_window"] is None

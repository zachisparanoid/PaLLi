from __future__ import annotations

import pytest

from core.config import config
from core.host_platform import HostPlatformService, LinuxWaylandBackend, WindowsBackend


def test_backend_selection_prefers_linux_x11(monkeypatch):
    service = HostPlatformService()
    monkeypatch.setattr("core.host_platform.py_platform.system", lambda: "Linux")
    monkeypatch.setattr(config, "HOST_PLATFORM_MODE", "auto", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")

    snapshot = service.platform_snapshot()

    assert snapshot["host_platform"] == "linux"
    assert snapshot["host_backend"] == "linux_x11"
    assert snapshot["display_server"] == "x11"


def test_backend_selection_detects_wayland(monkeypatch):
    service = HostPlatformService()
    monkeypatch.setattr("core.host_platform.py_platform.system", lambda: "Linux")
    monkeypatch.setattr(config, "HOST_PLATFORM_MODE", "auto", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")

    backend = service.backend()
    snapshot = service.platform_snapshot()

    assert isinstance(backend, LinuxWaylandBackend)
    assert snapshot["host_backend"] == "linux_wayland"
    assert snapshot["display_server"] == "wayland"


def test_backend_selection_can_force_windows(monkeypatch):
    service = HostPlatformService()
    monkeypatch.setattr(config, "HOST_PLATFORM_MODE", "windows_native", raising=False)

    backend = service.backend()
    snapshot = service.platform_snapshot()

    assert isinstance(backend, WindowsBackend)
    assert snapshot["host_backend"] == "windows_native"


def test_wayland_capability_health_reports_degraded_window_and_input():
    backend = LinuxWaylandBackend()

    health = backend.capability_health(
        desktop_enabled=True,
        input_enabled=True,
        machine_presence_enabled=True,
        capture_available=True,
        ocr_available=False,
    )

    assert health["window_control"]["status"] == "degraded"
    assert health["input_control"]["status"] == "degraded"
    assert "Wayland" in health["window_control"]["message"]


def test_windows_capability_health_degrades_without_runtime_deps():
    backend = WindowsBackend()

    health = backend.capability_health(
        desktop_enabled=True,
        input_enabled=True,
        machine_presence_enabled=True,
        capture_available=False,
        ocr_available=False,
    )

    assert health["desktop_capture"]["status"] == "degraded"
    assert health["desktop_open"]["status"] in {"ready", "degraded"}

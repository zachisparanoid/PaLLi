"""Tests for core.desktop_activity_watcher.

The watcher polls host_platform_service for focused-window and machine-state
data and walks watched directories for recent file mods. We mock those
inputs so the tests don't depend on the host environment.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.desktop_activity_watcher import (
    ActivitySample,
    ActivityTimelineSummary,
    DesktopActivityWatcher,
)


def _sample(
    *,
    captured_at: float,
    app: str,
    title: str = "",
    machine_state: str = "active",
    idle_seconds: float = 0.0,
    files: tuple[str, ...] = (),
) -> ActivitySample:
    return ActivitySample(
        captured_at=captured_at,
        focused_app=app,
        focused_window_title=title,
        machine_state=machine_state,
        idle_seconds=idle_seconds,
        recent_files=files,
    )


def test_summary_empty_watcher_returns_zero_samples():
    watcher = DesktopActivityWatcher()
    summary = asyncio.run(watcher.get_summary())
    assert summary.sample_count == 0
    assert summary.primary_app == ""
    assert "No recent activity" in summary.to_prose()


def test_summary_picks_dominant_app_from_window():
    watcher = DesktopActivityWatcher()
    now = time.time()
    interval = watcher.poll_interval_seconds
    samples = [
        _sample(captured_at=now - interval * 5, app="Code"),
        _sample(captured_at=now - interval * 4, app="Code"),
        _sample(captured_at=now - interval * 3, app="Code"),
        _sample(captured_at=now - interval * 2, app="Discord"),
        _sample(captured_at=now - interval * 1, app="Code"),
    ]
    summary = watcher._summarize(samples)
    assert summary.primary_app == "Code"
    assert summary.app_switches == 2  # Code→Discord, Discord→Code
    # 4 samples in Code at ~30s each = ~2 min
    expected_minutes = (4 * interval) / 60.0
    assert abs(summary.primary_app_minutes - expected_minutes) < 0.01


def test_summary_aggregates_recent_files_dedup_preserves_order():
    watcher = DesktopActivityWatcher()
    now = time.time()
    samples = [
        _sample(captured_at=now - 60, app="Code", files=("a.py", "b.py")),
        _sample(captured_at=now - 30, app="Code", files=("b.py", "c.py")),
        _sample(captured_at=now, app="Code", files=("a.py", "d.py")),
    ]
    summary = watcher._summarize(samples)
    # First-seen order: a.py, b.py, c.py, d.py — capped at the configured max
    assert summary.recent_files[:4] == ("a.py", "b.py", "c.py", "d.py")


def test_summary_to_prose_describes_app_switches():
    watcher = DesktopActivityWatcher()
    now = time.time()
    samples = [
        _sample(captured_at=now - 240, app="Code"),
        _sample(captured_at=now - 210, app="Slack"),
        _sample(captured_at=now - 180, app="Browser"),
        _sample(captured_at=now - 150, app="Code"),
        _sample(captured_at=now - 120, app="Slack"),
        _sample(captured_at=now - 90, app="Browser"),
        _sample(captured_at=now, app="Code"),
    ]
    summary = watcher._summarize(samples)
    prose = summary.to_prose()
    assert "hopping between apps" in prose
    assert "switches" in prose


def test_capture_sample_uses_host_platform(monkeypatch):
    watcher = DesktopActivityWatcher()
    fake_window = {"app": "TestApp", "title": "the title", "process_name": "test.exe"}
    monkeypatch.setattr(
        "core.desktop_activity_watcher.host_platform_service.focused_window_snapshot",
        lambda: fake_window,
    )
    monkeypatch.setattr(
        "core.desktop_activity_watcher.host_platform_service.machine_presence_state",
        lambda: "active",
    )
    monkeypatch.setattr(
        DesktopActivityWatcher, "_read_idle_seconds", staticmethod(lambda: 5.0)
    )
    monkeypatch.setattr(
        DesktopActivityWatcher, "_scan_recent_files", lambda self: ["foo.py"]
    )
    sample = asyncio.run(watcher._capture_sample())
    assert sample.focused_app == "TestApp"
    assert sample.focused_window_title == "the title"
    assert sample.machine_state == "active"
    assert sample.idle_seconds == 5.0
    assert sample.recent_files == ("foo.py",)


def test_trim_window_drops_old_samples():
    watcher = DesktopActivityWatcher()
    now = time.time()
    too_old = now - watcher.window_seconds - 60
    fresh = now - 30
    watcher._samples = [
        _sample(captured_at=too_old, app="Old"),
        _sample(captured_at=fresh, app="Fresh"),
    ]
    watcher._trim_window()
    assert len(watcher._samples) == 1
    assert watcher._samples[0].focused_app == "Fresh"


def test_scan_recent_files_finds_modified_file_in_watched_dir(tmp_path, monkeypatch):
    watcher = DesktopActivityWatcher()
    target = tmp_path / "subdir"
    target.mkdir()
    fresh_file = target / "fresh.py"
    fresh_file.write_text("# fresh")
    # Force lookback wide enough that a brand-new file is visible.
    monkeypatch.setattr(type(watcher), "watched_dirs", property(lambda self: [tmp_path]))
    monkeypatch.setattr(
        type(watcher), "file_activity_lookback_seconds", property(lambda self: 3600.0)
    )
    monkeypatch.setattr(
        type(watcher), "file_activity_max_files", property(lambda self: 5)
    )
    files = watcher._scan_recent_files()
    assert any("fresh.py" in f for f in files)


def test_scan_recent_files_skips_ignored_segments(tmp_path, monkeypatch):
    watcher = DesktopActivityWatcher()
    junk = tmp_path / "node_modules" / "lib"
    junk.mkdir(parents=True)
    (junk / "junk.js").write_text("// junk")
    real = tmp_path / "src"
    real.mkdir()
    (real / "real.py").write_text("# real")
    monkeypatch.setattr(type(watcher), "watched_dirs", property(lambda self: [tmp_path]))
    monkeypatch.setattr(
        type(watcher), "file_activity_lookback_seconds", property(lambda self: 3600.0)
    )
    monkeypatch.setattr(
        type(watcher), "file_activity_max_files", property(lambda self: 10)
    )
    files = watcher._scan_recent_files()
    assert any("real.py" in f for f in files)
    assert not any("junk.js" in f for f in files)


def test_watcher_does_not_start_when_disabled(monkeypatch):
    """DESKTOP_ACTIVITY_ENABLED=false (2026-08-18): a headless host has no
    focused window; the watcher only ever reported "unknown" and file mtimes
    under her own repo. Off means no task, no samples, no prose."""
    import asyncio
    import core.config as cfg
    from core.desktop_activity_watcher import DesktopActivityWatcher

    monkeypatch.setattr(cfg.config, "DESKTOP_ACTIVITY_ENABLED", False, raising=False)
    w = DesktopActivityWatcher()

    async def go():
        await w.start(owner_user_id=1)
        try:
            return w._running, w._task
        finally:
            await w.stop()

    running, task = asyncio.run(go())
    assert running is False
    assert task is None

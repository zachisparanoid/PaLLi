"""Rolling activity timeline for Jarvis-grade ambient awareness.

Polls (focused window, machine state, recent filesystem activity) on a
periodic tick and keeps a rolling window of the last N samples in memory.
Two consumers read the rolling window:

- `ai/situation_context.gather_recent_activity` summarizes "what's the user
  been doing the last 30 min" into the inner monologue's situation_context.
- The proactive layer reads the latest sample to decide if an autonomous
  action would be useful right now (e.g. "they've been on this same file
  for 90 minutes, could be a good moment to offer help").

Why a separate service from `desktop_perception` and `home_presence`:
- `desktop_perception_service.get_passive_snapshot()` is a *point-in-time*
  read with no history. It tells you "right now they're in VS Code,"
  not "they've been in VS Code for 47 minutes after a 9-minute Discord
  detour."
- `home_presence_service` tracks machine-presence transitions but at the
  granularity of locked/idle/active state — not "what app, what file."
- This module joins both signals into a single rolling timeline that's
  cheap to read and informative enough to power proactive judgments.

Design choices:
- Pure in-memory: no DB writes, no JSONL persistence. The whole window
  is rebuilt on restart. Persistent memory is the inner monologue's job;
  this is a working buffer.
- Polling, not event-driven. We could subscribe to home_presence events,
  but a uniform tick is simpler to reason about and the cost (one focused-
  window read + one stat() per watched dir, ~30s cadence) is negligible.
- Filesystem activity uses mtime polling against configured directories.
  No watchdog dependency. Tracks the N most recently-modified files.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import config
from core.desktop_perception import desktop_perception_service
from core.home_presence import home_presence_service
from core.host_platform import host_platform_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActivitySample:
    """A single tick of the rolling activity buffer."""

    captured_at: float
    focused_app: str
    focused_window_title: str
    machine_state: str
    idle_seconds: float
    recent_files: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "focused_app": self.focused_app,
            "focused_window_title": self.focused_window_title,
            "machine_state": self.machine_state,
            "idle_seconds": self.idle_seconds,
            "recent_files": list(self.recent_files),
        }


@dataclass(frozen=True, slots=True)
class ActivityTimelineSummary:
    """Compact summary of the rolling window for monologue context."""

    sample_count: int
    span_seconds: float
    primary_app: str
    primary_app_minutes: float
    app_switches: int
    machine_state_now: str
    recent_files: Tuple[str, ...]
    last_sample: Optional[ActivitySample] = None

    def to_prose(self) -> str:
        """Render a short natural-language description for situation_context."""
        if self.sample_count == 0:
            return "No recent activity captured yet."
        parts: List[str] = []
        if self.primary_app:
            mins = max(int(self.primary_app_minutes), 0)
            if mins >= 5:
                parts.append(f"primarily in {self.primary_app} (~{mins} min)")
            else:
                parts.append(f"currently in {self.primary_app}")
        if self.app_switches >= 3:
            parts.append(f"hopping between apps ({self.app_switches} switches in window)")
        if self.machine_state_now and self.machine_state_now != "active":
            parts.append(f"machine is {self.machine_state_now}")
        if self.recent_files:
            sample = list(self.recent_files)[:3]
            parts.append(f"recent file activity: {', '.join(sample)}")
        if not parts:
            return "Quiet — at the desk but not focused on anything in particular."
        return "Recent activity: " + "; ".join(parts) + "."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "span_seconds": self.span_seconds,
            "primary_app": self.primary_app,
            "primary_app_minutes": self.primary_app_minutes,
            "app_switches": self.app_switches,
            "machine_state_now": self.machine_state_now,
            "recent_files": list(self.recent_files),
            "last_sample": self.last_sample.to_dict() if self.last_sample else None,
        }


class DesktopActivityWatcher:
    """Background watcher that maintains a rolling activity timeline.

    The watcher is a passive observer. It does NOT trigger any LLM calls,
    Discord messages, or operator actions on its own. Its only job is to
    keep `self._samples` populated so other layers (situation_context,
    proactive) can read coherent recent history without each having to
    re-poll the desktop themselves.
    """

    def __init__(self) -> None:
        self._samples: List[ActivitySample] = []
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task[Any]] = None
        self._running = False
        self._owner_user_id: Optional[int] = None

    @property
    def poll_interval_seconds(self) -> float:
        return max(10.0, float(getattr(config, "DESKTOP_ACTIVITY_POLL_SECONDS", 30.0) or 30.0))

    @property
    def window_seconds(self) -> float:
        return max(60.0, float(getattr(config, "DESKTOP_ACTIVITY_WINDOW_SECONDS", 1800.0) or 1800.0))

    @property
    def watched_dirs(self) -> List[Path]:
        raw = getattr(config, "DESKTOP_ACTIVITY_WATCH_DIRS", "") or ""
        if isinstance(raw, str):
            entries = [item.strip() for item in raw.split(";") if item.strip()]
        elif isinstance(raw, (list, tuple)):
            entries = [str(item).strip() for item in raw if str(item).strip()]
        else:
            entries = []
        if not entries:
            entries = [str(Path.cwd())]
        resolved: List[Path] = []
        for entry in entries:
            try:
                path = Path(entry).expanduser().resolve()
            except Exception:
                continue
            if path.exists() and path.is_dir():
                resolved.append(path)
        return resolved

    @property
    def file_activity_lookback_seconds(self) -> float:
        return max(60.0, float(getattr(config, "DESKTOP_ACTIVITY_FILE_LOOKBACK_SECONDS", 600.0) or 600.0))

    @property
    def file_activity_max_files(self) -> int:
        return max(1, int(getattr(config, "DESKTOP_ACTIVITY_MAX_RECENT_FILES", 8) or 8))

    async def start(self, *, owner_user_id: int) -> None:
        if self._running:
            return
        # DESKTOP_ACTIVITY_ENABLED (2026-08-18): a headless host has no
        # focused window; this watcher only ever reported "unknown" and file
        # mtimes under her own repo. Off = no task, no samples, no prose.
        if not bool(getattr(config, "DESKTOP_ACTIVITY_ENABLED", True)):
            logger.info("desktop_activity_watcher: disabled (DESKTOP_ACTIVITY_ENABLED=false)")
            return
        self._running = True
        self._owner_user_id = int(owner_user_id)
        self._task = asyncio.create_task(self._run(), name="desktop-activity-watcher")
        logger.info(
            "desktop_activity_watcher: started (owner=%s, interval=%.0fs, window=%.0fs, dirs=%d)",
            owner_user_id,
            self.poll_interval_seconds,
            self.window_seconds,
            len(self.watched_dirs),
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.gather(self._task, return_exceptions=True)
            except Exception:
                pass
        self._task = None

    async def _run(self) -> None:
        # Sleep before first tick so startup logs aren't immediately
        # crowded by activity polling. The first sample lands at t+interval.
        try:
            await asyncio.sleep(min(15.0, self.poll_interval_seconds))
        except asyncio.CancelledError:
            return
        while self._running:
            try:
                sample = await self._capture_sample()
                async with self._lock:
                    self._samples.append(sample)
                    self._trim_window()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("desktop_activity_watcher: capture failed: %s", exc, exc_info=True)
            try:
                await asyncio.sleep(self.poll_interval_seconds)
            except asyncio.CancelledError:
                raise

    async def _capture_sample(self) -> ActivitySample:
        focused = await asyncio.to_thread(host_platform_service.focused_window_snapshot) or {}
        machine_state = await asyncio.to_thread(host_platform_service.machine_presence_state)
        idle_seconds = await asyncio.to_thread(self._read_idle_seconds)
        recent_files = await asyncio.to_thread(self._scan_recent_files)
        focused_app = (
            str(focused.get("app") or "").strip()
            or str(focused.get("process_name") or "").strip()
            or str(focused.get("wm_class") or "").strip()
            or "unknown"
        )
        title = str(focused.get("title") or "").strip()
        return ActivitySample(
            captured_at=time.time(),
            focused_app=focused_app,
            focused_window_title=title,
            machine_state=str(machine_state or "unknown"),
            idle_seconds=float(idle_seconds or 0.0),
            recent_files=tuple(recent_files),
        )

    @staticmethod
    def _read_idle_seconds() -> float:
        """Best-effort idle-time read. Returns 0 if not supported on host."""
        try:
            import ctypes
            from ctypes import wintypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return 0.0
            tick_count = ctypes.windll.kernel32.GetTickCount()
            return max(0.0, float(tick_count - info.dwTime) / 1000.0)
        except Exception:
            return 0.0

    def _scan_recent_files(self) -> List[str]:
        cutoff = time.time() - self.file_activity_lookback_seconds
        candidates: List[Tuple[float, Path]] = []
        ignore_segments = {
            ".git", "venv", ".venv", "node_modules", "__pycache__", ".next",
            "dist", "build", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
            "logs", "data", "inner_monologue",
        }
        for root in self.watched_dirs:
            try:
                for current_dir, dirnames, filenames in os.walk(root):
                    parts = Path(current_dir).parts
                    if any(seg in ignore_segments for seg in parts):
                        dirnames[:] = []
                        continue
                    dirnames[:] = [d for d in dirnames if d not in ignore_segments]
                    for fname in filenames:
                        if fname.startswith("."):
                            continue
                        full = Path(current_dir) / fname
                        try:
                            mtime = full.stat().st_mtime
                        except (OSError, PermissionError):
                            continue
                        if mtime >= cutoff:
                            candidates.append((mtime, full))
                            if len(candidates) > 200:
                                break
                    if len(candidates) > 200:
                        break
            except (OSError, PermissionError):
                continue
        candidates.sort(reverse=True, key=lambda item: item[0])
        seen: set[str] = set()
        results: List[str] = []
        for _, path in candidates:
            try:
                rel_root = next(
                    (root for root in self.watched_dirs if str(path).startswith(str(root))),
                    None,
                )
                rendered = (
                    str(path.relative_to(rel_root)) if rel_root else str(path)
                )
            except ValueError:
                rendered = str(path)
            rendered = rendered.replace("\\", "/")
            if rendered in seen:
                continue
            seen.add(rendered)
            results.append(rendered)
            if len(results) >= self.file_activity_max_files:
                break
        return results

    def _trim_window(self) -> None:
        cutoff = time.time() - self.window_seconds
        self._samples = [s for s in self._samples if s.captured_at >= cutoff]

    async def get_summary(self) -> ActivityTimelineSummary:
        async with self._lock:
            samples = list(self._samples)
        if not samples:
            return ActivityTimelineSummary(
                sample_count=0,
                span_seconds=0.0,
                primary_app="",
                primary_app_minutes=0.0,
                app_switches=0,
                machine_state_now="unknown",
                recent_files=(),
                last_sample=None,
            )
        return self._summarize(samples)

    def _summarize(self, samples: List[ActivitySample]) -> ActivityTimelineSummary:
        from collections import Counter

        app_counter: Counter[str] = Counter()
        prev_app: Optional[str] = None
        switches = 0
        recent_files_seen: List[str] = []
        seen_files: set[str] = set()
        interval = self.poll_interval_seconds
        for s in samples:
            if s.focused_app:
                app_counter[s.focused_app] += 1
            if prev_app is not None and s.focused_app and s.focused_app != prev_app:
                switches += 1
            prev_app = s.focused_app
            for path in s.recent_files:
                if path in seen_files:
                    continue
                seen_files.add(path)
                recent_files_seen.append(path)
        primary_app, primary_count = (app_counter.most_common(1) or [("", 0)])[0]
        primary_minutes = (primary_count * interval) / 60.0
        span = samples[-1].captured_at - samples[0].captured_at
        return ActivityTimelineSummary(
            sample_count=len(samples),
            span_seconds=span,
            primary_app=primary_app,
            primary_app_minutes=primary_minutes,
            app_switches=switches,
            machine_state_now=samples[-1].machine_state,
            recent_files=tuple(recent_files_seen[: self.file_activity_max_files]),
            last_sample=samples[-1],
        )

    async def get_latest_sample(self) -> Optional[ActivitySample]:
        async with self._lock:
            return self._samples[-1] if self._samples else None


desktop_activity_watcher = DesktopActivityWatcher()

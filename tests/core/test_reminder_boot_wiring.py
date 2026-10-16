"""Reminder delivery boot wiring (2026-07-01).

Latent bug: reminders SAVED fine (the 4 builtin tools write rows) but
never FIRED — ``reminder_scheduler.start()`` had no caller since the old
``bots/discord/bot.py`` registrar that owned it was deleted in Wave 5.
The delivery loop now starts/stops with the app lifecycle in
``core/main.py``, same fail-soft pattern as the inner monologue engine.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_start_reminder_delivery_starts_scheduler() -> None:
    from core import main as app_main
    from core.reminders import scheduler as sched_mod

    with patch.object(sched_mod.reminder_scheduler, "start", AsyncMock()) as start:
        await app_main._start_reminder_delivery()
    start.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_reminder_delivery_survives_scheduler_failure() -> None:
    from core import main as app_main
    from core.reminders import scheduler as sched_mod

    with patch.object(
        sched_mod.reminder_scheduler, "start", AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await app_main._start_reminder_delivery()  # must not raise


@pytest.mark.asyncio
async def test_stop_reminder_delivery_stops_scheduler() -> None:
    from core import main as app_main
    from core.reminders import scheduler as sched_mod

    with patch.object(sched_mod.reminder_scheduler, "stop", AsyncMock()) as stop:
        await app_main._stop_reminder_delivery()
    stop.assert_awaited_once()

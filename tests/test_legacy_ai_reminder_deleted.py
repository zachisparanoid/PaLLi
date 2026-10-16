"""After C1.6, the 6 legacy ai/reminder_* shim modules must not be importable.

C1 (Phase C, 2026-05-12) collapsed the 8-file ai/reminder_* cluster
(~4,000 lines) into core/reminders/ (parser.py, scheduler.py, binding.py,
store.py). C1.4 rewired all production consumers to canonical paths.
C1.6 deletes the legacy shims.
"""
import pytest


def test_reminder_time_parser_module_deleted():
    with pytest.raises(ImportError):
        import ai.reminder_time_parser  # noqa: F401


def test_reminder_scheduler_module_deleted():
    with pytest.raises(ImportError):
        import ai.reminder_scheduler  # noqa: F401


def test_reminder_runtime_module_deleted():
    with pytest.raises(ImportError):
        import ai.reminder_runtime  # noqa: F401


def test_reminder_schedule_planning_module_deleted():
    with pytest.raises(ImportError):
        import ai.reminder_schedule_planning  # noqa: F401


def test_reminder_schedule_coordination_module_deleted():
    with pytest.raises(ImportError):
        import ai.reminder_schedule_coordination  # noqa: F401


def test_reminder_delivery_module_deleted():
    with pytest.raises(ImportError):
        import ai.reminder_delivery  # noqa: F401

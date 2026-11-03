"""New home for reminder logic on the new core/turn spine.

Post-C1 collapse of the legacy 8-file ai/reminder_* cluster:
- parser.py: time-parsing (was ai/reminder_time_parser.py)
- store.py: thin re-export of core/scheduled_reminder_store.py
- scheduler.py: ReminderScheduler class + singleton + fire_reminder +
  ScheduledReminder dataclass
- binding.py: module-level dataclasses + planning/coordination/runtime/
  delivery helpers (split out for readability — scheduler.py wildcard-
  re-exports everything binding.py defines)

ai/reminder_*.py files are wildcard re-export shims for backward compat
until C1.4 rewires consumers and C1.6 deletes the shims.
"""

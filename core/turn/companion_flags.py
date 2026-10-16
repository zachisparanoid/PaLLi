"""Shared accessor for the companion-faculty feature flags.

The companion restoration (2026-05-21) gates each in-conversation faculty
behind a flag in ``settings.companion``. Recall, decide, reflect, and the
reply renderers all need to read those flags, so the accessor lives here
once instead of being duplicated per module.

``companion_settings()`` returns the live ``CompanionConfig`` or ``None``
when the settings service hasn't booted. Callers treat ``None`` as
"enabled by default" so faculties still work in early-startup / test paths
that run before settings load (matches the schema defaults).
"""
from __future__ import annotations

from typing import Any, Optional


def companion_settings() -> Optional[Any]:
    try:
        from core.settings_boot import settings_service
        snapshot = settings_service.snapshot()
        return getattr(snapshot, "companion", None) if snapshot else None
    except Exception:
        return None


def faculty_enabled(name: str) -> bool:
    """True if the named companion faculty flag is on (or settings unbooted)."""
    companion = companion_settings()
    if companion is None:
        return True
    return bool(getattr(companion, name, True))


def today_slot_on() -> bool:
    """The today slot's one gate (R15, memory that reaches, T12):
    ``companion.memory_reach``, fail-closed — unbooted settings, a missing
    field or any error read as OFF. Reflect writes the slot, the reply's
    facts band reads it and the night retires it, and all three ask here:
    reflect sits at the coupling budget and cannot reach the record's own
    accessor (``ai.memory_reach_index.enabled``), but already imports this
    module. Callers import it at call time, so the suite's pin holds."""
    try:
        companion = companion_settings()
        return companion is not None and bool(getattr(companion, "memory_reach", False))
    except Exception:
        return False


def speaks_first_on() -> bool:
    """``features.proactive_conversations``, the off switch for every message
    PaLLi starts on its own (2026-10-14): its reach-outs and shares to the
    owner, group openers, the hunch rail. Unbooted settings or an error read
    as ON, the schema default. Reminders and replies never ask it."""
    try:
        from core.settings_boot import settings_service
        snapshot = settings_service.snapshot()
        features = getattr(snapshot, "features", None) if snapshot else None
        return True if features is None else bool(getattr(features, "proactive_conversations", True))
    except Exception:
        return True


# Stable alias: tests reach the real gate past the suite's autouse pin.
today_slot_on_impl = today_slot_on

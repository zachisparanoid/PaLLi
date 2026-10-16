"""Step 27 Phase B (spec §4): how many things she may have going at once on
one channel — the run plus TASK sidecars — capped in CODE (his call 2: two).
``ai/her_time.py`` generalized: labeled, per channel, never blocking (a full
house returns False and the caller queues). Her OWN time stays on her_time,
one-at-a-time; his tasks never consume her hours.

A conversation is not a task (build-session correction 1, 2026-08-29): a
talk sidecar rides the TALK LANE — one per channel at a time, never counted
against the cap — so a full house never forces the thin answer on him.
"""
from __future__ import annotations

from typing import Dict, List, Set

_HELD: Dict[str, List[str]] = {}
_TALK: Set[str] = set()


def cap() -> int:
    try:
        from core.turn.companion_flags import companion_settings
        c = companion_settings()
        return max(1, int(getattr(c, "duplex_max_tasks", 2))) if c is not None else 2
    except Exception:
        return 2


def acquire(channel_key: str, label: str) -> bool:
    held = _HELD.setdefault(channel_key, [])
    if len(held) >= cap():
        return False
    held.append(str(label))
    return True


def release(channel_key: str, label: str) -> None:
    held = _HELD.get(channel_key)
    if held and label in held:
        held.remove(label)
    if held == []:
        _HELD.pop(channel_key, None)


def held(channel_key: str) -> List[str]:
    return list(_HELD.get(channel_key, []))


def talk_acquire(channel_key: str) -> bool:
    """The talk lane: one conversation sidecar per channel at a time."""
    if channel_key in _TALK:
        return False
    _TALK.add(channel_key)
    return True


def talk_release(channel_key: str) -> None:
    _TALK.discard(channel_key)


def talk_held(channel_key: str) -> bool:
    return channel_key in _TALK


def reset_for_tests() -> None:
    _HELD.clear()
    _TALK.clear()

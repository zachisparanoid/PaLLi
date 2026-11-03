"""Session keys, built in one place (Batch 8, task 1 — B8-1, ruling R7).

A session key names two different things, and since this batch they can
differ:

- the TRANSCRIPT a turn reads and writes (``data/agent_sessions/<key>.jsonl``).
  In a shared room (a Discord server channel, a Telegram group) that is the
  room's one conversation of what was said in it, by anyone: ``room_key``.
- the RUN STATE of the run answering the turn: its phase, tool count,
  now-doing line, stop and pause requests, queued steers, task slot and
  presence signage (``core/agent/steering.py``, ``core/agent/task_slots.py``).
  The owner's run keys it on the transcript key, as before. A guest's run
  keys it on ``guest_run_key(room_key, user_id)``, so a guest saying hello
  while she works for the owner can never drain, clear or take any of it.

``session_key_for`` is the whole mapping. ``core.agent.runner._derive_session_key``
(the sidecar override, then this) and ``ai.callbacks_lookup.session_key_for``
both delegate here, and ``core.turn.persistence`` builds the room key with
``room_key``. Pure, no imports: safe from any layer.
"""
from __future__ import annotations

from typing import Any

# The chat doors: one transcript per room (a DM is its own room, with its
# own channel id), keyed on the platform and the channel.
_ROOM_PLATFORM_BY_SURFACE = {
    "discord_text": "discord",
    "telegram_text": "telegram",
    "mobile_text": "mobile",
}
# Surfaces keyed on the owner id under a name of their own; every other
# surface is keyed ``{surface}:{owner_id}`` (discord_voice, reminder_fire,
# workbench, …).
_OWNER_KEYED_PREFIX = {
    "voice_local": "voice",
    "dashboard": "dashboard",
    "proactive": "proactive",
}


def room_key(platform: str, channel_id: Any) -> str:
    """A chat room's transcript key: ``discord:<channel>``, ``telegram:<chat>``."""
    return f"{platform}:{channel_id}"


def room_platform(surface: str, default: str = "discord") -> str:
    """The chat door a surface belongs to (``telegram_text`` → ``telegram``),
    or ``default`` for a surface that is not a chat door."""
    return _ROOM_PLATFORM_BY_SURFACE.get(str(surface or ""), default)


def guest_run_key(room_key: str, user_id: Any) -> str:
    """A guest's run-state key in a room: ``{room_key}:guest:{user_id}``.

    It only ever names run state, never a transcript file: the guest's
    visible words and her visible reply go to the room's file, as room
    content."""
    return f"{room_key}:guest:{user_id}"


def session_key_for(surface: str, channel_id: Any, owner_id: Any) -> str:
    """The transcript key for a plain (non-sidecar) turn."""
    platform = _ROOM_PLATFORM_BY_SURFACE.get(surface)
    if platform is not None and channel_id:
        return room_key(platform, channel_id)
    return f"{_OWNER_KEYED_PREFIX.get(surface, surface)}:{owner_id}"

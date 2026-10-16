"""The owner-presence sense — the first REAL signal of him (2026-08-18).

Why this exists. Her machine-presence and desktop senses read this headless
VM: ``loginctl`` reported SSH/tmux terminal idleness (Claude Code sessions,
not the owner at his desk) and there was no window manager, so
``focused_app`` was always "unknown" — she narrated "he's in the unknown
folder, half an hour in" for three days from that noise. Those senses are
now off on this host. The owner's call (08-18): she may sense everything
that is real. Discord presence — his status and activity across his own
devices — is real, already granted (``Intents.all()``), and cheap.

What this is. A small in-process record the Discord client writes and her
senses read (the moment's presence sense, the monologue's situation prose,
tier-1 wakes). No DB, no LLM. Deterministic phrasing is the presentation
exemption; deciding what a status *means* stays hers.

Contract:
- ``record_discord_presence(status, activity, at)`` — idempotent; keeps
  ``since`` across unchanged statuses; ``activity`` is the name of what he
  is doing (a game, a call) or "".
- Transitions notify a hook (the present-moment ``notice``) ONLY on the
  present↔offline edges, with the measured ``absence_s`` on the way back so
  the moment can promote a long absence to its priority arrival wake.
  idle↔online↔dnd flaps are silent — a mouse bump is not an event.
- ``render_line(now)`` — "" when unknown; otherwise one plain line.
- Hook failures never break the record.

Architecture: bots → core (the client writes here); ai → core (the senses
read here). Nothing here imports bots.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# What counts as "present" for the edge detection. Discord's own vocabulary.
_PRESENT = ("online", "idle", "dnd")
_KNOWN = ("online", "idle", "dnd", "offline")

_LABELS = {
    "online": "online",
    "idle": "idle",
    "dnd": "do not disturb",
    "offline": "offline",
}

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "status": "unknown",
    "activity": "",
    "since": None,        # when the current status began
    "updated_at": None,   # last time any report landed
    "offline_since": None,
}
_hook: Optional[Callable[..., Any]] = None


def reset_for_tests() -> None:
    global _hook
    with _lock:
        _state.update({
            "status": "unknown", "activity": "", "since": None,
            "updated_at": None, "offline_since": None,
        })
        _hook = None


def set_transition_hook(hook: Optional[Callable[..., Any]]) -> None:
    """Install the callable that receives ``(kind, **fields)`` on the
    present↔offline edges — in production the present-moment ``notice``."""
    global _hook
    _hook = hook


def snapshot() -> Dict[str, Any]:
    with _lock:
        return dict(_state)


def record_discord_presence(*, status: str, activity: str = "", at: Optional[float] = None) -> Dict[str, Any]:
    """Record what Discord says about him right now. Idempotent."""
    now = float(at) if at is not None else time.time()
    clean = str(status or "").strip().lower()
    if clean == "invisible":
        clean = "offline"
    if clean not in _KNOWN:
        clean = "unknown"
    act = " ".join(str(activity or "").split())[:80]

    kind: Optional[str] = None
    fields: Dict[str, Any] = {}
    with _lock:
        prev = str(_state.get("status") or "unknown")
        if clean != prev:
            was_present = prev in _PRESENT
            is_present = clean in _PRESENT
            if was_present and clean == "offline":
                kind = "owner_offline"
                _state["offline_since"] = now
            elif prev == "offline" and is_present:
                kind = "owner_online"
                off_since = _state.get("offline_since")
                fields["absence_s"] = max(0.0, now - float(off_since)) if off_since else 0.0
            _state["since"] = now
        _state["status"] = clean
        _state["activity"] = act
        _state["updated_at"] = now
        snap = dict(_state)

    if kind and _hook is not None:
        try:
            _hook(kind, **fields)
        except Exception as exc:  # the record is the truth; the hook is a courtesy
            logger.debug("owner_presence: transition hook failed: %s", exc)
    return snap


def render_line(*, now: Optional[float] = None) -> str:
    """One plain line for her senses, or "" when nothing real is known."""
    snap = snapshot()
    status = str(snap.get("status") or "unknown")
    if status not in _KNOWN:
        return ""
    label = _LABELS.get(status, status)
    if status == "offline":
        since = snap.get("since")
        if since is not None:
            gap = max(0.0, (float(now) if now is not None else time.time()) - float(since))
            return f"Discord: offline for {_human_duration(gap)}"
        return "Discord: offline"
    activity = str(snap.get("activity") or "").strip()
    if activity:
        return f"Discord: {label} — playing {activity}"
    return f"Discord: {label}"


def _human_duration(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 90:
        return "a minute"
    if s < 3600:
        return f"about {int(round(s / 60))} minutes"
    hours = s / 3600.0
    if hours < 1.5:
        return "about an hour"
    if hours < 24:
        return f"about {int(round(hours))} hours"
    days = hours / 24.0
    return "about a day" if days < 1.5 else f"about {int(round(days))} days"

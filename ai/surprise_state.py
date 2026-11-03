"""The surprising window (organ 8, spec §4): when her appraisal marks a REAL
departure, the next surprise_salience_minutes of her thoughts are remembered
harder. One tiny JSON file (restart-safe); every reader degrades to None.
App-side state; hermetic-redirected in tests (the deeds-ring guard class)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

STATE_PATH = Path("data/surprise/state.json")


def _companion():
    try:
        from core.turn.companion_flags import companion_settings
        return companion_settings()
    except Exception:
        return None


def mark(*, about: str, weight: str, at: Optional[float] = None) -> None:
    """Open the window — REAL surprises only (slight bumps nothing)."""
    if str(weight or "") != "real":
        return
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({
            "about": str(about or "")[:8],
            "at": float(at if at is not None else time.time()),
        }), encoding="utf-8")
    except Exception as exc:
        logger.warning("surprise_state: mark dropped (%s)", exc)


def active_window(*, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        at = float(raw.get("at"))
    except Exception:
        return None
    comp = _companion()
    minutes = float(getattr(comp, "surprise_salience_minutes", 60) or 60) if comp is not None else 60.0
    t = float(now if now is not None else time.time())
    if 0.0 <= t - at <= minutes * 60.0:
        return {"about": str(raw.get("about") or ""), "at": at}
    return None


def reset_for_tests() -> None:
    try:
        STATE_PATH.unlink(missing_ok=True)
    except Exception:
        pass

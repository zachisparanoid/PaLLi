"""Setup mode — she is up, and she has no brain yet (first door, task 1, 2026-09-17).

The owner's ruling of 2026-09-10 made the dashboard a stranger's first door: a
brain key and a browser is the first hour. So a provider that needs a key, with
no key at all, is no longer a reason to refuse to start. `config.validate()`
records the verdict as `config.brain_ready = False` and she boots anyway — both
chat doors shut, every thinking lane quiet, one soft line on the punch list
naming the address where a brain can be added.

This module is the handful of things every part of that shares, so each of them
says it the same way:

  * the ONE sentence a turn gets when it arrives anyway;
  * the address that sentence and the punch list both name;
  * `lane_idle()` — the gate every scheduled lane reads at its own TICK. The
    engines still START in setup mode: the setup API flips `config.brain_ready`
    on the running process, and a lane that was never started could not resume
    without a restart. Read fresh every time, never cached.

`brain_ready` is a boot VERDICT, not a feature switch: there is no setting for
it, and with a key present none of this runs.

`core.config` is the bottom of the import graph and must not import this module
at module scope. The reverse edge is taken inside the two functions that need
it, not at module scope either: core/config_schema.py imports the address helper
from here, and the schema stays importable without loading a Config.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DASHBOARD_ADDRESS = "http://127.0.0.1:3000"

# A quiet lane says so, but a lane that wakes every minute must not write sixty
# lines an hour about having nothing to do.
IDLE_LOG_EVERY_SECONDS = 3600.0
_last_idle_log: Dict[str, float] = {}
_clock = time.monotonic


class BrainNotConfigured(RuntimeError):
    """A provider call was made while no brain is configured.

    Every scheduled lane and the reply path are gated, so reaching this means a
    path nobody gated. It fails at once and says why, instead of sending a
    keyless request to a real endpoint and waiting out its retry ladder.
    """


def dashboard_address(frontend_url: Optional[str] = None) -> str:
    """Where a brain can be added: FRONTEND_URL when set, else the loopback
    default a fresh clone's dashboard listens on."""
    return str(frontend_url or "").strip() or DEFAULT_DASHBOARD_ADDRESS


def no_brain_sentence() -> str:
    """The one fixed thing she says to a turn that arrives with no brain.
    Canned by design: there is nothing to compose it with."""
    from core.config import config

    address = dashboard_address(getattr(config, "FRONTEND_URL", ""))
    return (
        f"I don't have a brain yet — open the dashboard at {address} "
        "and add a key, then I'm here."
    )


def brain_ready() -> bool:
    """Boot's verdict, read fresh. True until validate() has said otherwise, so
    a reader that runs before the first validate() behaves as it always did."""
    from core.config import config

    return bool(getattr(config, "brain_ready", True))


def doors_shut_for_lack_of_a_brain(cfg: Optional[object] = None) -> List[str]:
    """The chat doors THIS BOOT closed for lack of a brain — "Discord",
    "Telegram" — read off the punch list validate() wrote.

    Boot's own record rather than a fifth place that re-derives it: only a door
    whose token was set and whose flag was on gets that line, so a door that
    was off anyway, or missing its library, never shows up here.

    It deliberately does NOT read `brain_ready`, and since the final wave
    (2026-09-17) `launcher_support.check_doors_waiting_on_a_brain()` delegates
    here so that it cannot either. A successful `POST /api/setup/brain` flips
    `brain_ready` on the running process WITHOUT re-running validate() — on
    purpose, or the two door verdicts would read open while no Discord or
    Telegram task exists — and those doors still need the restart, because the
    bot tasks are created once, at launcher start, from the channel map boot
    built. A reader that stopped at `brain_ready` went silent in exactly the
    window the door creates, and the health readout fell back to "Discord is
    enabled but no Discord token is configured" for a door whose token is set.
    The line boot wrote is true in both states: it opens at the first restart.

    `cfg` is for a reader holding its own reference to the config rather than
    the live singleton — the doctor's module does, and tests replace it.
    """
    from core.config_schema import DOOR_PUNCH_KEY_BY_VERDICT

    if cfg is None:
        from core.config import config

        cfg = config
    doors = set(DOOR_PUNCH_KEY_BY_VERDICT.values())
    punch = getattr(cfg, "punch_list", None)
    return [item.key for item in getattr(punch, "warnings", []) if item.key in doors]


def lane_idle(lane: str) -> bool:
    """True when `lane` must skip this tick because there is no brain.

    One line per scheduler: `if lane_idle("night cycle"): return`. Says so at
    INFO at most once an hour per lane; with a brain it returns False and logs
    nothing at all.
    """
    if brain_ready():
        # Seeing a brain ends this lane's idle period, so its hour is forgotten:
        # if the brain is lost again the next idle period announces itself
        # instead of being swallowed by the old one's throttle.
        _last_idle_log.pop(lane, None)
        return False
    now = _clock()
    last = _last_idle_log.get(lane)
    if last is None or now - last >= IDLE_LOG_EVERY_SECONDS:
        _last_idle_log[lane] = now
        logger.info("setup mode: %s idle — no brain", lane)
    return True

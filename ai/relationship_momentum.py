"""Compounding, asymmetric relationship calibration — companion faculty F7.

Models how the relationship FEELS over time across four dimensions, each a
0..1 momentum value: closeness, warmth, playfulness, directness.

Design (agreed with the owner, 2026-05-22), meant to mirror how humans
actually calibrate a relationship in conversation:

  - COMPOUNDS: each turn nudges the state only a little, so the read builds
    slowly through many consistent moments instead of snapping per-turn.
  - ASYMMETRIC: closeness/warmth build slowly but erode faster and stickier
    (negativity bias) — negative shifts use a larger rate than positive ones.
  - SHARP TURNS: significant moments (vulnerability, deep intimacy) move the
    state fast, the way a real breakthrough or rupture does.
  - GENTLE DECAY on long absence — the relationship feels a touch rusty after
    a gap, without resetting (core closeness persists).

This is a felt-result model, not a simulation of cognition. Its only job is
to make PaLLi behave like it has actually been with you over time. Known
simplifications (deferred, see the companion psychology doc): context-specific
reads, salience stickiness, and rupture-and-repair deepening.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_DIR = Path("data/companion")
_DEFAULT = 0.3  # neutral-ish starting point — warm but not presumptuous

# Per-owner locks so concurrent turns (Discord + dashboard, overlapping
# reflect tasks) can't interleave the load-modify-write cycle and lose
# updates. RLock because update_from_turn holds it across save_state.
_OWNER_LOCKS: dict[int, threading.RLock] = {}
_OWNER_LOCKS_GUARD = threading.Lock()


def _owner_lock(owner_id: int) -> threading.RLock:
    with _OWNER_LOCKS_GUARD:
        lock = _OWNER_LOCKS.get(int(owner_id))
        if lock is None:
            lock = _OWNER_LOCKS[int(owner_id)] = threading.RLock()
        return lock

# Asymmetric update rates. Positive change is slow (trust is earned);
# negative change is faster and stickier (negativity bias); sharp turns
# move fast regardless of direction.
_ALPHA_POS = 0.06
_ALPHA_NEG = 0.16
_ALPHA_SHARP = 0.34
_DECAY_PER_GAP = 0.04  # rust on a long absence


@dataclass
class RelationshipState:
    closeness: float = _DEFAULT
    warmth: float = _DEFAULT
    playfulness: float = _DEFAULT
    directness: float = _DEFAULT
    updated_at: float = 0.0
    turn_count: int = 0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _path(owner_id: int) -> Path:
    return _STATE_DIR / f"relationship_{int(owner_id)}.json"


def load_state(owner_id: int) -> RelationshipState:
    try:
        p = _path(owner_id)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            known = {f.name for f in fields(RelationshipState)}
            return RelationshipState(**{k: v for k, v in data.items() if k in known})
    except Exception as exc:
        logger.debug("relationship_momentum: load failed: %s", exc)
    return RelationshipState()


def save_state(owner_id: int, state: RelationshipState) -> None:
    try:
        with _owner_lock(owner_id):
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            target = _path(owner_id)
            # Atomic write: temp file in the same directory, then os.replace,
            # so readers never see a torn/partial file.
            fd, tmp = tempfile.mkstemp(
                dir=str(_STATE_DIR), prefix=target.name + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(state)))
                os.replace(tmp, target)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
    except Exception as exc:
        logger.debug("relationship_momentum: save failed: %s", exc)


def _nudge(current: float, target: float, *, sharp: bool = False, negative: bool = False) -> float:
    if sharp:
        alpha = _ALPHA_SHARP
    elif negative:
        alpha = _ALPHA_NEG
    else:
        alpha = _ALPHA_POS
    return _clamp(current + alpha * (target - current))


def update_from_turn(
    owner_id: int, *, emotional_context: str | None, gap_ticks: int = 0
) -> RelationshipState:
    """Compound this turn's relationship signal into the persistent state.

    ``emotional_context`` is the decide pass's 7-tag register. ``gap_ticks``
    is the count of monologue ticks since the user's last message (a proxy
    for elapsed time / absence). Best-effort; never raises.
    """
    with _owner_lock(owner_id):
        return _update_from_turn_locked(
            owner_id, emotional_context=emotional_context, gap_ticks=gap_ticks
        )


def _update_from_turn_locked(
    owner_id: int, *, emotional_context: str | None, gap_ticks: int
) -> RelationshipState:
    state = load_state(owner_id)
    ec = str(emotional_context or "").strip().lower()

    # Gentle rust on a long absence: warmth/playfulness fade a little, but
    # closeness — the earned, durable part — persists.
    if gap_ticks >= 6:
        state.warmth = _clamp(state.warmth - _DECAY_PER_GAP)
        state.playfulness = _clamp(state.playfulness - _DECAY_PER_GAP)

    if ec == "deep_intimacy":
        state.closeness = _nudge(state.closeness, 1.0, sharp=True)
        state.warmth = _nudge(state.warmth, 1.0, sharp=True)
    elif ec == "vulnerable_close":
        state.closeness = _nudge(state.closeness, 0.9, sharp=True)
        state.warmth = _nudge(state.warmth, 0.9, sharp=True)
    elif ec == "venting":
        # Opening up builds closeness; warmth follows being received well.
        state.closeness = _nudge(state.closeness, 0.8)
        state.warmth = _nudge(state.warmth, 0.7)
    elif ec == "playful_banter":
        state.playfulness = _nudge(state.playfulness, 0.9)
        state.closeness = _nudge(state.closeness, 0.7)
        state.directness = _nudge(state.directness, 0.7)
    elif ec == "frustrated":
        # Ambiguous (frustration at life vs. at PaLLi). Treat as a mild
        # negative on warmth/playfulness — asymmetric, so it bites a little.
        state.warmth = _nudge(state.warmth, 0.4, negative=True)
        state.playfulness = _nudge(state.playfulness, 0.3, negative=True)
    elif ec == "casual_greeting":
        # Consistent low-stakes contact slowly accrues closeness.
        state.closeness = _nudge(state.closeness, 0.65)
    # "neutral" / unknown: no nudge — the state holds.

    state.updated_at = time.time()
    state.turn_count += 1
    save_state(owner_id, state)
    return state


def _band(value: float) -> str:
    return "high" if value >= 0.66 else ("moderate" if value >= 0.4 else "low")


def render_posture(state: RelationshipState) -> str:
    """Turn the four dimensions into a short prose directive for the reply.

    Returns "" during cold start (too few turns to have a real read) so PaLLi
    doesn't act overfamiliar before the relationship has actually accrued.
    """
    if state.turn_count < 3:
        return ""

    closeness = {
        "high": "You two are genuinely close — speak like someone who's been in their corner a long time, no distance.",
        "moderate": "You have an established, comfortable rapport — familiar, not formal.",
        "low": "You're still building rapport — warm and present, but don't presume deep closeness yet.",
    }[_band(state.closeness)]
    warmth = {
        "high": "Lead with warmth.",
        "moderate": "Be warm but not effusive.",
        "low": "Keep warmth measured right now.",
    }[_band(state.warmth)]
    playful = {
        "high": "Playful teasing and banter are welcome.",
        "moderate": "A little humor lands; read the moment.",
        "low": "Keep it mostly straight; humor isn't landing right now.",
    }[_band(state.playfulness)]
    direct = {
        "high": "They want bluntness — say the real thing, skip the cushioning.",
        "moderate": "Be direct, with a little tact.",
        "low": "Be gentle and indirect for now.",
    }[_band(state.directness)]

    return (
        "## Where you stand with them\n"
        "Your accumulated read on this relationship (built up over time — let "
        "it shape how you show up, don't recite it):\n"
        f"{closeness} {warmth} {playful} {direct}"
    )

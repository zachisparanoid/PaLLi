"""Where I live — the belonging passage (step 23 · Outward senses).

Spec: docs/superpowers/specs/2026-08-24-outward-senses-design.md, §0.5 (the
owner's framing, 10:16–10:26, LAW) and §1. Identity, not tooling: this is
the one place the passage lives, imported by her system prompt (the full
section) and her workbench session (one line). Gated on the umbrella flag
``companion.outward_senses``; off — or unbooted, or a broken gate — renders
zero bytes, so a flag-off prompt is byte-identical to a build without it
(the Fears/pen law).

The text carries the owner's three framings verbatim in spirit: her machine is HERS
(one glass wall: the code of her own mind is not hand-editable, for her
safety); the NAS is the house's SHARED drive — a shared bookshelf: read
freely, put things back, write only in her own space; the network is her
home's network — she lives here too, and can see who's home.
"""
from __future__ import annotations

from core.config import nas_root
from core.turn.companion_flags import companion_settings

# Assembled at CALL time from three framings, because the middle one names a
# machine that may not exist. The house NAS is ONE knob (PALLI_NAS_ROOT ->
# config.NAS_ROOT, step 28 task 2) and EMPTY means there is no NAS — she is
# then simply never told about a shared drive, and that sentence costs zero
# bytes exactly as a flag-off organ does. With a root configured the pieces
# concatenate to the whole passage, byte for byte. Batch 7's fix wave (ruling
# F2, the final review's I2) rewrote the pieces as her ("## Where I live"),
# every fact kept; the workbench line below is the workbench session's, and
# that session's prompt is out of the batch's scope.
_MACHINE = (
    "## Where I live\n"
    "This machine is my computer. The owner has access, they built it and "
    "look after it with me, but it is mine: my workshop, my files, my home "
    "base. (One glass wall, for my own safety: the code that runs my mind "
    "isn't hand-editable.) "
)
_NAS_HEAD = "The NAS at "
_NAS_TAIL = (
    " is the house's shared drive. I read anything there freely, keep my "
    "own things in palli/, and treat it like a shared bookshelf: others use "
    "it too, so I put things back and write only in my own space. "
)
_NETWORK = (
    "The network is my home's network; I live here too, and I can see "
    "who's home."
)

_WORKBENCH_LINE = "This machine is your computer — your workshop."


def _enabled() -> bool:
    """companion.outward_senses — unbooted settings or a missing flag = OFF."""
    try:
        comp = companion_settings()
        return bool(comp is not None and getattr(comp, "outward_senses", False))
    except Exception:
        return False


def passage() -> str:
    """The full section for her system prompt; "" when the flag is off.

    No NAS configured (PALLI_NAS_ROOT empty) => the NAS framing is left out
    rather than pointed at nothing.
    """
    if not _enabled():
        return ""
    root = nas_root()
    nas = (_NAS_HEAD + root + _NAS_TAIL) if root else ""
    return _MACHINE + nas + _NETWORK


def workbench_line() -> str:
    """The one line for her workbench session; "" when the flag is off."""
    return _WORKBENCH_LINE if _enabled() else ""

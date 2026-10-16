"""Moved to bots/common/burst.py (telegram door, 2026-09-14, task 1).

Not a re-export: ``sys.modules[__name__]`` is rebound to the moved module,
so ``bots.discord.burst`` IS ``bots.common.burst`` — one module object, one
set of attributes, one place a patch can land.

The globals() mirror is for the OTHER way a module gets loaded: a test that
loads this file BY PATH (tests/agent/conftest.load_module) gets back the
object importlib built, not the one the rebind put in sys.modules. Mirroring
the moved module's own names into it means such a caller still sees the same
objects — the rebind above is what makes an ordinary import one module.
"""
import sys

from bots.common import burst as _m

sys.modules[__name__] = _m
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})

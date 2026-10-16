"""Moved to bots/common/steering.py (telegram door, 2026-09-14, task 1).

Not a re-export: ``sys.modules[__name__]`` is rebound to the moved module,
so ``bots.discord.steering_fork`` IS ``bots.common.steering`` — one module
object, one set of attributes. The client reads ``steering_fork._flag_on``
and ``steering_fork.judge_mid_task`` through the module object and tests
monkeypatch them there; two objects would mean the patch lands on one and
the client reads the other.

The globals() mirror is for the OTHER way a module gets loaded: a test that
loads this file BY PATH (tests/agent/conftest.load_module) gets back the
object importlib built, not the one the rebind put in sys.modules.
"""
import sys

from bots.common import steering as _m

sys.modules[__name__] = _m
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})

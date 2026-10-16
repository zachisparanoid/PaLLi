"""outreach_allowed must be callable at all.

`ai/proactive_policy.py` referenced a module-level ``logger`` that was never
defined — not in any commit in the file's history. Every call to
``outreach_allowed`` raised ``NameError`` at the first log site, and
``core/turn/proactive._outreach_allowed`` caught it and failed closed:

    _outreach_allowed: ai.proactive_policy unavailable or raised
    (NameError: name 'logger' is not defined); failing closed —
    outreach blocked

So the spam guard was never consulted and the candidate rail never fired.
Failing closed was the right call by that handler; it is what kept a broken
module from turning into a broken behavior. It also made the breakage look
like a policy decision in the logs, which is why it survived so long.

This is a smoke test, not a policy test: it only pins that the module's own
names resolve. The daily-cap and frequency rules have their own coverage.
"""
from __future__ import annotations

import logging

import ai.proactive_policy as policy


def test_module_logger_exists_and_is_a_logger():
    assert isinstance(getattr(policy, "logger", None), logging.Logger)


def test_every_logger_reference_in_the_module_resolves():
    """The NameError only fired on the paths that logged, so a smoke import
    never caught it. Compile-and-scan the module's own globals instead."""
    import inspect

    src = inspect.getsource(policy)
    assert "logger.warning" in src or "logger.info" in src  # guard the guard
    missing = [
        name
        for name in ("logger",)
        if name not in vars(policy) and name not in policy.__builtins__
    ]
    assert missing == [], f"module references undefined global(s): {missing}"

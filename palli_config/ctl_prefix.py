"""How to invoke `palli_ctl` — asked, not assumed (week 1 task 5).

The tree told a stranger two different things. `palli_config/brain_render.py`
ended every screen with `palli_ctl brain --pick`; `scripts/palli_ctl.py` ended
its own with `venv/bin/python scripts/palli_ctl.py brain --pick`. Exactly one of
those runs on any given machine:

  * `palli_ctl` exists only after `pip install -e .` puts the console script on
    the PATH (pyproject.toml declares it; week 1 task 5).
  * `venv/bin/python scripts/palli_ctl.py` works from the repo root whether or
    not anything was installed — the owner's own venv has no pip at all.

So nothing hard-codes either form. Every "here is your next move" line asks this
at render time, and the answer is a command the reader can paste.
"""
from __future__ import annotations

import shutil

CONSOLE_SCRIPT = "palli_ctl"
REPO_INVOCATION = "venv/bin/python scripts/palli_ctl.py"


def ctl_prefix() -> str:
    """The prefix a reader can actually paste, decided by the PATH."""
    if shutil.which(CONSOLE_SCRIPT):
        return CONSOLE_SCRIPT
    return REPO_INVOCATION

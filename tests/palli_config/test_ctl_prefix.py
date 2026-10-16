"""Week 1 task 5 — the "next move" line has to be a command that actually runs.

Two truths lived in the tree at once. `palli_config/brain_render.py` printed
`palli_ctl brain --pick`, which only works after `pip install -e .`;
`scripts/palli_ctl.py` printed `venv/bin/python scripts/palli_ctl.py …`, which
only works from the repo root. A stranger who ran the wrong one got
"command not found" from the tool that had just told them what to type.

So neither form is hard-coded any more: both go through `ctl_prefix()`, which
asks the PATH.
"""
from __future__ import annotations

from palli_config.ctl_prefix import CONSOLE_SCRIPT, REPO_INVOCATION, ctl_prefix


def test_the_prefix_is_the_console_script_when_it_is_on_the_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/palli_ctl" if name == "palli_ctl" else None)
    assert ctl_prefix() == CONSOLE_SCRIPT == "palli_ctl"


def test_the_prefix_falls_back_to_the_repo_invocation(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert ctl_prefix() == REPO_INVOCATION == "venv/bin/python scripts/palli_ctl.py"

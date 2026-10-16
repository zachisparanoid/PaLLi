"""The defaults a stranger inherits (stranger-proofing week 1, task 2).

A fresh clone used to bind ``0.0.0.0`` with the dashboard in no-auth mode:
every device on the network could open the chat endpoint, and chat can reach
``local_shell``. The CIDR gate (``BACKEND_ALLOWED_NETWORKS``) was the only
thing in the way, and a knob is not a default. So the default is loopback,
and the template says out loud what turning it off opens.

The other knobs here are the budgets a stranger cannot be hurt by: the shell
timeout ceiling and the guest in-flight cap. (Batch 8, task 2b: the per-guest
rate limit went on the owner's word, 09-30 — "Drop the limit." — and the cap
is a queue now, so it is gone from config and the template.)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_ATTRS = (
    "BACKEND_HOST",
    "LOCAL_SHELL_MAX_TIMEOUT_SECONDS",
    "GUEST_MAX_IN_FLIGHT",
)
_CACHE: dict[str, str] = {}


def _defaults() -> dict[str, str]:
    """Read the defaults from a CHILD process, with every knob blanked.

    Not ``importlib.reload(core.config)``: this suite's conftest patches the
    config singleton in place, and a reload would hand every later test a
    different object. And blank rather than absent, because a real checkout's
    ``.env.local`` fills any var a test leaves unset — so "" has to read as
    unset too, or the default is untested where it matters most.
    """
    if _CACHE:
        return _CACHE
    env = {**os.environ, **{name: "" for name in _ATTRS}}
    code = "from core.config import config\n" + "\n".join(
        f"print(config.{name})" for name in _ATTRS
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == len(_ATTRS), proc.stdout
    _CACHE.update(dict(zip(_ATTRS, [line.strip() for line in lines])))
    return _CACHE


def _template() -> str:
    return (REPO / ".env.template").read_text(encoding="utf-8")


def test_the_backend_binds_loopback_unless_told_otherwise():
    assert _defaults()["BACKEND_HOST"] == "127.0.0.1"


def test_the_template_ships_the_loopback_bind_and_says_what_0000_opens():
    text = _template()
    assert "\nBACKEND_HOST=127.0.0.1\n" in text
    assert "\nBACKEND_HOST=0.0.0.0" not in text
    block = text.split("BACKEND_HOST=127.0.0.1")[0].rsplit("\n\n", 1)[-1].lower()
    assert "0.0.0.0" in block
    assert "network" in block


def test_the_template_says_what_the_no_login_dashboard_opens():
    text = _template()
    block = text.split("LOCAL_DASHBOARD_NO_AUTH=")[0].rsplit("\n\n", 1)[-1].lower()
    assert "no login" in block
    assert "loopback" in block
    assert "shell" in block


def test_the_shell_timeout_ceiling_has_a_safe_default():
    assert _defaults()["LOCAL_SHELL_MAX_TIMEOUT_SECONDS"] == "300"


def test_the_guest_in_flight_cap_has_a_safe_default():
    assert _defaults()["GUEST_MAX_IN_FLIGHT"] == "2"


def test_the_per_guest_rate_limit_is_gone_from_config_and_the_template():
    from core.config import Config

    assert not hasattr(Config, "GUEST_RATE_LIMIT_PER_10MIN")
    assert "GUEST_RATE_LIMIT_PER_10MIN" not in _template()

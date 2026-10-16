"""
Wave 0 asserted that the Slack and Telegram channel bots were gone. Half of
that premise was REVERSED on the owner's word (the telegram door,
2026-09-14): Telegram is a door he actually uses, built deliberately on the
same spine as Discord, with a plan, a spec and six tasks behind it.

Slack is still gone, and this file is still the tripwire that keeps it gone
— that half is untouched. The Telegram half now asserts the OPPOSITE, on
purpose, because a tripwire that says "this must not exist" about something
the owner asked for is not a guard, it is a stale note. The record of what
Wave 0 decided lives in git history and in the plan that overturned it.
"""
from __future__ import annotations

import os


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_slack_bot_directory_is_gone() -> None:
    path = os.path.join(REPO_ROOT, "bots", "slack")
    assert not os.path.exists(path), f"{path} should be deleted"


def test_the_telegram_door_is_here_on_purpose() -> None:
    """The Wave 0 assertion, inverted by the owner's decision. Every module
    the door is made of, so a half-deleted door cannot pass."""
    for name in ("__init__.py", "client.py", "intake.py", "reply.py",
                 "adapter.py"):
        path = os.path.join(REPO_ROOT, "bots", "telegram", name)
        assert os.path.isfile(path), f"{path} is missing"


def test_config_has_no_slack_fields() -> None:
    """The config dataclass should still declare nothing about Slack."""
    from core.config import config
    for field in (
        "ENABLE_SLACK_BOT",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SLACK_SOCKET_RECONNECT_SECONDS",
    ):
        assert not hasattr(config, field), (
            f"config still declares {field}; should be removed in Wave 0"
        )


def test_config_declares_the_two_telegram_knobs_and_nothing_else() -> None:
    """The door needs exactly two settings. `TELEGRAM_POLL_TIMEOUT_SECONDS`
    was a hosted-era knob and stays deleted — the polling timeout is
    python-telegram-bot's to choose."""
    from core.config import config

    assert hasattr(config, "ENABLE_TELEGRAM_BOT")
    assert hasattr(config, "TELEGRAM_BOT_TOKEN")
    assert not hasattr(config, "TELEGRAM_POLL_TIMEOUT_SECONDS")


def test_the_telegram_door_is_off_until_it_is_asked_for() -> None:
    """An install that has never heard of this door must be unchanged by
    its arrival: the flag defaults off and the token defaults blank.

    Read in a child process with both variables pinned EMPTY: `Config` reads
    the environment at import, and on the owner's checkout `.env` now sets
    the door on, which python-dotenv fills into any variable the test leaves
    unset (the week-1 hazard) — the in-process class attributes would then
    describe his install, not the default."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "ENABLE_TELEGRAM_BOT": "", "TELEGRAM_BOT_TOKEN": ""}
    out = subprocess.run(
        [sys.executable, "-c",
         "from core.config import Config; "
         "print(Config.ENABLE_TELEGRAM_BOT, repr(Config.TELEGRAM_BOT_TOKEN))"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "False ''", out


def test_the_telegram_package_imports_without_the_library() -> None:
    """The whole door is lazy: nothing under bots/telegram/ may import
    `telegram` at module level, or the suite (and a fresh clone that has not
    installed it yet) would fail on import instead of saying the door is
    shut."""
    import ast
    import pathlib

    package = pathlib.Path(REPO_ROOT) / "bots" / "telegram"
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:                       # MODULE level only
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [str(node.module or "")]
            for name in names:
                if name == "telegram" or name.startswith("telegram."):
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, offenders

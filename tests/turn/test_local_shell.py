"""Tests for core.turn.local_shell — autonomy-gated shell executor."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from core.turn.local_shell import (
    _build_subprocess_args,
    _decide_allowance,
    _get_default_interpreter,
    _is_obvious_readonly,
    execute_local_shell,
)


def test_obvious_readonly_recognizes_unix_basics():
    assert _is_obvious_readonly("ls -la")
    assert _is_obvious_readonly("ps aux")
    assert _is_obvious_readonly("grep foo bar.txt")
    assert _is_obvious_readonly("git status")
    assert _is_obvious_readonly("git log --oneline")
    assert _is_obvious_readonly("whoami")


def test_obvious_readonly_recognizes_windows_basics():
    assert _is_obvious_readonly("dir")
    assert _is_obvious_readonly("Get-Process")
    assert _is_obvious_readonly("get-childitem")
    assert _is_obvious_readonly("tasklist")


def test_obvious_readonly_rejects_mutating_git():
    # git is a head token that's read-only EXCEPT for known mutating subcommands.
    assert not _is_obvious_readonly("git commit -m foo")
    assert not _is_obvious_readonly("git push")
    assert not _is_obvious_readonly("git reset --hard")


def test_obvious_readonly_rejects_unknown_commands():
    assert not _is_obvious_readonly("rm -rf /")
    assert not _is_obvious_readonly("npm install foo")
    assert not _is_obvious_readonly("curl -X POST https://...")


def test_decide_allowance_unsafe_always_blocked():
    autonomy = {
        "allow_silent_low_risk_actions": True,
        "allow_silent_beneficial_actions": True,
        "allow_privileged_cli_actions": True,
        "require_confirmation_for_destructive_actions": False,
    }
    allowed, _ = _decide_allowance(
        intent_class="unsafe", autonomy=autonomy, require_confirmation_already_given=True,
    )
    assert allowed is False


def test_decide_allowance_read_only_always_allowed():
    autonomy = {
        "allow_silent_low_risk_actions": False,
        "allow_silent_beneficial_actions": False,
        "allow_privileged_cli_actions": False,
        "require_confirmation_for_destructive_actions": True,
    }
    allowed, _ = _decide_allowance(
        intent_class="read_only", autonomy=autonomy, require_confirmation_already_given=False,
    )
    assert allowed is True


def test_decide_allowance_mutating_requires_silent_low_risk():
    permissive = {"allow_silent_low_risk_actions": True}
    restrictive = {"allow_silent_low_risk_actions": False, "allow_silent_beneficial_actions": False}
    assert _decide_allowance(intent_class="mutating", autonomy=permissive, require_confirmation_already_given=False)[0] is True
    assert _decide_allowance(intent_class="mutating", autonomy=restrictive, require_confirmation_already_given=False)[0] is False


def test_decide_allowance_destructive_requires_confirmation():
    autonomy = {
        "allow_privileged_cli_actions": False,
        "require_confirmation_for_destructive_actions": True,
    }
    allowed_no_conf, _ = _decide_allowance(
        intent_class="destructive", autonomy=autonomy, require_confirmation_already_given=False,
    )
    allowed_with_conf, _ = _decide_allowance(
        intent_class="destructive", autonomy=autonomy, require_confirmation_already_given=True,
    )
    assert allowed_no_conf is False
    assert allowed_with_conf is True


def test_decide_allowance_destructive_with_privileged_and_no_required_confirm():
    """If user opted out of destructive confirmation AND has privileged_cli, run without confirm."""
    autonomy = {
        "allow_privileged_cli_actions": True,
        "require_confirmation_for_destructive_actions": False,
    }
    allowed, _ = _decide_allowance(
        intent_class="destructive", autonomy=autonomy, require_confirmation_already_given=False,
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_execute_local_shell_disabled_short_circuits():
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = False
        result = await execute_local_shell(
            command="ls", interpreter="bash", owner_user_id=1,
        )
    assert result.allowed is False
    assert "disabled" in result.refused_reason


@pytest.mark.asyncio
async def test_execute_local_shell_empty_command_refused():
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = True
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "powershell"
        result = await execute_local_shell(
            command="   ", interpreter=None, owner_user_id=1,
        )
    assert result.allowed is False
    assert result.refused_reason == "empty command"


@pytest.mark.asyncio
async def test_execute_local_shell_obvious_readonly_skips_classifier(monkeypatch):
    """The fast-path for ls/ps/etc should NOT call the LLM classifier."""
    classifier_mock = AsyncMock(return_value=("destructive", "should not be called"))
    monkeypatch.setattr("core.turn.local_shell._classify_intent_via_llm", classifier_mock)
    monkeypatch.setattr(
        "core.turn.local_shell._resolve_autonomy",
        AsyncMock(return_value={
            "allow_silent_low_risk_actions": False,
            "allow_silent_beneficial_actions": False,
            "allow_privileged_cli_actions": False,
            "require_confirmation_for_destructive_actions": True,
        }),
    )
    # Make subprocess fake so we don't actually run anything.
    monkeypatch.setattr(
        "core.turn.local_shell._run_blocking",
        lambda args, **kwargs: (0, "fake stdout", ""),
    )
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = True
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "bash"
        mock_config.LOCAL_SHELL_TIMEOUT_SECONDS = 5.0
        mock_config.LOCAL_SHELL_MAX_OUTPUT_CHARS = 1000
        result = await execute_local_shell(
            command="ls -la",
            interpreter="bash",
            owner_user_id=1,
        )
    assert result.intent_class == "read_only"
    assert result.allowed is True
    classifier_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_local_shell_unknown_command_invokes_classifier(monkeypatch):
    classifier_mock = AsyncMock(return_value=("read_only", "purely informational"))
    monkeypatch.setattr("core.turn.local_shell._classify_intent_via_llm", classifier_mock)
    monkeypatch.setattr(
        "core.turn.local_shell._resolve_autonomy",
        AsyncMock(return_value={
            "allow_silent_low_risk_actions": True,
            "allow_silent_beneficial_actions": True,
            "allow_privileged_cli_actions": False,
            "require_confirmation_for_destructive_actions": True,
        }),
    )
    monkeypatch.setattr(
        "core.turn.local_shell._run_blocking",
        lambda args, **kwargs: (0, "out", ""),
    )
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = True
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "bash"
        mock_config.LOCAL_SHELL_TIMEOUT_SECONDS = 5.0
        mock_config.LOCAL_SHELL_MAX_OUTPUT_CHARS = 1000
        result = await execute_local_shell(
            command="some_made_up_thing --flag",
            interpreter="bash",
            owner_user_id=1,
        )
    classifier_mock.assert_awaited_once()
    assert result.allowed is True


@pytest.mark.asyncio
async def test_execute_local_shell_classifier_unsafe_blocks(monkeypatch):
    monkeypatch.setattr(
        "core.turn.local_shell._classify_intent_via_llm",
        AsyncMock(return_value=("unsafe", "rm -rf /")),
    )
    monkeypatch.setattr(
        "core.turn.local_shell._resolve_autonomy",
        AsyncMock(return_value={
            "allow_silent_low_risk_actions": True,
            "allow_silent_beneficial_actions": True,
            "allow_privileged_cli_actions": True,
            "require_confirmation_for_destructive_actions": False,
        }),
    )
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = True
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "bash"
        result = await execute_local_shell(
            command="rm -rf / --no-preserve-root",
            interpreter="bash",
            owner_user_id=1,
        )
    assert result.allowed is False
    assert "unsafe" in result.refused_reason


# ---------------------------------------------------------------------------
# Platform default — the Windows-interpreter-on-Linux regression (2026-07-10).
# LOCAL_SHELL_DEFAULT_INTERPRETER historically defaulted to "powershell", so on
# any POSIX host local_shell tried to spawn powershell.exe and failed (and the
# stray `2>nul` litter it produced was band-aided into .gitignore). The default
# is now "auto" — resolve to the host's native shell.
# ---------------------------------------------------------------------------


def test_default_interpreter_is_unix_shell_on_posix():
    """'auto' must resolve to a Unix shell on a POSIX host, powershell on nt."""
    from core.turn import local_shell as ls

    with patch.object(ls, "config") as mock_config:
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "auto"
        interp = ls._get_default_interpreter()
    if os.name == "nt":
        assert interp == "powershell"
    else:
        assert interp in {"bash", "sh"}


def test_build_subprocess_args_defaults_to_unix_shell_on_posix():
    """An unrecognized/auto interpreter falls back to the host-native shell —
    bash/sh on POSIX, never powershell.exe."""
    args, _ = _build_subprocess_args(command="echo hi", interpreter="auto")
    if os.name == "nt":
        assert args[0].lower().startswith("powershell")
    else:
        assert args[0] in {"bash", "sh"}


@pytest.mark.asyncio
async def test_execute_local_shell_default_interpreter_runs_on_host(monkeypatch):
    """With the host-default interpreter, an obvious read-only command actually
    executes on this (POSIX) host and returns real output — no LLM in the path."""
    monkeypatch.setattr(
        "core.turn.local_shell._resolve_autonomy",
        AsyncMock(return_value={
            "allow_silent_low_risk_actions": True,
            "allow_silent_beneficial_actions": True,
            "allow_privileged_cli_actions": True,
            "require_confirmation_for_destructive_actions": False,
        }),
    )
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = True
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "auto"
        mock_config.LOCAL_SHELL_TIMEOUT_SECONDS = 15.0
        mock_config.LOCAL_SHELL_MAX_OUTPUT_CHARS = 4000
        result = await execute_local_shell(
            command="echo palli_linux_ok",
            interpreter=None,   # exercise the host-default resolution
            owner_user_id=1,
        )
    assert result.allowed is True
    assert result.return_code == 0, f"stderr={result.stderr!r}"
    assert "palli_linux_ok" in result.stdout

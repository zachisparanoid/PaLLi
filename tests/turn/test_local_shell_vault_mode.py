"""Batch 4, Task 11 (controller ruling R16): her vault saves keep the vault
file private.

Batch 1 found that pykeepass's save leaves ~/.palli/vault/credentials.kdbx at
mode 664, and "every save by her tools will do the same". Her saves are shell
commands (`flock ~/.palli/vault/.lock python3 … kp.save()`) that the
vault-save detector already recognizes, so after such a command finishes —
worked or not — the file is set back to 600. Best effort: a chmod that fails
is logged and never breaks her turn.

Both shell paths are covered: her chat tool (LocalShellTool, the path her
real saves take) and execute_local_shell (the proactive path). Nothing runs:
the subprocess is a fake that leaves the file at 664 the way a save does, and
the vault is a temp folder (core.secrets_redaction.VAULT_DIR points at it).
"""
from __future__ import annotations

import asyncio
import logging
import os
import stat
from unittest.mock import AsyncMock, patch

import pytest

import core.secrets_redaction as sr
from core.agent.builtin_tools.local_shell import LocalShellTool
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext
from core.turn import local_shell as ls

LOCKED_SAVE = (
    "flock ~/.palli/vault/.lock python3 - <<'EOF'\n"
    "from pykeepass import PyKeePass\n"
    "kp = PyKeePass('credentials.kdbx', password=pw, keyfile='k')\n"
    "kp.save()\n"
    "EOF"
)


@pytest.fixture
def kdbx(tmp_path, monkeypatch):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(mode=0o700)
    path = vault_dir / "credentials.kdbx"
    path.write_bytes(b"stand-in, not a vault")
    os.chmod(path, 0o600)
    monkeypatch.setattr(sr, "VAULT_DIR", vault_dir)
    return path


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _loosen(path):
    """What pykeepass's save does to the file."""
    os.chmod(path, 0o664)


# --- the helper ------------------------------------------------------------


def test_the_helper_puts_a_loose_vault_file_back_to_600_and_logs_it(kdbx, caplog):
    _loosen(kdbx)
    caplog.set_level(logging.INFO, logger="core.turn.local_shell")

    ls.keep_vault_file_private()

    assert _mode(kdbx) == 0o600
    assert any("664" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


def test_the_helper_never_raises(kdbx, monkeypatch, caplog):
    _loosen(kdbx)
    caplog.set_level(logging.WARNING, logger="core.turn.local_shell")

    def _refuse(path, mode):
        raise PermissionError(1, "Operation not permitted")

    with monkeypatch.context() as patched:
        patched.setattr(ls.os, "chmod", _refuse)
        ls.keep_vault_file_private()  # must not raise

    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_the_helper_with_no_vault_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "VAULT_DIR", tmp_path / "no-vault")
    ls.keep_vault_file_private()
    assert not (tmp_path / "no-vault").exists()


# --- her chat tool (the path her real saves take) ---------------------------


def _fake_shell(kdbx, *, returncode=0, loosen=True):
    async def _create(command, **kwargs):
        class _Proc:
            pass

        proc = _Proc()
        proc.returncode = returncode

        async def communicate():
            if loosen:
                _loosen(kdbx)  # the save ran
            return (b"saved\n", None)

        proc.communicate = communicate
        return proc

    return _create


def _ctx() -> ToolContext:
    return ToolContext(turn_owner_id=1, is_owner=True)


@pytest.mark.parametrize("returncode", [0, 1])
def test_her_chat_tool_leaves_the_vault_file_600_after_a_save(kdbx, monkeypatch, returncode):
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_shell(kdbx, returncode=returncode))

    result = asyncio.run(LocalShellTool().execute({"command": LOCKED_SAVE}, _ctx()))

    assert _mode(kdbx) == 0o600
    assert isinstance(result, ToolFailure) == bool(returncode)
    assert "saved" in str(result)


def test_her_chat_tool_leaves_other_commands_alone(kdbx, monkeypatch):
    """Only a command the vault-save detector matches is followed by a chmod."""
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_shell(kdbx))

    asyncio.run(LocalShellTool().execute({"command": "echo hello"}, _ctx()))

    assert _mode(kdbx) == 0o664


def test_her_chat_tool_turn_survives_a_chmod_that_fails(kdbx, monkeypatch):
    _loosen(kdbx)  # the save already ran; the chmod after it is what fails
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_shell(kdbx, loosen=False))

    def _refuse(path, mode):
        raise PermissionError(1, "Operation not permitted")

    with monkeypatch.context() as patched:
        patched.setattr(ls.os, "chmod", _refuse)
        result = asyncio.run(LocalShellTool().execute({"command": LOCKED_SAVE}, _ctx()))

    assert not isinstance(result, ToolFailure)
    assert "saved" in str(result)
    assert _mode(kdbx) == 0o664  # nothing changed, and nothing raised


# --- execute_local_shell (the proactive path) -------------------------------


_PERMISSIVE = {
    "allow_silent_low_risk_actions": True,
    "allow_silent_beneficial_actions": True,
    "allow_privileged_cli_actions": True,
    "require_confirmation_for_destructive_actions": False,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("rc", [0, 1])
async def test_execute_local_shell_leaves_the_vault_file_600_after_a_save(kdbx, monkeypatch, rc):
    async def _run(args, **kwargs):
        _loosen(kdbx)  # the save ran
        return (rc, "saved", "")

    monkeypatch.setattr(ls, "_classify_intent_via_llm", AsyncMock(return_value=("mutating", "test")))
    monkeypatch.setattr(ls, "_resolve_autonomy", AsyncMock(return_value=dict(_PERMISSIVE)))
    monkeypatch.setattr(ls, "_run_with_cancellation", _run)
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = True
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "bash"
        mock_config.LOCAL_SHELL_TIMEOUT_SECONDS = 30.0
        mock_config.LOCAL_SHELL_MAX_OUTPUT_CHARS = 8000
        result = await ls.execute_local_shell(command=LOCKED_SAVE, interpreter="bash", owner_user_id=1)

    assert result.allowed is True and result.return_code == rc
    assert _mode(kdbx) == 0o600

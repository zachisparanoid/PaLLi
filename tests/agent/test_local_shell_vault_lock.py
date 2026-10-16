"""LocalShellTool vault-lock guard (fix round 1, batch review, 2026-09-25).

Round 1 review found the lock rule had landed on the wrong path: it only
guarded ``core.turn.local_shell.execute_local_shell``, which has exactly one
caller — the proactive path. Her LIVE chat tool is
``LocalShellTool.execute`` in ``core.agent.builtin_tools.local_shell``,
which calls ``asyncio.create_subprocess_shell`` directly, and the
2026-09-24 vault race was two of THOSE calls racing each other. The same
deterministic ``vault_write_without_lock`` check now runs here too, before
the destructive-command guard and before any subprocess is spawned.
"""
from __future__ import annotations

import asyncio

from core.agent.builtin_tools.local_shell import LocalShellTool
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext
from core.turn.local_shell import VAULT_LOCK_REFUSAL

_UNLOCKED_SAVE = (
    "python3 - <<'EOF'\n"
    "from pykeepass import PyKeePass\n"
    "kp = PyKeePass('v.kdbx', password=pw, keyfile='k')\n"
    "kp.save()\n"
    "EOF"
)


def _ctx() -> ToolContext:
    return ToolContext(turn_owner_id=1, is_owner=True)


def test_execute_refuses_unlocked_vault_save_without_spawning_a_subprocess(monkeypatch):
    async def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError(
            "no subprocess should be spawned for an unlocked vault write"
        )

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _must_not_be_called)

    tool = LocalShellTool()
    result = asyncio.run(tool.execute({"command": _UNLOCKED_SAVE}, _ctx()))
    assert isinstance(result, ToolFailure)
    assert str(result) == VAULT_LOCK_REFUSAL


def test_execute_still_runs_a_locked_vault_save(monkeypatch):
    """The tightened check must not block a properly-locked save — confirm
    the tool still reaches the subprocess call for one."""
    called = {}

    async def _fake_create_subprocess_shell(command, **kwargs):
        called["command"] = command

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return (b"exit=0\n", None)

        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_create_subprocess_shell)

    locked_cmd = (
        "flock ~/.palli/vault/.lock python3 - <<'EOF'\n"
        "from pykeepass import PyKeePass\n"
        "kp = PyKeePass('v.kdbx', password=pw, keyfile='k')\n"
        "kp.save()\n"
        "EOF"
    )
    tool = LocalShellTool()
    result = asyncio.run(tool.execute({"command": locked_cmd}, _ctx()))
    assert called.get("command") == locked_cmd
    assert not isinstance(result, ToolFailure)


# --- Final fix wave (2026-09-26), item 1: the refusal log line -------------
# Her tool INPUT carries put-back real values (payload masking swaps them
# back so the command can work), so a log line that prints the command
# prints the secret into logs/. The line prints the command MASKED instead.
# Both values are fake.
_FAKE_KEY = "gsk_" + "Fake0Key1" * 3
_FAKE_SSN = "123-" + "45-" + "6789"


def test_the_refusal_log_line_never_prints_a_secret_the_command_carried(monkeypatch, caplog):
    import logging

    async def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError("no subprocess should be spawned for an unlocked vault write")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _must_not_be_called)
    caplog.set_level(logging.WARNING, logger="core.agent.builtin_tools.local_shell")
    command = f"KEY={_FAKE_KEY} SSN={_FAKE_SSN} " + _UNLOCKED_SAVE

    result = asyncio.run(LocalShellTool().execute({"command": command}, _ctx()))

    assert isinstance(result, ToolFailure)
    refusals = [r for r in caplog.records if "vault write refused" in r.getMessage()]
    assert refusals, caplog.text
    for record in refusals:
        assert _FAKE_KEY not in record.getMessage()
        assert _FAKE_SSN not in record.getMessage()
        assert "[MASKED:" in record.getMessage()
    assert _FAKE_KEY not in caplog.text and _FAKE_SSN not in caplog.text

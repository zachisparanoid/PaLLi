"""Task 7 (Batch 1, 2026-09-25): a vault save that isn't run under flock is
refused, with the reason.

2026-09-24: two of her saves ran at once — no lock serialized them — and the
vault was destroyed (four entries lost, see the parallel-save-race memory
note). ``vault_write_without_lock`` is the deterministic rule; it runs
*before* the LLM classifier so the refusal never depends on a judgment call.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.turn.local_shell import (
    VAULT_LOCK_REFUSAL,
    execute_local_shell,
    vault_write_without_lock,
)


def test_a_vault_save_without_flock_is_refused():
    cmd = (
        "python3 - <<'EOF'\n"
        "from pykeepass import PyKeePass\n"
        "kp = PyKeePass('v.kdbx', password=pw, keyfile='k')\n"
        "kp.save()\n"
        "EOF"
    )
    assert vault_write_without_lock(cmd) is True


def test_reads_and_locked_writes_pass():
    assert vault_write_without_lock(
        "python3 -c \"from pykeepass import PyKeePass; kp=PyKeePass('v'); print(len(kp.entries))\""
    ) is False
    assert vault_write_without_lock("flock ~/.palli/vault/.lock python3 save_entry.py") is False


def test_wrong_lock_file_is_still_refused():
    """Fix round 1 (Important, T7): the bare word 'flock' used to be enough
    to pass, even locking some unrelated file. `flock /tmp/x ...` sailed
    through review — the lock must name the vault's own lock file."""
    cmd = (
        "flock /tmp/x python3 - <<'EOF'\n"
        "from pykeepass import PyKeePass\n"
        "kp = PyKeePass('v.kdbx', password=pw, keyfile='k')\n"
        "kp.save()\n"
        "EOF"
    )
    assert vault_write_without_lock(cmd) is True


def test_real_vault_lock_path_passes():
    """The positive case the tightened check must still let through: flock
    on the actual vault lock file, any spelling of the path."""
    for lock_prefix in ("~", "$HOME", "/home/ubuntu"):
        cmd = (
            f"flock {lock_prefix}/.palli/vault/.lock python3 - <<'EOF'\n"
            "from pykeepass import PyKeePass\n"
            "kp = PyKeePass('v.kdbx', password=pw, keyfile='k')\n"
            "kp.save()\n"
            "EOF"
        )
        assert vault_write_without_lock(cmd) is False, lock_prefix


def test_inscript_fcntl_flock_passes():
    """The other accepted shape: the script locks the vault file itself in
    Python rather than the shell wrapping it in `flock ...`."""
    cmd = (
        "python3 - <<'EOF'\n"
        "import fcntl\n"
        "from pykeepass import PyKeePass\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "kp = PyKeePass('v.kdbx', password=pw, keyfile='k')\n"
        "kp.save()\n"
        "EOF"
    )
    assert vault_write_without_lock(cmd) is False


@pytest.mark.asyncio
async def test_execute_local_shell_refuses_unlocked_vault_write_without_calling_classifier(monkeypatch):
    """The refusal happens before the LLM classifier is ever reached — this
    is a deterministic gate, not a judgment call the classifier could
    override."""
    classifier_mock = AsyncMock(
        side_effect=AssertionError(
            "the LLM classifier must not be called for an unlocked vault write"
        )
    )
    monkeypatch.setattr("core.turn.local_shell._classify_intent_via_llm", classifier_mock)
    cmd = (
        "python3 - <<'EOF'\n"
        "from pykeepass import PyKeePass\n"
        "kp = PyKeePass('v.kdbx', password=pw, keyfile='k')\n"
        "kp.save()\n"
        "EOF"
    )
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = True
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "bash"
        result = await execute_local_shell(
            command=cmd,
            interpreter="bash",
            owner_user_id=1,
        )
    classifier_mock.assert_not_called()
    assert result.allowed is False
    assert result.intent_class == "vault_write_unlocked"
    assert result.refused_reason == VAULT_LOCK_REFUSAL
    assert "flock ~/.palli/vault/.lock" in result.refused_reason
    assert "2026-09-24" in result.refused_reason


# --- Final fix wave (2026-09-26), item 1: command log lines ---------------
# The command reaching this executor carries put-back real values, so both
# log lines that print it (the vault refusal and the pre-existing
# "local_shell: command=..." info line) print it MASKED. Both values fake.
_FAKE_KEY = "gsk_" + "Fake0Key1" * 3
_FAKE_SSN = "123-" + "45-" + "6789"


def _assert_no_secret_logged(caplog, marker: str) -> None:
    lines = [r for r in caplog.records if marker in r.getMessage()]
    assert lines, caplog.text
    for record in lines:
        assert _FAKE_KEY not in record.getMessage()
        assert _FAKE_SSN not in record.getMessage()
        assert "[MASKED:" in record.getMessage()
    assert _FAKE_KEY not in caplog.text and _FAKE_SSN not in caplog.text


@pytest.mark.asyncio
async def test_the_vault_refusal_log_line_prints_the_command_masked(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(
        "core.turn.local_shell._classify_intent_via_llm",
        AsyncMock(side_effect=AssertionError("classifier must not run")),
    )
    caplog.set_level(logging.INFO, logger="core.turn.local_shell")
    cmd = (
        f"KEY={_FAKE_KEY} SSN={_FAKE_SSN} python3 - <<'EOF'\n"
        "from pykeepass import PyKeePass\n"
        "kp = PyKeePass('v.kdbx', password=pw, keyfile='k')\n"
        "kp.save()\n"
        "EOF"
    )
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = True
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "bash"
        result = await execute_local_shell(command=cmd, interpreter="bash", owner_user_id=1)
    assert result.intent_class == "vault_write_unlocked"
    _assert_no_secret_logged(caplog, "vault write refused")


@pytest.mark.asyncio
async def test_the_command_info_log_line_prints_the_command_masked(monkeypatch, caplog):
    import logging

    # The classifier calls it unsafe, so it is refused after the info line —
    # nothing is ever spawned.
    monkeypatch.setattr(
        "core.turn.local_shell._classify_intent_via_llm",
        AsyncMock(return_value=("unsafe", "test")),
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
    caplog.set_level(logging.INFO, logger="core.turn.local_shell")
    cmd = f"curl -H 'Authorization: {_FAKE_KEY}' https://example.invalid/?ssn={_FAKE_SSN}"
    with patch("core.turn.local_shell.config") as mock_config:
        mock_config.LOCAL_SHELL_ENABLED = True
        mock_config.LOCAL_SHELL_DEFAULT_INTERPRETER = "bash"
        result = await execute_local_shell(command=cmd, interpreter="bash", owner_user_id=1)
    assert result.allowed is False
    _assert_no_secret_logged(caplog, "local_shell: command=")

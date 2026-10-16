# tests/agent/test_local_shell_guard.py
"""Destructive-command guard on the live local_shell tool (2026-06-11).

The guard must refuse system-destroying commands while leaving normal
operator work (including recursive deletes inside project dirs) untouched.
"""
from __future__ import annotations

import asyncio


def _load(name):
    from tests.agent.conftest import load_module
    return load_module(f"core/agent/builtin_tools/{name}.py", f"{name}_guard_under_test")


def _ctx():
    tools_mod = __import__("tests.agent.conftest", fromlist=["load_module"]).load_module(
        "core/agent/tools.py", "tools_for_guard_test"
    )
    return tools_mod.ToolContext(turn_owner_id=1, is_owner=True)


BLOCKED = [
    "rm -rf /",
    "rm -rf /*",
    "sudo rm -rf /",
    "rm -r ~",
    "rm -rf $HOME",
    "rm -rf /etc",
    "rm -rf /usr/",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda bs=1M",
    "echo junk > /dev/sda",
    "reboot",
    "sudo shutdown -h now",
    "poweroff",
    ":(){ :|:& };:",
    "chmod -R 777 /",
]

ALLOWED = [
    "echo hello",
    "ls /",
    "rm -rf /tmp/scratch",
    "rm -rf ./node_modules",
    "rm file.txt",
    "dd if=/dev/zero of=/tmp/disk.img bs=1M count=1",
    "git push origin main",
    "grep -R 'pattern' /home/ubuntu/projects",
    "cat /etc/hostname",
]


def test_blocked_reason_catches_destructive_commands():
    mod = _load("local_shell")
    for cmd in BLOCKED:
        assert mod._blocked_reason(cmd) is not None, f"should block: {cmd!r}"


def test_blocked_reason_allows_normal_work():
    mod = _load("local_shell")
    for cmd in ALLOWED:
        assert mod._blocked_reason(cmd) is None, f"should allow: {cmd!r}"


def test_execute_refuses_blocked_command_without_running_it():
    mod = _load("local_shell")
    tool = mod.LocalShellTool()
    out = str(asyncio.run(tool.execute({"command": "rm -rf /"}, _ctx())))
    assert "blocked by safety guard" in out
    assert "exit=" not in out  # never reached the subprocess


def test_a_blocked_command_is_a_failure_not_a_success():
    """Week 2 task 1 (review fix): a refusal is a call that ran and did not
    work — the same rule read_file and deliver_artifact already follow — so the
    guard returns ToolFailure and the dispatcher marks the result is_error.
    The wording is unchanged; only the type is."""
    from core.agent.tool_outcome import ToolFailure

    mod = _load("local_shell")
    raw = asyncio.run(mod.LocalShellTool().execute({"command": "rm -rf /"}, _ctx()))
    assert isinstance(raw, ToolFailure)
    text = str(raw)
    assert text.startswith("blocked by safety guard (recursive/forced delete")
    assert "rm -rf /" in text
    assert text.endswith("terminal (or set LOCAL_SHELL_GUARD_ENABLED=false).")


def test_execute_still_runs_safe_commands():
    mod = _load("local_shell")
    tool = mod.LocalShellTool()
    out = asyncio.run(tool.execute({"command": "echo guard-ok"}, _ctx()))
    assert "guard-ok" in out

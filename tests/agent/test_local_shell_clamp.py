"""local_shell's timeout is clamped, and a timed-out command takes its whole
process group with it (stranger-proofing week 1, task 2).

Two holes, one fix. The model chose ``timeout_seconds`` with no ceiling, so a
single command could hold a turn open for as long as it liked. And on timeout
the tool called ``proc.kill()``, which kills the shell she started and nothing
the shell started — the grandchildren outlived the session and kept running
(and kept spending) with nobody watching. ``workbench_shell`` already did this
right: its own session, and ``killpg`` on the way out.

Premise updated 2026-09-10 (week 2, task 1): a timed-out command is a command
that ran and did not work, so the branch returns ``ToolFailure`` — the same
wording, read through ``str()`` here, plus the is_error the dispatcher needs.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time

# A duration nothing else on the box is plausibly running, so pgrep cannot
# match an innocent bystander. This is the plan's ``psutil_like_children()``
# placeholder, written with pgrep so the suite needs no new dependency.
_SLEEP = "31.7"


def _marker_pids() -> list[int]:
    """pids whose command line mentions our marker sleep (empty list = none).

    A killed process is a zombie until reaped and a zombie has no command
    line, so pgrep -f cannot match one: an empty list really means gone.
    """
    proc = subprocess.run(
        ["pgrep", "-f", f"sleep {_SLEEP}"], capture_output=True, text=True,
    )
    return [int(tok) for tok in proc.stdout.split() if tok.strip().isdigit()]


def test_clamp_pins_the_models_timeout_into_the_configured_band():
    from core.agent.builtin_tools.local_shell import clamp_timeout

    assert clamp_timeout(30) == 30
    assert clamp_timeout(0) == 1
    assert clamp_timeout(-5) == 1
    assert clamp_timeout(10 ** 6) == 300
    assert clamp_timeout(None) == 30          # absent → the schema's default
    assert clamp_timeout("nonsense") == 30


def test_clamp_follows_the_configured_ceiling(monkeypatch):
    from core.config import config
    from core.agent.builtin_tools.local_shell import clamp_timeout

    monkeypatch.setattr(config, "LOCAL_SHELL_MAX_TIMEOUT_SECONDS", 5, raising=False)
    assert clamp_timeout(10 ** 6) == 5
    assert clamp_timeout(3) == 3


def test_a_timed_out_command_takes_its_whole_process_group_with_it():
    from core.agent.builtin_tools.local_shell import LocalShellTool
    from core.agent.tool_outcome import ToolFailure

    assert not _marker_pids(), "a stale marker process is already running"
    tool = LocalShellTool()
    try:
        raw = asyncio.run(
            tool.execute(
                {"command": f"sh -c 'sleep {_SLEEP} & sleep {_SLEEP}'",
                 "timeout_seconds": 1},
                None,
            )
        )
        # The TYPE, not only the wording: a plain f-string here would read as a
        # success at dispatch, and str() alone would not notice the reversion.
        assert isinstance(raw, ToolFailure)
        out = str(raw)
        assert "timed out" in out
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and _marker_pids():
            time.sleep(0.05)
        assert _marker_pids() == [], "the backgrounded child outlived the tool"
    finally:
        for pid in _marker_pids():              # never leak a process on failure
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_a_child_that_will_not_die_still_returns_within_the_reap_bound(monkeypatch):
    """Review round 1: the reap must be bounded.

    A process in uninterruptible sleep (a hung mount) survives SIGKILL, and an
    unbounded ``await proc.wait()`` after the group kill would hold the turn
    open forever — the exact stall this branch exists to end. She returns,
    keeps the "timed out" wording, and says she could not confirm the kill.
    """
    from core.agent.builtin_tools import local_shell as mod

    class _Undying:
        pid = 424242
        returncode = None

        async def communicate(self):
            await asyncio.Event().wait()       # never settles → the timeout fires

        async def wait(self):
            await asyncio.Event().wait()       # never dies → the reap must bound

        def kill(self):
            pass

    killed: list = []
    monkeypatch.setattr(mod, "_kill_process_group", killed.append)
    monkeypatch.setattr(mod, "_REAP_TIMEOUT_SECONDS", 0.2)

    async def fake_shell(*args, **kwargs):
        return _Undying()

    monkeypatch.setattr(mod.asyncio, "create_subprocess_shell", fake_shell)

    async def _drive():
        # The outer wait_for is the safety net: a regression to an unbounded
        # reap would HANG this test forever instead of failing it, and a test
        # that can hang is not a test.
        return await asyncio.wait_for(
            mod.LocalShellTool().execute({"command": "hang", "timeout_seconds": 1}, None),
            timeout=15,
        )

    started = time.monotonic()
    out = str(asyncio.run(_drive()))
    elapsed = time.monotonic() - started

    assert "timed out" in out
    assert "could not confirm the process group died" in out
    assert killed                              # she did try to kill it
    assert elapsed < 5, f"the reap was not bounded ({elapsed:.1f}s)"


def test_an_ordinary_command_still_runs_and_reports_its_exit_code():
    from core.agent.builtin_tools.local_shell import LocalShellTool

    out = asyncio.run(LocalShellTool().execute({"command": "echo hello"}, None))
    assert isinstance(out, str)               # exit 0 is a plain success string
    assert "exit=0" in out
    assert "hello" in out

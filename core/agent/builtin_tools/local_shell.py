from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
from typing import Any

from core.agent.payload_masking import mask_text
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext
from core.turn.local_shell import (
    VAULT_LOCK_REFUSAL,
    keep_vault_file_private,
    saves_the_vault,
    vault_write_without_lock,
)

logger = logging.getLogger(__name__)


# Destructive-command guard (2026-06-11). local_shell is owner-gated and
# auto-approved, and PaLLi can act on its own initiative (proactive turns) —
# so a thin last line of defense against catastrophic commands is warranted.
# Deliberately SURGICAL: only commands that destroy the system or mass-delete
# roots are refused; everything else (including rm -rf on project subdirs)
# stays unrestricted. Kill switch: LOCAL_SHELL_GUARD_ENABLED=false.
_ROOTISH_TARGET = (
    r"(?:/|~|\$HOME|/\*|~/\*?"
    r"|/(?:etc|usr|var|boot|bin|sbin|lib|lib64|home|root|opt|srv)/?\*?)"
)
_GUARD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\brm\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*\s+|--recursive\s+|-[a-zA-Z]+\s+)*"
            + _ROOTISH_TARGET + r"\s*(?:$|[;&|])",
        ),
        "recursive/forced delete of a root or system directory",
    ),
    (re.compile(r"\bmkfs(?:\.\w+)?\b"), "filesystem format (mkfs)"),
    (re.compile(r"\bdd\b[^|;&]*\bof=/dev/"), "raw write to a block device (dd)"),
    (re.compile(r">\s*/dev/(?:sd|nvme|vd|hd)"), "redirect onto a block device"),
    (
        re.compile(r"^\s*(?:sudo\s+)?(?:shutdown|reboot|poweroff|halt)\b"),
        "host shutdown/reboot (would also kill PaLLi itself)",
    ),
    (re.compile(r":\s*\(\s*\)\s*\{[^}]*\}\s*;?\s*:"), "fork bomb"),
    (
        re.compile(r"\b(?:chmod|chown)\b[^|;&]*-[a-zA-Z]*R[a-zA-Z]*[^|;&]*\s/(?:\s|$)"),
        "recursive permission change on /",
    ),
)


def _blocked_reason(command: str) -> str | None:
    """Return a human-readable reason if the command matches the guard, else None."""
    for pattern, reason in _GUARD_PATTERNS:
        if pattern.search(command):
            return reason
    return None


def clamp_timeout(value: Any) -> int:
    """The model's timeout_seconds, pinned into [1, the configured ceiling].

    Stranger-proofing week 1 (task 2): the schema advertised a default of 30
    and accepted anything, so one command could hold a turn open for as long
    as the model felt like asking for. Junk (or nothing) reads as the
    schema's 30; the ceiling is LOCAL_SHELL_MAX_TIMEOUT_SECONDS.
    """
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 30
    try:
        from core.config import config
        ceiling = int(getattr(config, "LOCAL_SHELL_MAX_TIMEOUT_SECONDS", 300))
    except Exception:  # noqa: BLE001 — a broken config never lifts the ceiling
        ceiling = 300
    ceiling = max(1, ceiling)
    return max(1, min(seconds, ceiling))


# How long to wait for the killed group to actually die before giving up and
# telling the truth about it. A process in uninterruptible sleep (a hung NFS
# mount) never dies, and an unbounded reap would hold the turn open forever —
# the exact stall the timeout branch exists to end.
_REAP_TIMEOUT_SECONDS = 5.0


def _kill_process_group(proc: "asyncio.subprocess.Process") -> None:
    """Kill the child AND everything it started.

    proc.kill() reached only the shell she launched; anything that shell
    backgrounded outlived the turn with nobody watching it. With
    start_new_session=True the child is its own process-group leader, so its
    pgid is its pid — the same shape workbench_shell has used since §4c.
    """
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except Exception:  # noqa: BLE001 — fall through to the single-process kill
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 — already gone
        pass


class LocalShellTool:
    name = "local_shell"
    description = "Run a shell command. Use cmd on Windows, bash/sh on Unix. Returns combined stdout+stderr."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command line to execute."},
            "timeout_seconds": {"type": "integer", "default": 30},
        },
        "required": ["command"],
    }
    # D.4 step 3: gate local_shell behind an explicit approval hook in
    # addition to owner_only. The owner_only flag already blocks
    # non-owner *surfaces*, but the approval hook adds a second layer
    # — when an approval hook is wired (dispatch.py does this for
    # ReAct mode), each invocation runs through it. The default hook
    # auto-approves for the resolved owner; future work can swap in a
    # user-prompting approval flow for sensitive commands. Pre-D.4
    # local_shell had requires_approval=False AND no dispatch-side
    # hook wiring, so the security hook at tools.py:141 was inert.
    requires_approval = True
    owner_only = True  # operator capability

    async def execute(
        self, input: dict[str, Any], ctx: ToolContext,
    ) -> str | ToolFailure:
        command = input["command"]
        if vault_write_without_lock(command):
            # The command is her tool INPUT, where masked values were put
            # back (final fix wave, 2026-09-26): log it masked, never raw.
            logger.warning("local_shell: vault write refused (no lock): command=%r", mask_text(command)[:120])
            return ToolFailure(VAULT_LOCK_REFUSAL)
        try:
            from core.config import config
            guard_enabled = bool(getattr(config, "LOCAL_SHELL_GUARD_ENABLED", True))
        except Exception:
            guard_enabled = True
        if guard_enabled:
            reason = _blocked_reason(command)
            if reason is not None:
                # A refusal is a call that ran and did not work (week 2, task 1),
                # so it carries the flag as well as the wording — which is
                # unchanged.
                return ToolFailure(
                    f"blocked by safety guard ({reason}): {command}\n"
                    "This class of command is refused automatically. If the owner "
                    "genuinely wants this, they should run it themselves in a "
                    "terminal (or set LOCAL_SHELL_GUARD_ENABLED=false)."
                )
        timeout = clamp_timeout(input.get("timeout_seconds", 30))
        saves_vault = saves_the_vault(command)
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,   # its own process group — killable as a unit
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            # A non-zero exit is a command that ran and did not work, so it
            # comes back as a failure (week 2, task 1). Same text either way.
            output = f"exit={proc.returncode}\n{stdout.decode('utf-8', errors='replace')}"
            return output if proc.returncode == 0 else ToolFailure(output)
        except asyncio.TimeoutError:
            confirmed_dead = True
            if proc is not None:
                _kill_process_group(proc)
                try:
                    # Reap it so no zombie is left behind — but BOUNDED: this
                    # branch must always return, even when the child cannot die.
                    await asyncio.wait_for(proc.wait(), timeout=_REAP_TIMEOUT_SECONDS)
                except Exception:  # noqa: BLE001 — it did not go; say so below
                    confirmed_dead = False
            note = "" if confirmed_dead else (
                "\n(could not confirm the process group died — something it "
                "started may still be running)"
            )
            return ToolFailure(f"command timed out after {timeout}s: {command}{note}")
        except Exception as exc:
            return ToolFailure(f"command failed: {type(exc).__name__}: {exc}")
        finally:
            # Batch 4 (R16): her vault saves run here, and pykeepass leaves
            # the file at 664. Worked, failed or timed out, it goes back to
            # 600; the helper never raises, so her result is unchanged.
            if saves_vault:
                keep_vault_file_private()

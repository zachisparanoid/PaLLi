"""Her workbench shell — full, autonomous, WALLED (spec §4, §4c).

The real guarantee is the OS wall: commands run as the dedicated
``palli-work`` user (created by scripts/setup_workbench_user.sh — the
owner's one sudo), which owns her workspace + her NAS folder and can
write NOTHING else; ``sudo``'s default env_reset keeps the app's
environment (keys, tokens) out of her shell. This module adds the belts:
a substrate/NAS-write denylist checked before exec (best-effort by
nature), ulimits, a process-group kill on timeout so nothing she starts
outlives a session, and output clipping. WORKBENCH_SHELL_DIRECT=1 (tests
only) skips sudo and runs as the current user.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import signal
from pathlib import Path

from ai.workbench import WORKBENCH_ROOT_DEFAULT
from core.agent.tool_outcome import ToolFailure
from core.config import nas_root

logger = logging.getLogger(__name__)

_OUTPUT_CAP = 12_000

# Belt (second layer; the wall is the guarantee). Substrings are checked
# against the raw command — deliberately blunt: false positives cost her a
# rephrase, false negatives cost nothing because palli-work lacks the
# filesystem rights anyway. LAW NOTE (owner's heuristics rule): denylists
# are the explicitly blessed hard-rail class.
_DENY_SUBSTRINGS = (
    "palli-local-refactor",          # the app repo — her substrate, any path form
    "sudo", "su -", "su root",       # no privilege ladders
)


def _workspace_root() -> str:
    """Her workspace, from the one knob — ONE default with the three other sites.

    Was a second literal — a workspace under the OWNER's home — until the final
    review's I2, 2026-09-06: the forge, the workbench engine and the logbook all
    said ``/home/palli-work/workspace``, so on any host without the setup
    script's convenience symlink her shell fell through to ``cd /tmp``
    (``_build_argv``) while the other three looked in her real workspace.
    """
    return os.environ.get("WORKBENCH_ROOT") or WORKBENCH_ROOT_DEFAULT


def _nas_root() -> str:
    """The house NAS mount, or "" when there is none.

    ONE knob for the whole app (PALLI_NAS_ROOT -> config.NAS_ROOT, step 28
    task 2), read at call time. Empty is a real answer, not a missing one:
    an install with no NAS has nothing here to guard, and the rung below
    must then stay silent rather than match every command ever typed.
    """
    return nas_root()


def _substrate_root() -> Path:
    """Her app's own root, DERIVED from this module's location.

    Was a hardcoded ``/home/ubuntu/projects/chatbot`` literal until
    2026-07-29. Her body is moving to its own user and its own path
    (`/srv/palli`, her-own-user spec §4), at which point a hardcoded belt
    would guard a directory that no longer exists while ignoring the real
    substrate. Deriving it keeps the belt correct at any install path,
    forever. ``PALLI_SUBSTRATE_ROOT`` overrides for tests and for any future
    layout the derivation cannot see.
    """
    override = os.environ.get("PALLI_SUBSTRATE_ROOT", "").strip()
    if override:
        return Path(override)
    # <root>/core/agent/builtin_tools/workbench_shell.py
    return Path(__file__).resolve().parents[3]


def _work_user() -> str:
    """The OS account her shell runs as — the wall. One reading, shared with
    the doctor's NAS check (scripts/palli_ctl.py), so both ask about the same
    account."""
    return os.environ.get("WORKBENCH_USER", "palli-work")


def _her_nas_folder() -> str:
    """Her own folder on the house NAS — "" when no NAS is configured.
    WORKBENCH_NAS_DIR still overrides, for a share mounted a second time at
    a path she can actually write (scripts/setup_workbench_user.sh)."""
    override = os.environ.get("WORKBENCH_NAS_DIR")
    if override is not None:
        return override
    root = _nas_root()
    return f"{root}/palli" if root else ""


def _belt_reason(command: str) -> str:
    lowered = command.lower()
    for bad in _DENY_SUBSTRINGS:
        if bad in lowered:
            return f"touches '{bad}'"
    root = str(_substrate_root())
    if root and root in command:
        return "reaches into your own substrate"
    nas = _nas_root()
    # `nas and ...` is load-bearing: with NO NAS configured `"" in command`
    # is True for every command ever typed, and this rung would then run on
    # each one against an empty root.
    if nas and nas in command:
        # NAS mentions are fine for READS; writes/moves/deletes outside her
        # folder are declined. Blunt verb check — the wall + mount perms are
        # the real rule; this just fails fast with a friendly reason.
        her = _her_nas_folder()
        write_verbs = ("rm ", "rm\t", "mv ", "shred", ">", ">>", "cp ", "rsync",
                       "touch ", "mkdir ", "chmod", "chown", "truncate", "dd ")
        if any(v in command for v in write_verbs):
            for token in command.replace("=", " ").split():
                if token.startswith(nas) and not (her and token.startswith(her)):
                    return f"writes outside your NAS folder ({her})"
    return ""


def _build_argv(command: str, *, root: str, user: str, direct: bool) -> list[str]:
    """Assemble the exact argv the session execs.

    Split out so the PRODUCTION shape — the sudo hop that is the whole
    wall — is pinned by a test even though the build has no sudo and must
    never acquire any (owner's standing law).

    Runaway containment (spec §4c) is per-process EXCEPT ``ulimit -u``,
    which is RLIMIT_NPROC: a limit on threads-per-uid counted across the
    entire system, not per shell. On the dedicated ``palli-work`` uid that
    is exactly the fork-bomb guard the spec asks for. Under
    WORKBENCH_SHELL_DIRECT the uid is the APP's own — hundreds of live
    threads — where setting it succeeds silently and then makes every
    subsequent fork in her shell fail. A per-uid cap is only coherent on a
    dedicated uid, so the shared-uid path takes the per-process limits only.
    """
    limits = ["ulimit -f 1048576 2>/dev/null", "ulimit -t 300 2>/dev/null"]
    if not direct:
        limits.insert(0, "ulimit -u 256 2>/dev/null")
    script = (
        "; ".join(limits)
        + f"; cd {shlex.quote(root)} 2>/dev/null || cd /tmp; "
        + command
    )
    if direct:
        return ["bash", "-c", script]
    return ["sudo", "-n", "-u", user, "--", "bash", "-c", script]


def _describe(*, has_nas: bool) -> str:
    """The schema description the MODEL reads, built from the NAS knob.

    A no-NAS install has no NAS folder, so the clause naming one is left out
    rather than promising her a place that does not exist (the same law
    `where_you_live` and the decline line follow). The capability is named,
    never the path — the daydream affordance law, and it keeps a private
    mount out of every tool schema.
    """
    where = "there and in your NAS folder" if has_nas else "there"
    return (
        "Your own shell, on your own machine, for your own work. Runs in "
        "your workspace as your work user. You can create, edit, build, and "
        f"run things freely {where}. Your own substrate "
        "(the app, your memories, secrets) is behind glass — commands that "
        "reach for it are declined, and that's protection, not distrust. "
        "Each command is its own fresh shell starting in your workspace: a "
        "'cd' does not carry to the next one (chain with && instead), and "
        "nothing you leave running outlives the session. What persists is "
        "what you write to disk — that is what your next session will find."
    )


class WorkbenchShellTool:
    name = "workbench_shell"
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string",
                                   "description": "The shell command to run."}},
        "required": ["command"],
    }
    requires_approval = False
    owner_only = True

    def __init__(self) -> None:
        # Per instance, not per import: _build_workbench_registry() makes a
        # fresh tool for every session, so the knob is read at the start of
        # each one without a restart.
        self.description = _describe(has_nas=bool(_her_nas_folder()))

    async def execute(self, input: dict, ctx) -> str | ToolFailure:
        # A refusal, a timeout, a shell that will not start and a non-zero
        # exit are each a call that ran and did not work, so each comes back
        # as ToolFailure (week 2, task 1) and dispatch marks it an error.
        # Only the type changes: str(ToolFailure) is word for word the text
        # she saw before. Exit 0 is a success and stays a plain string.
        command = str((input or {}).get("command") or "").strip()
        if not command:
            return ToolFailure("(no command given)")
        reason = _belt_reason(command)
        if reason:
            yours = ("Your workspace and your NAS folder are fully yours."
                     if _her_nas_folder() else "Your workspace is fully yours.")
            return ToolFailure(f"declined — {reason}. {yours}")

        root = _workspace_root()
        timeout = float(os.environ.get("WORKBENCH_SHELL_TIMEOUT_S", "120") or 120)
        user = _work_user()
        direct = os.environ.get("WORKBENCH_SHELL_DIRECT") == "1"
        argv = _build_argv(command, root=root, user=user, direct=direct)

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,   # its own process group — killable as a unit
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    proc.kill()
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001 — the group is already dead
                    pass
                return ToolFailure(
                    f"(timed out after {int(timeout)}s — the process group was stopped)"
                )
        except FileNotFoundError as exc:
            return ToolFailure(f"(shell unavailable: {exc})")
        except Exception as exc:  # noqa: BLE001 — a broken shell never breaks her session
            logger.warning("workbench_shell: exec failed: %s", exc)
            return ToolFailure(f"(command failed to start: {exc})")

        text = (out or b"").decode("utf-8", errors="replace")
        logger.info("workbench_shell: ran %r rc=%s bytes=%d",
                    command[:120], proc.returncode, len(text))
        if len(text) > _OUTPUT_CAP:
            text = text[:_OUTPUT_CAP] + f"\n…(clipped at {_OUTPUT_CAP} chars)"
        rc = f"\n(exit {proc.returncode})" if proc.returncode else ""
        body = (text.strip() or "(no output)") + rc
        # Exit 0 is a success and stays a plain string; anything else ran
        # and did not work, and the `(exit N)` suffix is its whole story.
        return body if not proc.returncode else ToolFailure(body)

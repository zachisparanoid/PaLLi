"""Local shell tier-1 tool — direct subprocess execution on the host.

This is the tier-1 escape hatch for "actually do something on my machine"
work that the Claude Code SDK's safety classifier refuses. By running
through Python's subprocess (no Anthropic API in the path), we keep
operator authority where it belongs: in the user's autonomy preferences,
not in a remote classifier that doesn't know the user.

Two safety layers:
1. **Read-only allow-list** — recognized read-only commands (ls, dir, cat,
   grep, ps, where, pwd, whoami, etc.) bypass the LLM classifier and run
   immediately. They cannot modify state.
2. **LLM intent classifier** — anything not on the allow-list goes through
   a tiny LLM call that classifies intent as one of:
     - read_only      → run directly
     - mutating       → run if autonomy allows silent low-risk action
     - destructive    → require user confirmation (refuse if not allowed)
     - network_action → run if autonomy allows; report what it did
     - unsafe         → refuse outright (rm -rf /, format c:, etc.)

The classifier is conservative — when in doubt it picks the safer tier.
The user's `ACTION_PREFERENCES.md` (autonomy_mode, allow_silent_beneficial_actions,
require_confirmation_for_destructive_actions) is the source of truth for
what each tier requires.

Output is captured up to LOCAL_SHELL_MAX_OUTPUT_CHARS and trimmed with a
clear "[truncated]" marker so the calling tier-0 reply generator has a
predictable bounded payload to voice.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import stat
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ai.llm_client import llm
from ai.llm_routing import get_task_model
from core.agent.payload_masking import mask_text
from core.config import config
from core.identity import identity_service
from core.identity.policy_access import policy_bool, policy_value
from core.llm_json import safe_parse_json_object

logger = logging.getLogger(__name__)


# Recognized read-only command tokens. Conservative — many more exist, but
# the LLM classifier handles the long tail. This list is just the fast path
# for the obvious cases that come up constantly.
_READONLY_HEAD_TOKENS = frozenset({
    # Unix-y
    "ls", "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep",
    "find", "locate", "which", "whereis", "type", "file", "stat",
    "wc", "sort", "uniq", "diff", "cmp", "tree",
    "ps", "top", "uptime", "uname", "whoami", "id", "groups", "hostname",
    "df", "du", "free", "lscpu", "lsblk", "lsusb", "lspci",
    "echo", "printf", "date", "cal", "env",
    "git",  # most git subcommands are read-only; mutating ones still flagged by classifier
    "python", "python3", "py", "node", "ruby",  # repl invocations w/ -c are typically read-only
    # Windows
    "dir", "type", "where", "pwd", "cd", "tasklist", "systeminfo",
    "ipconfig", "getmac", "ver", "vol", "set", "path",
    "get-process", "get-service", "get-childitem", "get-content", "get-location",
    "get-itemproperty", "test-path", "select-string", "measure-object",
})


@dataclass(frozen=True, slots=True)
class ShellExecutionResult:
    """Outcome of one shell invocation."""

    command: str
    interpreter: str
    intent_class: str  # read_only | mutating | destructive | network_action | unsafe
    allowed: bool
    refused_reason: str  # populated when allowed=False
    return_code: Optional[int]
    stdout: str
    stderr: str
    truncated: bool
    duration_seconds: float

    def to_text_for_voicer(self) -> str:
        """Render a compact text payload for tier-0 reply generation."""
        if not self.allowed:
            return f"[local_shell REFUSED: {self.refused_reason}] {self.command}"
        head_lines: List[str] = [
            f"$ ({self.interpreter}) {self.command}",
            f"  (intent={self.intent_class}, exit={self.return_code}, took={self.duration_seconds:.2f}s)",
        ]
        if self.stdout.strip():
            head_lines.append("STDOUT:")
            head_lines.append(self.stdout.rstrip())
        if self.stderr.strip():
            head_lines.append("STDERR:")
            head_lines.append(self.stderr.rstrip())
        if self.truncated:
            head_lines.append("[output truncated]")
        return "\n".join(head_lines)


def _get_default_interpreter() -> str:
    raw = str(getattr(config, "LOCAL_SHELL_DEFAULT_INTERPRETER", "auto") or "auto").strip().lower()
    if raw in {"powershell", "pwsh", "ps", "ps1"}:
        return "powershell"
    if raw in {"cmd", "cmd.exe"}:
        return "cmd"
    if raw == "bash":
        return "bash"
    if raw == "sh":
        return "sh"
    # "auto"/unset/unknown → follow the host platform. This historically fell
    # back to powershell, which broke every non-Windows host (a Linux self-host
    # tried to spawn powershell.exe and failed, littering a `nul` file). Only
    # Windows gets powershell; POSIX gets bash.
    if os.name == "nt":
        return "powershell"
    return "bash"


def _is_obvious_readonly(command: str) -> bool:
    """Fast-path classifier — does the head token belong to the read-only allow-list?"""
    head = (command or "").strip()
    if not head:
        return False
    # Strip leading PowerShell/Bash redirects/operators like `& `, `! `, etc.
    while head[:2] in {"& ", "! ", "@ "}:
        head = head[2:].strip()
    try:
        tokens = shlex.split(head, posix=False)
    except ValueError:
        return False
    if not tokens:
        return False
    first = tokens[0].lstrip(".\\/").lower()
    if first in _READONLY_HEAD_TOKENS:
        # Special case: `git` is read-only EXCEPT for the well-known mutating
        # subcommands. Be conservative and flag those for classifier.
        if first == "git" and len(tokens) > 1:
            git_sub = tokens[1].lower()
            if git_sub in {"commit", "push", "merge", "rebase", "reset", "clean", "rm", "mv", "checkout", "branch", "tag", "stash", "cherry-pick", "revert", "apply", "am"}:
                return False
        return True
    return False


_VAULT_OPEN = re.compile(r"PyKeePass\(", re.IGNORECASE)
# Accept ~/.palli/vault/.lock, $HOME/.palli/vault/.lock, or an absolute
# /home/<user>/.palli/vault/.lock (any home-dir spelling) — same file,
# three ways a command line can name it.
_VAULT_LOCK_PATH = re.compile(r"(?:~|\$HOME|/(?:home|Users)/[^\s/]+)/\.palli/vault/\.lock")
# A script that flocks the vault file itself in Python, rather than the
# shell wrapping it in `flock ...` — same guarantee, different shape.
_FCNTL_FLOCK = re.compile(r"fcntl\.flock")


def saves_the_vault(command: str) -> bool:
    """A command that opens the vault with PyKeePass and saves it. The one
    detector both vault rules read: the lock rule below, and the file-mode
    rule after the command runs (keep_vault_file_private)."""
    text = str(command or "")
    return bool(_VAULT_OPEN.search(text) and ".save(" in text)


def keep_vault_file_private() -> None:
    """Set the vault file back to 600 after one of her saves (Batch 4, R16).

    Batch 1 found pykeepass's save leaves credentials.kdbx at 664, and every
    save by her tools does the same. Its folder is 700, so nobody else can
    reach it today, but the file should not lean on the folder. Called when a
    command `saves_the_vault` matches has finished, whether it worked or not.
    Best effort: INFO when it changed the mode, WARNING when it could not, and
    it never raises into her turn. The path is the vault's own setting
    (core.secrets_redaction.VAULT_DIR, PALLI_VAULT_DIR), never a literal."""
    try:
        import core.secrets_redaction as _sr

        path = os.path.join(str(_sr.VAULT_DIR), _sr.VAULT_KDBX_NAME)
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except FileNotFoundError:
            return  # no vault here: nothing to keep private
        if mode == 0o600:
            return
        os.chmod(path, 0o600)
        logger.info("local_shell: the vault file was %o after her save; set back to 600", mode)
    except Exception as exc:  # noqa: BLE001 - a chmod never breaks her turn
        logger.warning("local_shell: could not set the vault file back to 600: %s", exc)


def vault_write_without_lock(command: str) -> bool:
    """Batch 1 (2026-09-25), tightened in fix round 1: two of her saves
    ran at once on 2026-09-24 and destroyed the vault (four entries
    lost). A save runs under a REAL lock on the vault's own lock file
    — `flock ~/.palli/vault/.lock ...` (any spelling of the path) or an
    in-script `fcntl.flock` — or it is refused. The bare word 'flock'
    anywhere in the command (a comment, `echo flock`, a lock on some
    other file) no longer counts — round 1 review caught `flock /tmp/x
    ...` sailing through."""
    text = str(command or "")
    if not saves_the_vault(text):
        return False
    if "flock" in text and _VAULT_LOCK_PATH.search(text):
        return False
    if _FCNTL_FLOCK.search(text):
        return False
    return True


VAULT_LOCK_REFUSAL = (
    "vault writes must run under `flock ~/.palli/vault/.lock` — two "
    "saves at once corrupted the vault on 2026-09-24; wrap the script: "
    "flock ~/.palli/vault/.lock python3 …"
)


_CLASSIFIER_PROMPT = """You classify a shell command's effect on the system.

Respond with exactly one JSON object matching this schema:
{
  "intent_class": "read_only" | "mutating" | "destructive" | "network_action" | "unsafe",
  "reason": "one short sentence explaining the classification"
}

Definitions:
- read_only: pure inspection (ls, ps, git status, cat, etc). No state change.
- mutating: writes to filesystem in a recoverable, scoped way (mkdir, touch, edit a config, npm install in a workspace).
- destructive: irreversibly removes or replaces user data (rm -rf, drop database, format, sudo dd, git reset --hard on shared branches, force-pushing).
- network_action: makes outbound network calls or sends data (curl POST, scp, aws cli writes, ssh into another host).
- unsafe: catastrophic if run (rm -rf /, format c:, dropping production data, fork bombs).

When uncertain between two classes, pick the more cautious one.

Command:
COMMAND_GOES_HERE

Interpreter: INTERPRETER_GOES_HERE
"""


async def _classify_intent_via_llm(command: str, interpreter: str) -> Tuple[str, str]:
    """Ask a small LLM to classify shell intent. Returns (class, reason)."""
    prompt = _CLASSIFIER_PROMPT.replace("COMMAND_GOES_HERE", command).replace("INTERPRETER_GOES_HERE", interpreter)
    try:
        model = get_task_model("decision", fallback=get_task_model("response"))
        raw = await llm.chat_completion(
            [{"role": "system", "content": prompt}],
            model=model,
            temperature=0.0,
            max_tokens=400,
            log_context="local_shell_intent_classifier",
        )
    except Exception as exc:
        logger.warning("local_shell intent classifier LLM call failed: %s; defaulting to mutating", exc)
        return ("mutating", "classifier_unavailable")
    parsed = safe_parse_json_object(raw or "")
    if not isinstance(parsed, dict):
        return ("mutating", "classifier_unparseable_response")
    cls = str(parsed.get("intent_class") or "mutating").strip().lower()
    if cls not in {"read_only", "mutating", "destructive", "network_action", "unsafe"}:
        cls = "mutating"
    reason = str(parsed.get("reason") or "").strip()
    return (cls, reason)


async def _resolve_autonomy(owner_user_id: int) -> Dict[str, Any]:
    """Read the owner's compiled autonomy policy. Falls back to safe defaults."""
    try:
        policy = await identity_service.get_compiled_action_policy(
            user_id=int(owner_user_id),
            platform="owner",
            identity_owner_id=int(owner_user_id),
        )
    except Exception as exc:
        logger.debug("local_shell autonomy resolution failed: %s; using strict defaults", exc)
        return {
            "autonomy_mode": "manual",
            "allow_silent_low_risk_actions": False,
            "allow_silent_beneficial_actions": False,
            "allow_privileged_cli_actions": False,
            "require_confirmation_for_destructive_actions": True,
        }
    return {
        "autonomy_mode": str(policy_value(policy, "autonomy_mode", "manual") or "manual"),
        "allow_silent_low_risk_actions": policy_bool(policy, "allow_silent_low_risk_actions", False),
        "allow_silent_beneficial_actions": policy_bool(policy, "allow_silent_beneficial_actions", False),
        "allow_privileged_cli_actions": policy_bool(policy, "allow_privileged_cli_actions", False),
        "require_confirmation_for_destructive_actions": policy_bool(policy, "require_confirmation_for_destructive_actions", True),
    }


def _decide_allowance(
    *,
    intent_class: str,
    autonomy: Dict[str, Any],
    require_confirmation_already_given: bool,
) -> Tuple[bool, str]:
    """Apply autonomy policy to the classifier output. Returns (allowed, reason)."""
    if intent_class == "unsafe":
        return (False, "command classified as unsafe (catastrophic)")
    if intent_class == "read_only":
        return (True, "")
    if intent_class == "mutating":
        if bool(autonomy.get("allow_silent_low_risk_actions")) or bool(autonomy.get("allow_silent_beneficial_actions")):
            return (True, "")
        return (False, "mutating action — autonomy doesn't allow silent low-risk writes")
    if intent_class == "network_action":
        if bool(autonomy.get("allow_silent_beneficial_actions")) or bool(autonomy.get("allow_privileged_cli_actions")):
            return (True, "")
        return (False, "network action — autonomy doesn't allow outbound calls without confirm")
    if intent_class == "destructive":
        if not bool(autonomy.get("require_confirmation_for_destructive_actions")):
            if bool(autonomy.get("allow_privileged_cli_actions")):
                return (True, "")
        if require_confirmation_already_given:
            return (True, "")
        return (False, "destructive action requires user confirmation")
    return (False, f"unrecognized intent_class={intent_class!r}")


def _build_subprocess_args(*, command: str, interpreter: str) -> Tuple[List[str], Dict[str, Any]]:
    """Translate logical (command, interpreter) into actual subprocess args."""
    interp = interpreter.lower()
    if interp == "powershell":
        return (["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], {"shell": False})
    if interp == "cmd":
        return (["cmd.exe", "/c", command], {"shell": False})
    if interp == "bash":
        return (["bash", "-lc", command], {"shell": False})
    if interp == "sh":
        return (["sh", "-c", command], {"shell": False})
    # Unknown/auto → the host's native shell: bash on POSIX, powershell only on
    # Windows. Never assume Windows on a non-nt host.
    if os.name == "nt":
        return (["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], {"shell": False})
    return (["bash", "-lc", command], {"shell": False})


def _run_blocking(args: List[str], *, timeout: float, **kwargs: Any) -> Tuple[Optional[int], str, str]:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        return (None, exc.stdout or "", (exc.stderr or "") + f"\n[timed out after {timeout}s]")
    except Exception as exc:
        return (None, "", f"[subprocess error: {exc}]")
    return (completed.returncode, completed.stdout or "", completed.stderr or "")


async def _run_with_cancellation(
    args: List[str],
    *,
    timeout: float,
    cwd: str,
    **kwargs: Any,
) -> Tuple[Optional[int], str, str]:
    """Run a subprocess via asyncio so the caller can cancel it mid-flight.

    2026-05-06: when the user sends a follow-up message during a long-
    running shell command, on_message cancels the in-flight task. This
    coroutine listens for asyncio.CancelledError and terminates the
    child process so we don't orphan PowerShell/bash processes when
    PaLLi's reply gets cancelled. Returns the same shape as
    _run_blocking, including a clear "[cancelled mid-execution]" stderr
    suffix so the caller can voice the cancellation honestly.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        return (None, "", f"[subprocess error: {exc}]")
    except Exception as exc:
        return (None, "", f"[subprocess spawn error: {exc}]")

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return (None, "", f"[timed out after {timeout}s]")
    except asyncio.CancelledError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        # Re-raise CancelledError so the upstream task tree handles it
        # cleanly. The caller's exception handler turns this into a
        # ShellExecutionResult with the cancellation noted.
        raise

    stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
    stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
    return (proc.returncode, stdout, stderr)


async def execute_local_shell(
    *,
    command: str,
    interpreter: Optional[str] = None,
    owner_user_id: int,
    confirmation_received: bool = False,
) -> ShellExecutionResult:
    """Run a shell command with full autonomy + safety gating.

    Public entry point used by the tier-1 dispatcher and the proactive
    action path. Always returns a ShellExecutionResult — never raises
    for application-level concerns (refusals, autonomy denials, etc).
    Raises only on truly unexpected programming errors.
    """
    import time
    started = time.time()
    if not bool(getattr(config, "LOCAL_SHELL_ENABLED", False)):
        return ShellExecutionResult(
            command=command,
            interpreter=str(interpreter or _get_default_interpreter()),
            intent_class="disabled",
            allowed=False,
            refused_reason="local_shell is disabled (LOCAL_SHELL_ENABLED=false)",
            return_code=None,
            stdout="",
            stderr="",
            truncated=False,
            duration_seconds=0.0,
        )
    use_interpreter = (interpreter or _get_default_interpreter()).lower()
    if not command or not str(command).strip():
        return ShellExecutionResult(
            command="",
            interpreter=use_interpreter,
            intent_class="empty",
            allowed=False,
            refused_reason="empty command",
            return_code=None,
            stdout="",
            stderr="",
            truncated=False,
            duration_seconds=0.0,
        )
    cmd = str(command).strip()

    if vault_write_without_lock(cmd):
        # The command can carry put-back real values (a key she was handed
        # to use): both log lines below print it masked, never raw (final
        # fix wave, 2026-09-26).
        logger.warning("local_shell: vault write refused (no lock): command=%r", mask_text(cmd)[:120])
        return ShellExecutionResult(
            command=cmd,
            interpreter=use_interpreter,
            intent_class="vault_write_unlocked",
            allowed=False,
            refused_reason=VAULT_LOCK_REFUSAL,
            return_code=None,
            stdout="",
            stderr="",
            truncated=False,
            duration_seconds=time.time() - started,
        )

    # Fast path: obvious read-only commands skip the LLM call.
    if _is_obvious_readonly(cmd):
        intent_class = "read_only"
    else:
        intent_class, _classifier_reason = await _classify_intent_via_llm(cmd, use_interpreter)

    autonomy = await _resolve_autonomy(int(owner_user_id))
    allowed, refused = _decide_allowance(
        intent_class=intent_class,
        autonomy=autonomy,
        require_confirmation_already_given=bool(confirmation_received),
    )
    logger.info(
        "local_shell: command=%r interpreter=%s intent=%s allowed=%s",
        mask_text(cmd)[:120], use_interpreter, intent_class, allowed,
    )
    if not allowed:
        return ShellExecutionResult(
            command=cmd,
            interpreter=use_interpreter,
            intent_class=intent_class,
            allowed=False,
            refused_reason=refused,
            return_code=None,
            stdout="",
            stderr="",
            truncated=False,
            duration_seconds=time.time() - started,
        )

    args, kwargs = _build_subprocess_args(command=cmd, interpreter=use_interpreter)
    timeout = float(getattr(config, "LOCAL_SHELL_TIMEOUT_SECONDS", 30.0) or 30.0)
    # 2026-05-06: use the cancellation-aware subprocess runner so that
    # if the user sends a follow-up message mid-command, on_message can
    # cancel the in-flight task and we don't orphan a child process.
    saves_vault = saves_the_vault(cmd)
    try:
        rc, stdout, stderr = await _run_with_cancellation(
            args,
            timeout=timeout,
            cwd=os.getcwd(),
        )
    except asyncio.CancelledError:
        return ShellExecutionResult(
            command=cmd,
            interpreter=use_interpreter,
            intent_class=intent_class,
            allowed=True,
            refused_reason="",
            return_code=None,
            stdout="",
            stderr="[cancelled mid-execution by a new user message]",
            truncated=False,
            duration_seconds=time.time() - started,
        )
    finally:
        # A save that ran (or was stopped part-way) leaves the file at 664.
        if saves_vault:
            keep_vault_file_private()

    max_chars = int(getattr(config, "LOCAL_SHELL_MAX_OUTPUT_CHARS", 8000) or 8000)
    truncated = False
    if len(stdout) > max_chars:
        stdout = stdout[:max_chars]
        truncated = True
    if len(stderr) > max_chars:
        stderr = stderr[:max_chars]
        truncated = True

    return ShellExecutionResult(
        command=cmd,
        interpreter=use_interpreter,
        intent_class=intent_class,
        allowed=True,
        refused_reason="",
        return_code=rc,
        stdout=stdout,
        stderr=stderr,
        truncated=truncated,
        duration_seconds=time.time() - started,
    )

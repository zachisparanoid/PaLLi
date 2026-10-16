#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
ENV_LOCAL_PATH = ROOT_DIR / ".env.local"
ENV_TEMPLATE_PATH = ROOT_DIR / ".env.template"

TEXT_TASK_NAMES = (
    "response",
    "analysis",
    "memory",
    "decision",
    "proactive",
    "reflection",
    "web_search",
)

TASK_PROVIDER_FIELDS = {
    "response": "LLM_RESPONSE_PROVIDER",
    "analysis": "LLM_ANALYSIS_PROVIDER",
    "memory": "LLM_MEMORY_PROVIDER",
    "decision": "LLM_DECISION_PROVIDER",
    "proactive": "LLM_PROACTIVE_PROVIDER",
    "reflection": "LLM_REFLECTION_PROVIDER",
    "web_search": "LLM_WEB_SEARCH_PROVIDER",
    "vision": "LLM_VISION_PROVIDER",
}

TASK_MODEL_FIELDS = {
    "response": "LLM_RESPONSE_MODEL",
    "analysis": "LLM_ANALYSIS_MODEL",
    "memory": "LLM_MEMORY_MODEL",
    "decision": "LLM_DECISION_MODEL",
    "proactive": "LLM_PROACTIVE_MODEL",
    "reflection": "LLM_REFLECTION_MODEL",
    "web_search": "LLM_WEB_SEARCH_MODEL",
    "vision": "LLM_VISION_MODEL",
}

CONFIG_SECTION_CHOICES = (
    "models",
    "identity",
    "companion",
    "channels",
    "voice",
    "memory",
    "runtime",
)

ONBOARD_FLOW_CHOICES = (
    "companion",
    "operator",
)

# Doctor checks that describe the OPTIONAL Next.js dashboard, and nothing else. `main.py`
# — the Discord path both docs lead with — never touches Node, and _assert_startable
# already tolerates `frontend_build_missing` by name. Every fresh clone has all of these
# (nobody has run `prepare` yet), so treating them as fatal ended a SUCCESSFUL `onboard`
# with a RuntimeError, and `onboard --skip-start` still exited 1 (review I5/I6).
DASHBOARD_ONLY_CHECKS = frozenset({
    "node_missing",
    "npm_missing",
    "frontend_deps_missing",
    "frontend_build_missing",
})


def _dashboard_next_steps() -> list[str]:
    return [
        "PaLLi is set up. She is not running yet:",
        "  Discord only        venv/bin/python main.py",
        f"  With the dashboard  {launcher_command_hint('prepare')}, then "
        f"{launcher_command_hint('start')}",
    ]


def ensure_settings_file(yaml_path: Path) -> bool:
    """Seed `yaml_path` from the shipped example if it is not there yet.

    A fresh clone has no data/palli.yaml at all — the public tree ships only
    data/palli.example.yaml — and the seed that copies one into place lives in
    core.settings_boot.boot_settings(), i.e. on main.py's startup path. Every settings
    reader in THIS file loads the yaml directly instead (LauncherController._yaml_settings
    builds its own SettingsService; so does run_brain_command), so the first command both
    docs hand a stranger answered

        settings file not found at <repo>/data/palli.yaml

    and wrote nothing. The file `onboard` needs was only created by `main.py`, the last
    step (review C1, 2026-09-06).

    Idempotent by construction: seed_from_example() refuses to overwrite an existing file
    and stays silent when there is nothing to copy from, so this is a no-op on every run
    after the first — and on the owner's machine, always.
    """
    try:
        from core.settings_boot import seed_from_example

        return bool(seed_from_example(Path(yaml_path)))
    except Exception as exc:  # a missing example is not a reason to refuse the command
        import logging

        logging.getLogger(__name__).debug("settings seed skipped: %s", exc)
        return False


def _parse_env_line(line: str) -> tuple[Optional[str], Optional[str]]:
    stripped = str(line or "").strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        return None, None
    key, raw_value = line.split("=", 1)
    normalized_key = key.strip()
    if not normalized_key:
        return None, None
    value = raw_value.strip()
    if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {'"', "'"}:
        value = value[1:-1]
    return normalized_key, value


def _env_value_text(value: Any) -> str:
    """The plain text of a value `configure` collected: bools as true/false,
    numbers as themselves, None as empty. Quoting is a separate question — the
    shared env writer answers it (see `_write_env_overrides`)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _format_env_value(value: Any) -> str:
    text = _env_value_text(value)
    if not text:
        return ""
    if any(ch.isspace() for ch in text) or any(ch in text for ch in '#"\''):
        return json.dumps(text, ensure_ascii=True)
    return text


def _read_env_assignments(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    assignments: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        key, value = _parse_env_line(raw_line)
        if key:
            assignments[key] = value or ""
    return assignments


_NEW_ENV_LOCAL_HEADER = (
    "# Local overrides written by `venv/bin/python scripts/palli_ctl.py configure`.",
    "# This file is loaded after `.env` and can safely override checked-in defaults.",
)


def _write_env_overrides(path: Path, updates: Dict[str, Any]) -> None:
    """Set these keys in `path` and change nothing else.

    2026-09-17 (first door, task 2): the body of this moved to
    `palli_config/envfile.py`, which the `brain` command and the setup API
    already write through — one writer, so the CLI and the door cannot disagree
    about what `.env.local` means. Two things stay here because they are this
    command's own: `_env_value_text`, which renders the bools and numbers
    `configure` collects (the shared writer takes strings and does the quoting
    itself), and the two-line header a file this command CREATES opens with.

    What it gains from the move: the file's newline style survives. `.env.local`
    is CRLF, and this used to re-join with "\\n" and convert the whole file.
    """
    from palli_config.envfile import atomic_write_text, set_env_keys

    normalized_updates = {
        str(key): _env_value_text(value)
        for key, value in (updates or {}).items()
        if str(key).strip()
    }
    # Bytes, not `read_text`: universal newlines would hand the writer LF text,
    # and it takes the style from what it is given.
    existed = path.exists()
    text = path.read_bytes().decode("utf-8") if existed else ""
    if not existed and normalized_updates:
        text = "\n".join(_NEW_ENV_LOCAL_HEADER) + "\n"
    atomic_write_text(path, set_env_keys(text, normalized_updates))


@contextlib.contextmanager
def _preserve_windows_console_modes():
    if os.name != "nt":
        yield
        return

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        yield
        return
    kernel32 = windll.kernel32

    handles = {
        "stdin": -10,
        "stdout": -11,
        "stderr": -12,
    }
    saved_modes: List[tuple[int, int]] = []
    for handle_id in handles.values():
        handle = kernel32.GetStdHandle(handle_id)
        if not handle or int(handle) in {0, -1}:
            continue
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            saved_modes.append((handle, int(mode.value)))
    try:
        yield
    finally:
        for handle, mode in saved_modes:
            try:
                kernel32.SetConsoleMode(handle, mode)
            except Exception:
                continue


def _repo_python_candidate() -> Path:
    if os.name == "nt":
        return ROOT_DIR / "venv" / "Scripts" / "python.exe"
    return ROOT_DIR / "venv" / "bin" / "python"


def _ensure_repo_python() -> None:
    candidate = _repo_python_candidate()
    if not candidate.exists():
        return
    active_prefix = Path(getattr(sys, "prefix", sys.executable)).resolve()
    target_prefix = (ROOT_DIR / "venv").resolve()
    if active_prefix == target_prefix:
        return
    target = candidate
    os.execv(str(target), [str(target), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_repo_python()

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config import config
from core.launcher_support import (
    append_launcher_log,
    build_doctor_report,
    database_writability,
    inspect_launcher_service,
    launcher_command_hint,
    launcher_log_path,
    launcher_snapshot,
    load_launcher_state,
    process_is_running,
    resolve_npm_binary,
    resolve_repo_python_path,
    save_launcher_state,
    service_log_path,
    summarize_doctor_report,
    website_build_present,
    website_node_modules_present,
)
from ai.llm_codex_cli import CodexCLIProvider
from palli_config.ctl_prefix import ctl_prefix

# The dashboard's own bootstrap: GET here mints the double-submit CSRF cookie
# that POST /api/chat/message then requires (api/web_auth.py, core/web_security.py).
AUTH_ME_PATH = "/api/web/auth/me"
CHAT_MESSAGE_PATH = "/api/chat/message"


class _FixedLengthReader:
    """Hands tarfile exactly the byte count it already wrote into the header.

    tarfile writes a member's header, size included, before it reads a byte of
    the file. A live file under ~/.palli (a browser profile being rewritten) can
    shrink between that stat and the last read; tarfile then raises with a short
    member already in the stream, and every member after it is misaligned: the
    whole archive is torn, not just that one file. Padding the missing tail with
    zeros keeps the archive whole; `torn` records that it happened.
    """

    def __init__(self, handle: Any, size: int) -> None:
        self._handle = handle
        self._left = size
        self.torn = False

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > self._left:
            size = self._left
        if size <= 0:
            return b""
        data = b""
        if not self.torn:
            try:
                data = self._handle.read(size)
            except OSError:
                data = b""
            if len(data) < size:
                self.torn = True
        self._left -= size
        return data + b"\0" * (size - len(data))


class LauncherController:
    def __init__(self) -> None:
        self.root_dir = ROOT_DIR
        self.website_dir = self.root_dir / "website"
        self.env_path = ENV_PATH
        self.env_local_path = ENV_LOCAL_PATH
        self.palli_yaml_path = ROOT_DIR / "data" / "palli.yaml"
        self._printed_next_steps = False
        self._selected_models_preset = ""
        self.env_template_path = ENV_TEMPLATE_PATH

    def _now(self) -> float:
        return time.time()

    def _load_state(self) -> Dict[str, Any]:
        return load_launcher_state()

    def _save_state(self, state: Dict[str, Any]) -> None:
        save_launcher_state(state)

    def _save_last_doctor(self, report: Dict[str, Any]) -> None:
        state = self._load_state()
        state["last_doctor"] = summarize_doctor_report(report)
        self._save_state(state)

    def _print_payload(self, payload: Dict[str, Any], *, as_json: bool) -> None:
        if as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=True))
            return
        print(str(payload.get("summary") or "").strip() or "No summary available.")
        for item in payload.get("checks") or []:
            severity = str(item.get("severity") or "info").upper()
            print(f"[{severity}] {item.get('code')}: {item.get('message')}")
            # A check may carry the sentences that make it actionable (config_invalid
            # names the blank key). The one-line form stays exactly as it was; the
            # reasons go under it, indented, so nothing that parsed this output breaks
            # and nobody has to run --json to find out what went wrong.
            for reason in (item.get("details") or {}).get("reasons") or []:
                print(f"    - {reason}")

    def _python_command(self) -> List[str]:
        info = resolve_repo_python_path()
        if not info["exists"] or not info["executable"]:
            raise RuntimeError(f"Missing repo virtualenv Python: {info['path']}")
        return [str(info["path"])]

    def _npm_command(self) -> List[str]:
        npm_bin = resolve_npm_binary()
        if not npm_bin:
            raise RuntimeError("npm is required for website prepare/start.")
        return [str(npm_bin)]

    def _frontend_runtime_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["NEXT_PUBLIC_API_PROXY_TARGET"] = str(config.get_backend_origin() or getattr(config, "BACKEND_URL", "") or "").rstrip("/")
        return env

    def _frontend_bind_args(self) -> List[str]:
        host, port = config.get_frontend_host_port() or ("127.0.0.1", 3000)
        return ["--hostname", str(host), "--port", str(port)]

    def _backend_command(self) -> List[str]:
        return self._python_command() + ["main.py"]

    def _codex_executable(self) -> Optional[str]:
        configured = str(getattr(config, "CODEX_CLI_PATH", "") or "codex").strip() or "codex"
        if os.path.isabs(configured):
            return configured if Path(configured).exists() else None
        return shutil.which(configured)

    def _run_codex_command(
        self,
        args: List[str],
        *,
        capture_output: bool,
        timeout_seconds: Optional[float] = None,
    ) -> subprocess.CompletedProcess[str]:
        executable = self._codex_executable()
        if not executable:
            raise RuntimeError(
                "Codex CLI was not found. Install it and ensure `codex` is on PATH, "
                "or set `CODEX_CLI_PATH` in `.env.local`."
            )
        with _preserve_windows_console_modes():
            return subprocess.run(
                [executable, *args],
                cwd=str(self.root_dir),
                text=True,
                capture_output=capture_output,
                timeout=timeout_seconds,
                check=False,
            )

    def _codex_login_status(self) -> subprocess.CompletedProcess[str]:
        return self._run_codex_command(["login", "status"], capture_output=True, timeout_seconds=10.0)

    def _codex_login_status_details(
        self,
        completed: Optional[subprocess.CompletedProcess[str]] = None,
    ) -> Dict[str, Any]:
        status = completed or self._codex_login_status()
        executable = self._codex_executable() or ""
        return CodexCLIProvider._parse_login_status(
            returncode=int(getattr(status, "returncode", 1) or 1),
            stdout_text=str(getattr(status, "stdout", "") or ""),
            stderr_text=str(getattr(status, "stderr", "") or ""),
            executable_path=str(executable),
        )

    @staticmethod
    def _codex_auth_mode_label(status_details: Dict[str, Any]) -> str:
        auth_mode = str((status_details or {}).get("auth_mode") or "").strip().lower()
        if auth_mode == "chatgpt":
            return "ChatGPT"
        if auth_mode == "apikey":
            return "API key"
        return "unknown authentication"

    def _run_fresh_doctor_report(self) -> Dict[str, Any]:
        completed = subprocess.run(
            self._python_command() + [str(Path(__file__).resolve()), "doctor", "--json"],
            cwd=str(self.root_dir),
            text=True,
            capture_output=True,
            timeout=30.0,
            check=False,
        )
        payload = str(completed.stdout or "").strip()
        if not payload:
            raise RuntimeError(
                "Configuration was written, but the follow-up doctor run produced no output."
            )
        try:
            report = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Configuration was written, but the follow-up doctor run returned invalid JSON."
            ) from exc
        report["_exit_code"] = int(completed.returncode or 0)
        return report

    @staticmethod
    def _prompt_text(prompt: str, *, default: Optional[str] = None) -> str:
        suffix = f" [{default}]" if default not in {None, ""} else ""
        value = input(f"{prompt}{suffix}: ").strip()
        return value if value else str(default or "").strip()

    @staticmethod
    def _prompt_yes_no(prompt: str, *, default: bool) -> bool:
        default_token = "Y/n" if default else "y/N"
        value = input(f"{prompt} [{default_token}]: ").strip().lower()
        if not value:
            return default
        return value in {"y", "yes"}

    def _prompt_choice(self, prompt: str, *, choices: List[str], default: str) -> str:
        print(prompt)
        for index, choice in enumerate(choices, start=1):
            marker = " (default)" if choice == default else ""
            print(f"  {index}. {choice}{marker}")
        raw_value = input("Select an option: ").strip()
        if not raw_value:
            return default
        if raw_value.isdigit():
            selected_index = int(raw_value)
            if 1 <= selected_index <= len(choices):
                return choices[selected_index - 1]
        normalized = raw_value.strip().lower()
        for choice in choices:
            if normalized == choice.lower():
                return choice
        raise RuntimeError(f"Unsupported selection: {raw_value}")

    @staticmethod
    def _prompt_int(prompt: str, *, default: int) -> int:
        raw_value = input(f"{prompt} [{default}]: ").strip()
        if not raw_value:
            return int(default)
        try:
            return int(raw_value)
        except ValueError as exc:
            raise RuntimeError(f"Expected an integer for `{prompt}`.") from exc

    @staticmethod
    def _prompt_secret(prompt: str, *, currently_configured: bool) -> Optional[str]:
        suffix = " [configured; enter to keep]" if currently_configured else " [blank to skip]"
        raw_value = input(f"{prompt}{suffix}: ")
        value = str(raw_value or "").strip()
        return value or None

    @staticmethod
    def _clean_optional_value(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    def _normalize_sections(self, sections: Optional[List[str]]) -> List[str]:
        if not sections:
            return list(CONFIG_SECTION_CHOICES)
        normalized_sections: List[str] = []
        for raw_section in sections:
            section = str(raw_section or "").strip().lower()
            if section not in CONFIG_SECTION_CHOICES:
                raise RuntimeError(f"Unsupported configure section: {raw_section}")
            if section not in normalized_sections:
                normalized_sections.append(section)
        return normalized_sections

    def _runtime_base_url(self) -> str:
        origin_getter = getattr(config, "get_backend_origin", None)
        if callable(origin_getter):
            origin = str(origin_getter() or "").strip()
            if origin:
                return origin.rstrip("/")
        backend_url = str(getattr(config, "BACKEND_URL", "") or "").strip()
        if backend_url:
            return backend_url.rstrip("/")
        host = str(getattr(config, "BACKEND_HOST", "127.0.0.1") or "127.0.0.1").strip()
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        port = int(getattr(config, "BACKEND_PORT", 8000) or 8000)
        return f"http://{host}:{port}"

    def _runtime_request(
        self,
        *,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 10.0,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """`headers` (week 1 task 5) carries the CSRF pair for state-changing
        calls. Everything else here is unchanged."""
        base_url = self._runtime_base_url()
        normalized_path = "/" + str(path or "").lstrip("/")
        data = None
        request_headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        for key, value in dict(headers or {}).items():
            request_headers[str(key)] = str(value)
        request = urllib.request.Request(
            f"{base_url}{normalized_path}",
            data=data,
            method=str(method or "GET").upper(),
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=max(float(timeout_seconds or 0.0), 1.0)) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{request.method} {normalized_path} failed with HTTP {exc.code}: {body or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"{request.method} {normalized_path} could not reach the local backend at `{base_url}`."
            ) from exc

        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{request.method} {normalized_path} returned invalid JSON from `{base_url}`."
            ) from exc

    def _runtime_request_safe(
        self,
        *,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 10.0,
    ) -> Dict[str, Any]:
        try:
            data = self._runtime_request(
                method=method,
                path=path,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        except RuntimeError as exc:
            return {
                "status": "error",
                "error": str(exc),
            }
        if isinstance(data, dict):
            return data
        return {
            "status": "ok",
            "value": data,
        }

    def _sync_runtime_config(self, updates: Dict[str, Any]) -> None:
        for key, value in (updates or {}).items():
            os.environ[str(key)] = _format_env_value(value)
            if hasattr(config, str(key)):
                setattr(config, str(key), value)

    def _build_structured_settings_updates(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        structured_updates: Dict[str, Any] = {}
        clean_updates = dict(updates or {})

        default_provider = str(clean_updates.get("LLM_PROVIDER", "") or "").strip().lower()
        if default_provider:
            structured_updates["llm.default_provider"] = default_provider

        for task_name, provider_field in TASK_PROVIDER_FIELDS.items():
            model_field = TASK_MODEL_FIELDS[task_name]
            raw_provider = clean_updates.get(provider_field)
            raw_model = clean_updates.get(model_field)
            if raw_provider is None and raw_model is None:
                continue

            provider_name = str(
                raw_provider if raw_provider is not None else getattr(config, provider_field, "")
                or ""
            ).strip().lower()
            model_name = str(
                raw_model if raw_model is not None else getattr(config, model_field, "")
                or ""
            ).strip()
            if not provider_name or not model_name:
                continue

            structured_updates[f"llm.routing.{task_name}"] = {
                "provider": provider_name,
                "model": model_name,
            }

        voice_engine = str(clean_updates.get("VOICE_ENGINE_DEFAULT", "") or "").strip().lower()
        if voice_engine:
            structured_updates["voice.engine"] = voice_engine

        if "ENABLE_DISCORD_BOT" in clean_updates:
            structured_updates["channels.discord.enabled"] = bool(clean_updates["ENABLE_DISCORD_BOT"])
        if "ENABLE_TELEGRAM_BOT" in clean_updates:
            structured_updates["channels.telegram.enabled"] = bool(clean_updates["ENABLE_TELEGRAM_BOT"])
        if "ENABLE_MOBILE_DOOR" in clean_updates:
            structured_updates["channels.mobile.enabled"] = bool(clean_updates["ENABLE_MOBILE_DOOR"])
        if "ENABLE_WEB_SEARCH" in clean_updates:
            structured_updates["features.web_search"] = bool(clean_updates["ENABLE_WEB_SEARCH"])

        return structured_updates

    def _sync_structured_settings(self, updates: Dict[str, Any]) -> str:
        env_rows = self._build_structured_settings_updates(updates)
        structured_updates: Dict[str, Any] = dict(env_rows)
        # R3 (2026-09-03): a models preset applies ALL of its yaml overrides — the ten
        # lanes, not the eight the env keys can express.
        preset_name = str(getattr(self, "_selected_models_preset", "") or "")
        if preset_name:
            definition = self._yaml_presets().get(preset_name)
            if definition is not None:
                structured_updates.update(definition.overrides)
                # Ruling 21 (2026-09-05): the yaml is read first by the router — a model the
                # run set explicitly must reach it too
                for lane, provider_field in TASK_PROVIDER_FIELDS.items():
                    key = f"llm.routing.{lane}"
                    env_row, preset_row = env_rows.get(key), definition.overrides.get(key)
                    if (
                        isinstance(env_row, dict) and isinstance(preset_row, dict)
                        and env_row.get("provider") == preset_row.get("provider")
                        and env_row.get("model") and env_row.get("model") != preset_row.get("model")
                    ):
                        structured_updates[key] = env_row   # the run's own model (e.g. --codex-model) wins
        if not structured_updates:
            return ""

        try:
            from core.settings_boot import boot_settings, settings_service

            boot_settings()
            proposal = settings_service.propose(
                structured_updates,
                reason="palli_ctl configure sync",
            )
            if not proposal.change_id:
                return ""
            settings_service.apply(proposal.change_id)
            yaml_path = getattr(settings_service, "_yaml_path", None)
            if yaml_path:
                return f"Synced structured settings to `{yaml_path}`."
            return "Synced structured settings."
        except Exception as exc:
            return f"Note: structured settings sync failed; runtime may still prefer stale YAML values ({exc})."

    def _frontend_install_command(self) -> List[str]:
        return self._npm_command() + ["install"]

    def _frontend_build_command(self) -> List[str]:
        return self._npm_command() + ["run", "build"]

    def _frontend_start_command(self) -> List[str]:
        return self._npm_command() + ["run", "start", "--", *self._frontend_bind_args()]

    def _log_frontend_command_output(self, action: str, output: str) -> None:
        cleaned = str(output or "").strip()
        if not cleaned:
            return
        for line in cleaned.splitlines():
            append_launcher_log(f"{action}: {line.rstrip()}")

    def _is_locked_next_artifact_failure(self, output: str) -> bool:
        lowered = str(output or "").lower()
        if "eperm" not in lowered or ".next" not in lowered:
            return False
        return any(token in lowered for token in ("unlink", "rename", "rmdir", "operation not permitted"))

    def _frontend_failure_detail(self, output: str) -> str:
        for line in str(output or "").splitlines():
            lowered = line.lower()
            if "eperm" in lowered or "error" in lowered:
                return line.strip()
        return ""

    def _frontend_command_failure_message(self, *, action: str, retry_command: str, exit_code: int, output: str) -> str:
        log_path = launcher_log_path()
        if self._is_locked_next_artifact_failure(output):
            detail = self._frontend_failure_detail(output)
            detail_suffix = f" Next.js reported: {detail}" if detail else ""
            next_dir = self.website_dir / ".next"
            return (
                f"{action} failed because Windows appears to be locking a stale Next.js build artifact under "
                f"`{next_dir}` (EPERM touching `.next`).{detail_suffix} "
                f"Stop any running dashboard/Next.js process, close tools that may have files open in `.next`, "
                f"delete `{next_dir}`, then rerun `{launcher_command_hint(retry_command)}`. "
                f"Captured npm output was written to `{log_path}`."
            )
        return (
            f"{action} failed with exit code {exit_code}. "
            f"Captured npm output was written to `{log_path}`."
        )

    def _run_frontend_command(self, command: List[str], *, action: str, retry_command: str, as_json: bool = False) -> None:
        completed = subprocess.run(
            command,
            cwd=str(self.website_dir),
            env=self._frontend_runtime_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = str(completed.stdout or "")
        if completed.returncode == 0:
            if output.strip() and not as_json:
                print(output.rstrip())
            return
        self._log_frontend_command_output(action, output)
        raise RuntimeError(
            self._frontend_command_failure_message(
                action=action,
                retry_command=retry_command,
                exit_code=int(completed.returncode or 1),
                output=output,
            )
        )

    def _spawn(self, service_name: str, command: List[str], *, cwd: Path, env: Optional[Dict[str, str]] = None) -> int:
        log_file = service_log_path(service_name)
        log_handle = open(log_file, "a", encoding="utf-8")
        kwargs: Dict[str, Any] = {
            "cwd": str(cwd),
            "env": env or os.environ.copy(),
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        log_handle.close()
        append_launcher_log(f"started {service_name} pid={process.pid} command={' '.join(command)}")
        return int(process.pid)

    def _update_service_state(self, service_name: str, *, pid: int, command: List[str]) -> None:
        state = self._load_state()
        services = dict(state.get("services") or {})
        services[str(service_name)] = {
            "pid": int(pid),
            "started_at": self._now(),
            "updated_at": self._now(),
            "command": list(command),
            "log_path": str(service_log_path(service_name)),
            "metadata": {
                "cwd": str(self.root_dir if service_name == "backend" else self.website_dir),
            },
        }
        state["services"] = services
        self._save_state(state)

    def _remove_service_state(self, service_name: str) -> None:
        state = self._load_state()
        services = dict(state.get("services") or {})
        services.pop(str(service_name), None)
        state["services"] = services
        self._save_state(state)

    def _wait_for_running(self, service_name: str, *, timeout_seconds: float = 30.0) -> Dict[str, Any]:
        deadline = self._now() + timeout_seconds
        last_snapshot: Dict[str, Any] = {}
        while self._now() < deadline:
            snapshot = inspect_launcher_service(service_name)
            last_snapshot = snapshot
            if str(snapshot.get("status") or "") == "running":
                return snapshot
            time.sleep(0.5)
        return last_snapshot

    def _readiness_probe_target(self, service_name: str, snapshot: Dict[str, Any]) -> str:
        base_url = str(snapshot.get("url") or "").rstrip("/")
        if not base_url:
            host = str(snapshot.get("host") or "127.0.0.1")
            port = snapshot.get("port")
            base_url = f"http://{host}:{port}" if port else host
        suffix = "/api/health" if str(service_name or "").strip().lower() == "backend" else "/dashboard"
        return f"{base_url}{suffix}"

    def _probe_failure_summary(self, service_name: str, snapshot: Dict[str, Any]) -> str:
        probe = dict(snapshot.get("probe") or {})
        target = self._readiness_probe_target(service_name, snapshot)
        if probe.get("http_ok"):
            return f"is succeeding at `{target}`"
        status_code = probe.get("status_code")
        if status_code is not None:
            return f"returned HTTP {status_code} from `{target}`"
        error = str(probe.get("error") or "").strip()
        if error:
            return f"could not reach `{target}` ({error})"
        if probe.get("reachable"):
            return f"reached `{target}` but did not report ready"
        return f"did not reach `{target}`"

    def _backend_database_failure_context(self) -> str:
        try:
            details = dict(database_writability() or {})
        except Exception as exc:
            return f"SQLite launch check could not be collected ({type(exc).__name__}: {exc})."

        configured_path = str(details.get("path") or "").strip()
        absolute_path = str(details.get("absolute_path") or "").strip()
        target_kind = str(details.get("target_kind") or "").strip()
        summary = str(details.get("summary") or "").strip()
        error = str(details.get("error") or "").strip()

        parts: List[str] = []
        if configured_path:
            parts.append(f"configured path `{configured_path}`")
        if absolute_path:
            label = "effective path"
            if absolute_path == configured_path:
                label = "path"
            parts.append(f"{label} `{absolute_path}`")
        if target_kind:
            parts.append(f"target `{target_kind}`")
        if "openable" in details:
            parts.append(f"openable `{bool(details.get('openable'))}`")
        if "writable" in details:
            parts.append(f"writable `{bool(details.get('writable'))}`")
        if details.get("journal_mode_fallback_active"):
            parts.append("journal fallback `DELETE`")

        explanation = summary or error or "No SQLite diagnostics summary was available."
        if error and error not in explanation:
            explanation = f"{explanation} Error: {error}"

        if parts:
            return f"SQLite launch check: {', '.join(parts)}. {explanation}"
        return f"SQLite launch check: {explanation}"

    def _ready_state_timeout_message(self, service_name: str, snapshot: Dict[str, Any], *, timeout_seconds: float) -> str:
        service_label = str(service_name or "service").strip().title()
        status = str(snapshot.get("status") or "unknown")
        ownership = str(snapshot.get("ownership") or "unknown")
        port = snapshot.get("port")
        port_open = bool(snapshot.get("port_open"))
        state_parts = [
            f"status `{status}`",
            f"ownership `{ownership}`",
            f"port `{port}` is {'open' if port_open else 'closed'}",
        ]
        pid = snapshot.get("pid")
        if pid is not None:
            pid_state = "still running" if bool(snapshot.get("pid_running")) else "no longer running"
            state_parts.append(f"launcher pid `{pid}` is {pid_state}")
        service_log = snapshot.get("log_path") or str(service_log_path(service_name))
        database_context = ""
        if str(service_name or "").strip().lower() == "backend":
            database_context = f"{self._backend_database_failure_context()} "
        return (
            f"{service_label} failed to reach ready state within {timeout_seconds:.0f}s. "
            f"Current state: {', '.join(state_parts)}. "
            f"Readiness probe {self._probe_failure_summary(service_name, snapshot)}. "
            f"{database_context}"
            f"Check `{service_log}` for {str(service_name or '').strip().lower()} output and "
            f"`{launcher_log_path()}` for launcher events."
        )

    def _ensure_prepare_prereqs(self) -> None:
        report = build_doctor_report()
        blockers = {
            item.get("code")
            for item in (report.get("checks") or [])
            if item.get("severity") == "core_blocker"
        }
        tolerated = {"frontend_deps_missing", "frontend_build_missing"}
        real_blockers = blockers - tolerated
        self._save_last_doctor(report)
        if real_blockers:
            raise RuntimeError(str(report.get("summary") or "Environment has core blockers."))

    def doctor(self, *, as_json: bool, fix_mode: bool = False) -> int:
        # probe_llm: the doctor is a person asking "is this install ready", so it
        # pays for one live PONG and one balance read (week 1 task 1b). The
        # health endpoint, which shares this builder, does not.
        report = build_doctor_report(probe_llm=True)
        self._add_doctor_check(report, self._backup_freshness_check())
        self._add_doctor_check(report, self._check_workbench_nas())
        vault_check = self._check_vault_mode(fix_mode=fix_mode)
        self._add_doctor_check(report, vault_check)
        self._add_doctor_check(report, self._organ_liveness_check())
        self._save_last_doctor(report)
        self._print_payload(report, as_json=as_json)
        if fix_mode and not as_json:
            changed = list((vault_check.get("details") or {}).get("changed") or [])
            self._print_fix_changes(changed)
            self._print_legacy_residue_guidance(report, something_changed=bool(changed))
            self._print_check_fix_guidance(report)
        return 1 if report["counts"]["core_blockers"] else 0

    @staticmethod
    def _add_doctor_check(report: Dict[str, Any], check: Dict[str, Any]) -> None:
        """Append one check the shared builder does not make (it is also the
        health endpoint's body) and keep counts, status and summary true to
        it, the way build_doctor_report derives them. Never a core blocker."""
        report.setdefault("checks", []).append(check)
        counts = report.setdefault("counts", {})
        severity = check.get("severity")
        if severity == "warning":
            counts["warnings"] = int(counts.get("warnings") or 0) + 1
            if not counts.get("core_blockers"):
                report["status"] = "warning"
                report["summary"] = f"Ready with {counts['warnings']} warning(s)."
        elif severity == "ok":
            counts["ok"] = int(counts.get("ok") or 0) + 1

    def _print_legacy_residue_guidance(self, report: Dict[str, Any], *, something_changed: bool = False) -> None:
        """Print the legacy-residue fix guidance from the doctor report.

        Phase 8e: doctor --fix is the operator's home for legacy
        hosted-product cleanup. Currently advisory only — prints what to
        clean. A future revision can layer in actual cleanup commands
        once the contract for "do not destroy live data" is locked down.
        """
        residue_check = next(
            (
                check
                for check in report.get("checks") or []
                if check.get("code") in {"legacy_residue_present", "legacy_residue_clean"}
            ),
            None,
        )
        if residue_check is None:
            return
        guidance = ((residue_check.get("details") or {}).get("fix_guidance")) or []
        if not guidance:
            print("\n--fix: no legacy hosted-product residue to clean up.")
            return
        print("\n--fix: legacy hosted-product residue guidance")
        print("-" * 60)
        for line in guidance:
            print(line)
        if something_changed:
            # --fix did change something (the vault's permissions, printed
            # under "--fix changed"), so "No data was modified" would be false.
            print(
                "\nThe SQL above is advisory: it was not run. Apply it after taking "
                "a backup. The only thing --fix changed is listed under \"--fix changed\"."
            )
            return
        print(
            "\nThis is advisory output. No data was modified. Apply the "
            "suggested SQL after taking a backup."
        )

    @staticmethod
    def _print_fix_changes(changed: List[str]) -> None:
        """`--fix` is advice except for one thing: the vault's permissions.
        When it changed them it says so first, and names each change."""
        if not changed:
            return
        print("\n--fix changed (the vault's permissions, nothing else)")
        print("-" * 60)
        for line in changed:
            print(f"  {line}")

    # Checks this file adds itself whose recipe (details["fix_guidance"])
    # `doctor --fix` prints. The shared builder's legacy-residue check has a
    # printer of its own above.
    FIX_GUIDANCE_CODES = (
        "workbench_nas_unwritable",
        "workbench_nas_shared_folder_writable",
        "vault_mode_loose",
    )

    def _print_check_fix_guidance(self, report: Dict[str, Any]) -> None:
        """One labeled block per warning check of ours that carries a recipe."""
        for check in report.get("checks") or []:
            guidance = (check.get("details") or {}).get("fix_guidance") or []
            if (check.get("code") not in self.FIX_GUIDANCE_CODES
                    or check.get("severity") != "warning" or not guidance):
                continue
            print(f"\n--fix: {check.get('code')}")
            print("-" * 60)
            for line in guidance:
                print(f"  {line}")

    # Her writable NAS folder (fix plan 7.1). The owner's ruling, 2026-09-28:
    # "New empty subfolder." The folder she is promised by default holds his
    # own files and must never become writable, so the recipe gives her a NEW,
    # EMPTY folder on the share, mounted by itself here, and WORKBENCH_NAS_DIR
    # points her workbench at it.
    WORKBENCH_NAS_MOUNT = "/mnt/palli-nas"
    # A NAS that has gone away can hang a touch on its mount; the doctor waits
    # this long and then says the check timed out. Short, because `configure`
    # runs the whole doctor (a live model probe included) under a 30 s budget
    # (_run_fresh_doctor_report), and a hung mount must not use it up.
    WORKBENCH_NAS_PROBE_TIMEOUT_SECONDS = 5.0

    def _check_workbench_nas(self, nas_dir: Optional[str] = None) -> Dict[str, Any]:
        """One doctor line: can her work account write to her NAS folder?

        The folder is her workbench tool's own answer (`_her_nas_folder`:
        WORKBENCH_NAS_DIR, else <PALLI_NAS_ROOT>/palli), so the doctor checks
        the place she is actually promised, as the account she actually works
        as. Never a blocker. No NAS, no work account, or her own work switched
        off means there is nothing to check: a stranger's box is not wrong for
        lacking them, so those are notes, not warnings."""
        from core.agent.builtin_tools.workbench_shell import _her_nas_folder, _work_user

        folder = str(_her_nas_folder() if nas_dir is None else nas_dir).strip()
        if not folder:
            return {
                "code": "workbench_nas_unconfigured",
                "severity": "info",
                "message": "No NAS folder for her workbench (PALLI_NAS_ROOT and WORKBENCH_NAS_DIR are unset).",
                "details": {},
            }
        user = _work_user()
        ids = self._work_user_ids(user)
        if ids is None or not self._own_work_enabled():
            why = (f"there is no `{user}` account on this machine" if ids is None
                   else "her own work (companion.own_work) is off")
            return {
                "code": "workbench_nas_skipped",
                "severity": "info",
                "message": f"Her NAS folder was not checked: {why}.",
                "details": {"folder": folder, "user": user},
            }
        uid, gid = ids
        details: Dict[str, Any] = {"folder": folder, "user": user, "uid": uid, "gid": gid}
        writable = self._probe_writable_as_work_user(folder)
        if writable is None:
            return {
                "code": "workbench_nas_probe_timed_out",
                "severity": "warning",
                "message": (
                    f"Checking her NAS folder {folder} timed out after "
                    f"{self.WORKBENCH_NAS_PROBE_TIMEOUT_SECONDS:g} s — the NAS may be unreachable "
                    "or its mount hung, and she cannot save there while it is."
                ),
                "details": details,
            }
        if writable and nas_dir is None and os.environ.get("WORKBENCH_NAS_DIR") is None:
            # The default folder (<PALLI_NAS_ROOT>/palli) is a SHARED one: it can
            # hold the owner's own files, and she must never be able to change
            # them (the owner's ruling, 2026-09-28). Writable there is not a
            # green light; a folder WORKBENCH_NAS_DIR names on purpose is.
            details["fix_guidance"] = self._shared_nas_folder_fix_guidance(
                folder=folder, user=user, uid=uid, gid=gid)
            return {
                "code": "workbench_nas_shared_folder_writable",
                "severity": "warning",
                "message": (
                    f"Her work account ({user}) can write to the default shared NAS folder {folder}: "
                    "anything else in it is hers to change or delete. "
                    f"`{ctl_prefix()} doctor --fix` prints how to give her a folder of her own."
                ),
                "details": details,
            }
        if writable:
            return {
                "code": "workbench_nas_writable",
                "severity": "ok",
                "message": f"Her work account ({user}) can write to her NAS folder {folder}.",
                "details": details,
            }
        details["fix_guidance"] = self._workbench_nas_fix_guidance(uid=uid, gid=gid)
        return {
            "code": "workbench_nas_unwritable",
            "severity": "warning",
            "message": (
                f"Her work account ({user}) cannot write to her NAS folder {folder}, so she cannot "
                f"save anything there. `{ctl_prefix()} doctor --fix` prints the recipe."
            ),
            "details": details,
        }

    @staticmethod
    def _work_user_ids(user: str) -> Optional[tuple[int, int]]:
        """(uid, gid) of her work account, or None when it does not exist here.
        The recipe's numbers come from here, never from one machine's 1001."""
        try:
            import pwd

            entry = pwd.getpwnam(user)
        except (ImportError, KeyError):
            return None
        return (int(entry.pw_uid), int(entry.pw_gid))

    def _own_work_enabled(self) -> bool:
        """companion.own_work, read from data/palli.yaml without seeding it.

        A file that is not there yet or cannot be read counts as ON: that is
        the companion-flags convention (core/turn/companion_flags.py) and the
        shipped example's value, and the config check reports a broken file
        on its own line."""
        try:
            from core.settings_service import SettingsService

            path = Path(self.palli_yaml_path)
            if not path.is_file():
                return True
            companion = getattr(SettingsService(yaml_path=path).load(), "companion", None)
            return True if companion is None else bool(getattr(companion, "own_work", True))
        except Exception:  # noqa: BLE001 - see the docstring: unreadable reads as on
            return True

    def _probe_writable_as_work_user(self, path: str) -> Optional[bool]:
        """Create and remove one file in `path` AS her work account: True when
        that worked, False when it did not, None when it timed out.

        Not `test -w`: on a CIFS mount with a forced uid that reads the mode
        bits the client made up, not what the server allows. The setup
        script's own probe is a touch and an rm for the same reason. The
        folder goes in as an argument ($1), never pasted into the shell text,
        and `sudo -n` fails rather than asking for a password. Anything else
        that stops the probe (no sudo, no such account) reads as not
        writable; nothing here raises."""
        from core.agent.builtin_tools.workbench_shell import _work_user

        script = 'p="$1/.palli-doctor-probe"; touch "$p" && rm -f "$p"'
        try:
            done = subprocess.run(
                ["sudo", "-n", "-u", _work_user(), "sh", "-c", script, "_", str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.WORKBENCH_NAS_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0

    def _shared_nas_folder_fix_guidance(self, *, folder: str, user: str, uid: int, gid: int) -> List[str]:
        """How to move her off the shared default folder onto one of her own."""
        return [
            f"Her NAS folder is the default shared folder {folder}, and {user} can write to it.",
            "Give her a dedicated, EMPTY folder instead and point her workbench at it:",
            f"1. Create an empty folder for her alone, e.g. {folder}/workbench, owned by {user} "
            f"(uid={uid}, gid={gid}). On a CIFS share, mount only that folder, as the recipe for an "
            f"unwritable folder does: //<server>/<share>/palli/workbench {self.WORKBENCH_NAS_MOUNT} cifs "
            f"<options>,uid={uid},gid={gid},dir_mode=0770,file_mode=0660,nofail,_netdev 0 0",
            "2. Put WORKBENCH_NAS_DIR=<that folder> in .env.local and restart her.",
            f"3. Take {user}'s write access off {folder}: what is in it is not hers.",
        ]

    def _workbench_nas_fix_guidance(self, *, uid: int, gid: int) -> List[str]:
        """The subfolder recipe, with her account's own uid/gid filled in and
        placeholders for the server, the share and the mount options."""
        mount = self.WORKBENCH_NAS_MOUNT
        return [
            "Give her a NEW, EMPTY folder on the NAS and mount only that folder, writable by her work account.",
            "Never open a folder that already holds your files: everything in it would become hers to change or delete.",
            "1. On the NAS, create an empty folder: <share>/palli/workbench",
            f"2. sudo mkdir -p {mount}",
            "3. Add one line to /etc/fstab (<options> = the guest or credentials options your other NAS mount uses):",
            f"   //<server>/<share>/palli/workbench {mount} cifs <options>,uid={uid},gid={gid},"
            "dir_mode=0770,file_mode=0660,nofail,_netdev 0 0",
            f"4. sudo mount {mount}",
            f"5. Put WORKBENCH_NAS_DIR={mount} in .env.local, restart her, and run `{ctl_prefix()} doctor` again.",
        ]

    # The vault's modes (Batch 4, R16): the file only its owner may read or
    # write, the folder only its owner may enter.
    VAULT_FILE_MODE = 0o600
    VAULT_DIR_MODE = 0o700

    def _check_vault_mode(self, fix_mode: bool = False) -> Dict[str, Any]:
        """One doctor line: is the vault private (the file 600, its folder 700)?

        Batch 1 found every vault save left credentials.kdbx at 664. Her saves
        now set it back themselves (core/turn/local_shell.py); this is the
        second line. With `--fix` it chmods them and the line says what it
        changed. The path is the vault's own setting (PALLI_VAULT_DIR, else
        ~/.palli/vault, read by core.secrets_redaction). Never a blocker."""
        import stat

        import core.secrets_redaction as sr

        vault_dir = Path(sr.VAULT_DIR)
        kdbx = vault_dir / sr.VAULT_KDBX_NAME
        loose = []
        try:
            if not kdbx.is_file():
                return {
                    "code": "vault_absent",
                    "severity": "info",
                    "message": f"No vault at {kdbx} — nothing to keep private.",
                    "details": {},
                }
            for path, want in ((kdbx, self.VAULT_FILE_MODE), (vault_dir, self.VAULT_DIR_MODE)):
                have = stat.S_IMODE(path.stat().st_mode)
                if have != want:
                    loose.append((path, have, want))
        except OSError as exc:
            # A vault the doctor cannot even look at is a line to read, not a crash.
            return {
                "code": "vault_mode_unreadable",
                "severity": "warning",
                "message": f"Could not read the vault's permissions at {kdbx}: {exc.strerror or exc}.",
                "details": {},
            }
        if not loose:
            return {
                "code": "vault_mode_ok",
                "severity": "ok",
                "message": f"The vault is private ({kdbx.name} 600, its folder 700).",
                "details": {},
            }
        changed: List[str] = []
        failed: List[str] = []
        if fix_mode:
            still = []
            for path, have, want in loose:
                try:
                    os.chmod(path, want)
                except OSError as exc:
                    failed.append(f"{path}: {exc.strerror or exc}")
                    still.append((path, have, want))
                    continue
                changed.append(f"{path}: {have:o} -> {want:o}")
            loose = still
        if not loose:
            return {
                "code": "vault_mode_fixed",
                "severity": "ok",
                "message": "--fix made the vault private: " + "; ".join(changed) + ".",
                "details": {"changed": changed},
            }
        words = "; ".join(
            f"{'its folder' if path == vault_dir else path.name} is {have:o} (should be {want:o})"
            for path, have, want in loose
        )
        message = f"The vault is not private: {words}."
        if failed:
            message += " --fix could not change it: " + "; ".join(failed) + "."
        else:
            message += f" `{ctl_prefix()} doctor --fix` makes it private."
        return {
            "code": "vault_mode_loose",
            "severity": "warning",
            "message": message,
            "details": {
                "changed": changed,
                "fix_guidance": [f"chmod {want:o} {path}" for path, have, want in loose],
            },
        }

    def prepare(self, *, as_json: bool) -> int:
        self._ensure_prepare_prereqs()
        append_launcher_log("prepare started")
        if not website_node_modules_present():
            self._run_frontend_command(
                self._frontend_install_command(),
                action="Website dependency install",
                retry_command="prepare",
                as_json=as_json,
            )
        self._run_frontend_command(
            self._frontend_build_command(),
            action="Website production build",
            retry_command="prepare",
            as_json=as_json,
        )
        report = build_doctor_report()
        self._save_last_doctor(report)
        payload = {
            "status": "ok" if website_build_present() else "warning",
            "summary": "Prepare completed." if website_build_present() else "Prepare completed but the frontend build is still missing.",
            "checks": report.get("checks") or [],
        }
        self._print_payload(payload, as_json=as_json)
        append_launcher_log("prepare completed")
        return 0 if website_build_present() else 1

    def _assert_startable(self) -> None:
        report = build_doctor_report()
        checks = list(report.get("checks") or [])
        tolerated = {"frontend_build_missing"}
        blockers = [
            item
            for item in checks
            if item.get("severity") == "core_blocker" and item.get("code") not in tolerated
        ]
        self._save_last_doctor(report)
        if blockers:
            raise RuntimeError(str(report.get("summary") or "Environment has core blockers."))
        if any(item.get("code") == "frontend_deps_missing" for item in checks):
            raise RuntimeError(f"Website dependencies are missing. Run `{launcher_command_hint('prepare')}` first.")

    @staticmethod
    def _refuse_while_she_runs(backend: Dict[str, Any]) -> None:
        """Batch 8 Task 11 A3: never a second her.

        Started from tmux, she holds :8000 with no launcher record; the launcher
        reads that as "self" (core/launcher_support.her_listener_pid). Falling
        through would spawn a second main.py on the same tokens — two of her on
        one Discord bot. `start` refuses instead, and says who is running.
        """
        if str(backend.get("ownership") or "") == "self":
            raise RuntimeError(
                f"Not starting: she is already running (pid {backend.get('pid')}), "
                "started outside the launcher. A second one would run on the same "
                "tokens; stop her where she was started first."
            )

    def start(self) -> int:
        # Before the doctor: whatever else it finds, a running her is the answer.
        self._refuse_while_she_runs(inspect_launcher_service("backend"))
        self._assert_startable()
        started_services: List[str] = []
        ready_timeout_seconds = 30.0
        append_launcher_log("start requested")

        backend = inspect_launcher_service("backend")
        frontend = inspect_launcher_service("frontend")

        # And again after it: she may have come up while the doctor ran.
        self._refuse_while_she_runs(backend)
        if backend["ownership"] == "external":
            raise RuntimeError(f"Backend port {backend['port']} is occupied by a foreign process.")
        if frontend["ownership"] == "external":
            raise RuntimeError(f"Frontend port {frontend['port']} is occupied by a foreign process.")
        if backend["ownership"] == "launcher" and backend["status"] != "running":
            raise RuntimeError(f"Backend is launcher-managed but degraded. Run `{launcher_command_hint('restart')}`.")
        if frontend["ownership"] == "launcher" and frontend["status"] != "running":
            raise RuntimeError(f"Frontend is launcher-managed but degraded. Run `{launcher_command_hint('restart')}`.")

        if not website_build_present():
            self._run_frontend_command(
                self._frontend_build_command(),
                action="Website production build",
                retry_command="start",
            )

        if backend["status"] != "running":
            backend_pid = self._spawn("backend", self._backend_command(), cwd=self.root_dir)
            self._update_service_state("backend", pid=backend_pid, command=self._backend_command())
            started_services.append("backend")
            backend = self._wait_for_running("backend", timeout_seconds=ready_timeout_seconds)
            if backend["status"] != "running":
                message = self._ready_state_timeout_message("backend", backend, timeout_seconds=ready_timeout_seconds)
                self.stop()
                raise RuntimeError(message)

        if frontend["status"] != "running":
            frontend_pid = self._spawn(
                "frontend",
                self._frontend_start_command(),
                cwd=self.website_dir,
                env=self._frontend_runtime_env(),
            )
            self._update_service_state("frontend", pid=frontend_pid, command=self._frontend_start_command())
            started_services.append("frontend")
            frontend = self._wait_for_running("frontend", timeout_seconds=ready_timeout_seconds)
            if frontend["status"] != "running":
                message = self._ready_state_timeout_message("frontend", frontend, timeout_seconds=ready_timeout_seconds)
                if started_services:
                    self.stop()
                raise RuntimeError(message)

        print("PaLLi launcher is running.")
        print(f"Backend: {backend['url']}")
        print(f"Frontend: {frontend['url']}")
        append_launcher_log("start completed")
        return 0

    @staticmethod
    def _signal_service(pid: int, sig: int, *, group: bool) -> None:
        """Signal a service the launcher started, and (``group``) everything under it.

        The stranger test (2026-10-10): `_spawn` starts each service in a session of
        its own, but only the recorded pid was signalled. For the frontend that pid is
        npm, which exits, while next-server under it kept the port, so `restart` then
        refused to start ("occupied by a foreign process"). The group is signalled
        only when the caller has checked the pid is the launcher's (see `stop`) and
        the pid leads a group (start_new_session made it so): a pid in someone else's
        group, this process's own included, is signalled alone."""
        try:
            leads_its_group = os.getpgid(pid) == pid
        except OSError:
            leads_its_group = False
        if group and leads_its_group:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)

    @staticmethod
    def _process_started_at(pid: int) -> Optional[float]:
        """When ``pid`` started, in epoch seconds, from Linux /proc; None anywhere
        that cannot be read (another system, a pid that is gone)."""
        try:
            fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
            ticks = int(fields[19])   # starttime, field 22 of the whole line
            boot = next(
                int(line.split()[1])
                for line in Path("/proc/stat").read_text().splitlines()
                if line.startswith("btime ")
            )
            return boot + ticks / os.sysconf("SC_CLK_TCK")
        except Exception:
            return None

    def _recorded_process_is_ours(self, pid: int, record: Dict[str, Any]) -> Optional[bool]:
        """Fable's review (2026-10-10): a record left by a crash or a reboot can
        name a pid the system has since given to something else, and a group signal
        would then reach a whole tmux pane. True: the pid started within seconds of
        the record. False: it started at another time, so it is not ours. None: the
        start time cannot be read here, so only the pid itself is ever signalled."""
        started = self._process_started_at(pid)
        recorded = float(record.get("started_at") or 0.0)
        if started is None or recorded <= 0:
            return None
        return abs(started - recorded) <= 30.0

    def _terminate_pid(self, pid: int, *, group: bool = False) -> None:
        if pid <= 0:
            return
        try:
            if os.name == "nt":
                os.kill(pid, signal.SIGTERM)
            else:
                self._signal_service(pid, signal.SIGTERM, group=group)
        except OSError:
            return

    def _kill_pid(self, pid: int, *, group: bool = False) -> None:
        if pid <= 0:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                self._signal_service(pid, signal.SIGKILL, group=group)
        except OSError:
            return

    def _stop_orphaned_group(self, pgid: int) -> None:
        """The leader is gone but its group lives on (npm exited, next-server kept
        the port). Linux never gives a number to a new process while it is still a
        live group's, so this reaches only what the launcher started."""
        if os.name == "nt" or pgid <= 0:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            return
        deadline = self._now() + 10.0
        while self._now() < deadline:
            try:
                os.killpg(pgid, 0)
            except OSError:
                return
            time.sleep(0.25)
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGKILL)

    def stop(self) -> int:
        append_launcher_log("stop requested")
        state = self._load_state()
        services = dict(state.get("services") or {})
        for service_name in ("frontend", "backend"):
            record = dict(services.get(service_name) or {})
            pid = int(record.get("pid") or 0)
            if pid <= 0:
                self._remove_service_state(service_name)
                continue
            if not process_is_running(pid):
                self._stop_orphaned_group(pid)
                self._remove_service_state(service_name)
                append_launcher_log(f"stopped {service_name} pid={pid} (already gone)")
                continue
            ours = self._recorded_process_is_ours(pid, record)
            if ours is False:
                self._remove_service_state(service_name)
                append_launcher_log(
                    f"stop: {service_name} pid={pid} started at another time than its record; "
                    "it is not the launcher's process and was left alone"
                )
                continue
            self._terminate_pid(pid, group=bool(ours))
            deadline = self._now() + 10.0
            while self._now() < deadline:
                if not inspect_launcher_service(service_name)["pid_running"]:
                    break
                time.sleep(0.25)
            if inspect_launcher_service(service_name)["pid_running"]:
                self._kill_pid(pid, group=bool(ours))
            if ours:
                self._stop_orphaned_group(pid)
            # The port, not only the pid (Fable's review): next-server may still be
            # closing its socket when npm is gone, and `restart` starts right after.
            deadline = self._now() + 10.0
            while self._now() < deadline and inspect_launcher_service(service_name).get("port_open"):
                time.sleep(0.25)
            self._remove_service_state(service_name)
            append_launcher_log(f"stopped {service_name} pid={pid}")
        print("PaLLi launcher stopped.")
        return 0

    def restart(self) -> int:
        self.stop()
        return self.start()

    def status(self, *, as_json: bool) -> int:
        snapshot = launcher_snapshot()
        services = dict(snapshot.get("services") or {})
        payload = {
            "generated_at": self._now(),
            "summary": "Launcher status snapshot.",
            "services": services,
            "last_doctor": snapshot.get("last_doctor"),
            "overall_status": "running"
            if all(str((services.get(name) or {}).get("status") or "") == "running" for name in ("backend", "frontend"))
            else (
                "degraded"
                if any(str((services.get(name) or {}).get("status") or "") == "degraded" for name in ("backend", "frontend"))
                else "stopped"
            ),
        }
        if as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            print(f"Overall: {payload['overall_status']}")
            for service_name in ("backend", "frontend"):
                item = dict(services.get(service_name) or {})
                ownership = str(item.get("ownership", "none"))
                if ownership == "self":
                    # A3: her, started outside the launcher (from tmux).
                    ownership = f"self, pid {item.get('pid')}, started outside the launcher"
                print(
                    f"{service_name.title()}: {item.get('status', 'stopped')} "
                    f"({ownership}) {item.get('url', '')}"
                )
            if snapshot.get("last_doctor"):
                print(f"Last doctor: {snapshot['last_doctor'].get('summary', '')}")
        return 0

    def _yaml_settings(self):
        """The structured settings (data/palli.yaml). Presets live there — one
        definition for `settings preset`, `configure`, `brain` and the wizard
        (configurator spec 2026-09-03 §3.3 R3)."""
        from core.settings_service import SettingsService

        ensure_settings_file(Path(self.palli_yaml_path))
        return SettingsService(yaml_path=Path(self.palli_yaml_path)).load()

    def _yaml_presets(self) -> Dict[str, Any]:
        return dict(self._yaml_settings().presets)

    def _build_configure_updates(
        self,
        *,
        preset: str,
        codex_model: Optional[str],
        codex_profile: Optional[str],
    ) -> tuple[Dict[str, Any], List[str]]:
        definition = self._yaml_presets().get(preset)
        if definition is None:
            raise RuntimeError(f"Unsupported preset: {preset}. Presets: {', '.join(self._yaml_presets())}.")
        updates: Dict[str, Any] = dict(definition.env)
        notes: List[str] = []
        brain = str(updates.get("LLM_PROVIDER", "") or "").strip().lower()
        from core.llm_capabilities import BRAIN_LANE, PROVIDERS as CAPABILITIES, check_route

        # Ruling 22 (2026-09-05): `palli_ctl brain` REFUSES these; configure only warns,
        # because the operator onboarding flow's default preset is one of them.
        verdict = check_route(brain, BRAIN_LANE)
        if verdict.blocked:
            notes.append(
                f"WARNING — her voice on this preset: {verdict.reason} "
                f"Prefer `{ctl_prefix()} brain {preset} --except response` (move the background only) "
                f"or `{ctl_prefix()} brain --pick`."
            )
        for key, value in definition.overrides.items():
            if not key.startswith("llm.routing.") or not isinstance(value, dict):
                continue
            lane = key.split(".")[-1]
            if lane not in TASK_PROVIDER_FIELDS:
                continue  # appraisal / narrative_author: the yaml row is the only one
            updates[TASK_PROVIDER_FIELDS[lane]] = str(value.get("provider", "") or "")
            updates[TASK_MODEL_FIELDS[lane]] = str(value.get("model", "") or "")
        if brain == "codex":
            chosen = str(codex_model or updates.get("CODEX_MODEL") or getattr(config, "CODEX_MODEL", "gpt-5.5") or "gpt-5.5").strip()
            for lane, provider_field in TASK_PROVIDER_FIELDS.items():
                if updates.get(provider_field) == "codex":
                    updates[TASK_MODEL_FIELDS[lane]] = chosen
            updates["CODEX_MODEL"] = chosen
            updates["LLM_RESPONSE_MODEL"] = chosen
            updates["CODEX_PROFILE"] = str(
                codex_profile if codex_profile is not None else getattr(config, "CODEX_PROFILE", "") or ""
            ).strip()
        vision = str(updates.get(TASK_PROVIDER_FIELDS["vision"], "") or "")
        if vision and vision != brain:
            who = CAPABILITIES[vision].plain_name if vision in CAPABILITIES else vision
            notes.append(f"Vision runs on {who} — this preset keeps it there on purpose.")
        return updates, notes

    def _ensure_configure_prereqs(self, *, preset: str, non_interactive: bool) -> None:
        from core.llm_capabilities import PROVIDERS as CAPABILITIES, Auth

        settings = self._yaml_settings()
        definition = settings.presets.get(preset)
        if definition is None:
            raise RuntimeError(f"Unsupported preset: {preset}. Presets: {', '.join(self._yaml_presets())}.")
        brain = str(definition.env.get("LLM_PROVIDER", "") or "").strip().lower()
        cap = CAPABILITIES.get(brain)
        if cap is None:
            raise RuntimeError(f"`{preset}` names a provider PaLLi does not know: {brain!r}.")
        if cap.auth is Auth.API_KEY:
            ref = getattr(settings.providers.get(brain), "api_key_ref", None)
            key_env = str(getattr(ref, "name", "") or "")
            if key_env and not str(getattr(config, key_env, "") or "").strip():
                raise RuntimeError(f"`{preset}` requires {key_env}.")
        if brain == "local_openai":
            if not str(getattr(config, "LOCAL_LLM_API_BASE", "") or "").strip():
                raise RuntimeError(f"`{preset}` requires LOCAL_LLM_API_BASE.")
            if not str(getattr(config, "LOCAL_LLM_MODEL", "") or "").strip():
                raise RuntimeError(f"`{preset}` requires LOCAL_LLM_MODEL.")
        if brain != "codex":
            return
        status = self._codex_login_status()
        if status.returncode == 0:
            return
        message = f"Codex CLI is not authenticated. Run `{ctl_prefix()} auth codex` first."
        if non_interactive:
            raise RuntimeError(message)
        raise RuntimeError(f"{message} Then rerun `{ctl_prefix()} configure`.")

    def auth_codex(self, *, status_only: bool, device_auth: bool, quiet: bool = False) -> int:
        status = self._codex_login_status()
        status_details = self._codex_login_status_details(status)
        if status_only:
            if status.returncode == 0:
                if not quiet:
                    print(
                        f"Codex CLI is authenticated using {self._codex_auth_mode_label(status_details)}."
                    )
                return 0
            raise RuntimeError(
                f"Codex CLI is not authenticated. Run `{ctl_prefix()} auth codex`."
            )

        if status.returncode == 0:
            if not quiet:
                print(
                    f"Codex CLI is already authenticated using {self._codex_auth_mode_label(status_details)}."
                )
            return 0

        login_args = ["login"]
        if device_auth:
            login_args.append("--device-auth")
        completed = self._run_codex_command(login_args, capture_output=False)
        if completed.returncode != 0:
            raise RuntimeError(f"Codex login failed with exit code {completed.returncode}.")

        verify = self._codex_login_status()
        if verify.returncode != 0:
            raise RuntimeError(
                "Codex login finished, but `codex login status` still reports no active authentication."
            )
        verify_details = self._codex_login_status_details(verify)
        if not quiet:
            print(
                f"Codex CLI authentication is ready using {self._codex_auth_mode_label(verify_details)}."
            )
        return 0

    def _infer_runtime_preset(self) -> str:
        provider = str(getattr(config, "LLM_PROVIDER", "deepseek") or "deepseek").strip().lower()
        for name, definition in self._yaml_presets().items():
            if str(definition.env.get("LLM_PROVIDER", "") or "").strip().lower() == provider:
                return name
        return "deepseek-primary"

    def _build_models_section_updates(
        self,
        *,
        preset: Optional[str],
        non_interactive: bool,
        codex_model: Optional[str],
        codex_profile: Optional[str],
    ) -> tuple[str, Dict[str, Any], List[str]]:
        selected_preset = str(preset or "").strip().lower()
        if not selected_preset:
            if non_interactive:
                selected_preset = self._infer_runtime_preset()
            else:
                selected_preset = self._prompt_choice(
                    "Choose a PaLLi runtime preset:",
                    choices=list(self._yaml_presets()),
                    default=self._infer_runtime_preset(),
                )
        if selected_preset not in self._yaml_presets():
            raise RuntimeError(f"Unsupported preset: {selected_preset}. Presets: {', '.join(self._yaml_presets())}.")

        selected_codex_model = codex_model
        selected_codex_profile = codex_profile
        if selected_preset == "codex-primary" and not non_interactive:
            if selected_codex_model is None:
                selected_codex_model = self._prompt_text(
                    "Codex model",
                    default=str(getattr(config, "CODEX_MODEL", "gpt-5.5") or "gpt-5.5"),
                )
            if selected_codex_profile is None:
                selected_codex_profile = self._prompt_text(
                    "Codex profile (blank for default)",
                    default=str(getattr(config, "CODEX_PROFILE", "") or ""),
                )

        self._ensure_configure_prereqs(preset=selected_preset, non_interactive=non_interactive)
        updates, notes = self._build_configure_updates(
            preset=selected_preset,
            codex_model=selected_codex_model,
            codex_profile=selected_codex_profile,
        )
        return selected_preset, updates, notes

    def _build_identity_section_updates(self, *, non_interactive: bool) -> tuple[Dict[str, Any], List[str]]:
        if non_interactive:
            return {
                "LOCAL_OWNER_EMAIL": str(getattr(config, "LOCAL_OWNER_EMAIL", "local@palli.local") or "local@palli.local"),
                "LOCAL_OWNER_DISPLAY_NAME": str(
                    getattr(config, "LOCAL_OWNER_DISPLAY_NAME", "Local Operator") or "Local Operator"
                ),
                "ENABLE_IDENTITY_ARCHITECTURE": bool(getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True)),
                "IDENTITY_ENABLE_PRIVILEGED_AUTONOMY": bool(
                    getattr(config, "IDENTITY_ENABLE_PRIVILEGED_AUTONOMY", False)
                ),
            }, []

        print("\nIdentity")
        owner_email = self._prompt_text(
            "Local owner email",
            default=str(getattr(config, "LOCAL_OWNER_EMAIL", "local@palli.local") or "local@palli.local"),
        )
        owner_name = self._prompt_text(
            "Local owner display name",
            default=str(getattr(config, "LOCAL_OWNER_DISPLAY_NAME", "Local Operator") or "Local Operator"),
        )
        identity_enabled = self._prompt_yes_no(
            "Keep the identity architecture enabled?",
            default=bool(getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True)),
        )
        privileged_autonomy = self._prompt_yes_no(
            "Allow privileged autonomy behaviors?",
            default=bool(getattr(config, "IDENTITY_ENABLE_PRIVILEGED_AUTONOMY", False)),
        )
        notes: List[str] = []
        if privileged_autonomy:
            notes.append("Privileged autonomy was enabled. Keep confirmation and action-preference settings tight.")
        return {
            "LOCAL_OWNER_EMAIL": owner_email,
            "LOCAL_OWNER_DISPLAY_NAME": owner_name,
            "ENABLE_IDENTITY_ARCHITECTURE": identity_enabled,
            "IDENTITY_ENABLE_PRIVILEGED_AUTONOMY": privileged_autonomy,
        }, notes

    def _build_companion_section_updates(self, *, non_interactive: bool) -> tuple[Dict[str, Any], List[str]]:
        # 2026-05-12 — Open-Threads Agenda (T17): the legacy
        # ENABLE_COMPANION_AGENDA + COMPANION_AGENDA_* knobs are gone. The
        # agenda is unconditional; this section now reports rather than
        # prompts. Kept (rather than removed) so the launcher's "companion"
        # CLI section + the onboarding flow choice continue to exist and
        # print something sensible. A future cleanup can remove the section
        # entirely once the launcher CLI surface is reworked.
        if not non_interactive:
            print("\nCompanion")
            print("  Open-Threads Agenda is always on. No per-section toggles required.")
        return {}, []

    def _build_channels_section_updates(self, *, non_interactive: bool) -> tuple[Dict[str, Any], List[str]]:
        if non_interactive:
            return {
                "ENABLE_DISCORD_BOT": bool(getattr(config, "ENABLE_DISCORD_BOT", True)),
                "ENABLE_TELEGRAM_BOT": bool(getattr(config, "ENABLE_TELEGRAM_BOT", False)),
                "DISCORD_PRIMARY_GUILD_ID": self._clean_optional_value(getattr(config, "DISCORD_PRIMARY_GUILD_ID", "")),
            }, []

        print("\nChannels")
        discord_enabled = self._prompt_yes_no(
            "Enable the Discord bot?",
            default=bool(getattr(config, "ENABLE_DISCORD_BOT", True)),
        )
        telegram_enabled = self._prompt_yes_no(
            "Enable the Telegram bot?",
            default=bool(getattr(config, "ENABLE_TELEGRAM_BOT", False)),
        )
        discord_token = self._prompt_secret(
            "Discord bot token",
            currently_configured=bool(str(getattr(config, "DISCORD_TOKEN", "") or "").strip()),
        ) if discord_enabled else ""
        telegram_token = self._prompt_secret(
            "Telegram bot token",
            currently_configured=bool(str(getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()),
        ) if telegram_enabled else ""
        guild_default = self._clean_optional_value(getattr(config, "DISCORD_PRIMARY_GUILD_ID", ""))
        guild_prompt = input(
            f"Primary Discord guild id [{guild_default or 'blank'}]: "
        ).strip()
        discord_guild = guild_prompt or guild_default or ""

        updates: Dict[str, Any] = {
            "ENABLE_DISCORD_BOT": discord_enabled,
            "ENABLE_TELEGRAM_BOT": telegram_enabled,
            "DISCORD_PRIMARY_GUILD_ID": discord_guild,
        }
        if discord_enabled and discord_token is not None:
            updates["DISCORD_TOKEN"] = discord_token
        if telegram_enabled and telegram_token is not None:
            updates["TELEGRAM_BOT_TOKEN"] = telegram_token
        notes: List[str] = []
        if discord_enabled and not str(updates.get("DISCORD_TOKEN", getattr(config, "DISCORD_TOKEN", "")) or "").strip():
            notes.append("Discord is enabled but DISCORD_TOKEN is still blank.")
        if telegram_enabled and not str(updates.get("TELEGRAM_BOT_TOKEN", getattr(config, "TELEGRAM_BOT_TOKEN", "")) or "").strip():
            notes.append("Telegram is enabled but TELEGRAM_BOT_TOKEN is still blank.")
        return updates, notes

    def _build_voice_section_updates(self, *, non_interactive: bool) -> tuple[Dict[str, Any], List[str]]:
        # Step 31 (2026-08-20, spec §7): the bridge is deleted — classic is
        # the only engine; the chooser and its OPENAI_API_KEY guards left.
        if non_interactive:
            updates: Dict[str, Any] = {
                "ENABLE_VOICE_FEATURES": bool(getattr(config, "ENABLE_VOICE_FEATURES", True)),
                "VOICE_ENGINE_DEFAULT": "classic",
                "VOICE_LOCAL_TTS_VOICE": str(getattr(config, "VOICE_LOCAL_TTS_VOICE", "") or ""),
            }
            return updates, []

        print("\nVoice")
        voice_enabled = self._prompt_yes_no(
            "Enable voice features?",
            default=bool(getattr(config, "ENABLE_VOICE_FEATURES", True)),
        )
        local_tts_voice = self._prompt_text(
            "Local TTS voice (blank keeps current)",
            default=str(getattr(config, "VOICE_LOCAL_TTS_VOICE", "") or ""),
        )
        return {
            "ENABLE_VOICE_FEATURES": voice_enabled,
            "VOICE_ENGINE_DEFAULT": "classic",
            "VOICE_LOCAL_TTS_VOICE": local_tts_voice,
        }, []

    def _build_memory_section_updates(self, *, non_interactive: bool) -> tuple[Dict[str, Any], List[str]]:
        if non_interactive:
            return {
                "MEMORY_WORKSPACE_ENABLED": bool(getattr(config, "MEMORY_WORKSPACE_ENABLED", True)),
                "MEMORY_INDEX_ENABLED": bool(getattr(config, "MEMORY_INDEX_ENABLED", True)),
                "MEMORY_LOCAL_EMBEDDINGS_ENABLED": bool(getattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", True)),
                "MEMORY_SEARCH_TOP_K": int(getattr(config, "MEMORY_SEARCH_TOP_K", 8) or 8),
            }, []

        print("\nMemory")
        workspace_enabled = self._prompt_yes_no(
            "Enable memory workspace features?",
            default=bool(getattr(config, "MEMORY_WORKSPACE_ENABLED", True)),
        )
        index_enabled = self._prompt_yes_no(
            "Enable memory indexing?",
            default=bool(getattr(config, "MEMORY_INDEX_ENABLED", True)),
        )
        local_embeddings_enabled = self._prompt_yes_no(
            "Prefer local embeddings?",
            default=bool(getattr(config, "MEMORY_LOCAL_EMBEDDINGS_ENABLED", True)),
        )
        top_k = self._prompt_int(
            "Memory search top-k",
            default=int(getattr(config, "MEMORY_SEARCH_TOP_K", 8) or 8),
        )
        return {
            "MEMORY_WORKSPACE_ENABLED": workspace_enabled,
            "MEMORY_INDEX_ENABLED": index_enabled,
            "MEMORY_LOCAL_EMBEDDINGS_ENABLED": local_embeddings_enabled,
            "MEMORY_SEARCH_TOP_K": max(top_k, 1),
        }, []

    def _build_runtime_section_updates(self, *, non_interactive: bool) -> tuple[Dict[str, Any], List[str]]:
        # E.5 Wave 4 step 3 (2026-05-13): web_search default now reads
        # data/palli.yaml -> features.web_search via settings_service.
        try:
            from core.settings_boot import settings_service as _settings
            current_web_search = bool(_settings.snapshot().features.web_search)
        except Exception:
            current_web_search = True

        if non_interactive:
            return {
                "LOCAL_DASHBOARD_NO_AUTH": bool(getattr(config, "LOCAL_DASHBOARD_NO_AUTH", True)),
                "ENABLE_WEB_SEARCH": current_web_search,
                "ALLOW_INSECURE_CORS": bool(getattr(config, "ALLOW_INSECURE_CORS", True)),
                "BACKEND_PORT": int(getattr(config, "BACKEND_PORT", 8000) or 8000),
                "FRONTEND_URL": str(getattr(config, "FRONTEND_URL", "http://localhost:3000") or "http://localhost:3000"),
            }, []

        print("\nRuntime")
        open_dashboard = self._prompt_yes_no(
            "Keep the local dashboard open without login?",
            default=bool(getattr(config, "LOCAL_DASHBOARD_NO_AUTH", True)),
        )
        web_search_enabled = self._prompt_yes_no(
            "Enable web search features?",
            default=current_web_search,
        )
        allow_insecure_cors = self._prompt_yes_no(
            "Allow broad local/LAN CORS access?",
            default=bool(getattr(config, "ALLOW_INSECURE_CORS", True)),
        )
        backend_port = self._prompt_int(
            "Backend port",
            default=int(getattr(config, "BACKEND_PORT", 8000) or 8000),
        )
        frontend_url = self._prompt_text(
            "Frontend URL",
            default=str(getattr(config, "FRONTEND_URL", "http://localhost:3000") or "http://localhost:3000"),
        )
        return {
            "LOCAL_DASHBOARD_NO_AUTH": open_dashboard,
            "ENABLE_WEB_SEARCH": web_search_enabled,
            "ALLOW_INSECURE_CORS": allow_insecure_cors,
            "BACKEND_PORT": max(backend_port, 1),
            "FRONTEND_URL": frontend_url,
        }, []

    def configure(
        self,
        *,
        preset: Optional[str],
        non_interactive: bool,
        codex_model: Optional[str],
        codex_profile: Optional[str],
        skip_doctor: bool,
        sections: Optional[List[str]] = None,
    ) -> int:
        selected_sections = self._normalize_sections(sections)
        updates: Dict[str, Any] = {}
        notes: List[str] = []
        selected_preset = str(preset or "").strip().lower()

        if not non_interactive:
            print("PaLLi configure")
            print(f"Sections: {', '.join(selected_sections)}")

        for section in selected_sections:
            section_updates: Dict[str, Any]
            section_notes: List[str]
            if section == "models":
                selected_preset, section_updates, section_notes = self._build_models_section_updates(
                    preset=selected_preset,
                    non_interactive=non_interactive,
                    codex_model=codex_model,
                    codex_profile=codex_profile,
                )
            elif section == "identity":
                section_updates, section_notes = self._build_identity_section_updates(non_interactive=non_interactive)
            elif section == "companion":
                section_updates, section_notes = self._build_companion_section_updates(non_interactive=non_interactive)
            elif section == "channels":
                section_updates, section_notes = self._build_channels_section_updates(non_interactive=non_interactive)
            elif section == "voice":
                section_updates, section_notes = self._build_voice_section_updates(non_interactive=non_interactive)
            elif section == "memory":
                section_updates, section_notes = self._build_memory_section_updates(non_interactive=non_interactive)
            elif section == "runtime":
                section_updates, section_notes = self._build_runtime_section_updates(non_interactive=non_interactive)
            else:
                raise RuntimeError(f"Unsupported configure section: {section}")
            updates.update(section_updates)
            notes.extend(section_notes)

        _write_env_overrides(self.env_local_path, updates)
        self._sync_runtime_config(updates)
        self._selected_models_preset = selected_preset if "models" in selected_sections else ""
        structured_settings_message = self._sync_structured_settings(updates)

        print(f"Wrote configuration overrides to `{self.env_local_path}`.")
        if structured_settings_message:
            print(structured_settings_message)
        if "models" in selected_sections:
            print(f"Selected preset: {selected_preset}")
        print(f"Configured sections: {', '.join(selected_sections)}")
        for key in (
            "LLM_PROVIDER",
            "LLM_RESPONSE_PROVIDER",
            "LLM_ANALYSIS_PROVIDER",
            "LLM_MEMORY_PROVIDER",
            "LLM_DECISION_PROVIDER",
            "LLM_PROACTIVE_PROVIDER",
            "LLM_REFLECTION_PROVIDER",
            "LLM_WEB_SEARCH_PROVIDER",
            "LLM_VISION_PROVIDER",
            "CODEX_MODEL",
            "CODEX_PROFILE",
            "LOCAL_OWNER_EMAIL",
            "LOCAL_OWNER_DISPLAY_NAME",
            "ENABLE_DISCORD_BOT",
            "ENABLE_TELEGRAM_BOT",
            "VOICE_ENGINE_DEFAULT",
            "MEMORY_WORKSPACE_ENABLED",
            "MEMORY_INDEX_ENABLED",
            "LOCAL_DASHBOARD_NO_AUTH",
        ):
            if key in updates:
                print(f"{key}={_format_env_value(updates[key])}")
        for note in notes:
            print(f"Note: {note}")

        if skip_doctor:
            print("Skipped follow-up doctor run.")
            return 0

        report = self._run_fresh_doctor_report()
        summary = str(report.get("summary") or "Doctor completed.")
        print(f"Doctor: {summary}")
        blockers = [
            check for check in (report.get("checks") or [])
            if str(check.get("severity") or "") == "core_blocker"
        ]
        real = [c for c in blockers if str(c.get("code") or "") not in DASHBOARD_ONLY_CHECKS]
        if real or (int(report.get("_exit_code") or 0) != 0 and not blockers):
            raise RuntimeError(
                f"Configuration was written, but doctor still reports blockers. {summary}"
        )
        if blockers or not website_node_modules_present():
            # Dashboard-only, or simply not built yet. Setup succeeded; say what is left
            # rather than raising — and do not point at `start`, which would refuse.
            self._printed_next_steps = True
            for line in _dashboard_next_steps():
                print(line)
            return 0
        print(f"Next: {launcher_command_hint('start')}")
        return 0

    def onboard(
        self,
        *,
        flow: Optional[str],
        preset: Optional[str],
        non_interactive: bool,
        codex_model: Optional[str],
        codex_profile: Optional[str],
        skip_doctor: bool,
        skip_start: bool,
    ) -> int:
        selected_flow = str(flow or "").strip().lower()
        if not selected_flow:
            if non_interactive:
                selected_flow = "companion"
            else:
                selected_flow = self._prompt_choice(
                    "Choose an onboarding flow:",
                    choices=list(ONBOARD_FLOW_CHOICES),
                    default="companion",
                )
        if selected_flow not in ONBOARD_FLOW_CHOICES:
            raise RuntimeError(f"Unsupported onboarding flow: {selected_flow}")

        chosen_preset = str(preset or "").strip().lower()
        if not chosen_preset:
            chosen_preset = "codex-primary" if selected_flow == "operator" else self._infer_runtime_preset()

        if not non_interactive:
            print("PaLLi onboard")
            print("This wizard sets up the local owner, model routing, channels, voice, memory, and runtime defaults.")
            print(f"Flow: {selected_flow}")

        self.configure(
            preset=chosen_preset,
            non_interactive=non_interactive,
            codex_model=codex_model,
            codex_profile=codex_profile,
            skip_doctor=skip_doctor,
            sections=list(CONFIG_SECTION_CHOICES),
        )
        if skip_start:
            print("Skipped automatic start.")
            return 0
        if not website_node_modules_present():
            # Review I5. `start` brings the dashboard up alongside the backend, and
            # _assert_startable refuses outright while the website deps are missing —
            # which they are on EVERY fresh clone. So onboard's own last step, offered
            # with default=yes, ended a successful setup in a RuntimeError. Say what to
            # run instead, and exit 0: the setup did succeed. (configure() says the same
            # thing when its own doctor run reaches this state; do not say it twice.)
            if not getattr(self, "_printed_next_steps", False):
                for line in _dashboard_next_steps():
                    print(line)
            return 0
        if not non_interactive:
            should_start = self._prompt_yes_no("Start PaLLi now?", default=True)
            if not should_start:
                print(f"Next: {launcher_command_hint('start')}")
                return 0
        return self.start()

    @staticmethod
    def _truncate(text: Any, *, width: int = 96) -> str:
        cleaned = " ".join(str(text or "").split())
        if len(cleaned) <= width:
            return cleaned
        return cleaned[: max(width - 3, 1)].rstrip() + "..."

    @staticmethod
    def _coerce_items(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [dict(item) for item in items if isinstance(item, dict)]
        return []

    def _csrf_token(self) -> str:
        """Mint the double-submit CSRF cookie the way the dashboard does.

        `POST /api/chat/message` is CSRF-protected even in local-no-auth mode,
        and deliberately: with no login, that cookie is the only thing between a
        webpage open on any device that can reach this backend and a chat turn
        that can call her shell tool. A GET of /api/web/auth/me sets it; the
        POST has to send it back both as the cookie and as the header.
        """
        base_url = self._runtime_base_url()
        request = urllib.request.Request(f"{base_url}{AUTH_ME_PATH}", method="GET")
        cookie_name = str(getattr(config, "CSRF_COOKIE_NAME", "palli_csrf") or "palli_csrf")
        try:
            with urllib.request.urlopen(request, timeout=15.0) as response:
                headers = list(response.getheaders())
        except urllib.error.HTTPError as exc:
            # HTTPError subclasses URLError, so it has to be caught first — a 404
            # or a 500 reported as "could not reach the backend" sends the reader
            # after the wrong problem.
            raise RuntimeError(
                f"GET {AUTH_ME_PATH} failed with HTTP {exc.code}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"GET {AUTH_ME_PATH} could not reach the local backend at `{base_url}`."
            ) from exc
        for header, value in headers:
            if header.lower() != "set-cookie" or f"{cookie_name}=" not in value:
                continue
            return value.split(f"{cookie_name}=", 1)[1].split(";", 1)[0]
        raise RuntimeError(
            f"GET {AUTH_ME_PATH} minted no {cookie_name} cookie — is this backend in local mode?"
        )

    def chat(self, *, message: Optional[str], show_events: bool = False) -> int:
        """Talk to her through the route that exists (week 1 task 5).

        This used to POST to /api/web/runtime/local/probe/message, a route
        deleted in Wave 5 — the only advertised terminal path that did not need
        Discord returned 404 to everyone who tried it.
        """
        current_message = str(message or "").strip()
        interactive = not current_message
        cookie_name = str(getattr(config, "CSRF_COOKIE_NAME", "palli_csrf") or "palli_csrf")
        token = self._csrf_token()

        while True:
            if not current_message:
                raw_value = input("chat> ").strip()
                if not raw_value or raw_value.lower() in {"quit", "exit"}:
                    return 0
                current_message = raw_value

            result = self._runtime_request(
                method="POST",
                path=CHAT_MESSAGE_PATH,
                payload={"message": current_message},
                timeout_seconds=180.0,
                headers={
                    "X-CSRF-Token": token,
                    "Cookie": f"{cookie_name}={token}",
                },
            )
            if not isinstance(result, dict):
                raise RuntimeError("The chat endpoint returned an unexpected response shape.")

            reply = str(result.get("response") or "").strip()
            print(f"PaLLi: {reply}" if reply else "PaLLi: [no response delivered]")
            delegation = str(result.get("delegation_text") or "").strip()
            if delegation:
                print(delegation)
            if show_events and result.get("beats"):
                print(json.dumps(result.get("beats"), indent=2, ensure_ascii=True))

            if not interactive:
                return 0
            current_message = ""

    # --- backup (week 1 task 5) ---------------------------------------------
    # What is irreplaceable here is not the code — it is data/ and identity/:
    # her memory, her stakes, her story. Everything else can be re-cloned.

    BACKUP_TREES = ("data", "identity")
    # -wal/-shm belong to a database that is being copied properly below; a
    # *.tmp is a half-written file; .env* is keys, and keys never go in an
    # archive somebody might hand to somebody else. data/delegations/ is
    # that same rule one directory up: it holds HOST credentials — the file
    # tools refuse it by name for exactly that reason — so no path under it
    # rides the archive either. A backup is a thing you hand to someone.
    BACKUP_SKIP_SUFFIXES = (".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal", ".tmp")
    BACKUP_SKIP_SUBPATH = "/data/delegations/"

    # Stop the leaks, task 6. The nightly copy goes to a NAS share the whole LAN
    # can read, and with --include-home it carries ~/.palli: the vault and the
    # vault's own key. So the archive is encrypted with openssl (a bare box can
    # restore it with nothing of hers installed), only ciphertext ever touches
    # the target, it is kept to N, and it is never written into an empty mount
    # point that only looks like the NAS. The way back:
    #   openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    #     -in palli-backup-X.tar.gz.enc -out palli-backup-X.tar.gz -pass file:PASSFILE
    BACKUP_OPENSSL = "openssl"
    BACKUP_CIPHER_ARGS = ("enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt")
    BACKUP_PASSFILE_NAME = "backup.passphrase"
    # openssl reads only the passfile's first line, and an EMPTY first line
    # encrypts with an empty password and exits 0: ciphertext anyone can open.
    BACKUP_PASSPHRASE_MIN_CHARS = 16
    BACKUP_HOME_ARCNAME = "home/.palli"
    # logs/ and models/ are bulk that re-creates itself; a *.sock is a live
    # endpoint, not data. backup.passphrase is skipped wherever it sits: an
    # archive never carries the key to itself.
    BACKUP_HOME_SKIP_DIRS = ("logs", "models")
    BACKUP_HOME_SKIP_SUFFIXES = (".sock",)
    # Her vault saves take ~/.palli/vault/.lock exclusively (`flock`). The
    # backup takes it shared while it copies the vault, waiting this long.
    BACKUP_VAULT_LOCK_WAIT_SECONDS = 30.0
    # palli-backup-<UTC stamp>[-N].tar.gz[.enc]: -N only when a run in the same
    # second already took the plain name.
    BACKUP_NAME_PATTERN = re.compile(r"^palli-backup-(\d{8}-\d{6})(?:-(\d+))?\.tar\.gz(\.enc)?$")
    # A failure you can see (final fix wave, 2026-09-26). The nightly run is a
    # cron line appending to logs/backup.log, which nobody reads: a failing
    # backup was invisible until the day it was needed. A good run records
    # itself here; `doctor` warns once the record is older than this.
    BACKUP_LAST_OK_NAME = "backup.last-ok"
    BACKUP_STALE_HOURS = 36
    BACKUP_LOG_HINT = "tail logs/backup.log"

    @staticmethod
    def _backup_excluded(path: Path) -> bool:
        name = path.name
        if name.startswith(".env"):
            return True
        if LauncherController.BACKUP_SKIP_SUBPATH in path.as_posix():
            return True
        # palli.yaml.tmp.XXXX (core/settings_service.py): a save mid-write that
        # is renamed away a moment later. Never the file of record.
        if ".tmp." in name:
            return True
        return name.endswith(LauncherController.BACKUP_SKIP_SUFFIXES)

    @staticmethod
    def _snapshot_database(source: Path, destination: Path) -> None:
        """VACUUM INTO, not a file copy.

        She is normally running while this happens. Copying a live SQLite file
        catches it mid-transaction and the copy restores to a torn database —
        the WAL holding the rest of the truth is a separate file we deliberately
        do not archive. VACUUM INTO asks SQLite for a consistent snapshot of the
        committed state instead, which is the only copy worth keeping.
        """
        import sqlite3

        destination.parent.mkdir(parents=True, exist_ok=True)
        connection = None
        try:
            connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            connection.execute("VACUUM INTO ?", (str(destination),))
            return
        except (sqlite3.Error, OSError):
            # A read-only connection cannot replay a leftover -wal, and that is
            # precisely the state a crashed PaLLi leaves behind — the one time
            # a backup matters most. Fall through and reconnect writable so
            # SQLite can checkpoint first. It changes no row; it only folds the
            # WAL back in.
            pass
        finally:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()

        destination.unlink(missing_ok=True)
        try:
            connection = sqlite3.connect(str(source))
        except (sqlite3.Error, OSError) as exc:
            raise RuntimeError(f"Could not open `{source}` to back it up: {exc}") from exc
        try:
            connection.execute("VACUUM INTO ?", (str(destination),))
        except (sqlite3.Error, OSError) as exc:
            # `database is locked`, a full disk, an unwritable staging directory:
            # all of these used to arrive as a stack trace. main() prints a
            # RuntimeError as one line.
            raise RuntimeError(f"Could not snapshot `{source}`: {exc}") from exc
        finally:
            connection.close()

    @staticmethod
    def _backup_stamp() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    @staticmethod
    def _backup_free_name(target_dir: Path, stamp: str, suffix: str) -> Path:
        """Three runs inside one second must leave three archives, not one
        archive overwritten twice: the plain stamp while it is free, then -2, -3."""
        candidate = target_dir / f"palli-backup-{stamp}{suffix}"
        number = 1
        while candidate.exists() or candidate.with_name(candidate.name + ".partial").exists():
            number += 1
            candidate = target_dir / f"palli-backup-{stamp}-{number}{suffix}"
        return candidate

    @classmethod
    def _backup_last_ok_path(cls) -> Path:
        return Path.home() / ".palli" / cls.BACKUP_LAST_OK_NAME

    def _write_backup_last_ok(self, archive_path: Path) -> None:
        """{"at": UTC ISO, "archive": file name only} — mode 600 from its first
        byte, and atomic: a temp file beside it, fsynced, then renamed over."""
        from datetime import datetime, timezone

        path = self._backup_last_ok_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {"at": datetime.now(timezone.utc).isoformat(), "archive": Path(archive_path).name}
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(fd, 0o600)  # the umask can only narrow 0o600; this pins it exactly
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _backup_age_words(hours: float) -> str:
        if hours < 48:
            return f"{int(hours)} h"
        return f"{int(hours // 24)} days"

    def _organ_liveness_check(self, root_dir: Optional[Path] = None, now: Optional[Any] = None) -> Dict[str, Any]:
        """One doctor line with her organs' table under it (Batch 6, item 3.8):
        did every organ that is on write, or have nothing to do? A warning when
        one should have written and did not; never a blocker. Read the way a
        separate process must (ai.organ_liveness.install_report): this CLI never
        changes directory, so data/, the database and her repo are anchored at
        the repo root, the flags come from data/palli.yaml (the
        `_own_work_enabled` way), and the database is opened read-only. The
        rows are the table: name, status (wrote / quiet / stale), detail. An
        install whose night cycle is switched off says so, not "no night has
        run yet" forever."""
        from datetime import datetime

        root = Path(root_dir) if root_dir is not None else self.root_dir
        try:
            # Inside the guard: a checker that cannot even be imported is
            # this line's warning too, never a crash of the whole doctor.
            from ai import organ_liveness

            report = organ_liveness.install_report(root, now=now or datetime.now())
            switched_off = report is None and organ_liveness.night_cycle_switched_off(root / "data")
        except Exception as exc:  # noqa: BLE001 - a check that cannot run is a line, not a crash
            why = " ".join(str(exc).split()) or type(exc).__name__
            return {
                "code": "organ_liveness_unreadable",
                "severity": "warning",
                "message": f"Could not check her organs: {why}.",
                "details": {},
            }
        if switched_off:
            return {
                "code": "organ_liveness_night_cycle_off",
                "severity": "info",
                "message": "The night cycle is switched off, so no night runs and nothing nightly is due.",
                "details": {},
            }
        if report is None:
            return {
                "code": "organ_liveness_no_night_yet",
                "severity": "info",
                "message": "No night has run on this install yet, so no organ is due to have written.",
                "details": {},
            }
        width = max((len(e.name) for e in report.entries), default=0)
        rows = [f"{e.name:<{width}}  {e.status:<5}  {e.detail}" for e in report.entries]
        stale = report.stale_names
        if stale:
            return {
                "code": "organ_liveness_stale",
                "severity": "warning",
                "message": f"{len(stale)} organ(s) should have written and did not: {', '.join(stale)}.",
                "details": {"reasons": rows},
            }
        wrote = sum(1 for e in report.entries if e.status == "wrote")
        return {
            "code": "organ_liveness_ok",
            "severity": "info",
            "message": (f"Every organ that is on wrote or had nothing to do "
                        f"({wrote} wrote, {len(report.entries) - wrote} quiet)."),
            "details": {"reasons": rows},
        }

    def _backup_freshness_check(self) -> Dict[str, Any]:
        """One doctor line about the last good backup. Never a blocker: a
        stranger may not use backups at all, so no record is only a note."""
        from datetime import datetime, timezone

        path = self._backup_last_ok_path()
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {
                "code": "backup_none_recorded",
                "severity": "info",
                "message": "No backup recorded yet (a successful `backup` records itself).",
                "details": {},
            }
        except OSError:
            raw = ""  # unreadable: the branch below says so
        try:
            data = json.loads(raw)
            at = datetime.fromisoformat(str(data["at"]))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            archive = str(data.get("archive") or "")
        except (ValueError, KeyError, TypeError, AttributeError):
            return {
                "code": "backup_record_unreadable",
                "severity": "warning",
                "message": f"Cannot read the last-backup record at {path} — check `{self.BACKUP_LOG_HINT}`.",
                "details": {},
            }
        hours = max(0.0, (datetime.now(timezone.utc) - at).total_seconds() / 3600)
        details = {"at": at.isoformat(), "archive": archive, "age_hours": round(hours, 1)}
        age = self._backup_age_words(hours)
        if hours > self.BACKUP_STALE_HOURS:
            return {
                "code": "backup_stale",
                "severity": "warning",
                "message": (
                    f"The last good backup was {age} ago (more than {self.BACKUP_STALE_HOURS} h) — "
                    f"check `{self.BACKUP_LOG_HINT}`."
                ),
                "details": details,
            }
        return {
            "code": "backup_recent",
            "severity": "ok",
            "message": f"The last good backup was {age} ago.",
            "details": details,
        }

    def _backup_prune(self, target_dir: Path, *, encrypted: bool, keep: int, just_written: Path) -> int:
        """Delete all but the newest `keep` archives of this kind (.enc, or plain).

        Newest by the order they were MADE, read from the name: the stamp, then
        the -N. Sorting the names themselves gets a same-second pair backwards
        ("-2" sorts before ".tar") and would prune the newer one. Anything in the
        directory that is not named like ours is never touched.
        """
        ours = []
        try:
            listing = list(target_dir.iterdir())
        except OSError as exc:
            # The archive is already safe; a share that will not list is a
            # reason to keep one too many tonight, not to fail the run.
            print(f"warning: could not list `{target_dir}` to prune it: {exc}", file=sys.stderr)
            return 0
        for path in listing:
            match = self.BACKUP_NAME_PATTERN.match(path.name)
            if match is None or bool(match.group(3)) != encrypted or not path.is_file():
                continue
            ours.append(((match.group(1), int(match.group(2) or 1)), path))
        ours.sort()
        pruned = 0
        for _order, path in ours[:-keep]:
            if path == just_written:
                continue
            try:
                path.unlink()
            except OSError as exc:
                print(f"warning: could not prune `{path}`: {exc}", file=sys.stderr)
                continue
            pruned += 1
        return pruned

    def _backup_home_files(self, top: Path, *, skip: set) -> tuple:
        """Every file under `top` worth archiving, sorted, and how many
        directories could not be read. `skip` holds real paths (logs/, models/,
        the vault while the lock is not held, the target, the archive being
        written) and prunes them wherever the walk meets them."""
        unreadable: list = []
        found: list = []
        for current, dirnames, filenames in os.walk(top, onerror=unreadable.append):
            here = Path(current)
            dirnames[:] = sorted(name for name in dirnames if os.path.realpath(here / name) not in skip)
            for name in sorted(filenames):
                if name == self.BACKUP_PASSFILE_NAME or name.endswith(self.BACKUP_HOME_SKIP_SUFFIXES):
                    continue
                path = here / name
                if os.path.realpath(path) in skip:
                    continue
                found.append(path)
        return found, len(unreadable)

    @staticmethod
    def _backup_add_live_file(archive: Any, path: Path, arcname: str) -> str:
        """Add one file from a tree that changes under us. Returns "added",
        "torn" (it changed size while being read; the archive is still whole),
        "skipped" (it vanished or could not be read: counted, never fatal) or
        "ignored" (a socket or a fifo: not data). An error WRITING the archive
        is not the file's fault, and it propagates."""
        import stat

        try:
            mode = os.lstat(path).st_mode
        except OSError:
            return "skipped"
        if stat.S_ISLNK(mode):
            try:
                info = archive.gettarinfo(str(path), arcname=arcname)
            except OSError:
                return "skipped"
            archive.addfile(info)
            return "added"
        if not stat.S_ISREG(mode):
            return "ignored"
        try:
            handle = open(path, "rb")
        except OSError:
            return "skipped"
        with handle:
            try:
                info = archive.gettarinfo(arcname=arcname, fileobj=handle)
            except OSError:
                return "skipped"
            if info is None:
                return "ignored"
            reader = _FixedLengthReader(handle, info.size)
            archive.addfile(info, reader)
        return "torn" if reader.torn else "added"

    @contextlib.contextmanager
    def _backup_vault_read_lock(self, vault_dir: Path):
        """Hold ~/.palli/vault/.lock SHARED while the vault is copied. Her saves
        take it exclusively (`flock ~/.palli/vault/.lock ...`), so a save can
        never tear the copy. A save still holding it after the wait is not worth
        losing the whole backup over: copy anyway, and say so."""
        import fcntl

        lock_path = vault_dir / ".lock"
        wait = float(self.BACKUP_VAULT_LOCK_WAIT_SECONDS)
        descriptor: Optional[int] = None
        try:
            # flock(2) needs only a read descriptor. O_RDWR would fail on a
            # .lock she cannot write (created by root, say), and the vault
            # would be copied unlocked.
            descriptor = os.open(lock_path, os.O_RDONLY | os.O_CREAT, 0o600)
        except OSError as exc:
            print(
                f"warning: could not open the vault lock `{lock_path}` ({exc.strerror}) — "
                "copying the vault without it",
                file=sys.stderr,
            )
        if descriptor is None:
            yield
            return
        locked = False
        try:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
            if not locked:
                print(
                    f"warning: a vault save held `{lock_path}` for more than {wait:g} s — "
                    "copying the vault without the lock; this copy of it may be torn",
                    file=sys.stderr,
                )
            yield
        finally:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _backup_add_home(self, archive: Any, home_root: Path, counts: Dict[str, int], *, skip: set) -> None:
        vault_dir = home_root / "vault"
        skip = set(skip) | {os.path.realpath(home_root / name) for name in self.BACKUP_HOME_SKIP_DIRS}
        skip |= {os.path.realpath(vault_dir), os.path.realpath(vault_dir / ".lock")}

        def _add(paths: list) -> None:
            for path in paths:
                arcname = f"{self.BACKUP_HOME_ARCNAME}/{path.relative_to(home_root).as_posix()}"
                outcome = self._backup_add_live_file(archive, path, arcname)
                if outcome in ("added", "torn"):
                    counts["home"] += 1
                if outcome in ("skipped", "torn"):
                    counts[outcome] += 1

        files, unreadable = self._backup_home_files(home_root, skip=skip)
        counts["skipped"] += unreadable
        _add(files)
        if vault_dir.is_dir():
            # Listed only once the lock is held: a save that swaps a file in by
            # rename would otherwise change the vault under the listing.
            with self._backup_vault_read_lock(vault_dir):
                files, unreadable = self._backup_home_files(vault_dir, skip=skip)
                counts["skipped"] += unreadable
                _add(files)

    @staticmethod
    def _backup_tree_files(source_tree: Path) -> list:
        return sorted(source_tree.rglob("*"))

    def _backup_write_tar(
        self,
        tar_path: Path,
        counts: Dict[str, int],
        *,
        root: Path,
        home_root: Optional[Path],
        skip: set,
    ) -> None:
        import tarfile
        import tempfile

        with tempfile.TemporaryDirectory() as staging_name:
            staging = Path(staging_name)
            with tarfile.open(tar_path, "w:gz") as archive:
                for tree in self.BACKUP_TREES:
                    source_tree = root / tree
                    if not source_tree.is_dir():
                        continue
                    for path in self._backup_tree_files(source_tree):
                        # --out can point inside data/, and the archive is
                        # opened before this walk starts: without this it
                        # would put its own half-written self inside itself.
                        if path == tar_path:
                            continue
                        if self._backup_excluded(path):
                            continue
                        relative = path.relative_to(root)
                        if path.name.endswith(".sqlite3") and path.is_file():
                            snapshot = staging / relative
                            self._snapshot_database(path, snapshot)
                            archive.add(snapshot, arcname=str(relative))
                            counts["databases"] += 1
                            counts["files"] += 1
                            continue
                        # Everything else here is live too (settings saves,
                        # her notes): a file that vanishes or will not read is
                        # skipped and counted, not the end of the night's run,
                        # and one that shrinks mid-read is padded. Directories
                        # come back "ignored": the files carry the tree.
                        outcome = self._backup_add_live_file(archive, path, str(relative))
                        if outcome in ("added", "torn"):
                            counts["files"] += 1
                        if outcome == "skipped":
                            counts["files_skipped"] += 1
                        elif outcome == "torn":
                            counts["files_torn"] += 1
                if home_root is not None:
                    self._backup_add_home(
                        archive, home_root, counts, skip=skip | {os.path.realpath(staging)}
                    )

    def backup(
        self,
        *,
        out_dir: Optional[str] = None,
        include_home: bool = False,
        encrypt: bool = False,
        passfile: Optional[str] = None,
        keep: Optional[int] = None,
        require_mount: Optional[str] = None,
    ) -> int:
        import stat
        import tempfile

        # First, before anything is created: an unmounted NAS leaves an empty
        # directory where the share should be, and writing into it fills the
        # local disk while looking like a working backup. Skipped, not failed.
        root = Path(self.root_dir)
        target_dir = Path(out_dir).expanduser() if out_dir else root / "backups"
        if require_mount:
            if not os.path.ismount(require_mount):
                print(f"{require_mount} is not mounted — backup skipped")
                return 2
            # A typo in either flag would pass the check above and still write
            # to the local disk: the guard defeated in silence.
            mount_real = os.path.realpath(require_mount)
            if os.path.commonpath([os.path.realpath(target_dir), mount_real]) != mount_real:
                raise RuntimeError(
                    f"`{target_dir}` is not inside `{require_mount}` — --out must be on the "
                    "mount that --require-mount checks."
                )
        if include_home and not encrypt:
            raise RuntimeError(
                "--include-home needs --encrypt: ~/.palli holds the vault and its key, and keys "
                "never go in an archive somebody might hand to somebody else."
            )
        if keep is not None and keep < 1:
            raise RuntimeError("--keep counts the archive just made, so it must be 1 or more.")
        passfile_path: Optional[Path] = None
        openssl: Optional[str] = None
        if encrypt:
            # Checked before any archiving work: a night spent building an
            # archive that cannot be encrypted is a night with no backup.
            passfile_path = (
                Path(passfile).expanduser()
                if passfile
                else Path.home() / ".palli" / self.BACKUP_PASSFILE_NAME
            )
            try:
                mode = passfile_path.stat().st_mode
            except OSError as exc:
                raise RuntimeError(
                    f"No backup passphrase file at `{passfile_path}` ({exc.strerror}) — "
                    "create it (one line), mode 600."
                ) from exc
            if not stat.S_ISREG(mode):
                raise RuntimeError(f"`{passfile_path}` is not a file.")
            if mode & 0o077:
                raise RuntimeError(
                    f"`{passfile_path}` is open to other users (mode {stat.S_IMODE(mode):o}) — "
                    "chmod 600 it: it is the key to every backup."
                )
            try:
                with open(passfile_path, "rb") as handle:
                    first_line = handle.readline(4096)
            except OSError as exc:
                raise RuntimeError(f"Cannot read `{passfile_path}` ({exc.strerror}).") from exc
            # The length only, never the content.
            length = len(first_line.rstrip(b"\r\n").decode("utf-8", errors="replace"))
            if length < self.BACKUP_PASSPHRASE_MIN_CHARS:
                raise RuntimeError(
                    f"The passphrase in `{passfile_path}` is {length} characters; it needs at "
                    f"least {self.BACKUP_PASSPHRASE_MIN_CHARS}. openssl reads only the first line, "
                    "and an empty one encrypts with an empty password."
                )
            openssl = shutil.which(self.BACKUP_OPENSSL)
            if not openssl:
                raise RuntimeError(
                    f"`{self.BACKUP_OPENSSL}` was not found — it is what encrypts the archive."
                )

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Cannot write backups to `{target_dir}`: {exc}") from exc
        archive_path = self._backup_free_name(
            target_dir, self._backup_stamp(), ".tar.gz.enc" if encrypt else ".tar.gz"
        )
        home_root: Optional[Path] = Path.home() / ".palli" if include_home else None
        home_missing = home_root is not None and not home_root.is_dir()
        if home_missing:
            home_root = None
        skip = {os.path.realpath(target_dir)}
        if passfile_path is not None:
            skip.add(os.path.realpath(passfile_path))
        counts = {
            "files": 0,
            "databases": 0,
            "files_skipped": 0,
            "files_torn": 0,
            "home": 0,
            "skipped": 0,
            "torn": 0,
        }

        # A half-written archive that carries the right name is worse than no
        # archive at all: it is the one you reach for after losing the disk.
        # Build under .partial and rename only once the whole tree is in.
        partial_path = archive_path.with_name(archive_path.name + ".partial")
        private_dir: Optional[Path] = None
        try:
            if not encrypt:
                self._backup_write_tar(partial_path, counts, root=root, home_root=home_root, skip=skip)
            else:
                # Only ciphertext ever touches the target, a share the whole LAN
                # can read. The plaintext tar is built in a private local
                # directory (mkdtemp: mode 700) and removed before this returns,
                # success or not.
                private_dir = Path(tempfile.mkdtemp(prefix="palli-backup-"))
                plain_path = private_dir / archive_path.name[: -len(".enc")]
                self._backup_write_tar(
                    plain_path,
                    counts,
                    root=root,
                    home_root=home_root,
                    skip=skip | {os.path.realpath(private_dir)},
                )
                result = subprocess.run(
                    [
                        str(openssl),
                        *self.BACKUP_CIPHER_ARGS,
                        "-in",
                        str(plain_path),
                        "-out",
                        str(partial_path),
                        "-pass",
                        f"file:{passfile_path}",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    detail = (result.stderr or "").strip().splitlines()
                    raise RuntimeError(
                        f"Encrypting the backup failed (openssl exit {result.returncode})"
                        + (f": {detail[-1]}" if detail else "")
                    )
        except OSError as exc:
            partial_path.unlink(missing_ok=True)
            raise RuntimeError(f"Backup to `{archive_path}` failed: {exc}") from exc
        except BaseException:
            partial_path.unlink(missing_ok=True)
            raise
        finally:
            if private_dir is not None:
                shutil.rmtree(private_dir, ignore_errors=True)
        partial_path.replace(archive_path)

        # Paths and counts only: never the passphrase, never a file's contents.
        size_mb = archive_path.stat().st_size / (1024 * 1024)
        print(f"{archive_path}")
        summary = f"{counts['files']} files ({counts['databases']} database snapshots), {size_mb:.1f} MB"
        if counts["files_skipped"]:
            summary += f", {counts['files_skipped']} skipped (vanished or unreadable mid-walk)"
        if counts["files_torn"]:
            summary += f", {counts['files_torn']} changed while being read"
        print(summary)
        if include_home:
            if home_missing:
                print("~/.palli: not found, nothing of it added")
            else:
                line = (
                    f"~/.palli: {counts['home']} files, {counts['skipped']} skipped "
                    "(vanished or unreadable mid-walk)"
                )
                if counts["torn"]:
                    line += f", {counts['torn']} changed while being read"
                print(line)
        if keep is not None:
            pruned = self._backup_prune(
                target_dir, encrypted=encrypt, keep=keep, just_written=archive_path
            )
            print(f"kept the newest {keep}, pruned {pruned}")
        # Only now, with the archive in place and pruned, is this a good run.
        # A record that cannot be written does not undo the backup: doctor
        # will call the old record stale, which is the visible failure.
        try:
            self._write_backup_last_ok(archive_path)
        except OSError as exc:
            print(f"could not record this backup in {self._backup_last_ok_path()}: {exc.strerror or exc}")
        return 0

    def logs(self, *, target: str, follow: bool) -> int:
        normalized = str(target or "launcher").strip().lower()
        if normalized not in {"backend", "frontend", "launcher"}:
            raise RuntimeError(f"Unsupported log target: {normalized}")
        path = launcher_log_path() if normalized == "launcher" else service_log_path(normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        for line in lines[-200:]:
            sys.stdout.write(line)
        sys.stdout.flush()
        if not follow:
            return 0
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                chunk = handle.readline()
                if chunk:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    continue
                time.sleep(0.5)

    def _handle_settings_configure(self) -> int:
        """Interactive settings configurator with guided menus."""
        import sys

        # Ensure Unicode output works on Windows
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass

        from core.settings_boot import boot_settings, settings_service

        boot_settings()
        snapshot = settings_service.snapshot()

        W = 52  # box width

        # Use ASCII-safe box drawing that works on all terminals
        def box(title: str) -> None:
            border = "=" * (W + 2)
            print(f"\n  {border}")
            print(f"  |{title.center(W)}|")
            print(f"  {border}")

        def header(title: str) -> None:
            print(f"\n  === {title} {'=' * max(0, W - len(title) - 6)}")

        def show_current() -> None:
            s = settings_service.snapshot()
            llm = s.llm
            voice = s.voice
            features = s.features
            channels = s.channels
            print("\n  Current Setup:")
            print(f"    Default provider: {llm.default_provider}")
            for task in ("response", "decision", "analysis", "memory",
                         "proactive", "reflection", "web_search", "vision"):
                entry = getattr(llm.routing, task, None)
                if entry:
                    print(f"    {task:<14} {entry.provider} / {entry.model}")
            print(f"    Voice:          {voice.engine.value} engine, "
                  f"{voice.stt_provider.value} STT, {voice.tts_provider.value} TTS")
            feat_list = []
            if features.proactive_conversations:
                feat_list.append("proactive")
            if features.web_search:
                feat_list.append("web-search")
            if features.home_assistant:
                feat_list.append("home-assistant")
            chan_list = []
            if channels.discord.enabled:
                chan_list.append("Discord")
            if channels.telegram.enabled:
                chan_list.append("Telegram")
            if channels.mobile.enabled:
                chan_list.append("Mobile")
            if channels.slack.enabled:
                chan_list.append("Slack")
            print(f"    Features:       {', '.join(feat_list) or 'none'}")
            print(f"    Channels:       {', '.join(chan_list) or 'none'}")

        def apply_changes(changes: Dict[str, Any], reason: str) -> bool:
            try:
                proposal = settings_service.propose(changes, reason=reason)
            except Exception as exc:
                print(f"\n  X Invalid change: {exc}")
                return False
            if not proposal.diff:
                print("\n  - No change -- already set to that value.")
                return True
            print("\n  Proposed changes:")
            for entry in proposal.diff:
                print(f"    {entry['path']}: {entry['old']} -> {entry['new']}")
            if not self._prompt_yes_no("  Apply?", default=True):
                print("  Cancelled.")
                return False
            result = settings_service.apply(proposal.change_id)
            print(f"  [OK] Applied. Rollback token: {result.rollback_token}")
            return True

        def configure_routing() -> None:
            header("LLM Routing")
            s = settings_service.snapshot()
            tasks = ["response", "decision", "analysis", "memory",
                     "proactive", "reflection", "web_search", "vision"]

            print("\n  Which task to change?")
            for i, task in enumerate(tasks, 1):
                entry = getattr(s.llm.routing, task)
                print(f"    {i}. {task:<14} (currently: {entry.provider} / {entry.model})")
            print(f"    9. Change default provider for all tasks")
            print(f"    0. Back")

            raw = input("\n  Select: ").strip()
            if raw == "0" or not raw:
                return
            if raw == "9":
                providers = list(s.providers.keys())
                print("\n  Available providers:")
                for i, p in enumerate(providers, 1):
                    marker = " (current)" if p == s.llm.default_provider else ""
                    print(f"    {i}. {p}{marker}")
                prov_raw = input(f"\n  Select provider [{s.llm.default_provider}]: ").strip()
                if not prov_raw:
                    return
                if prov_raw.isdigit() and 1 <= int(prov_raw) <= len(providers):
                    new_provider = providers[int(prov_raw) - 1]
                else:
                    new_provider = prov_raw
                apply_changes({"llm.default_provider": new_provider}, "configure: default provider")
                return

            if not raw.isdigit() or not (1 <= int(raw) <= len(tasks)):
                print("  Invalid selection.")
                return
            task = tasks[int(raw) - 1]
            current_entry = getattr(s.llm.routing, task)

            # Pick provider
            providers = list(s.providers.keys())
            print(f"\n  Available providers:")
            for i, p in enumerate(providers, 1):
                marker = " (current)" if p == current_entry.provider else ""
                print(f"    {i}. {p}{marker}")
            prov_raw = input(f"\n  Provider [{current_entry.provider}]: ").strip()
            if not prov_raw:
                selected_provider = current_entry.provider
            elif prov_raw.isdigit() and 1 <= int(prov_raw) <= len(providers):
                selected_provider = providers[int(prov_raw) - 1]
            else:
                selected_provider = prov_raw

            # Pick model
            provider_cfg = s.providers.get(selected_provider)
            if provider_cfg and provider_cfg.models:
                print(f"\n  Available models for {selected_provider}:")
                for i, m in enumerate(provider_cfg.models, 1):
                    marker = " (current)" if m.id == current_entry.model else ""
                    ctx = f"  [{m.context_window:,} ctx]" if m.context_window else ""
                    flags = ""
                    if m.supports_reasoning:
                        flags += " [reasoning]"
                    if m.supports_vision:
                        flags += " [vision]"
                    print(f"    {i}. {m.display_name or m.id}{ctx}{flags}{marker}")
                model_raw = input(f"\n  Model [{current_entry.model}]: ").strip()
                if not model_raw:
                    selected_model = current_entry.model
                elif model_raw.isdigit() and 1 <= int(model_raw) <= len(provider_cfg.models):
                    selected_model = provider_cfg.models[int(model_raw) - 1].id
                else:
                    selected_model = model_raw
            else:
                selected_model = self._prompt_text(
                    f"  Model name", default=current_entry.model
                )

            apply_changes(
                {f"llm.routing.{task}": {"provider": selected_provider, "model": selected_model}},
                f"configure: {task} routing",
            )

        def configure_voice() -> None:
            header("Voice")
            s = settings_service.snapshot()
            v = s.voice

            print(f"\n  Current: {v.engine.value} engine, "
                  f"{v.stt_provider.value} STT, {v.tts_provider.value} TTS")
            print(f"  Interaction: {v.interaction_mode.value}")

            # Step 31 (2026-08-20, spec §7): the engine menu item left with
            # the deleted bridge — classic is the only voice engine.
            print("\n  What to change?")
            print("    2. STT provider (deepgram / faster_whisper)")
            print("    3. TTS provider (deepgram / lemonfox / pyttsx3)")
            print("    4. Interaction mode (direct_address / passive)")
            print("    0. Back")

            raw = input("\n  Select: ").strip()
            if raw == "2":
                new = self._prompt_choice(
                    "  STT provider:",
                    choices=["deepgram", "faster_whisper"],
                    default=v.stt_provider.value,
                )
                apply_changes({"voice.stt_provider": new}, "configure: STT provider")
            elif raw == "3":
                new = self._prompt_choice(
                    "  TTS provider:",
                    choices=["deepgram", "lemonfox", "pyttsx3"],
                    default=v.tts_provider.value,
                )
                apply_changes({"voice.tts_provider": new}, "configure: TTS provider")
            elif raw == "4":
                new = self._prompt_choice(
                    "  Interaction mode:",
                    choices=["direct_address", "passive"],
                    default=v.interaction_mode.value,
                )
                apply_changes({"voice.interaction_mode": new}, "configure: interaction mode")

        def configure_features() -> None:
            header("Features")
            s = settings_service.snapshot()
            f = s.features

            toggles = [
                ("proactive_conversations", "Proactive conversations", f.proactive_conversations),
                ("web_search", "Web search", f.web_search),
                ("home_assistant", "Home Assistant", f.home_assistant),
            ]

            print()
            for field, label, current in toggles:
                status = "ON" if current else "OFF"
                print(f"    {label:<26} [{status}]")

            print("\n  Toggle which feature?")
            for i, (field, label, _) in enumerate(toggles, 1):
                print(f"    {i}. {label}")
            print("    0. Back")

            raw = input("\n  Select: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(toggles):
                field, label, current = toggles[int(raw) - 1]
                new_val = not current
                action_word = "enable" if new_val else "disable"
                apply_changes(
                    {f"features.{field}": new_val},
                    f"configure: {action_word} {label}",
                )

        def configure_channels() -> None:
            header("Channels")
            s = settings_service.snapshot()
            c = s.channels

            channel_list = [
                ("discord", "Discord", c.discord.enabled),
                ("telegram", "Telegram", c.telegram.enabled),
                ("slack", "Slack", c.slack.enabled),
            ]

            print()
            for key, label, enabled in channel_list:
                status = "ON" if enabled else "OFF"
                print(f"    {label:<12} [{status}]")

            print("\n  Toggle which channel?")
            for i, (key, label, _) in enumerate(channel_list, 1):
                print(f"    {i}. {label}")
            print("    0. Back")

            raw = input("\n  Select: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(channel_list):
                key, label, current = channel_list[int(raw) - 1]
                new_val = not current
                action_word = "enable" if new_val else "disable"
                apply_changes(
                    {f"channels.{key}": {"enabled": new_val}},
                    f"configure: {action_word} {label}",
                )

        def configure_preset() -> None:
            header("Presets")
            s = settings_service.snapshot()
            presets = list(s.presets.keys())

            if not presets:
                print("\n  No presets defined in palli.yaml.")
                return

            print("\n  Available presets:")
            for i, name in enumerate(presets, 1):
                overrides = s.presets[name].overrides
                summary = ", ".join(f"{k}={v}" if isinstance(v, str) else k for k, v in list(overrides.items())[:3])
                if len(overrides) > 3:
                    summary += f", ... (+{len(overrides) - 3} more)"
                print(f"    {i}. {name}")
                print(f"       {summary}")
            print("    0. Back")

            raw = input("\n  Select: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(presets):
                name = presets[int(raw) - 1]
                apply_changes(
                    s.presets[name].overrides,
                    f"configure: preset {name}",
                )

        def restore_defaults() -> None:
            header("Restore Defaults")
            print("\n  This will reset ALL settings to the factory defaults")
            print("  from .env.template (the fresh-install state).")
            print()
            print("  Your current routing, voice, features, and channel")
            print("  settings will be overwritten. Presets are preserved.")
            if not self._prompt_yes_no("\n  Restore factory defaults?", default=False):
                print("  Cancelled.")
                return

            import yaml as _yaml

            seed_path = Path(__file__).resolve().parent.parent / "data" / "palli.yaml"
            default_yaml = _yaml.safe_load(
                (Path(__file__).resolve().parent.parent / "data" / "palli.yaml").read_text(encoding="utf-8")
            )

            # Build changes that reset every section to the seed values
            from core.settings_schema import PalliSettings

            try:
                seed = PalliSettings.model_validate(default_yaml)
            except Exception as exc:
                print(f"\n  X Could not parse seed defaults: {exc}")
                return

            current = settings_service.snapshot()
            changes: Dict[str, Any] = {}

            # Reset LLM routing
            changes["llm.default_provider"] = seed.llm.default_provider
            for task in ("response", "decision", "analysis", "memory",
                         "proactive", "reflection", "web_search", "vision"):
                seed_entry = getattr(seed.llm.routing, task)
                changes[f"llm.routing.{task}"] = {
                    "provider": seed_entry.provider,
                    "model": seed_entry.model,
                }

            # Reset voice
            changes["voice.engine"] = seed.voice.engine.value
            changes["voice.stt_provider"] = seed.voice.stt_provider.value
            changes["voice.tts_provider"] = seed.voice.tts_provider.value
            changes["voice.interaction_mode"] = seed.voice.interaction_mode.value

            # Reset features
            changes["features.proactive_conversations"] = seed.features.proactive_conversations
            changes["features.web_search"] = seed.features.web_search
            changes["features.home_assistant"] = seed.features.home_assistant

            # Reset channels
            changes["channels.discord"] = {"enabled": seed.channels.discord.enabled}
            changes["channels.telegram"] = {"enabled": seed.channels.telegram.enabled}
            changes["channels.mobile"] = {"enabled": seed.channels.mobile.enabled}
            changes["channels.slack"] = {"enabled": seed.channels.slack.enabled}

            apply_changes(changes, "configure: restore factory defaults")

        # ── Main loop ──────────────────────────────────────────────
        box("PaLLi Settings Configurator")
        show_current()

        while True:
            print("\n  What would you like to configure?")
            print("    1. LLM Routing     (providers and models per task)")
            print("    2. Voice           (engine, STT/TTS provider)")
            print("    3. Features        (toggle proactive, web search, etc.)")
            print("    4. Channels        (enable/disable Discord, Telegram, Slack)")
            print("    5. Apply a preset  (switch everything at once)")
            print("    6. Restore defaults (reset to factory settings)")
            print("    7. Show current    (refresh the overview)")
            print("    0. Done")

            raw = input("\n  Select: ").strip()
            if raw == "0" or raw.lower() in ("done", "exit", "quit", "q"):
                print("\n  Done. Settings saved to data/palli.yaml.\n")
                return 0
            elif raw == "1":
                configure_routing()
            elif raw == "2":
                configure_voice()
            elif raw == "3":
                configure_features()
            elif raw == "4":
                configure_channels()
            elif raw == "5":
                configure_preset()
            elif raw == "6":
                restore_defaults()
            elif raw == "7":
                show_current()
            else:
                print("  Invalid selection.")

    def _handle_settings(self, args: argparse.Namespace) -> int:
        from core.settings_boot import boot_settings, settings_service
        boot_settings()

        action = getattr(args, "settings_action", None)
        if not action:
            print("Specify a settings action: configure, list, get, set, preset")
            return 1

        if action == "configure":
            return self._handle_settings_configure()

        if action == "list":
            import yaml
            print(yaml.safe_dump(settings_service.snapshot().model_dump(mode="json"), sort_keys=False))
            return 0

        if action == "get":
            section = args.section
            data = settings_service.snapshot().model_dump(mode="json")
            value = data.get(section)
            if value is None:
                print(f"Unknown section: {section}")
                return 1
            import yaml
            print(yaml.safe_dump({section: value}, sort_keys=False))
            return 0

        if action == "set":
            dot_path = args.dot_path
            raw_value = args.value
            try:
                value = json.loads(raw_value)
            except (json.JSONDecodeError, ValueError):
                if raw_value.lower() in ("true", "yes", "on"):
                    value: Any = True
                elif raw_value.lower() in ("false", "no", "off"):
                    value = False
                else:
                    value = raw_value

            try:
                proposal = settings_service.propose({dot_path: value}, reason="palli_ctl set")
            except Exception as exc:
                print(f"Invalid change: {exc}")
                return 1
            if not proposal.diff:
                print("No change — already set to that value.")
                return 0
            for entry in proposal.diff:
                print(f"  {entry['path']}: {entry['old']} -> {entry['new']}")
            result = settings_service.apply(proposal.change_id)
            print(f"Applied. Rollback token: {result.rollback_token}")
            return 0

        if action == "preset":
            preset_name = args.preset_name
            snapshot = settings_service.snapshot()
            preset = snapshot.presets.get(preset_name)
            if not preset:
                print(f"Unknown preset: {preset_name}")
                print(f"Available: {', '.join(snapshot.presets.keys())}")
                return 1
            # Ruling 23 (final review F4): this lever writes data/palli.yaml and nothing else,
            # so it must not touch her voice's row. Her voice is set in .env.local, which this
            # command cannot write; moving the yaml row alone leaves the two halves disagreeing.
            overrides = {k: v for k, v in preset.overrides.items() if k != "llm.routing.response"}
            try:
                proposal = settings_service.propose(overrides, reason=f"preset: {preset_name}")
            except Exception as exc:
                print(f"Preset failed validation: {exc}")
                return 1
            if not proposal.diff:
                print("No change — already matches that preset.")
                return 0
            print(
                "Her voice is set in .env.local and is not moved by this command. "
                "To move everything, including her voice: "
                f"{ctl_prefix()} brain {preset_name}"
            )
            print(f"Applying preset '{preset_name}':")
            for entry in proposal.diff:
                print(f"  {entry['path']}: {entry['old']} -> {entry['new']}")
            result = settings_service.apply(proposal.change_id)
            print(f"Applied. Rollback token: {result.rollback_token}")
            return 0

        print(f"Unknown settings action: {action}")
        return 1


def _git_porcelain_env_local() -> str:
    """`git status --porcelain -- .env.local`, or "" when git is unavailable."""
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--", ".env.local"],
            cwd=str(ROOT_DIR), capture_output=True, text=True, timeout=10.0,
        )
        return completed.stdout or ""
    except Exception:
        return ""


def run_brain_command(
    args: argparse.Namespace,
    *,
    env_local: Optional[Path] = None,
    palli_yaml: Optional[Path] = None,
    env_base: Optional[Path] = None,
    input_fn=input,
    prober=None,
    git_status_fn=None,
    out=print,
) -> int:
    """`palli_ctl brain` — who is thinking for PaLLi, and the one honest lever to
    change it (docs/superpowers/specs/2026-09-03-provider-configurator-design.md
    §3.2). Her voice reads .env.local; every other lane reads data/palli.yaml;
    this writes both, behind the capability matrix, in plain English. Every
    seam is injectable so the tests never touch the owner's files or a socket."""
    from core.llm_capabilities import BRAIN_LANE, Level
    from core.settings_service import SettingsService, SettingsServiceError
    from palli_config import brain as _brain
    from palli_config import brain_render as _render
    from palli_config.probes import probe_providers

    live_env = env_local is None
    env_local = env_local or ENV_LOCAL_PATH
    palli_yaml = palli_yaml or (ROOT_DIR / "data" / "palli.yaml")
    if env_base is None and live_env:
        env_base = ENV_PATH
    rollback_path = Path(palli_yaml).parent / _brain.ROLLBACK_FILENAME
    prober = prober if prober is not None else probe_providers
    git_status_fn = git_status_fn or _git_porcelain_env_local
    verbose = bool(getattr(args, "verbose", False))

    ensure_settings_file(Path(palli_yaml))
    service = SettingsService(yaml_path=Path(palli_yaml))
    try:
        settings = service.load()
    except SettingsServiceError as exc:
        out(f"  Cannot read {palli_yaml}: {exc}")
        return 1
    picture = _brain.load_picture(env_local=Path(env_local), palli_yaml=Path(palli_yaml), env_base=env_base)

    if getattr(args, "undo", False):
        try:
            if getattr(args, "dry_run", False):
                # Final review F3: --dry-run writes nothing, and undo is not an exception.
                # Inside the same try, so "nothing to undo" still exits 1.
                out(f"\n  {_brain.undo_preview(rollback_path)}\n")
                return 0
            result = _brain.undo(
                env_local=Path(env_local), settings_service=service,
                rollback_path=rollback_path, git_status=git_status_fn,
            )
        except (_brain.BrainError, SettingsServiceError) as exc:
            out(f"  {exc}")
            return 1
        out(f"\n  {result.message}\n")
        return 0

    preset = getattr(args, "preset", None)
    except_lanes = list(getattr(args, "except_lanes", None) or [])
    pins: Dict[str, tuple] = {}
    lane, to = getattr(args, "lane", None), getattr(args, "to", None)
    if lane or to:
        if not (lane and to and "/" in str(to)):
            out("  --lane needs --to <provider>/<model>, e.g. --lane vision --to claude/haiku")
            return 1
        provider, model = str(to).split("/", 1)
        pins[str(lane)] = (provider, model)

    if getattr(args, "pick", False):
        text, choices = _render.render_pick_menu(picture, settings)
        out(text)
        try:
            raw = str(input_fn("  > ") or "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            out("  Nothing changed.")
            return 0
        if raw in ("", "q", "quit"):
            out("  Nothing changed.")
            return 0
        if not raw.isdecimal() or not (1 <= int(raw) <= len(choices)):
            out("  Not one of the choices. Nothing changed.")
            return 1
        choice = choices[int(raw) - 1]
        preset, except_lanes = choice.preset, list(choice.except_lanes)

    if getattr(args, "show", False) or (preset is None and not pins):
        out(_render.render_show(picture, verbose=verbose))
        return 0

    try:
        target = _brain.resolve_target(picture, settings, preset=preset, except_lanes=except_lanes, pins=pins)
    except _brain.BrainError as exc:
        out(f"  Invalid change: {exc}")
        return 1
    if not target.moving(picture):
        out("\n  No change — she already runs that.\n")
        return 0

    # spec §3.2 step 2: a BLOCK refuses before any probe fires (final review F1)
    checks = _brain.preflight(picture, target, prober=None)
    blocks = [c for c in checks if c.level is Level.BLOCK]
    if blocks and not getattr(args, "force", False):
        alternative = None
        if preset is not None and BRAIN_LANE not in target.excepted:
            alternative = _brain.resolve_target(
                picture, settings, preset=preset, except_lanes=[*except_lanes, BRAIN_LANE], pins=pins,
            )
        out(_render.render_refusal(picture, target, blocks, alternative=alternative))
        return 1

    checks = _brain.preflight(picture, target, prober=prober)

    try:
        plan = _brain.plan_writes(picture, target, env_local=Path(env_local), settings_service=service, checks=checks)
    except (SettingsServiceError, ValueError) as exc:
        out(f"  Invalid change: {_brain.plain_settings_error(exc)}")
        return 1
    out(_render.render_preview(picture, target, checks, verbose=verbose))
    if getattr(args, "dry_run", False):
        out("  Dry run — nothing was written.\n")
        return 0
    if not getattr(args, "yes", False):
        try:
            answer = str(input_fn("  Apply? [y/N] ") or "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            out("  Nothing changed.")
            return 0
        if answer not in ("y", "yes"):
            out("  Nothing changed.")
            return 0
    try:
        applied = _brain.apply_plan(
            plan, env_local=Path(env_local), settings_service=service,
            rollback_path=rollback_path, git_status=git_status_fn,
        )
    except _brain.BrainError as exc:
        out(f"  {exc}")
        return 1
    lines = []
    if verbose:
        lines = [f"rollback record: {applied.rollback_path}", f"settings token (this process only): {applied.rollback_token or '-'}"]
    out(_render.render_applied(verbose_lines=lines))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PaLLi local launcher controller")
    subparsers = parser.add_subparsers(dest="command")

    onboard = subparsers.add_parser("onboard", help="Run first-time local setup for PaLLi")
    onboard.add_argument("--flow", choices=list(ONBOARD_FLOW_CHOICES))
    onboard.add_argument("--preset", help="a preset from data/palli.yaml (palli_ctl brain --pick lists them)")
    onboard.add_argument("--non-interactive", action="store_true")
    onboard.add_argument("--codex-model")
    onboard.add_argument("--codex-profile")
    onboard.add_argument("--skip-doctor", action="store_true")
    onboard.add_argument("--skip-start", action="store_true")

    configure = subparsers.add_parser("configure", help="Write guided PaLLi settings into .env.local")
    configure.add_argument("--preset", help="a preset from data/palli.yaml (palli_ctl brain --pick lists them)")
    configure.add_argument("--section", action="append", choices=list(CONFIG_SECTION_CHOICES))
    configure.add_argument("--non-interactive", action="store_true")
    configure.add_argument("--codex-model")
    configure.add_argument("--codex-profile")
    configure.add_argument("--skip-doctor", action="store_true")

    auth = subparsers.add_parser("auth", help="Manage local runtime authentication helpers")
    auth_subparsers = auth.add_subparsers(dest="auth_command")
    auth_codex = auth_subparsers.add_parser("codex", help="Check or establish Codex CLI authentication")
    auth_codex.add_argument("--status", action="store_true", dest="status_only")
    auth_codex.add_argument("--device-auth", action="store_true")

    doctor = subparsers.add_parser("doctor", help="Validate the local runtime environment")
    doctor.add_argument("--json", action="store_true", dest="json_output")
    doctor.add_argument(
        "--fix",
        action="store_true",
        dest="fix_mode",
        help=(
            "Print operator guidance for any legacy hosted-product residue "
            "(Stripe, MFA, beta-seat artifacts) detected in config or DB, "
            "and for her NAS folder when her work "
            "account cannot write to it. Advisory, with one exception: a "
            "vault file looser than 600 or a vault folder looser than 700 "
            "is set back, and the output says so."
        ),
    )

    prepare = subparsers.add_parser("prepare", help="Install website deps if needed and build the dashboard")
    prepare.add_argument("--json", action="store_true", dest="json_output")

    subparsers.add_parser("start", help="Start backend and dashboard")
    subparsers.add_parser("stop", help="Stop backend and dashboard")
    subparsers.add_parser("restart", help="Restart backend and dashboard")

    status = subparsers.add_parser("status", help="Show launcher status")
    status.add_argument("--json", action="store_true", dest="json_output")

    chat = subparsers.add_parser("chat", help="Talk to PaLLi through the live local backend")
    chat.add_argument("message", nargs="*")
    # --surface-id/--lane-id went with the probe route this used to POST to;
    # /api/chat/message has no such concept (week 1 task 5).
    chat.add_argument("--show-events", action="store_true", help="Also print her turn's beats")

    backup = subparsers.add_parser(
        "backup",
        help="Archive data/ and identity/ — her memory and her story — to backups/",
    )
    backup.add_argument("--out", dest="out_dir", default=None, help="Write the archive here instead")
    backup.add_argument(
        "--include-home",
        dest="include_home",
        action="store_true",
        help="Also archive ~/.palli (the vault and its key); needs --encrypt",
    )
    backup.add_argument(
        "--encrypt",
        action="store_true",
        help="Encrypt with openssl (AES-256-CBC, PBKDF2); only ciphertext reaches --out",
    )
    backup.add_argument(
        "--passfile",
        default=None,
        help="Passphrase file for --encrypt, mode 600 (default ~/.palli/backup.passphrase)",
    )
    backup.add_argument(
        "--keep", type=int, default=None, help="Keep only the newest N archives of this kind"
    )
    backup.add_argument(
        "--require-mount",
        dest="require_mount",
        default=None,
        help="Skip (exit 2) unless this path is a mount point, e.g. the NAS share",
    )

    logs = subparsers.add_parser("logs", help="Show launcher logs")
    logs.add_argument("target", nargs="?", default="launcher", choices=["backend", "frontend", "launcher"])
    logs.add_argument("--follow", action="store_true")

    settings_parser = subparsers.add_parser("settings", help="View and modify PaLLi settings")
    settings_subparsers = settings_parser.add_subparsers(dest="settings_action")

    settings_subparsers.add_parser("configure", help="Interactive guided settings editor")

    settings_subparsers.add_parser("list", help="Show all current settings")

    get_parser = settings_subparsers.add_parser("get", help="Show a specific settings section")
    get_parser.add_argument("section", help="Section name (e.g., llm, voice, features)")

    set_parser = settings_subparsers.add_parser("set", help="Change a single setting")
    set_parser.add_argument("dot_path", help="Dot-path to the setting (e.g., llm.default_provider)")
    set_parser.add_argument("value", help="New value (string, boolean, or JSON)")

    preset_parser = settings_subparsers.add_parser("preset", help="Apply a named preset")
    preset_parser.add_argument("preset_name", help="Preset name (e.g., codex-primary)")

    brain = subparsers.add_parser("brain", help="Who is thinking for PaLLi — see it, or change it")
    brain.add_argument("preset", nargs="?", help="a preset from data/palli.yaml (see --pick)")
    brain.add_argument("--show", action="store_true", help="the live picture (the default)")
    brain.add_argument("--pick", action="store_true", help="choose a setup from a list, in English")
    brain.add_argument("--except", dest="except_lanes", action="append", default=[], metavar="LANE",
                       help="leave this lane where it is (repeatable)")
    brain.add_argument("--lane", metavar="LANE", help="move one lane (with --to)")
    brain.add_argument("--to", metavar="PROVIDER/MODEL", help="where --lane goes, e.g. claude/haiku")
    brain.add_argument("--undo", action="store_true", help="reverse the last change, both files")
    brain.add_argument("--dry-run", action="store_true", dest="dry_run", help="everything up to the write, then stop")
    brain.add_argument("--verbose", action="store_true", help="add the dot-paths, env keys and dialects")
    brain.add_argument("--force", action="store_true", help="proceed past a refusal (prints what it overrides)")
    brain.add_argument("--yes", "-y", action="store_true", help="do not ask before writing")

    forge_parser = subparsers.add_parser(
        "forge", help="See or toggle the tools PaLLi forged for herself (step 34)"
    )
    forge_subparsers = forge_parser.add_subparsers(dest="forge_action")
    forge_subparsers.add_parser("list", help="Show forged tools and their state")
    forge_revoke = forge_subparsers.add_parser(
        "revoke", help="Bench a forged tool (sets enabled=false in the manifest)"
    )
    forge_revoke.add_argument("name", help="Forged tool name")
    forge_restore = forge_subparsers.add_parser(
        "restore", help="Return a benched forged tool to her belt (resets errors)"
    )
    forge_restore.add_argument("name", help="Forged tool name")

    collaborators_parser = subparsers.add_parser(
        "collaborators",
        help="Who also sees her work in shared rooms: list, add or remove a collaborator",
    )
    collaborators_subparsers = collaborators_parser.add_subparsers(dest="collaborators_action")
    collaborators_subparsers.add_parser("list", help="Show the collaborators you named")
    collaborators_add = collaborators_subparsers.add_parser(
        "add", help="Name someone a collaborator on one door"
    )
    collaborators_add.add_argument("platform", choices=("discord", "telegram"), help="the door")
    collaborators_add.add_argument("user_id", help="their numeric user id on that door")
    collaborators_add.add_argument("name", nargs="?", default="", help="a name to show in the list")
    collaborators_remove = collaborators_subparsers.add_parser(
        "remove", help="Make a collaborator a stranger again"
    )
    collaborators_remove.add_argument("platform", choices=("discord", "telegram"), help="the door")
    collaborators_remove.add_argument("user_id", help="their numeric user id on that door")
    phone_parser = subparsers.add_parser(
        "phone", help="The phone app: pair a phone, list the paired ones, or unpair one"
    )
    phone_subparsers = phone_parser.add_subparsers(dest="phone_action")
    phone_subparsers.add_parser("pair", help="Show a QR code and a one-use code to pair a phone")
    phone_subparsers.add_parser("list", help="Show the paired phones")
    phone_revoke = phone_subparsers.add_parser("revoke", help="Unpair a phone")
    phone_revoke.add_argument("device_id", type=int, help="its id, from `phone list`")

    quiet_parser = subparsers.add_parser(
        "quiet",
        help="Her record of quiet choices: when she chose not to answer, who wrote, and why",
    )
    quiet_parser.add_argument(
        "--days", type=int, default=7, metavar="N", help="how many days back, today included (default 7)"
    )

    replies_parser = subparsers.add_parser(
        "replies",
        help="What each reply used: the lines from the record, the memory notes and facts, and the searches",
    )
    replies_parser.add_argument(
        "--days", type=int, default=3, metavar="N", help="how many days back, today included (default 3)"
    )
    replies_parser.add_argument(
        "--last", type=int, default=None, metavar="N", help="only the N most recent replies"
    )

    presence_parser = subparsers.add_parser(
        "presence",
        help="How much of PaLLi's own reaches you: stakes voiced, replies, self-started messages, own time (read-only)",
    )
    presence_parser.add_argument(
        "--days", type=int, default=30, metavar="N", help="how many days back, today included (default 30)"
    )
    presence_parser.add_argument("--json", dest="json_output", action="store_true", help="the counts as JSON")
    presence_parser.add_argument(
        "--save", action="store_true", help="also write the counts to data/presence/<date>.json as a baseline"
    )
    presence_parser.add_argument(
        "--judge", action="store_true",
        help="also have the model read each reply with the message before it and count the ones with "
             "something of PaLLi's own (one model call per reply)",
    )
    presence_parser.add_argument(
        "--judge-limit", dest="judge_limit", type=int, default=500, metavar="N",
        help="with --judge: at most the N most recent distinct replies (default 500)",
    )

    spending_parser = subparsers.add_parser(
        "spending",
        help="The spending check: today's AI spending so far, a normal day, the balance and the last alert",
    )
    spending_subparsers = spending_parser.add_subparsers(dest="spending_action")
    spending_calls = spending_subparsers.add_parser(
        "calls",
        help="Her AI calls one by one: the cost of each lane, the most expensive calls, checked against the balance",
    )
    spending_calls.add_argument("--day", metavar="YYYY-MM-DD", help="which day (default today)")
    spending_calls.add_argument(
        "--days", type=int, default=1, metavar="N", help="N days ending on --day (default 1)"
    )
    spending_calls.add_argument("--lane", metavar="NAME", help="only this lane, with every call listed")
    spending_calls.add_argument(
        "--top", type=int, default=10, metavar="N", help="how many of the most expensive calls (default 10)"
    )
    spending_calls.add_argument("--every", action="store_true", help="list every call in time order")

    memory_parser = subparsers.add_parser(
        "memory",
        help="Her memory of you: what the nightly sweep archived, bring a line back, "
        "or build the searchable record of every message",
    )
    memory_subparsers = memory_parser.add_subparsers(dest="memory_action")
    memory_archived = memory_subparsers.add_parser(
        "archived", help="List the lines the sweep moved to MEMORY_ARCHIVE.md, with their ids"
    )
    memory_archived.add_argument(
        "--grep", default=None, metavar="TEXT", help="only lines containing TEXT (case-insensitive)"
    )
    memory_restore = memory_subparsers.add_parser(
        "restore", help="Move one archived line back into the same section of MEMORY.md"
    )
    memory_restore.add_argument("item_id", metavar="mcb_id", help="the line's id (see `memory archived`)")
    memory_reach_build = memory_subparsers.add_parser(
        "reach-build",
        help="Build or rebuild the searchable record of every message, from the database and the room files",
    )
    memory_reach_build.add_argument(
        "--rebuild",
        action="store_true",
        help="start from nothing beside the record and swap it in at the end "
        "(refused while PaLLi has the record open; the old record then stays as it was)",
    )
    memory_subparsers.add_parser(
        "reach-status",
        help="What the searchable record holds: lines, rooms, first and last day, "
        "lines still without meaning, and the model that made it",
    )

    threads_parser = subparsers.add_parser(
        "threads", help="Her follow-up board: what was retired, or put a thread back on it"
    )
    threads_subparsers = threads_parser.add_subparsers(dest="threads_action")
    threads_retired = threads_subparsers.add_parser(
        "retired", help="List the dismissed threads (retired unsurfaced, or the backstop's), with their ids"
    )
    threads_retired.add_argument(
        "--grep", default=None, metavar="TEXT", help="only threads whose line contains TEXT (case-insensitive)"
    )
    threads_unretire = threads_subparsers.add_parser(
        "unretire", help="Return one dismissed thread to the board"
    )
    threads_unretire.add_argument("thread_id", metavar="id", help="the thread's id (see `threads retired`)")

    onboarding_parser = subparsers.add_parser(
        "onboarding", help="Answer one of her first-contact questions by hand"
    )
    onboarding_subparsers = onboarding_parser.add_subparsers(dest="onboarding_action")
    onboarding_answer = onboarding_subparsers.add_parser(
        "answer", help="Record pref_<target>: <value> on the owner's preferences document"
    )
    onboarding_answer.add_argument("target", help="the question's target, e.g. checkin_posture")
    onboarding_answer.add_argument("value", help="your answer, as free text")

    history_parser = subparsers.add_parser(
        "history",
        help="Her identity documents' saved copies: what the nightly prune keeps, or prune them now",
    )
    history_subparsers = history_parser.add_subparsers(dest="history_action")
    prune_text = (
        "Keep the newest copies of each identity document (memory.history_keep_last), one a day "
        "for memory.history_daily_days days, one a week before that and the first ever; remove the "
        "rest. The live documents are never touched. The nightly cycle does the same on its own."
    )
    history_prune = history_subparsers.add_parser("prune", help=prune_text, description=prune_text)
    history_prune.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be kept and removed, per document, with sizes; change nothing",
    )

    db_parser = subparsers.add_parser("db", help="Her database: give the space a prune freed back to the disk")
    db_subparsers = db_parser.add_subparsers(dest="db_action")
    shrink_text = (
        "Rebuild her database file without its free pages (VACUUM with secure_delete on, then truncate the "
        "write-ahead log), so the space a prune freed goes back to the disk. Take a fresh backup first "
        f"(`{ctl_prefix()} backup`). Refuses while a backup runs, from 03:00 to 04:30, around the "
        "nightly 03:20 backup, and in the first half hour of her night pass (06:30 by default). "
        "She can keep running; her writes wait about a second."
    )
    db_subparsers.add_parser("shrink", help=shrink_text, description=shrink_text)

    return parser


def run_forge_command(action: Optional[str], name: str = "") -> int:
    """His lever, never required (step 34 spec §6): the forge itself is
    autonomous — these commands only observe and toggle what she built.
    Takes effect on her very next turn (the registry is rebuilt per turn)."""
    from ai import forge_manifest

    entries = forge_manifest.read_manifest()
    if action in (None, "list"):
        if not entries:
            print("No forged tools yet.")
            return 0
        for entry in entries:
            state = "enabled" if entry.get("enabled") else "revoked/benched"
            print(
                f"{entry.get('name')}  [{state}]  "
                f"forged {str(entry.get('created_at', ''))[:19]}  "
                f"errors={entry.get('consecutive_errors', 0)}  "
                f"ask: {str(entry.get('original_ask', ''))[:60]}"
            )
        return 0
    if action in ("revoke", "restore"):
        if forge_manifest.get_entry(name) is None:
            print(f"No forged tool named {name!r}.", file=sys.stderr)
            return 1
        if action == "revoke":
            forge_manifest.update_entry(name, enabled=False)
            print(f"{name} benched (enabled=false). "
                  f"`{ctl_prefix()} forge restore {name}` undoes this.")
        else:
            forge_manifest.update_entry(name, enabled=True,
                                        consecutive_errors=0)
            print(f"{name} restored to her belt (error count reset).")
        return 0
    print(f"Unknown forge action {action!r}. Use list, revoke, or restore.",
          file=sys.stderr)
    return 1


def run_collaborators_command(args: argparse.Namespace, *, out=print, err=None) -> int:
    """`palli_ctl collaborators list | add <platform> <id> [name] | remove
    <platform> <id>` (Batch 8, task 1 — B8-1, rulings R8/R9). In shared rooms
    a collaborator also sees the work she did there; everyone else but you is
    a stranger and sees only what was said out loud. The same list her
    owner-only `collaborators` tool keeps (data/collaborators.json), so a
    change here is live on her very next turn."""
    from core import collaborators

    if err is None:
        def err(message: str) -> None:
            print(message, file=sys.stderr)

    action = getattr(args, "collaborators_action", None)
    try:
        if action in (None, "list"):
            entries = collaborators.list_collaborators()
            if not entries:
                out("No collaborators yet: in shared rooms, everyone but you is a stranger.")
                return 0
            for entry in entries:
                out(f"{collaborators.describe(entry)}  added {str(entry.get('added_at', ''))[:19]}")
            return 0
        platform = str(getattr(args, "platform", "") or "")
        user_id = str(getattr(args, "user_id", "") or "")
        if action == "add":
            name = str(getattr(args, "name", "") or "")
            if collaborators.add_collaborator(platform, user_id, name=name):
                out(f"{collaborators.describe({'platform': platform, 'user_id': user_id, 'name': name})} "
                    "is a collaborator now.")
            else:
                out(f"{platform} {user_id} was already a collaborator.")
            return 0
        if action == "remove":
            if collaborators.remove_collaborator(platform, user_id):
                out(f"{platform} {user_id} is a stranger again.")
                return 0
            err(f"{platform} {user_id} is not on the list.")
            return 1
    except ValueError as exc:
        err(f"Not changed: {exc}.")
        return 1
    except collaborators.CollaboratorStoreError as exc:
        err(f"The collaborators list can't be read ({exc}); nothing was changed.")
        return 1
    err(f"Unknown collaborators action {action!r}. Use list, add, or remove.")
    return 1


def run_presence_command(args: argparse.Namespace, *, out=print) -> int:
    """`palli_ctl presence [--days N] [--json] [--save] [--judge [--judge-limit N]]`
    (the presence work, step one, 2026-10-11): how much of PaLLi's own reaches
    the owner, recounted from the install's own ledgers and database, read-only,
    with what each number counts said beside it (core/presence_report.py).
    `--save` writes the counts to data/presence/<date>.json so a later run
    compares against a baseline. `--judge` (2026-10-12) adds the model-judged
    count of replies with something of PaLLi's own in them; the counts are
    collected once, printed and saved from the same run."""
    import json as _json

    from core import presence_report

    days = max(1, int(getattr(args, "days", 30) or 30))
    data_dir = ROOT_DIR / "data"
    db_path = Path(str(getattr(config, "DATABASE_PATH", "") or "data/palli-local.sqlite3"))
    if not db_path.is_absolute():
        db_path = ROOT_DIR / db_path
    kwargs = dict(data_dir=data_dir, db_path=db_path, repo_root=ROOT_DIR, days=days)
    if getattr(args, "judge", False):
        kwargs["judge"] = presence_report.model_judge
        kwargs["judge_limit"] = max(1, int(getattr(args, "judge_limit", 500) or 500))
    counts = presence_report.collect(**kwargs)
    if getattr(args, "json_output", False):
        out(_json.dumps(counts, indent=1, sort_keys=True))
    else:
        for line in presence_report.lines_for(counts):
            out(line)
    if getattr(args, "save", False):
        saved = presence_report.write_baseline(data_dir / "presence", counts=counts)
        out(f"Saved: {saved}")
    return 0


def run_quiet_command(args: argparse.Namespace, *, out=print) -> int:
    """`palli_ctl quiet [--days N]` (Batch 8, task 2a — ruling T2-4): her record
    of quiet choices, oldest first — when, which door, who wrote, the start
    of what they said, and her own one-line reason. The same record she reads
    through her introspect tool (data/quiet_choices/)."""
    from core import quiet_record

    days = max(1, int(getattr(args, "days", 7) or 7))
    rows = quiet_record.read(days)
    if not rows:
        out(f"No quiet choices in the last {days} day{'s' if days != 1 else ''}.")
        return 0
    for row in rows:
        out(quiet_record.describe(row))
    return 0


def run_replies_command(args: argparse.Namespace, *, out=print) -> int:
    """`palli_ctl replies [--days N] [--last N]` (R15, memory that reaches,
    T13): what each reply used, oldest first. One line per reply (when, who,
    the start of PaLLi's reply, how many lines from the record, memory notes
    and facts), then what it drew on, one item a line. The same record as
    the dashboards' /replies page (data/reply_sources/), read with nothing
    running."""
    from core import reply_sources

    days = max(1, int(getattr(args, "days", 3) or 3))
    rows = reply_sources.read(days)
    last = int(getattr(args, "last", None) or 0)
    if last > 0:
        rows = rows[-last:]
    if not rows:
        out(f"No replies recorded in the last {days} day{'s' if days != 1 else ''}.")
        return 0
    for row in rows:
        out(reply_sources.describe(row))
        for line in reply_sources.details(row):
            out(f"    {line}")
    return 0


def run_spending_command(args: argparse.Namespace, *, out=print) -> int:
    """`palli_ctl spending` (owner decision T2b-6, 2026-10-04): what the
    spending check has measured — today's AI spending so far, a normal day,
    the threshold, the balance and the last alert. Read-only: it reads
    data/spending/ and never asks the provider. `palli_ctl spending calls`
    (the owner, 10-05: "call by call"): the call ledger, lane by lane."""
    if getattr(args, "spending_action", None) == "calls":
        from datetime import date

        from core import spending_calls

        try:
            day = date.fromisoformat(args.day) if args.day else None
        except ValueError:
            out(f"--day wants YYYY-MM-DD, not {args.day!r}")
            return 2
        for line in spending_calls.report_lines(
            day=day, days=max(1, args.days), lane=args.lane, top=max(0, args.top), every=args.every,
        ):
            out(line)
        return 0
    from core import spending

    for line in spending.report_lines():
        out(line)
    return 0


def _point_identity_service_at(root_dir: Path) -> None:
    """The identity store bakes a RELATIVE IDENTITY_ROOT_PATH (the default,
    `identity`) against the current directory, and this CLI never chdirs; so
    a relative setting is resolved against ``root_dir`` (the repo) and the
    service's roots repointed. An absolute setting is left alone."""
    configured = str(getattr(config, "IDENTITY_ROOT_PATH", "identity") or "identity")
    if Path(configured).is_absolute():
        return
    from core.identity.document_store import IdentityDocumentStore
    from core.identity.materialization import IdentityBundleMaterializer
    from core.identity.service import identity_service

    store = IdentityDocumentStore(identity_root=str(root_dir / configured))
    identity_service.document_store = store
    identity_service.materializer = IdentityBundleMaterializer(document_store=store)
    identity_service.identity_root = store.identity_root
    identity_service.core_root = store.core_root
    identity_service.template_root = store.template_root
    identity_service.user_root = store.user_root
    identity_service.owner_root = store.owner_root
    identity_service._compiled_cache = {}


def _memory_owner_id() -> int:
    """The local owner's app user id, from the brain (the night cycle's own
    resolver)."""
    from ai.night_cycle import _resolve_owner_id

    owner_id = asyncio.run(_resolve_owner_id())
    if owner_id <= 0:
        raise RuntimeError("No local owner is set up yet, so there is no memory to look at.")
    return owner_id


def run_memory_command(
    args: argparse.Namespace,
    *,
    owner_id: Optional[int] = None,
    out=print,
    builder: Any = None,
    palli_running: Any = None,
) -> int:
    """`palli_ctl memory archived [--grep TEXT]` and `palli_ctl memory restore
    <mcb_id>` (Batch 5, memory that picks, rulings R29/R43). The nightly sweep
    moves a long-unused line of MEMORY.md to MEMORY_ARCHIVE.md; these are the
    owner's two levers over it: see what moved, with the id each line is known
    by, and bring one line back. Both write through the versioned store.

    `palli_ctl memory reach-build [--rebuild]` and `palli_ctl memory
    reach-status` (R15, memory that reaches): build the searchable record of
    every message and see what it holds (`_run_memory_reach`). `builder` and
    `palli_running` stand in for the real build and the launcher's look."""
    action = getattr(args, "memory_action", None)
    if action in ("reach-build", "reach-status"):
        return _run_memory_reach(action, args, out=out, builder=builder, palli_running=palli_running)
    if action not in ("archived", "restore"):
        raise RuntimeError(
            f"Specify a memory action. Examples: `{ctl_prefix()} memory archived`, "
            f"`{ctl_prefix()} memory restore <mcb_id>`, `{ctl_prefix()} memory reach-build`, "
            f"`{ctl_prefix()} memory reach-status`."
        )
    from ai import memory_sweep

    _point_identity_service_at(ROOT_DIR)
    owner = int(owner_id) if owner_id is not None else _memory_owner_id()
    if action == "archived":
        needle = str(getattr(args, "grep", None) or "").strip().lower()
        archived = asyncio.run(memory_sweep.list_archived(owner))
        if needle:
            archived = [line for line in archived if needle in line.text.lower()]
        if not archived:
            out(f"Nothing archived matches {needle!r}." if needle else "Nothing archived yet.")
            return 0
        for line in archived:
            section = line.section[3:] if line.section.startswith("## ") else line.section
            out(f"{line.item_id}  {section}  {line.day or '-'}  {line.text}")
        return 0
    item_id = str(getattr(args, "item_id", "") or "").strip()
    try:
        result = asyncio.run(memory_sweep.restore_line(owner, item_id))
    except LookupError:
        raise RuntimeError(
            f"No archived line has the id {item_id}. `{ctl_prefix()} memory archived` lists them."
        ) from None
    section = str(result["section"])
    section = section[3:] if section.startswith("## ") else section
    if result.get("inserted", True):
        out(f"Restored to {section} of MEMORY.md: {result['line']}")
    else:
        out(f"Already in {section} of MEMORY.md: {result['line']} — removed from the archive.")
    return 0


def _palli_running() -> bool:
    """Whether PaLLi is up, by the launcher's own look (`start` refuses on the
    same answer): its record of a live pid, or the backend port held by this
    checkout's main.py. A look that fails reads as not running; it only decides
    a warning, and the rebuild's own refusal is the real guard."""
    try:
        return str(inspect_launcher_service("backend").get("ownership") or "") in ("launcher", "self")
    except Exception:
        return False


def _reach_counts(counts: Dict[str, Any]) -> str:
    """The record's counts on one line: rows= rooms= first= last= vectors_missing=."""
    return (
        f"rows={counts.get('rows', 0)} rooms={counts.get('rooms', 0)} "
        f"first={counts.get('first_day') or '-'} last={counts.get('last_day') or '-'} "
        f"vectors_missing={counts.get('vectors_missing', 0)}"
    )


def _reach_meaning(stored: Optional[str], current: str, local_on: bool) -> str:
    """One line on the record's meaning: the model that made its vectors, and
    whether the settings still name that model."""
    if not local_on:
        held = f" (the vectors it has are {stored}'s)" if stored else ""
        return f"meaning: off in the settings (MEMORY_LOCAL_EMBEDDINGS_ENABLED), words only{held}"
    if stored and stored != current:
        return (
            f"meaning: made by {stored}, but the settings now name {current}; "
            f"`{ctl_prefix()} memory reach-build` (or PaLLi's next search) makes every line's meaning again"
        )
    if stored:
        return f"meaning: {stored}"
    return f"meaning: none yet ({current} makes it)"


def _run_memory_reach(action: str, args: argparse.Namespace, *, out, builder: Any, palli_running: Any) -> int:
    """`memory reach-build [--rebuild]` (ai.memory_reach_index.build: both
    sources, words and meaning, no time budget; its progress lines as they
    come, then the counts) and `memory reach-status` (the record read only,
    never made: a missing record is said, not created)."""
    from ai import memory_reach_index as reach

    if action == "reach-status":
        return _memory_reach_status(reach, out=out)
    rebuild = bool(getattr(args, "rebuild", False))
    if rebuild and (palli_running or _palli_running)():
        out(
            "PaLLi is running: the rebuild may be refused if PaLLi has the record open when the new "
            "one is swapped in. Nothing is lost then; stop PaLLi or wait until it is quiet, and run it again."
        )
    build = builder or reach.build
    try:
        result = asyncio.run(build(rebuild=rebuild, progress=out))
    except reach.RecordInUse:
        raise RuntimeError(
            "The record is in use (PaLLi has it open), so the rebuild was not swapped in and the old "
            "record is as it was. Stop PaLLi or wait until it is quiet, then run it again."
        ) from None
    out(f"{_reach_counts(result)} seconds={result.get('seconds', 0)}")
    out(_reach_meaning(result.get("embed_model"), reach.embed_model_name(), reach.local_embeddings_on()))
    error = str(result.get("embed_error") or "")
    if error:
        out(
            f"Embedding stopped ({error}): {result.get('vectors_missing', 0)} lines have words only for now. "
            f"PaLLi's searches fill them in, or run `{ctl_prefix()} memory reach-build` again."
        )
        return 1
    return 0


def _memory_reach_status(reach: Any, *, out) -> int:
    """`memory reach-status`: the record opened read-only (a missing one is
    said, never made), its counts and the model that made its meaning."""
    import sqlite3

    from ai import memory_reach_search

    path = reach.record_path()
    try:
        conn = memory_reach_search.open_record(path)
        if conn is None:
            out(
                f"The searchable record hasn't been built yet (nothing at {path}). "
                f"`{ctl_prefix()} memory reach-build` builds it."
            )
            return 0
        try:
            counts = reach.status(conn)
            stored = reach.get_state(conn, reach.EMBED_MODEL_KEY)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"The record at {path} can't be read ({type(exc).__name__}: {exc}). "
            f"`{ctl_prefix()} memory reach-build --rebuild` makes it again."
        ) from None
    out(f"record: {path}")
    out(_reach_counts(counts))
    out(_reach_meaning(stored, reach.embed_model_name(), reach.local_embeddings_on()))
    return 0


def run_threads_command(args: argparse.Namespace, *, owner_id: Optional[int] = None, out=print) -> int:
    """`palli_ctl threads retired [--grep TEXT]` and `palli_ctl threads unretire
    <id>` (Batch 5, memory that picks, rulings R14/R50). The nightly retirement
    dismisses a thread her judgment never surfaced once it is older than
    memory.thread_archive_days, and the arbiter's backstop dismisses old ones;
    these are the owner's two levers: see what was set down, with the id each
    thread is known by, and put one back on the board. Nothing is deleted."""
    action = getattr(args, "threads_action", None)
    if action not in ("retired", "unretire"):
        raise RuntimeError(
            f"Specify a threads action. Examples: `{ctl_prefix()} threads retired`, "
            f"`{ctl_prefix()} threads unretire <id>`."
        )
    from core.database import brain

    owner = int(owner_id) if owner_id is not None else _memory_owner_id()
    rows = asyncio.run(brain.list_dismissed_open_threads(owner_user_id=owner))
    if action == "retired":
        needle = str(getattr(args, "grep", None) or "").strip().lower()
        now = time.time()
        lines: List[str] = []
        for t in rows:
            # the age the two age rules see (core.open_threads._AGED_AT):
            # MAX(created_at, COALESCE(updated_at, created_at)) — the last touch
            created = float(t.created_at or now)
            touched = max(created, float(getattr(t, "updated_at", None) or created))
            age_d = max(0, int((now - touched) // 86400))
            kind = getattr(t.source_kind, "value", str(t.source_kind))
            lines.append(f"{t.id}  {kind}  {age_d}d  {t.resolution_reason or '-'}  {t.topic_summary}")
        if needle:
            lines = [line for line in lines if needle in line.lower()]
        if not lines:
            out(f"No retired threads match {needle!r}." if needle else "No retired threads.")
            return 0
        for line in lines:
            out(line)
        return 0
    thread_id = str(getattr(args, "thread_id", "") or "").strip()
    # the owner's own dismissed rows only — an id from another owner's board is unknown here
    match = next((t for t in rows if t.id == thread_id), None)
    if match is None or not asyncio.run(brain.unretire_open_thread(thread_id=thread_id)):
        raise RuntimeError(
            f"No retired thread has the id {thread_id}. `{ctl_prefix()} threads retired` lists them."
        )
    out(f"Back on the board: [{thread_id[:8]}] {match.topic_summary}")
    return 0


def run_onboarding_command(args: argparse.Namespace, *, owner_id: Optional[int] = None, out=print) -> int:
    """`palli_ctl onboarding answer <target> <value>` (Batch 5, memory that
    picks, rulings R15/R17). Writes `pref_<target>: <value>` on the owner's
    preferences document through the versioned store — the key
    core.first_contact_targets reads to close the target, so the question
    stops being raised. Free text: FIRST_CONTACT.md's options are suggestions,
    not a vocabulary."""
    action = getattr(args, "onboarding_action", None)
    if action != "answer":
        raise RuntimeError(
            f"Specify an onboarding action. Example: `{ctl_prefix()} onboarding answer checkin_posture proactive`."
        )
    target = str(getattr(args, "target", "") or "").strip()
    if target.startswith("pref_"):
        target = target[len("pref_"):]
    value = " ".join(str(getattr(args, "value", "") or "").split())
    if not target or not value:
        raise RuntimeError(
            f"Both a target and a value are needed, e.g. `{ctl_prefix()} onboarding answer checkin_posture proactive`."
        )
    from core.first_contact_targets import known_targets
    from core.identity.service import identity_service

    _point_identity_service_at(ROOT_DIR)
    # Fix round 1 (M9): the target must be one of her first-contact questions
    # — a typo such as `checkin_postur` would write a junk pref_ key onto the
    # live preferences document, which then renders in every reply.
    valid = known_targets(identity_service.identity_root)
    if not valid:
        fc_path = Path(identity_service.identity_root) / "bootstrap" / "FIRST_CONTACT.md"
        raise RuntimeError(
            f"Cannot read the first-contact targets: {fc_path} is missing or unreadable; nothing was recorded."
        )
    if target not in valid:
        raise RuntimeError(
            f"{target!r} is not one of her first-contact questions. Valid targets: {', '.join(valid)}."
        )
    owner = int(owner_id) if owner_id is not None else _memory_owner_id()
    key = f"pref_{target}"
    written = asyncio.run(
        identity_service.silently_maintain_document(
            user_id=owner,
            platform="owner",
            doc_key="preferences",
            updates={key: value},
            actor_id="palli_ctl",
            reason=f"onboarding_answer:{target}",
            identity_owner_id=owner,
        )
    )
    if written is None:
        raise RuntimeError(f"The preferences document refused the write of {key}; nothing was recorded.")
    out(f"Recorded {key}: {value} on the owner's preferences document.")
    return 0


def _size(count: int) -> str:
    if count < 1000:
        return f"{count} B"
    if count < 1_000_000:
        return f"{count / 1000:.1f} KB"
    return f"{count / 1_000_000:.1f} MB"


def _history_knobs() -> tuple:
    """(memory.history_keep_last, memory.history_daily_days) from data/palli.yaml.
    Settings that cannot be read stop the command: a silent fall back to the
    defaults would read as "the prune is off"."""
    from core.identity import history_prune
    from core.settings_boot import settings_service

    try:
        memory = settings_service.snapshot().memory
    except Exception as exc:
        raise RuntimeError(f"Cannot read the settings, so nothing was pruned: {exc}") from exc
    return history_prune.history_prune_settings(memory)


def run_history_command(
    args: argparse.Namespace, *, db: Any = None, now: Optional[float] = None, out=print
) -> int:
    """`palli_ctl history prune [--dry-run]` (Batch 8, task 3; ruling T3-4).
    The nightly prune of her identity documents' saved copies, by hand: per
    document, the newest memory.history_keep_last copies, one a day for
    memory.history_daily_days days, one an ISO week before that and the
    first ever stay; the rest go. --dry-run prints each document's counts and
    sizes and changes nothing. The live documents are never touched."""
    action = getattr(args, "history_action", None)
    if action != "prune":
        raise RuntimeError(
            f"Specify a history action. Example: `{ctl_prefix()} history prune --dry-run`."
        )
    from core.identity import history_prune

    keep_last, daily_days = _history_knobs()
    if keep_last <= 0:
        out(
            "The identity-history prune is off (memory.history_keep_last is 0 in data/palli.yaml); "
            "nothing was removed. Set it (10 keeps a useful history) to turn the prune on."
        )
        return 0
    if db is None:
        from core.database import brain as db
    dry_run = bool(getattr(args, "dry_run", False))
    result = asyncio.run(
        history_prune.prune_history(
            db, keep_last=keep_last, daily_days=daily_days, now=now, dry_run=dry_run
        )
    )
    keep_word, remove_word = ("keep", "remove") if dry_run else ("kept", "removed")
    for stream in result.streams:
        out(
            f"user {stream.user_id}  {stream.platform}  {stream.doc_key}: "
            f"{keep_word} {stream.kept} ({_size(stream.kept_bytes)}), "
            f"{remove_word} {stream.removed} ({_size(stream.removed_bytes)})"
        )
    out(
        f"Total: {keep_word} {result.kept} ({_size(result.kept_bytes)}), "
        f"{remove_word} {result.removed} ({_size(result.bytes)})."
    )
    if dry_run:
        out(f"A dry run: nothing was removed. `{ctl_prefix()} history prune` removes them.")
    else:
        out(
            "The space stays inside the database file until it is reused; "
            f"`{ctl_prefix()} db shrink` gives it back to the disk (take a fresh backup first)."
        )
    return 0


# `db shrink` stays away from the nightly backup (cron, 03:20): local 03:00 to
# 04:30, and any running `palli_ctl.py backup` (ruling T3-4). Nothing marks a
# running backup on disk, so its process is looked for.
SHRINK_REFUSED_FROM_MINUTE = 3 * 60
SHRINK_REFUSED_UNTIL_MINUTE = 4 * 60 + 30
# ...and from her night pass's first half hour. Until 2026-10-05 the night
# started at 04:00, inside the window above; it now starts at its run time
# (companion.night_cycle_run_at, 06:30 by default — the owner, 10-05: off
# DeepSeek's peak), so the night has a window of its own.
SHRINK_NIGHT_GUARD_MINUTES = 30


def _night_run_at() -> Tuple[int, int]:
    """(hour, minute) her night pass starts, from her settings; 06:30 when
    they cannot be read."""
    try:
        from ai.night_cycle import night_run_at

        return night_run_at()
    except Exception:  # noqa: BLE001 - the default is the safe answer
        return 6, 30


def _backup_process(proc_root: Path = Path("/proc")) -> Optional[int]:
    """The pid of a running `palli_ctl.py backup` (the 03:20 cron run, its
    shell, or one by hand), or None. Reads /proc/<pid>/cmdline; where there
    is no /proc, nothing is found and only the clock guards."""
    me = os.getpid()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        tokens = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").split()
        if "backup" in tokens and any("palli_ctl.py" in token for token in tokens):
            return int(entry.name)
    return None


def _database_bytes(path: Path) -> int:
    total = 0
    for part in (path, path.with_name(path.name + "-wal")):
        with contextlib.suppress(OSError):
            total += part.stat().st_size
    return total


def _shrink_database(path: Path) -> bool:
    """secure_delete on, VACUUM, then fold the write-ahead log back in and
    truncate it. True when the log was truncated; a reader mid-read can hold
    it for a moment, so the checkpoint is retried."""
    import sqlite3

    try:
        connection = sqlite3.connect(str(path), timeout=15.0, isolation_level=None)
    except (sqlite3.Error, OSError) as exc:
        raise RuntimeError(f"Could not open `{path}` to shrink it: {exc}") from exc
    try:
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("VACUUM")
        for _attempt in range(5):
            row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if not row or not row[0]:
                return True
            time.sleep(1.0)
        return False
    except sqlite3.Error as exc:
        raise RuntimeError(f"Could not shrink `{path}`: {exc}") from exc
    finally:
        connection.close()


def run_db_command(
    args: argparse.Namespace,
    *,
    db_path: Optional[Path] = None,
    now: Any = None,
    proc_root: Optional[Path] = None,
    night_run_at: Optional[Tuple[int, int]] = None,
    out=print,
) -> int:
    """`palli_ctl db shrink` (Batch 8, task 3; rulings T3-4, T3-6): VACUUM her
    database with secure_delete on and truncate its write-ahead log, so the
    space the identity-history prune freed goes back to the disk. A wrap
    step, by hand, after a fresh backup; refuses from 03:00 to 04:30 local,
    in the first half hour of her night pass (06:30 by default) and while
    any `palli_ctl.py backup` runs."""
    action = getattr(args, "db_action", None)
    if action != "shrink":
        raise RuntimeError(f"Specify a db action. Example: `{ctl_prefix()} db shrink`.")
    from datetime import datetime

    clock = now if now is not None else datetime.now()
    minute = clock.hour * 60 + clock.minute
    if SHRINK_REFUSED_FROM_MINUTE <= minute < SHRINK_REFUSED_UNTIL_MINUTE:
        raise RuntimeError(
            "Not between 03:00 and 04:30: the nightly backup runs at 03:20 and may still be "
            "copying the database. Run `db shrink` later, after a fresh backup."
        )
    night_hour, night_minute = night_run_at if night_run_at is not None else _night_run_at()
    night_from = night_hour * 60 + night_minute
    if night_from <= minute < night_from + SHRINK_NIGHT_GUARD_MINUTES:
        night_until = night_from + SHRINK_NIGHT_GUARD_MINUTES
        raise RuntimeError(
            f"Not between {night_from // 60:02d}:{night_from % 60:02d} and "
            f"{night_until // 60:02d}:{night_until % 60:02d}: her night pass starts at "
            f"{night_hour:02d}:{night_minute:02d} and writes to the database. "
            "Run `db shrink` later, after a fresh backup."
        )
    pid = _backup_process(proc_root if proc_root is not None else Path("/proc"))
    if pid is not None:
        raise RuntimeError(f"A backup is running (pid {pid}); run `db shrink` once it has finished.")
    if db_path is None:
        from core.database import resolve_database_path

        db_path = resolve_database_path()
    path = Path(db_path)
    if not path.is_file():
        raise RuntimeError(f"There is no database at `{path}`; nothing to shrink.")
    before = _database_bytes(path)
    truncated = _shrink_database(path)
    after = _database_bytes(path)
    out(f"Shrank {path.name}: {_size(before)} -> {_size(after)}.")
    if not truncated:
        out(
            "The write-ahead log was busy and was not truncated; it folds back in at her next "
            "checkpoint. Run `db shrink` again later to truncate it now."
        )
    return 0



# The commands that build the doctor's report or start her: each seeds the settings
# file first, as `brain` and the settings reader already do.
_LAUNCHER_COMMANDS = frozenset({"doctor", "prepare", "start", "restart", "status"})


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    controller = LauncherController()
    if args.command in _LAUNCHER_COMMANDS:
        # The stranger test (2026-10-10): `prepare`, the README's first command after
        # the install, stopped a fresh clone with "3 core blocker(s)". Nothing on this
        # path seeded data/palli.yaml, so the lane routing fell back to the template's
        # LLM_PROVIDER (a cloud provider with no key yet) for every lane, before the
        # door could ask for a brain. The settings service loads lazily, so a seed
        # before the doctor's first read is enough (see ensure_settings_file).
        ensure_settings_file(Path(controller.palli_yaml_path))
    try:
        if args.command == "onboard":
            return controller.onboard(
                flow=getattr(args, "flow", None),
                preset=getattr(args, "preset", None),
                non_interactive=bool(getattr(args, "non_interactive", False)),
                codex_model=getattr(args, "codex_model", None),
                codex_profile=getattr(args, "codex_profile", None),
                skip_doctor=bool(getattr(args, "skip_doctor", False)),
                skip_start=bool(getattr(args, "skip_start", False)),
            )
        if args.command == "configure":
            return controller.configure(
                preset=getattr(args, "preset", None),
                non_interactive=bool(getattr(args, "non_interactive", False)),
                codex_model=getattr(args, "codex_model", None),
                codex_profile=getattr(args, "codex_profile", None),
                skip_doctor=bool(getattr(args, "skip_doctor", False)),
                sections=list(getattr(args, "section", None) or []),
            )
        if args.command == "auth":
            if getattr(args, "auth_command", None) != "codex":
                raise RuntimeError(f"Specify an auth target. Example: `{ctl_prefix()} auth codex`.")
            return controller.auth_codex(
                status_only=bool(getattr(args, "status_only", False)),
                device_auth=bool(getattr(args, "device_auth", False)),
            )
        if args.command == "doctor":
            return controller.doctor(
                as_json=bool(args.json_output),
                fix_mode=bool(getattr(args, "fix_mode", False)),
            )
        if args.command == "prepare":
            return controller.prepare(as_json=bool(args.json_output))
        if args.command == "start":
            return controller.start()
        if args.command == "stop":
            return controller.stop()
        if args.command == "restart":
            return controller.restart()
        if args.command == "status":
            return controller.status(as_json=bool(args.json_output))
        if args.command == "chat":
            raw_message = getattr(args, "message", None) or []
            return controller.chat(
                message=" ".join(str(part) for part in raw_message).strip() or None,
                show_events=bool(getattr(args, "show_events", False)),
            )
        if args.command == "backup":
            return controller.backup(
                out_dir=getattr(args, "out_dir", None),
                include_home=bool(getattr(args, "include_home", False)),
                encrypt=bool(getattr(args, "encrypt", False)),
                passfile=getattr(args, "passfile", None),
                keep=getattr(args, "keep", None),
                require_mount=getattr(args, "require_mount", None),
            )
        if args.command == "logs":
            return controller.logs(target=str(args.target or "launcher"), follow=bool(args.follow))
        if args.command == "settings":
            return controller._handle_settings(args)
        if args.command == "brain":
            return run_brain_command(args)
        if args.command == "forge":
            return run_forge_command(
                getattr(args, "forge_action", None),
                str(getattr(args, "name", "") or ""),
            )
        if args.command == "collaborators":
            return run_collaborators_command(args)
        if args.command == "phone":
            # The mobile door (2026-10-13): the work lives in core/mobile_cli.py.
            from core.mobile_cli import run_phone_command

            return run_phone_command(getattr(args, "phone_action", None) or "",
                                     device_id=getattr(args, "device_id", None))
        if args.command == "quiet":
            return run_quiet_command(args)
        if args.command == "replies":
            return run_replies_command(args)
        if args.command == "presence":
            return run_presence_command(args)
        if args.command == "spending":
            return run_spending_command(args)
        if args.command == "memory":
            return run_memory_command(args)
        if args.command == "threads":
            return run_threads_command(args)
        if args.command == "onboarding":
            return run_onboarding_command(args)
        if args.command == "history":
            return run_history_command(args)
        if args.command == "db":
            return run_db_command(args)
    except subprocess.CalledProcessError as exc:
        append_launcher_log(f"command failed exit_code={exc.returncode}: {exc}")
        print(f"Launcher command failed: {exc}", file=sys.stderr)
        return int(exc.returncode or 1)
    except RuntimeError as exc:
        append_launcher_log(f"runtime error: {exc}")
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

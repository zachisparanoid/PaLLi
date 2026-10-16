from __future__ import annotations

import asyncio
import contextlib
import json
import importlib.util
import logging
import os
import platform
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.backend_bootstrap import backend_bootstrap
from core.config import config
from core.database import effective_database_path_setting, resolve_database_path
from core.her_listener import her_listener_pid
from core.host_platform import host_platform_service
from core.launcher_runtime import collect_prompt_loading_status
from core.plugin_runtime import plugin_runtime
from core.sqlite_database_bootstrap import inspect_sqlite_database_path
from palli_config.ctl_prefix import ctl_prefix

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_STATE_PATH = PROJECT_ROOT / "data" / "launcher-state.json"
LAUNCHER_LOG_DIR = PROJECT_ROOT / "logs" / "launcher"
LAUNCHER_LOG_PATH = LAUNCHER_LOG_DIR / "launcher.log"
SERVICE_LOG_PATHS = {
    "backend": LAUNCHER_LOG_DIR / "backend.log",
    "frontend": LAUNCHER_LOG_DIR / "frontend.log",
}


def ensure_launcher_runtime_dirs() -> None:
    LAUNCHER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHER_LOG_DIR.mkdir(parents=True, exist_ok=True)


def launcher_state_path() -> Path:
    return LAUNCHER_STATE_PATH


def launcher_log_path() -> Path:
    return LAUNCHER_LOG_PATH


def service_log_path(service_name: str) -> Path:
    return SERVICE_LOG_PATHS[str(service_name or "").strip().lower()]


def append_launcher_log(message: str) -> None:
    ensure_launcher_runtime_dirs()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LAUNCHER_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message.rstrip()}\n")


def load_launcher_state() -> Dict[str, Any]:
    try:
        with LAUNCHER_STATE_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        payload = {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("services", {})
    return payload


def save_launcher_state(state: Dict[str, Any]) -> None:
    ensure_launcher_runtime_dirs()
    payload = dict(state or {})
    payload.setdefault("services", {})
    payload["updated_at"] = time.time()
    with LAUNCHER_STATE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")


def summarize_doctor_report(report: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(report or {})
    return {
        "generated_at": float(payload.get("generated_at") or time.time()),
        "status": str(payload.get("status") or "unknown"),
        "summary": str(payload.get("summary") or ""),
        "counts": dict(payload.get("counts") or {}),
    }


def _with_last_doctor_summary(launcher: Optional[Dict[str, Any]], summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = dict(launcher or {})
    payload["last_doctor"] = dict(summary or payload.get("last_doctor") or {}) or None
    return payload


def normalize_probe_host(host: Optional[str]) -> str:
    clean = str(host or "").strip() or "127.0.0.1"
    if clean in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return clean


def process_is_running(pid: Optional[int]) -> bool:
    try:
        clean_pid = int(pid or 0)
    except Exception:
        return False
    if clean_pid <= 0:
        return False
    try:
        os.kill(clean_pid, 0)
    except OSError:
        return False
    return True


# Batch 8 Task 11 A3: who holds her port — the readers (her_listener_pid,
# imported above) live in core/her_listener.py, standard library only, so the
# vault scrub can ask the same question under system python3 (second read F6,
# 2026-10-04). Called below through this module's name, so a test's patch of
# launcher_support.her_listener_pid reaches it.


def _open_http_url(url: str, *, timeout: float = 0.75) -> Dict[str, Any]:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="ignore")
            return {
                "reachable": True,
                "status_code": int(response.getcode() or 0),
                "body": body,
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        return {
            "reachable": True,
            "status_code": int(exc.code or 0),
            "body": body,
            "error": "",
        }
    except Exception as exc:
        return {
            "reachable": False,
            "status_code": None,
            "body": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _probe_tcp_port(host: str, port: int, *, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((normalize_probe_host(host), int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _service_target(service_name: str) -> Dict[str, Any]:
    normalized = str(service_name or "").strip().lower()
    if normalized == "backend":
        host, port = config.get_backend_host_port() or ("127.0.0.1", int(getattr(config, "BACKEND_PORT", 8000)))
        base_url = (config.get_backend_origin() or str(getattr(config, "BACKEND_URL", "") or "")).rstrip("/")
        return {
            "name": "backend",
            "host": normalize_probe_host(host),
            "port": int(port),
            "url": base_url,
            "health_url": f"{base_url}/api/health",
            "log_path": str(service_log_path("backend")),
        }
    host, port = config.get_frontend_host_port() or ("127.0.0.1", 3000)
    base_url = (config.get_frontend_origin() or str(getattr(config, "FRONTEND_URL", "") or "")).rstrip("/")
    return {
        "name": "frontend",
        "host": normalize_probe_host(host),
        "port": int(port),
        "url": base_url,
        "health_url": f"{base_url}/dashboard",
        "log_path": str(service_log_path("frontend")),
    }


def _probe_service(service_name: str) -> Dict[str, Any]:
    target = _service_target(service_name)
    response = _open_http_url(target["health_url"])
    http_ok = False
    payload: Optional[Dict[str, Any]] = None
    if response["reachable"] and response["status_code"] is not None:
        status_code = int(response["status_code"])
        if target["name"] == "backend":
            try:
                payload = json.loads(response["body"] or "{}")
            except Exception:
                payload = None
            http_ok = status_code == 200 and isinstance(payload, dict) and str(payload.get("status") or "").strip().lower() in {"healthy", "ready"}
        else:
            http_ok = 200 <= status_code < 500

    return {
        **target,
        "reachable": bool(response["reachable"]),
        "status_code": response["status_code"],
        "http_ok": http_ok,
        "payload": payload,
        "error": str(response["error"] or ""),
        "port_open": _probe_tcp_port(target["host"], target["port"]),
    }


def inspect_launcher_service(service_name: str, *, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    snapshot = load_launcher_state() if state is None else dict(state or {})
    services = dict(snapshot.get("services") or {})
    record = dict(services.get(str(service_name or "").strip().lower()) or {})
    probe = _probe_service(service_name)
    pid = record.get("pid")
    pid_running = process_is_running(pid)
    her_pid: Optional[int] = None
    ownership = "none"
    if pid_running:
        ownership = "launcher"
    elif probe["port_open"]:
        # A3: an open port with no live launcher record is "self" when the
        # listener is her (this process, or this checkout's main.py) — she was
        # started outside the launcher, from tmux. Anyone else stays "external".
        if probe["name"] == "backend":
            her_pid = her_listener_pid(probe["port"])
        ownership = "self" if her_pid else "external"
    elif record:
        ownership = "stale"
    status = "stopped"
    if ownership in {"launcher", "self"}:
        status = "running" if probe["http_ok"] else "degraded"
    elif ownership == "external":
        status = "degraded"
    elif record:
        status = "stopped"
    if her_pid:
        reported_pid: Optional[int] = int(her_pid)
    else:
        reported_pid = int(pid) if pid_running else (int(pid) if str(pid or "").isdigit() else None)
    return {
        "service": probe["name"],
        "url": probe["url"],
        "host": probe["host"],
        "port": probe["port"],
        "log_path": probe["log_path"],
        "pid": reported_pid,
        "pid_running": pid_running,
        "ownership": ownership,
        "managed": ownership == "launcher",
        "status": status,
        "probe": {
            "reachable": probe["reachable"],
            "http_ok": probe["http_ok"],
            "status_code": probe["status_code"],
            "error": probe["error"],
        },
        "port_open": probe["port_open"],
        "stale_state": bool(record) and not pid_running,
        "started_at": record.get("started_at"),
        "updated_at": record.get("updated_at"),
        "metadata": dict(record.get("metadata") or {}),
    }


def launcher_snapshot() -> Dict[str, Any]:
    state = load_launcher_state()
    backend = inspect_launcher_service("backend", state=state)
    frontend = inspect_launcher_service("frontend", state=state)
    last_doctor = dict(state.get("last_doctor") or {})
    return {
        "state_path": str(LAUNCHER_STATE_PATH),
        "log_dir": str(LAUNCHER_LOG_DIR),
        "services": {
            "backend": backend,
            "frontend": frontend,
        },
        "last_doctor": last_doctor or None,
    }


def resolve_repo_python_path() -> Dict[str, Any]:
    candidates: List[Path] = []
    if platform.system().lower().startswith("win"):
        candidates.append(PROJECT_ROOT / "venv" / "Scripts" / "python.exe")
    else:
        candidates.append(PROJECT_ROOT / "venv" / "bin" / "python")
    candidate = candidates[0]
    return {
        "path": str(candidate),
        "exists": candidate.exists(),
        "executable": os.access(candidate, os.X_OK) if candidate.exists() else False,
    }


def resolve_node_binary() -> Optional[str]:
    candidates = ["node.exe", "node"] if platform.system().lower().startswith("win") else ["node"]
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def resolve_npm_binary() -> Optional[str]:
    candidates = ["npm.cmd", "npm"] if platform.system().lower().startswith("win") else ["npm"]
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def launcher_command_hint(command: str) -> str:
    """The one place most of PaLLi's "run this next" lines are built.

    The base used to be the repo invocation, always — which is a lie on a
    machine that has `pip install -e .`'d the console script, and was the other
    half of the two-truths problem week 1 task 5 set out to end. `ctl_prefix()`
    asks the PATH instead.
    """
    action = str(command or "").strip()
    return f"{ctl_prefix()} {action}".strip()


def website_node_modules_present() -> bool:
    return (PROJECT_ROOT / "website" / "node_modules").is_dir()


def website_build_present() -> bool:
    return (PROJECT_ROOT / "website" / ".next" / "BUILD_ID").is_file()


def database_writability() -> Dict[str, Any]:
    raw_path = effective_database_path_setting()
    absolute_path = resolve_database_path(raw_path)
    details = inspect_sqlite_database_path(absolute_path)
    details["path"] = raw_path
    details["absolute_path"] = str(absolute_path)
    return details


# ---------------------------------------------------------------- the model server

# The two local doors, and the endpoint that proves each one is answering. The shipped
# configuration points every lane at one local Ollama: the nine text lanes through its
# OpenAI-shaped endpoint (`local_openai`, LOCAL_LLM_API_BASE, which already ends in
# /v1) and the vision lane through the native API (`ollama`, OLLAMA_BASE_URL). Until
# 2026-09-06 nothing in `doctor` asked whether either answered — so an install with no
# Ollama reported ready, `brain` said "she can use her hands", and the first message
# failed. Only the doors this install actually routes to are probed: a cloud install
# must not be told about a server it never wanted.
LOCAL_LLM_DOORS: Dict[str, Dict[str, str]] = {
    "local_openai": {"setting": "LOCAL_LLM_API_BASE", "path": "/models"},
    "ollama": {"setting": "OLLAMA_BASE_URL", "path": "/api/tags"},
}
# Short on purpose: this function is also the body of GET /api/health/detail, and a
# server that is simply not there refuses the connection immediately.
LOCAL_LLM_PROBE_TIMEOUT_S = 2.0


def _local_llm_doors_in_use(llm_status: Optional[Dict[str, Any]] = None) -> List[str]:
    """Every local door this install routes to, in LOCAL_LLM_DOORS order."""
    wanted = {str(getattr(config, "LLM_PROVIDER", "") or "").strip().lower()}
    status = dict(llm_status or {})
    wanted.add(str(status.get("default_provider") or "").strip().lower())
    for route in (status.get("task_routing") or {}).values():
        route = dict(route or {})
        wanted.add(str(route.get("provider") or route.get("requested_provider") or "").strip().lower())
    return [name for name in LOCAL_LLM_DOORS if name in wanted]


def _model_ids(body: str) -> set:
    """Every model id in an Ollama /api/tags or an OpenAI-style /v1/models body."""
    try:
        payload = json.loads(body or "")
    except Exception:
        return set()
    rows = []
    if isinstance(payload, dict):
        for key in ("models", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend(value)
    names = set()
    for row in rows:
        if isinstance(row, dict):
            for key in ("name", "id", "model"):
                value = str(row.get(key) or "").strip()
                if value:
                    names.add(value)
                    break
        elif isinstance(row, str) and row.strip():
            names.add(row.strip())
    return names


def _shipped_response_model() -> str:
    """Her voice's model tag, without a provider prefix if one was written in."""
    model = str(getattr(config, "LLM_RESPONSE_MODEL", "") or "").strip()
    for door in LOCAL_LLM_DOORS:
        if model.lower().startswith(f"{door}/"):
            return model[len(door) + 1:]
    return model


def check_local_llm_reachable(llm_status: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Warnings, never blockers: is the local model server there, and is her model pulled?

    A host that runs its models on another machine, or starts Ollama after PaLLi, is a
    real install and not a broken one — so this never fails the doctor. It only stops
    the shipped install from reporting ready when the one thing it depends on is not
    running (final review I6).
    """
    checks: List[Dict[str, Any]] = []
    known: set = set()
    any_ready = False
    for door in _local_llm_doors_in_use(llm_status):
        setting = LOCAL_LLM_DOORS[door]["setting"]
        base = str(getattr(config, setting, "") or "").strip().rstrip("/")
        if not base:
            continue
        url = f"{base}{LOCAL_LLM_DOORS[door]['path']}"
        probe = _open_http_url(url, timeout=LOCAL_LLM_PROBE_TIMEOUT_S)
        status_code = int(probe.get("status_code") or 0)
        details = {"provider": door, "setting": setting, "url": url,
                   "status_code": status_code, "error": str(probe.get("error") or "")}
        if not probe.get("reachable") or status_code >= 400:
            reason = str(probe.get("error") or "") or f"HTTP {status_code}"
            checks.append({
                "code": "local_llm_unreachable",
                "severity": "warning",
                "message": (
                    f"The local model server is not answering at {url} ({reason}). "
                    f"PaLLi will start and every reply will fail. Install Ollama and "
                    f"run `ollama serve`, or point {setting} at the machine that runs "
                    f"your models."
                ),
                "details": details,
            })
            continue
        names = _model_ids(str(probe.get("body") or ""))
        known |= names
        any_ready = True
        details["model_count"] = len(names)
        checks.append({
            "code": "local_llm_ready",
            "severity": "ok",
            "message": f"The local model server answers at {url} ({len(names)} model(s)).",
            "details": details,
        })

    model = _shipped_response_model()
    if any_ready and known and model and model not in known:
        checks.append({
            "code": "local_llm_model_missing",
            "severity": "warning",
            "message": (
                f"`{model}` is not pulled on the local model server — "
                f"`ollama pull {model}`."
            ),
            "details": {"model": model, "available": sorted(known)[:20]},
        })
    return checks


# =============================================================================
# The brain answers, and there is money behind it (week 1 task 1b)
#
# "The key is present" was never the question. A typo, a revoked key, a provider
# outage and an empty account all look identical to a config check, and all four
# end the same way: she starts, and every reply fails. So one tiny PONG, and the
# balance when the provider will tell us.
#
# Both are WARNINGS in the doctor and both are opt-in (`probe_llm=True`), because
# build_doctor_report() is also the body of GET /api/health/detail and a live
# call on every health poll would cost money and latency. PALLI_PREFLIGHT_OFFLINE
# skips them entirely — the test suite sets it, and so does anyone without a
# network.
# =============================================================================

PREFLIGHT_OFFLINE_ENV = "PALLI_PREFLIGHT_OFFLINE"


def preflight_offline() -> bool:
    return str(os.environ.get(PREFLIGHT_OFFLINE_ENV, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _run_probe(coroutine_factory: Callable[[], Any]) -> Any:
    """Run one async probe from sync code. None on any failure — a probe that
    cannot run is a probe with nothing to say, never an exception up the stack."""
    try:
        return asyncio.run(coroutine_factory())
    except Exception as exc:
        # This module has no module-level logger — it otherwise only ever mutes
        # logging — so the one debug line names its own.
        logging.getLogger(__name__).debug("live probe could not run: %s", exc)
        return None


def probe_llm_brain(provider_name: str) -> Dict[str, Any]:
    """Ask the configured brain to answer. Never raises.

    `ran` separates "we asked and the answer was no" from "we could not ask".
    Only the first is the stranger's problem; a probe of ours that cannot run is
    ours, and must never become a punch item telling them to check their key.
    """
    if preflight_offline():
        return {"skipped": True, "ran": False, "ok": False, "latency_ms": 0, "error": ""}
    from palli_config import providers as provider_tools

    result = _run_probe(lambda: provider_tools.validate_provider(provider_name))
    if result is None:
        return {
            "skipped": False,
            "ran": False,
            "ok": False,
            "latency_ms": 0,
            "error": "the check itself could not run",
        }
    return {
        "skipped": False,
        "ran": True,
        "ok": bool(result.ok),
        "latency_ms": int(result.latency_ms or 0),
        "error": str(result.error or ""),
    }


def probe_llm_balance(provider_name: str) -> Dict[str, Any]:
    """Read what is left on the account.

    Three states, kept apart on purpose: `supported` False means this provider
    has no balance API and there is nothing to say; `supported` with `known`
    False means the read failed and that IS worth saying; `known` carries the
    number. `low` means under the floor — an account the provider marks
    unavailable is a different sentence, so `available` stays its own field.
    """
    from palli_config import providers as provider_tools

    threshold = float(getattr(config, "LLM_LOW_BALANCE_WARN", 2.0) or 0.0)
    blank = {
        "skipped": False,
        "supported": provider_tools.has_balance_api(provider_name),
        "known": False,
        "low": False,
        "threshold": threshold,
        "currency": "",
        "total_balance": 0.0,
        "available": False,
    }
    if preflight_offline():
        return {**blank, "skipped": True}

    balance = _run_probe(lambda: provider_tools.fetch_balance(provider_name))
    if balance is None:
        return blank
    return {
        "skipped": False,
        "supported": True,
        "known": True,
        "low": bool(balance.total_balance < threshold),
        "threshold": threshold,
        "currency": str(balance.currency),
        "total_balance": float(balance.total_balance),
        "available": bool(balance.available),
    }


def check_llm_brain_and_balance(provider_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Doctor checks for the two live questions. Warnings, never blockers — a
    host with no network, or a provider having a bad hour, is a real install."""
    provider = str(provider_name or getattr(config, "LLM_PROVIDER", "") or "").strip()
    if not provider:
        return []
    if not getattr(config, "brain_ready", True):
        # Setup mode (first door, task 1, 2026-09-17): nothing to PONG and no
        # account to read. Its own sentence and never a warning — the missing
        # brain already has one (check_brain_configured), and a keyless 401
        # reported as "did not answer" would send a stranger after a key they
        # have not got yet.
        return [{
            "code": "llm_brain_answers", "severity": "ok",
            "message": (
                f"The brain ({provider}) was not asked: no brain is configured "
                "yet (setup mode)."
            ),
            "details": {"skipped": True, "reason": "setup_mode"},
        }]
    checks: List[Dict[str, Any]] = []

    brain = probe_llm_brain(provider)
    if brain["skipped"]:
        checks.append({
            "code": "llm_brain_answers", "severity": "ok",
            "message": f"The brain ({provider}) was not asked: skipped (offline).",
            "details": brain,
        })
    elif not brain["ran"]:
        # Ours, not theirs: say so plainly instead of sending them after a key.
        checks.append({
            "code": "llm_brain_answers", "severity": "warning",
            "message": (
                f"The brain ({provider}) could not be checked: {brain['error']}."
            ),
            "details": brain,
        })
    elif brain["ok"]:
        checks.append({
            "code": "llm_brain_answers", "severity": "ok",
            "message": f"The brain ({provider}) answered in {brain['latency_ms']} ms.",
            "details": brain,
        })
    else:
        checks.append({
            "code": "llm_brain_answers", "severity": "warning",
            "message": (
                f"The brain ({provider}) did not answer: {brain['error']}. "
                f"Every reply will fail until it does — check its key in .env.local, "
                f"or run `{ctl_prefix()} brain`."
            ),
            "details": brain,
        })

    balance = probe_llm_balance(provider)
    if balance["skipped"]:
        checks.append({
            "code": "llm_balance", "severity": "ok",
            "message": "Balance: skipped (offline).",
            "details": balance,
        })
    elif balance["known"]:
        checks.append({
            "code": "llm_balance", "severity": "ok",
            "message": (
                f"Balance: {balance['total_balance']:.2f} {balance['currency']} "
                f"({provider})."
            ),
            "details": balance,
        })
        if balance["low"]:
            checks.append({
                "code": "llm_balance_low", "severity": "warning",
                "message": (
                    f"Balance is low ({balance['total_balance']:.2f} "
                    f"{balance['currency']}, floor {balance['threshold']:.2f}) — "
                    f"top up before she runs dry."
                ),
                "details": balance,
            })
        if not balance["available"]:
            # Its own sentence: an account can hold money and still be shut off,
            # and "low" would send the reader to the wrong fix.
            checks.append({
                "code": "llm_balance_unavailable", "severity": "warning",
                "message": (
                    f"The {provider} account is marked unavailable — she will get "
                    f"no answers until it is settled, whatever the balance says."
                ),
                "details": balance,
            })
    elif balance["supported"]:
        checks.append({
            "code": "llm_balance", "severity": "warning",
            "message": f"The balance could not be read ({provider}).",
            "details": balance,
        })
    return checks


def check_brain_configured() -> List[Dict[str, Any]]:
    """Setup mode on the doctor (first door, task 1 — spec §5): the punch
    list's own line, at `warning`. Read off the punch list validate() just
    wrote rather than composed again, so the two surfaces cannot drift."""
    if getattr(config, "brain_ready", True):
        return []
    from core.config_schema import BRAIN_PUNCH_KEY

    punch = getattr(config, "punch_list", None)
    line = next(
        (
            f"{item.key} — {item.why}; {item.where}"
            for item in getattr(punch, "warnings", [])
            if item.key == BRAIN_PUNCH_KEY
        ),
        f"{BRAIN_PUNCH_KEY} — no brain configured",
    )
    return [{"code": "llm_brain_missing", "severity": "warning", "message": line}]


def check_doors_waiting_on_a_brain() -> List[Dict[str, Any]]:
    """Setup mode shuts both chat doors whatever their tokens say. A door that
    would have opened must not be reported as missing its token — that sends a
    stranger to fix the one thing that is fine. The schema already wrote that
    door's line; this is the same line, by door, for the two checks below that
    would otherwise say "no token".

    It reads boot's punch list and NOT `brain_ready` (final wave, 2026-09-17).
    A successful `POST /api/setup/brain` flips `brain_ready` on the running
    process without re-running validate(), and the doors stay shut until the
    restart — so a `brain_ready` early return went silent in exactly that
    window and the health readout started lying about a token that is set.
    `core.setup_mode.doors_shut_for_lack_of_a_brain()` is the one reader of
    that punch list; this delegates to it so the two cannot drift again.
    """
    from core.setup_mode import doors_shut_for_lack_of_a_brain

    shut = set(doors_shut_for_lack_of_a_brain(config))
    if not shut:
        return []
    punch = getattr(config, "punch_list", None)
    return [
        {
            "code": f"{item.key.lower()}_closed_no_brain",
            "severity": "warning",
            "message": f"{item.key} — {item.why}; {item.where}",
        }
        for item in getattr(punch, "warnings", [])
        if item.key in shut
    ]


def _check_optional_capabilities() -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    # Boot's verdict, like its two siblings (week 2 task 7, final review): a
    # blank token degrades Discord to off for this boot, and validate() is what
    # decides that — doctor reports the decision instead of re-deriving it. The
    # fallback is the expression this used to compute, for a stand-in config
    # that never went through validate(); the token is AND-ed on top, belt and
    # braces, because Config declares the flag True at class level.
    discord_ready = bool(
        getattr(
            config,
            "effective_discord_enabled",
            getattr(config, "ENABLE_DISCORD_BOT", False)
            and str(getattr(config, "DISCORD_TOKEN", "") or "").strip(),
        )
    ) and bool(str(getattr(config, "DISCORD_TOKEN", "") or "").strip())
    waiting_on_a_brain = {item["code"]: item for item in check_doors_waiting_on_a_brain()}
    if bool(getattr(config, "ENABLE_DISCORD_BOT", False)) and not discord_ready:
        checks.append(
            waiting_on_a_brain.get("discord_closed_no_brain")
            or {
                "code": "discord_token_missing",
                "severity": "warning",
                "message": "Discord is enabled but no Discord token is configured.",
            }
        )

    if bool(getattr(config, "ENABLE_VOICE_FEATURES", False)):
        try:
            from voice.bootstrap import voice_bootstrap_status

            status = voice_bootstrap_status()
        except Exception as exc:
            status = None
            checks.append(
                {
                    "code": "voice_bootstrap_error",
                    "severity": "warning",
                    "message": f"Voice bootstrap could not be inspected: {type(exc).__name__}: {exc}",
                }
            )
        else:
            if not bool(status.ok):
                checks.append(
                    {
                        "code": "voice_bootstrap_degraded",
                        "severity": "warning",
                        "message": "Voice bootstrap is degraded on this host.",
                    }
                )

    def _module_available(module_name: str) -> bool:
        try:
            return importlib.util.find_spec(module_name) is not None
        except ModuleNotFoundError:
            return False

    capture_backend = host_platform_service.capture_backend(
        module_available=_module_available,
        binary_available=lambda name: bool(shutil.which(name)),
    )
    host_health = host_platform_service.capability_health(
        desktop_enabled=bool(getattr(config, "OPERATOR_DESKTOP_ENABLED", False)),
        input_enabled=bool(getattr(config, "OPERATOR_DESKTOP_ALLOW_INPUT_CONTROL", False)),
        machine_presence_enabled=bool(getattr(config, "LOCAL_MACHINE_PRESENCE_ENABLED", True)),
        capture_available=bool(capture_backend),
        ocr_available=bool(getattr(config, "OPERATOR_OCR_ENABLED", True) and _module_available("pytesseract") and shutil.which("tesseract")),
    )
    degraded = [
        (key, item)
        for key, item in host_health.items()
        if str((item or {}).get("status") or "").strip().lower() == "degraded"
    ]
    for key, item in degraded:
        checks.append(
            {
                "code": f"host_{key}_degraded",
                "severity": "warning",
                "message": str((item or {}).get("message") or f"{key} is degraded on this host."),
            }
        )

    if bool(getattr(config, "OPERATOR_DESKTOP_VISION_ENABLED", False)) and not shutil.which("tesseract"):
        checks.append(
            {
                "code": "ocr_binary_missing",
                "severity": "warning",
                "message": "Tesseract is unavailable; OCR-assisted desktop inspection will be degraded.",
            }
        )
    return checks


def config_validation_reasons() -> List[str]:
    """Why config.validate() would say no, in the words it logs and discards.

    Asked of the SCHEMA directly rather than scraped out of the log. logging.disable()
    stops a record from being created at all, so there is nothing for a handler to catch,
    and the alternative — unmuting the root logger around the call — would spray a live
    backend's console from a read-only inspection. The validators are pure
    (core/config_schema.py: "Pydantic validators must be side-effect-free"), so running
    them a second time costs one pass and changes nothing.

    Empty means "the schema is satisfied". config.validate() can still return False on
    top of that, for the one check that is NOT in the schema: the Codex CLI login probe,
    which does subprocess I/O and therefore stayed in Config.validate(). The caller says
    so rather than printing a confident blank."""
    try:
        from pydantic import ValidationError

        from core.config_schema import CoreConfigSchema
    except Exception as exc:  # pragma: no cover - import failure is its own disaster
        return [f"could not load the config schema ({type(exc).__name__}: {exc})"]
    # The collector catches the blockers the schema RECORDS instead of raising
    # (week 2 task 4: an allowed-networks list that parses to nothing). Without
    # it the doctor's only word for one of those would be the Codex fallback
    # sentence below.
    collector: Dict[str, Any] = {}
    out: List[str] = []
    try:
        CoreConfigSchema.from_config(config, context=collector)
    except ValidationError as exc:
        for err in exc.errors():
            field = ".".join(str(part) for part in err.get("loc", ()) or ())
            # Pydantic prefixes a model_validator's message; the message already names
            # the field, so the prefix is noise in front of the sentence a reader needs.
            message = str(err.get("msg", "")).removeprefix("Value error, ")
            out.append(f"{field}: {message}" if field else message)
    except Exception as exc:
        return [f"{type(exc).__name__}: {exc}"]
    out.extend(
        f"{item.key}: {item.why} ({item.where})"
        for item in collector.get("blockers", [])
    )
    return out


class _ThreadMute(logging.Filter):
    """Drops the records made by one thread (second read F7)."""

    def __init__(self, ident: int) -> None:
        super().__init__()
        self._ident = ident

    def filter(self, record: logging.LogRecord) -> bool:
        return record.thread != self._ident


@contextlib.contextmanager
def _logging_muted():
    """The doctor's mute around config.validate() (review I1).

    On the main thread — `palli_ctl doctor` — it is the old process-wide
    logging.disable(). Off it — /api/health/detail, built in a worker thread
    since the second read's F7 (2026-10-04) — a global disable would drop
    every line her loop logs meanwhile, and two overlapping polls could put
    back each other's saved level and leave logging off for good. So there
    only this thread's records are dropped, by a filter on every handler for
    the length of the mute."""
    if threading.current_thread() is threading.main_thread():
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            yield
        finally:
            logging.disable(previous)
        return
    mute = _ThreadMute(threading.get_ident())
    handlers = set(logging.root.handlers)
    for candidate in list(logging.root.manager.loggerDict.values()):
        handlers.update(getattr(candidate, "handlers", None) or ())
    for handler in handlers:
        handler.addFilter(mute)
    try:
        yield
    finally:
        for handler in handlers:
            handler.removeFilter(mute)


def build_doctor_report(*, probe_llm: bool = False) -> Dict[str, Any]:
    """`probe_llm` sends one live PONG and reads the balance. Off by default:
    this function is also the body of GET /api/health/detail, and a live call on
    every health poll would spend money and add latency. `palli_ctl doctor`
    turns it on."""
    checks: List[Dict[str, Any]] = []
    llm_status: Dict[str, Any] = {}

    def add_check(code: str, severity: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        checks.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                "details": dict(details or {}),
            }
        )

    python_info = resolve_repo_python_path()
    if not python_info["exists"] or not python_info["executable"]:
        add_check(
            "python_venv_missing",
            "core_blocker",
            f"Expected repo virtualenv Python at {python_info['path']}.",
            details=python_info,
        )
    else:
        add_check("python_venv_ready", "ok", "Repo virtualenv Python is available.", details=python_info)

    # The mute is _logging_muted(): process-wide on the main thread, this
    # thread's records only off it (second read F7).
    with _logging_muted():
        config_valid = bool(config.validate())
        # INSIDE the mute, not after it (review I1). The helper's own docstring is the
        # argument: it asks the schema rather than the log precisely so a read-only
        # inspection cannot spray a live console — and then the call sat outside the
        # `finally`, so the second validation pass ran with logging live and wrote
        # provider/plugin warnings out anyway. That is not only a CLI cosmetic:
        # build_doctor_report() is also the body of GET /api/health/detail
        # (build_public_health_detail → core/main.py), so on a backend with an invalid
        # config every health poll was re-emitting them into her log.
        reasons = [] if config_valid else config_validation_reasons()
    if not config_valid:
        # The reasons are the whole value of this check. config.validate() logs one line
        # per violation and returns a bare False, and the logging.disable() above — there
        # so a doctor run does not spray the console — means not even those lines survive.
        # For years this check said "Config validation failed." and nothing else, on the
        # first run of a fresh install, when a blank EXA_API_KEY or DEEPGRAM_API_KEY was
        # the cause and naming it would have ended the problem (step 28 task 4 fix 1).
        if not reasons:
            # The schema is satisfied, so the no came from a check that lives outside
            # it. Name the Codex CLI login probe only when a provider actually routes
            # to codex; otherwise say what is known rather than guess (2026-09-09: the
            # first CI run blamed Codex for a missing DeepSeek key).
            lanes = (getattr(config, "LLM_PROVIDER", "") or "",) + tuple(
                getattr(config, name, "") or ""
                for name in ("LLM_RESPONSE_PROVIDER", "LLM_ANALYSIS_PROVIDER", "LLM_MEMORY_PROVIDER",
                             "LLM_DECISION_PROVIDER", "LLM_PROACTIVE_PROVIDER", "LLM_REFLECTION_PROVIDER",
                             "LLM_WEB_SEARCH_PROVIDER", "LLM_VISION_PROVIDER")
            )
            if any(str(lane).lower() == "codex" for lane in lanes):
                reasons = [
                    "the Codex CLI is not logged in, and a provider routes to codex "
                    f"(run `{ctl_prefix()} auth codex`)"
                ]
            else:
                reasons = [
                    "config.validate() refused, but the schema names no violation — run "
                    f"`{ctl_prefix()} doctor` with logging on, or `main.py --preflight-only`, to see its reason"
                ]
        add_check(
            "config_invalid",
            "core_blocker",
            "Config validation failed.",
            details={"reasons": reasons},
        )
    else:
        add_check("config_valid", "ok", "Config validation passed.")
    # Setup mode (first door, task 1 — spec §5): the punch list's own line, at
    # `warning`. Nothing live is asked, so it rides a plain health poll too.
    for item in check_brain_configured():
        add_check(str(item["code"]), str(item["severity"]), str(item["message"]))

    prompt_status = collect_prompt_loading_status()
    if prompt_status["critical_missing"]:
        add_check(
            "prompt_readiness_warning",
            "warning",
            str(prompt_status.get("summary") or "Critical prompt readiness needs attention."),
            details=prompt_status,
        )
    else:
        add_check(
            "prompt_readiness_ready",
            "ok",
            str(prompt_status.get("summary") or "Critical startup prompts are ready."),
            details=prompt_status,
        )

    # Phase 8e: legacy hosted-product residue. Detects dormant Stripe/MFA/
    # beta-seat artifacts so operators can decide whether to clean them up
    # via `palli_ctl.py doctor --fix`. Pure inspection — never mutates state.
    try:
        from core.legacy_residue import check_legacy_residue

        residue_report = check_legacy_residue(config=config)
    except Exception as exc:
        add_check(
            "legacy_residue_unavailable",
            "warning",
            f"Legacy residue check failed. ({type(exc).__name__}: {exc})",
        )
    else:
        residue_findings = [
            {
                "code": f.code,
                "message": f.message,
                "count": f.count,
                "severity": f.severity,
            }
            for f in residue_report.findings
        ]
        residue_details = {
            "findings": residue_findings,
            "inspected_database": residue_report.inspected_database,
            "fix_guidance": residue_report.fix_guidance(),
        }
        if residue_report.has_residue:
            add_check(
                "legacy_residue_present",
                "warning",
                residue_report.summary(),
                details=residue_details,
            )
        else:
            add_check(
                "legacy_residue_clean",
                "ok",
                "No legacy hosted-product residue detected.",
                details=residue_details,
            )

    try:
        from ai.llm_client import llm

        llm_status = dict(llm.get_runtime_status() or {})
    except Exception as exc:
        add_check(
            "llm_runtime_status_unavailable",
            "warning",
            f"LLM runtime status could not be collected. ({type(exc).__name__}: {exc})",
        )
    else:
        for issue in llm_status.get("route_issues") or []:
            severity = str(issue.get("severity") or "warning").strip().lower()
            add_check(
                f"llm_route_{str(issue.get('task') or 'unknown').strip().lower()}_{str(issue.get('code') or 'issue').strip().lower()}",
                "core_blocker" if severity == "error" else "warning",
                str(issue.get("message") or "LLM routing issue needs attention."),
                details=dict(issue or {}),
            )

        providers = list(llm_status.get("providers") or [])
        providers_by_name = {
            str((provider or {}).get("provider") or "").strip().lower(): dict(provider or {})
            for provider in providers
            if str((provider or {}).get("provider") or "").strip()
        }
        codex_diagnostics = dict(providers_by_name.get("codex") or {})
        codex_in_use = str(llm_status.get("default_provider") or "").strip().lower() == "codex"
        if not codex_in_use:
            for route in (llm_status.get("task_routing") or {}).values():
                if str((route or {}).get("requested_provider") or "").strip().lower() == "codex":
                    codex_in_use = True
                    break
        for item in check_local_llm_reachable(llm_status):
            add_check(str(item.get("code") or "local_llm"), str(item.get("severity") or "warning"),
                      str(item.get("message") or ""), details=dict(item.get("details") or {}))

        if probe_llm:
            for item in check_llm_brain_and_balance():
                add_check(str(item.get("code") or "llm_live"), str(item.get("severity") or "warning"),
                          str(item.get("message") or ""), details=dict(item.get("details") or {}))

        if codex_in_use and codex_diagnostics:
            codex_details = {
                "configured": bool(codex_diagnostics.get("configured")),
                "auth_mode": str(codex_diagnostics.get("auth_mode") or ""),
                "login_status": str(codex_diagnostics.get("login_status") or ""),
                "login_status_text": str(codex_diagnostics.get("login_status_text") or ""),
                "codex_executable": str(codex_diagnostics.get("codex_executable") or ""),
                "profile": str(codex_diagnostics.get("profile") or ""),
            }
            auth_mode = str(codex_diagnostics.get("auth_mode") or "").strip().lower()
            if auth_mode == "chatgpt":
                add_check(
                    "codex_auth_chatgpt",
                    "ok",
                    "Codex CLI is authenticated with ChatGPT in this runtime context.",
                    details=codex_details,
                )
            elif auth_mode == "apikey":
                add_check(
                    "codex_auth_api_key",
                    "warning",
                    "Codex CLI is authenticated with an API key in this runtime context. If you expect ChatGPT plan access, re-run Codex login in the same OS/user context that launches PaLLi.",
                    details=codex_details,
                )

    db_info = database_writability()
    if not db_info["writable"]:
        add_check(
            "database_not_writable",
            "core_blocker",
            str(db_info.get("summary") or "Database path is not writable."),
            details=db_info,
        )
    else:
        add_check("database_writable", "ok", "Database path is writable.", details=db_info)
        if db_info.get("journal_mode_fallback_active"):
            add_check(
                "database_journal_fallback",
                "warning",
                str(
                    db_info.get("journal_mode_warning")
                    or "SQLite WAL mode is unavailable for the configured database filesystem; runtime will use DELETE journal mode."
                ),
                details=db_info,
            )

    node_path = resolve_node_binary()
    npm_path = resolve_npm_binary()
    if not node_path:
        add_check("node_missing", "core_blocker", "Node.js is required for the packaged local dashboard.")
    else:
        add_check("node_ready", "ok", f"Node.js available at {node_path}.")
    if not npm_path:
        add_check("npm_missing", "core_blocker", "npm is required for the packaged local dashboard.")
    else:
        add_check("npm_ready", "ok", f"npm available at {npm_path}.")

    deps_present = website_node_modules_present()
    if deps_present:
        add_check("frontend_deps_ready", "ok", "Website dependencies are installed.")
    else:
        severity = "warning" if npm_path else "core_blocker"
        add_check(
            "frontend_deps_missing",
            severity,
            f"Website dependencies are missing; run `{launcher_command_hint('prepare')}`.",
        )

    build_present = website_build_present()
    if build_present:
        add_check("frontend_build_ready", "ok", "Website production build is present.")
    else:
        # Review I6: never a CORE BLOCKER while the deps it would be built FROM are also
        # missing. That is the state of every fresh clone — nobody has run `prepare` yet —
        # and both checks fired, so a correctly-configured keyless install opened its very
        # first `doctor` with "3 core blocker(s) need attention" for one problem and one
        # command. Nothing actually blocks on this code either: _assert_startable lists it
        # in `tolerated` verbatim, start() builds it on demand, and `main.py` — the Discord
        # path both docs lead with — never touches Node at all.
        severity = "warning" if npm_path or not deps_present else "core_blocker"
        add_check(
            "frontend_build_missing",
            severity,
            f"Website production build is missing; run `{launcher_command_hint('prepare')}`.",
        )

    launcher = launcher_snapshot()
    for service_name in ("backend", "frontend"):
        service = dict((launcher.get("services") or {}).get(service_name) or {})
        ownership = str(service.get("ownership") or "none")
        if ownership == "external":
            add_check(
                f"{service_name}_port_external",
                "core_blocker",
                f"{service_name.title()} port {service.get('port')} is occupied by a foreign process.",
                details=service,
            )
        elif ownership == "self":
            # A3: the port is taken — by her. Never "available for launcher
            # control" (the else below), never a foreign process.
            add_check(
                f"{service_name}_running_outside_launcher",
                "ok",
                f"She is running (pid {service.get('pid')}, started outside the launcher).",
                details=service,
            )
        elif str(service.get("status") or "") == "degraded" and ownership == "launcher":
            add_check(
                f"{service_name}_managed_degraded",
                "warning",
                f"{service_name.title()} is launcher-managed but not healthy yet.",
                details=service,
            )
        else:
            add_check(
                f"{service_name}_port_clear",
                "ok",
                f"{service_name.title()} port is available for launcher control.",
                details=service,
            )

    for item in _check_optional_capabilities():
        add_check(str(item.get("code") or "warning"), str(item.get("severity") or "warning"), str(item.get("message") or ""))
    # Boot's verdict, exactly as the Discord check above reads it (task 6
    # review, important 3). This used to read the raw token alone, which left
    # `doctor` the ONE surface silent about the other way this door shuts: a
    # token set on a tree where python-telegram-bot was never installed. The
    # fallback is the expression it used to compute, for a stand-in config
    # that never went through validate(), with the token AND-ed on top.
    _telegram_token = str(getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    telegram_ready = bool(
        getattr(
            config,
            "effective_telegram_enabled",
            getattr(config, "ENABLE_TELEGRAM_BOT", False) and _telegram_token,
        )
    ) and bool(_telegram_token)
    _telegram_waiting = next(
        (item for item in check_doors_waiting_on_a_brain()
         if item["code"] == "telegram_closed_no_brain"),
        None,
    )
    if _telegram_waiting is not None:
        # Setup mode shut it, not the token and not the library (first door, task 1).
        add_check(_telegram_waiting["code"], "warning", _telegram_waiting["message"])
    elif bool(getattr(config, "ENABLE_TELEGRAM_BOT", False)) and not telegram_ready:
        add_check(
            "telegram_token_missing",
            "warning",
            "Telegram bot is enabled but TELEGRAM_BOT_TOKEN is not configured."
            if not _telegram_token
            else "Telegram bot is enabled and a token is set, but the door is "
                 "closed for this boot: python-telegram-bot is not installed "
                 "(pip install -r requirements.txt).",
        )
    # The mobile door (2026-10-13): no token; setup mode or a missing address.
    _mobile_waiting = next(
        (item for item in check_doors_waiting_on_a_brain() if item["code"] == "mobile_closed_no_brain"),
        None,
    )
    if _mobile_waiting is not None:
        add_check(_mobile_waiting["code"], "warning", _mobile_waiting["message"])
    elif bool(getattr(config, "ENABLE_MOBILE_DOOR", False)) and not str(
            getattr(config, "MOBILE_PUBLIC_URL", "") or "").strip():
        add_check("mobile_public_url_missing", "warning",
                  "The mobile door is on but MOBILE_PUBLIC_URL is not set: pairing will ask "
                  "for the address by hand.")
    if bool(getattr(config, "ENABLE_SLACK_BOT", False)):
        if not str(getattr(config, "SLACK_BOT_TOKEN", "") or "").strip():
            add_check(
                "slack_bot_token_missing",
                "warning",
                "Slack bot is enabled but SLACK_BOT_TOKEN is not configured.",
            )
        if not str(getattr(config, "SLACK_APP_TOKEN", "") or "").strip():
            add_check(
                "slack_app_token_missing",
                "warning",
                "Slack bot is enabled but SLACK_APP_TOKEN is not configured.",
            )
    for item in plugin_runtime.doctor_checks():
        add_check(str(item.get("code") or "plugin_warning"), str(item.get("severity") or "warning"), str(item.get("message") or ""))

    blockers = [item for item in checks if item["severity"] == "core_blocker"]
    warnings = [item for item in checks if item["severity"] == "warning"]
    ok_items = [item for item in checks if item["severity"] == "ok"]
    status = "blocked" if blockers else ("warning" if warnings else "ok")
    summary = "Environment is ready."
    if blockers:
        summary = f"{len(blockers)} core blocker(s) need attention before full start."
    elif warnings:
        summary = f"Ready with {len(warnings)} warning(s)."
    report = {
        "generated_at": time.time(),
        "status": status,
        "summary": summary,
        "counts": {
            "core_blockers": len(blockers),
            "warnings": len(warnings),
            "ok": len(ok_items),
        },
        "checks": checks,
        "launcher": launcher,
        "database": db_info,
        "llm": llm_status,
        "prompts": prompt_status,
    }
    report["launcher"] = _with_last_doctor_summary(report.get("launcher"), summarize_doctor_report(report))
    return report


def build_channels_readout() -> Dict[str, Any]:
    """Which doors are configured, and which are actually open.

    Split out of build_public_health_detail for the telegram door
    (2026-09-14, task 2) so the answer can be read without building the whole
    doctor report. `ready` is boot's verdict for this run, ANDed with the raw
    token — belt and braces, the same rule the channel map and the Discord
    readout keep: a config object that has not been through validate() must
    not report a door open on a blank key.
    """
    return {
        "discord": {
            "enabled": bool(getattr(config, "ENABLE_DISCORD_BOT", False)),
            "configured": bool(str(getattr(config, "DISCORD_TOKEN", "") or "").strip()),
            "ready": bool(
                getattr(
                    config,
                    "effective_discord_enabled",
                    getattr(config, "ENABLE_DISCORD_BOT", False)
                    and str(getattr(config, "DISCORD_TOKEN", "") or "").strip(),
                )
            )
            and bool(str(getattr(config, "DISCORD_TOKEN", "") or "").strip()),
        },
        "telegram": {
            "enabled": bool(getattr(config, "ENABLE_TELEGRAM_BOT", False)),
            "configured": bool(str(getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()),
            "ready": bool(
                getattr(
                    config,
                    "effective_telegram_enabled",
                    getattr(config, "ENABLE_TELEGRAM_BOT", False)
                    and str(getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip(),
                )
            )
            and bool(str(getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()),
        },
        "mobile": {
            "enabled": bool(getattr(config, "ENABLE_MOBILE_DOOR", False)),
            "configured": bool(str(getattr(config, "MOBILE_PUBLIC_URL", "") or "").strip()),
            "ready": bool(getattr(config, "effective_mobile_enabled", False)),
        },
        "slack": {
            "enabled": bool(getattr(config, "ENABLE_SLACK_BOT", False)),
            "configured": bool(
                str(getattr(config, "SLACK_BOT_TOKEN", "") or "").strip()
                and str(getattr(config, "SLACK_APP_TOKEN", "") or "").strip()
            ),
        },
    }


def build_public_health_detail() -> Dict[str, Any]:
    from ai.llm_client import llm
    from voice.bootstrap import voice_bootstrap_status
    from core.channel_runtime import channel_runtime

    doctor = build_doctor_report()
    launcher = _with_last_doctor_summary(
        dict(doctor.get("launcher") or {}) or launcher_snapshot(),
        summarize_doctor_report(doctor),
    )
    backend_service = dict((launcher.get("services") or {}).get("backend") or {})
    frontend_service = dict((launcher.get("services") or {}).get("frontend") or {})
    db_info = database_writability()
    prompt_status = dict(doctor.get("prompts") or {})
    startup = backend_bootstrap.snapshot()
    startup_status = str(startup.get("status") or "").strip().lower()
    voice = voice_bootstrap_status()
    warnings = [
        {
            "code": str(item.get("code") or ""),
            "severity": str(item.get("severity") or ""),
            "message": str(item.get("message") or ""),
        }
        for item in doctor.get("checks") or []
        if str(item.get("severity") or "") in {"core_blocker", "warning"}
    ]
    warnings.extend(backend_bootstrap.warning_items())
    plugin_snapshot = plugin_runtime.build_health_snapshot()
    channel_snapshot = channel_runtime.snapshot()
    backend_status = str(backend_service.get("status") or "ready")
    if startup_status in {"ready", "degraded", "failed"}:
        backend_status = startup_status
    overall_status = "healthy"
    if doctor["counts"]["core_blockers"] or startup_status in {"degraded", "failed"}:
        overall_status = "degraded"
    return {
        "status": overall_status,
        "version": "2.0.0",
        "deployment_mode": str(getattr(config, "PALLI_DEPLOYMENT_MODE", "local") or "local"),
        "host": {
            **host_platform_service.platform_snapshot(),
            "capabilities": host_platform_service.capability_health(
                desktop_enabled=bool(getattr(config, "OPERATOR_DESKTOP_ENABLED", True)),
                input_enabled=bool(getattr(config, "OPERATOR_DESKTOP_ALLOW_INPUT_CONTROL", True)),
                machine_presence_enabled=bool(getattr(config, "LOCAL_MACHINE_PRESENCE_ENABLED", True)),
                capture_available=bool(
                    host_platform_service.capture_backend(
                        module_available=lambda name: importlib.util.find_spec(name) is not None,
                        binary_available=lambda name: bool(shutil.which(name)),
                    )
                ),
                ocr_available=bool(
                    getattr(config, "OPERATOR_OCR_ENABLED", True)
                    and importlib.util.find_spec("pytesseract") is not None
                    and shutil.which("tesseract")
                ),
            ),
        },
        "backend": {
            "url": str(getattr(config, "BACKEND_URL", "") or ""),
            "host": backend_service.get("host"),
            "port": backend_service.get("port"),
            "status": backend_status,
            "probe": backend_service.get("probe") or {},
            "startup": startup,
        },
        "frontend": {
            "url": str(getattr(config, "FRONTEND_URL", "") or ""),
            "host": frontend_service.get("host"),
            "port": frontend_service.get("port"),
            "status": str(frontend_service.get("status") or "stopped"),
            "probe": frontend_service.get("probe") or {},
        },
        "database": db_info,
        "prompts": prompt_status,
        "llm": llm.get_runtime_status(),
        "voice": {
            "bootstrap_ok": bool(voice.ok),
            "bootstrap_messages": list(voice.messages),
            "bootstrap_details": dict(voice.details),
        },
        "discord": {
            "enabled": bool(getattr(config, "ENABLE_DISCORD_BOT", False)),
            "token_configured": bool(str(getattr(config, "DISCORD_TOKEN", "") or "").strip()),
            # Boot's verdict for this run, not a fourth place that re-derives
            # it (week 2 task 7): a blank token degrades Discord to off rather
            # than stopping the boot, so "ready" is whatever validate() decided.
            # AND a token, belt and braces: Config declares the flag True at
            # class level, so an object that has not been through validate()
            # must not report ready on a blank token.
            "ready": bool(
                getattr(
                    config,
                    "effective_discord_enabled",
                    getattr(config, "ENABLE_DISCORD_BOT", False)
                    and str(getattr(config, "DISCORD_TOKEN", "") or "").strip(),
                )
            )
            and bool(str(getattr(config, "DISCORD_TOKEN", "") or "").strip()),
        },
        "channels": {
            **build_channels_readout(),
            "runtime": channel_snapshot,
        },
        "launcher": launcher,
        "plugins": plugin_snapshot,
        "doctor": {
            "status": doctor["status"],
            "summary": doctor["summary"],
            "counts": dict(doctor["counts"] or {}),
            "last_report": dict(launcher.get("last_doctor") or {}) or None,
        },
        "warnings": warnings,
    }

from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_process_config():
    """`configure` and `brain` write the chosen keys into os.environ AND onto the shared
    config object (LauncherController._apply_env_updates) — that is the product: the
    running process must see its new settings. In a test file it is pollution: after the
    codex-configure tests, every later test in this process ran with a provider routed to
    codex, and on a machine with no Codex login (the first CI run, 2026-09-09) the doctor
    refused a config the test had just called valid. Snapshot both, restore both."""
    import os
    from core.config import config as _shared

    env_before = dict(os.environ)
    attrs_before = {name: getattr(_shared, name, None) for name in dir(_shared) if name.isupper()}
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(env_before)
        # Only what existed before is restored; attributes a test ADDED with
        # monkeypatch(raising=False) are monkeypatch's to undo, not ours.
        for name, value in attrs_before.items():
            try:
                setattr(_shared, name, value)
            except Exception:  # noqa: BLE001 - a read-only attribute is not ours to restore
                pass


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A successful `backup` records itself in ~/.palli/backup.last-ok and
    `doctor` reads that record (final fix wave, 2026-09-26). No test in this
    file may reach the real ~/.palli, so HOME points at tmp for every one of
    them; a test that builds its own home still sets HOME itself.

    `doctor` also checks her NAS folder AS her work account, through sudo
    (Batch 4, Task 10). On a machine whose environment names a NAS that would
    be a real sudo against a real share inside a test, so the folder is set
    to none here; tests/scripts/test_doctor_workbench_nas.py covers the check
    with the probe replaced."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("WORKBENCH_NAS_DIR", "")


@pytest.fixture(autouse=True)
def _no_organ_liveness_reads(monkeypatch):
    """`doctor` also reads her organs from the repo's data/ (Batch 6, item
    3.8) — in a live checkout, her real record. No test in this file may
    read it, so the check's source is stood in for: no night has run, an
    info line and never a warning. tests/scripts/test_doctor_organ_liveness.py
    covers the check against a tmp install."""
    import ai.organ_liveness as organ_liveness

    monkeypatch.setattr(organ_liveness, "install_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(organ_liveness, "night_cycle_switched_off", lambda *args, **kwargs: False)


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "palli_ctl.py"
    spec = importlib.util.spec_from_file_location("palli_ctl_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frontend_start_command_uses_frontend_url_host_port(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()
    monkeypatch.setattr(module, "resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr(module.config, "get_frontend_host_port", lambda: ("localhost", 3100))

    command = controller._frontend_start_command()

    assert command == [
        "/usr/bin/npm",
        "run",
        "start",
        "--",
        "--hostname",
        "localhost",
        "--port",
        "3100",
    ]


def test_frontend_runtime_env_uses_backend_origin(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()
    monkeypatch.setattr(module.config, "get_backend_origin", lambda: "http://127.0.0.1:8000")

    env = controller._frontend_runtime_env()

    assert env["NEXT_PUBLIC_API_PROXY_TARGET"] == "http://127.0.0.1:8000"


def test_save_last_doctor_persists_current_report_summary(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()
    saved_state = {}

    monkeypatch.setattr(controller, "_load_state", lambda: {"services": {}, "last_doctor": {"summary": "Old summary."}})
    monkeypatch.setattr(controller, "_save_state", lambda state: saved_state.update(state))

    controller._save_last_doctor(
        {
            "generated_at": 222.0,
            "status": "warning",
            "summary": "Current doctor summary.",
            "counts": {"core_blockers": 0, "warnings": 2, "ok": 5},
            "launcher": {
                "last_doctor": {
                    "generated_at": 111.0,
                    "status": "ok",
                    "summary": "Older nested summary.",
                    "counts": {"core_blockers": 0, "warnings": 0, "ok": 6},
                }
            },
        }
    )

    assert saved_state["last_doctor"] == {
        "generated_at": 222.0,
        "status": "warning",
        "summary": "Current doctor summary.",
        "counts": {"core_blockers": 0, "warnings": 2, "ok": 5},
    }


def test_prepare_surfaces_actionable_next_artifact_lock_guidance(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()

    class _Completed:
        returncode = 1
        stdout = (
            "Error: EPERM: operation not permitted, unlink "
            "'C:\\Users\\Sam\\Desktop\\palli-local\\website\\.next\\server\\app\\account\\memory.meta'"
        )

    launcher_logs: list[str] = []

    monkeypatch.setattr(controller, "_ensure_prepare_prereqs", lambda: None)
    monkeypatch.setattr(module, "website_node_modules_present", lambda: True)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: _Completed())
    monkeypatch.setattr(module, "append_launcher_log", launcher_logs.append)
    monkeypatch.setattr(module, "launcher_log_path", lambda: Path("logs/launcher/launcher.log"))

    with pytest.raises(RuntimeError) as excinfo:
        controller.prepare(as_json=False)

    message = str(excinfo.value)
    assert "Windows appears to be locking a stale Next.js build artifact" in message
    assert f"`{module.ctl_prefix()} prepare`" in message
    assert "delete `" in message and ".next`" in message
    assert "launcher.log`" in message
    assert any("memory.meta" in entry for entry in launcher_logs)


def test_start_surfaces_actionable_next_artifact_lock_guidance_when_build_missing(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()

    class _Completed:
        returncode = 1
        stdout = (
            "EPERM: operation not permitted, unlink "
            "'C:\\Users\\Sam\\Desktop\\palli-local\\website\\.next\\server\\app\\account\\memory.meta'"
        )

    launcher_logs: list[str] = []

    monkeypatch.setattr(controller, "_assert_startable", lambda: None)
    # The launcher looks for THIS checkout's venv before it spawns; a CI runner has
    # none (2026-09-09, the first CI run: "Missing repo virtualenv Python").
    monkeypatch.setattr(module, "resolve_repo_python_path",
                        lambda: {"exists": True, "executable": True, "path": "venv/bin/python"})
    monkeypatch.setattr(module, "website_build_present", lambda: False)
    monkeypatch.setattr(
        module,
        "inspect_launcher_service",
        lambda service_name: {
            "service": service_name,
            "status": "stopped",
            "ownership": "none",
            "port": 8000 if service_name == "backend" else 3000,
            "url": "http://127.0.0.1:8000" if service_name == "backend" else "http://127.0.0.1:3000",
        },
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: _Completed())
    monkeypatch.setattr(module, "append_launcher_log", launcher_logs.append)
    monkeypatch.setattr(module, "launcher_log_path", lambda: Path("logs/launcher/launcher.log"))

    with pytest.raises(RuntimeError) as excinfo:
        controller.start()

    message = str(excinfo.value)
    assert "Windows appears to be locking a stale Next.js build artifact" in message
    assert f"`{module.ctl_prefix()} start`" in message
    assert "launcher.log`" in message
    assert any("memory.meta" in entry for entry in launcher_logs)


def test_assert_startable_uses_python_prepare_hint(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()

    monkeypatch.setattr(
        module,
        "build_doctor_report",
        lambda: {
            "summary": "Ready with warnings.",
            "checks": [
                {
                    "code": "frontend_deps_missing",
                    "severity": "warning",
                    "message": "Website dependencies are missing.",
                }
            ],
        },
    )
    monkeypatch.setattr(controller, "_save_last_doctor", lambda report: None)

    with pytest.raises(RuntimeError) as excinfo:
        controller._assert_startable()

    assert str(excinfo.value) == f"Website dependencies are missing. Run `{module.ctl_prefix()} prepare` first."


def test_start_backend_timeout_surfaces_probe_and_log_context(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()

    backend_timeout_snapshot = {
        "service": "backend",
        "status": "degraded",
        "ownership": "launcher",
        "host": "127.0.0.1",
        "port": 8000,
        "url": "http://127.0.0.1:8000",
        "log_path": "logs/launcher/backend.log",
        "pid": 4321,
        "pid_running": True,
        "port_open": True,
        "probe": {
            "reachable": True,
            "http_ok": False,
            "status_code": 503,
            "error": "",
        },
    }

    monkeypatch.setattr(controller, "_assert_startable", lambda: None)
    # The launcher looks for THIS checkout's venv before it spawns; a CI runner has
    # none (2026-09-09, the first CI run: "Missing repo virtualenv Python").
    monkeypatch.setattr(module, "resolve_repo_python_path",
                        lambda: {"exists": True, "executable": True, "path": "venv/bin/python"})
    monkeypatch.setattr(module, "website_build_present", lambda: True)
    monkeypatch.setattr(
        module,
        "inspect_launcher_service",
        lambda service_name: {
            "service": service_name,
            "status": "stopped" if service_name == "backend" else "running",
            "ownership": "none" if service_name == "backend" else "launcher",
            "host": "127.0.0.1",
            "port": 8000 if service_name == "backend" else 3000,
            "url": "http://127.0.0.1:8000" if service_name == "backend" else "http://127.0.0.1:3000",
            "log_path": "logs/launcher/backend.log" if service_name == "backend" else "logs/launcher/frontend.log",
            "probe": {},
            "pid": None,
            "pid_running": False,
            "port_open": False,
        },
    )
    monkeypatch.setattr(controller, "_spawn", lambda *args, **kwargs: 4321)
    monkeypatch.setattr(controller, "_update_service_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(controller, "_wait_for_running", lambda *args, **kwargs: backend_timeout_snapshot)
    monkeypatch.setattr(controller, "stop", lambda: 0)
    monkeypatch.setattr(
        module,
        "database_writability",
        lambda: {
            "path": "data/palli-local.sqlite3",
            "absolute_path": "C:/Users/Sam/Desktop/palli-local/data/palli-local.sqlite3",
            "target_kind": "file",
            "openable": True,
            "writable": True,
            "summary": "Database path is writable.",
            "error": "",
        },
    )
    monkeypatch.setattr(module, "launcher_log_path", lambda: Path("logs/launcher/launcher.log"))

    with pytest.raises(RuntimeError) as excinfo:
        controller.start()

    message = str(excinfo.value)
    assert "Backend failed to reach ready state within 30s." in message
    assert "status `degraded`" in message
    assert "launcher pid `4321` is still running" in message
    assert "HTTP 503" in message
    assert "`http://127.0.0.1:8000/api/health`" in message
    assert "SQLite launch check:" in message
    assert "configured path `data/palli-local.sqlite3`" in message
    assert "Database path is writable." in message
    assert "`logs/launcher/backend.log`" in message
    assert "launcher.log`" in message


def test_start_backend_timeout_surfaces_sqlite_probe_context_for_broken_path(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()

    backend_timeout_snapshot = {
        "service": "backend",
        "status": "stopped",
        "ownership": "stale",
        "host": "127.0.0.1",
        "port": 8000,
        "url": "http://127.0.0.1:8000",
        "log_path": "logs/launcher/backend.log",
        "pid": 4040,
        "pid_running": False,
        "port_open": False,
        "probe": {
            "reachable": False,
            "http_ok": False,
            "status_code": None,
            "error": "ConnectionRefusedError: [WinError 10061] No connection could be made",
        },
    }

    monkeypatch.setattr(controller, "_assert_startable", lambda: None)
    # The launcher looks for THIS checkout's venv before it spawns; a CI runner has
    # none (2026-09-09, the first CI run: "Missing repo virtualenv Python").
    monkeypatch.setattr(module, "resolve_repo_python_path",
                        lambda: {"exists": True, "executable": True, "path": "venv/bin/python"})
    monkeypatch.setattr(module, "website_build_present", lambda: True)
    monkeypatch.setattr(
        module,
        "inspect_launcher_service",
        lambda service_name: {
            "service": service_name,
            "status": "stopped" if service_name == "backend" else "running",
            "ownership": "none" if service_name == "backend" else "launcher",
            "host": "127.0.0.1",
            "port": 8000 if service_name == "backend" else 3000,
            "url": "http://127.0.0.1:8000" if service_name == "backend" else "http://127.0.0.1:3000",
            "log_path": "logs/launcher/backend.log" if service_name == "backend" else "logs/launcher/frontend.log",
            "probe": {},
            "pid": None,
            "pid_running": False,
            "port_open": False,
        },
    )
    monkeypatch.setattr(controller, "_spawn", lambda *args, **kwargs: 4040)
    monkeypatch.setattr(controller, "_update_service_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(controller, "_wait_for_running", lambda *args, **kwargs: backend_timeout_snapshot)
    monkeypatch.setattr(controller, "stop", lambda: 0)
    monkeypatch.setattr(
        module,
        "database_writability",
        lambda: {
            "path": "data/broken.sqlite3",
            "absolute_path": "C:/Users/Sam/Desktop/palli-local/data/broken.sqlite3",
            "target_kind": "directory",
            "openable": False,
            "writable": False,
            "summary": "Database path points to a directory.",
            "error": "DATABASE_PATH points to a directory: C:/Users/Sam/Desktop/palli-local/data/broken.sqlite3",
        },
    )
    monkeypatch.setattr(module, "launcher_log_path", lambda: Path("logs/launcher/launcher.log"))

    with pytest.raises(RuntimeError) as excinfo:
        controller.start()

    message = str(excinfo.value)
    assert "Backend failed to reach ready state within 30s." in message
    assert "could not reach `http://127.0.0.1:8000/api/health`" in message
    assert "SQLite launch check:" in message
    assert "configured path `data/broken.sqlite3`" in message
    assert "effective path `C:/Users/Sam/Desktop/palli-local/data/broken.sqlite3`" in message
    assert "target `directory`" in message
    assert "openable `False`" in message
    assert "writable `False`" in message
    assert "Database path points to a directory." in message
    assert "DATABASE_PATH points to a directory" in message


def test_start_frontend_timeout_surfaces_probe_and_log_context(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()

    frontend_timeout_snapshot = {
        "service": "frontend",
        "status": "stopped",
        "ownership": "stale",
        "host": "127.0.0.1",
        "port": 3000,
        "url": "http://127.0.0.1:3000",
        "log_path": "logs/launcher/frontend.log",
        "pid": 5151,
        "pid_running": False,
        "port_open": False,
        "probe": {
            "reachable": False,
            "http_ok": False,
            "status_code": None,
            "error": "ConnectionRefusedError: [WinError 10061] No connection could be made",
        },
    }

    monkeypatch.setattr(controller, "_assert_startable", lambda: None)
    # The launcher looks for THIS checkout's venv before it spawns; a CI runner has
    # none (2026-09-09, the first CI run: "Missing repo virtualenv Python").
    monkeypatch.setattr(module, "resolve_repo_python_path",
                        lambda: {"exists": True, "executable": True, "path": "venv/bin/python"})
    monkeypatch.setattr(module, "website_build_present", lambda: True)
    monkeypatch.setattr(
        module,
        "inspect_launcher_service",
        lambda service_name: {
            "service": service_name,
            "status": "running" if service_name == "backend" else "stopped",
            "ownership": "launcher" if service_name == "backend" else "none",
            "host": "127.0.0.1",
            "port": 8000 if service_name == "backend" else 3000,
            "url": "http://127.0.0.1:8000" if service_name == "backend" else "http://127.0.0.1:3000",
            "log_path": "logs/launcher/backend.log" if service_name == "backend" else "logs/launcher/frontend.log",
            "probe": {},
            "pid": 1234 if service_name == "backend" else None,
            "pid_running": service_name == "backend",
            "port_open": service_name == "backend",
        },
    )
    monkeypatch.setattr(controller, "_spawn", lambda *args, **kwargs: 5151)
    monkeypatch.setattr(controller, "_update_service_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(controller, "_wait_for_running", lambda *args, **kwargs: frontend_timeout_snapshot)
    monkeypatch.setattr(controller, "stop", lambda: 0)
    monkeypatch.setattr(module, "launcher_log_path", lambda: Path("logs/launcher/launcher.log"))

    with pytest.raises(RuntimeError) as excinfo:
        controller.start()

    message = str(excinfo.value)
    assert "Frontend failed to reach ready state within 30s." in message
    assert "ownership `stale`" in message
    assert "launcher pid `5151` is no longer running" in message
    assert "could not reach `http://127.0.0.1:3000/dashboard`" in message
    assert "ConnectionRefusedError" in message
    assert "SQLite launch check:" not in message
    assert "`logs/launcher/frontend.log`" in message
    assert "launcher.log`" in message


def test_build_parser_supports_configure_auth_onboard_and_chat():
    module = _load_module()
    parser = module.build_parser()

    configure_args = parser.parse_args(
        ["configure", "--preset", "codex-primary", "--section", "voice", "--skip-doctor"]
    )
    auth_args = parser.parse_args(["auth", "codex", "--status"])
    onboard_args = parser.parse_args(["onboard", "--flow", "operator", "--skip-start"])
    chat_args = parser.parse_args(["chat", "hello", "there"])

    assert configure_args.command == "configure"
    assert configure_args.preset == "codex-primary"
    assert configure_args.section == ["voice"]
    assert configure_args.skip_doctor is True
    assert auth_args.command == "auth"
    assert auth_args.auth_command == "codex"
    assert auth_args.status_only is True
    assert onboard_args.command == "onboard"
    assert onboard_args.flow == "operator"
    assert onboard_args.skip_start is True
    assert chat_args.command == "chat"
    assert chat_args.message == ["hello", "there"]


def test_auth_codex_uses_status_check_only_when_requested(monkeypatch, capsys):
    module = _load_module()
    controller = module.LauncherController()
    calls: list[list[str]] = []

    def _run_codex_command(args, *, capture_output, timeout_seconds=None):
        calls.append(list(args))
        return module.subprocess.CompletedProcess(
            ["codex", *args],
            0,
            stdout="Logged in using ChatGPT\n",
            stderr="",
        )

    monkeypatch.setattr(controller, "_run_codex_command", _run_codex_command)
    monkeypatch.setattr(controller, "_codex_executable", lambda: "C:\\Users\\Sam\\AppData\\Roaming\\npm\\codex.cmd")

    assert controller.auth_codex(status_only=True, device_auth=False) == 0

    captured = capsys.readouterr()
    assert "Codex CLI is authenticated using ChatGPT." in captured.out
    assert calls == [["login", "status"]]


def test_auth_codex_runs_login_then_verifies_status(monkeypatch, capsys):
    module = _load_module()
    controller = module.LauncherController()
    calls: list[list[str]] = []
    statuses = iter(
        [
            module.subprocess.CompletedProcess(["codex", "login", "status"], 1, stdout="", stderr=""),
            module.subprocess.CompletedProcess(["codex", "login"], 0, stdout="", stderr=""),
            module.subprocess.CompletedProcess(["codex", "login", "status"], 0, stdout="Logged in using an API key - sk-proj-***abcd\n", stderr=""),
        ]
    )

    def _run_codex_command(args, *, capture_output, timeout_seconds=None):
        calls.append(list(args))
        return next(statuses)

    monkeypatch.setattr(controller, "_run_codex_command", _run_codex_command)
    monkeypatch.setattr(controller, "_codex_executable", lambda: "C:\\Users\\Sam\\AppData\\Roaming\\npm\\codex.cmd")

    assert controller.auth_codex(status_only=False, device_auth=False) == 0

    captured = capsys.readouterr()
    assert "Codex CLI authentication is ready using API key." in captured.out
    assert calls == [["login", "status"], ["login"], ["login", "status"]]


def test_auth_codex_reports_already_authenticated_auth_mode(monkeypatch, capsys):
    module = _load_module()
    controller = module.LauncherController()

    monkeypatch.setattr(
        controller,
        "_run_codex_command",
        lambda args, *, capture_output, timeout_seconds=None: module.subprocess.CompletedProcess(
            ["codex", *args],
            0,
            stdout="Logged in using ChatGPT\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(controller, "_codex_executable", lambda: "C:\\Users\\Sam\\AppData\\Roaming\\npm\\codex.cmd")

    assert controller.auth_codex(status_only=False, device_auth=False) == 0

    captured = capsys.readouterr()
    assert "Codex CLI is already authenticated using ChatGPT." in captured.out


def test_configure_codex_primary_writes_env_local_and_runs_doctor(monkeypatch, tmp_path, capsys):
    module = _load_module()
    controller = module.LauncherController()
    controller.palli_yaml_path = tmp_path / "data" / "palli.yaml"  # seeded from the example; never the checkout's
    env_local_path = tmp_path / ".env.local"
    controller.env_local_path = env_local_path

    monkeypatch.setattr(module.config, "OPENAI_API_KEY", "", raising=False)
    # Step 31: the bridge is deleted; classic is the only engine value.
    monkeypatch.setattr(module.config, "VOICE_ENGINE_DEFAULT", "classic", raising=False)
    monkeypatch.setattr(module.config, "CODEX_MODEL", "gpt-5.1-codex-max", raising=False)
    monkeypatch.setattr(module.config, "CODEX_PROFILE", "", raising=False)
    monkeypatch.setattr(module.config, "LLM_VISION_MODEL", "gpt-4o", raising=False)
    monkeypatch.setattr(
        controller,
        "_codex_login_status",
        lambda: module.subprocess.CompletedProcess(["codex", "login", "status"], 0, stdout="Authenticated\n", stderr=""),
    )
    monkeypatch.setattr(
        controller,
        "_run_fresh_doctor_report",
        lambda: {"summary": "Doctor looks good.", "_exit_code": 0},
    )
    monkeypatch.setattr(controller, "_sync_structured_settings", lambda updates: "")

    assert controller.configure(
        preset="codex-primary",
        non_interactive=True,
        codex_model="gpt-5.1-codex-max",
        codex_profile="personal",
        skip_doctor=False,
    ) == 0

    contents = env_local_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=codex" in contents
    assert "LLM_RESPONSE_PROVIDER=codex" in contents
    assert "LLM_RESPONSE_MODEL=gpt-5.1-codex-max" in contents
    assert "LLM_ANALYSIS_PROVIDER=codex" in contents
    assert "CODEX_MODEL=gpt-5.1-codex-max" in contents
    assert "CODEX_PROFILE=personal" in contents
    assert "VOICE_ENGINE_DEFAULT=classic" in contents

    captured = capsys.readouterr()
    assert "Selected preset: codex-primary" in captured.out
    assert "Doctor: Doctor looks good." in captured.out


def test_configure_codex_primary_requires_login_when_non_interactive(monkeypatch, tmp_path):
    module = _load_module()
    controller = module.LauncherController()
    controller.palli_yaml_path = tmp_path / "data" / "palli.yaml"  # seeded from the example; never the checkout's

    monkeypatch.setattr(
        controller,
        "_codex_login_status",
        lambda: module.subprocess.CompletedProcess(["codex", "login", "status"], 1, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError) as excinfo:
        controller.configure(
            preset="codex-primary",
            non_interactive=True,
            codex_model="gpt-5.1-codex-max",
            codex_profile=None,
            skip_doctor=True,
        )

    assert f"Run `{module.ctl_prefix()} auth codex` first" in str(excinfo.value)


def test_configure_codex_primary_requires_separate_auth_when_interactive(monkeypatch, tmp_path):
    module = _load_module()
    controller = module.LauncherController()
    controller.palli_yaml_path = tmp_path / "data" / "palli.yaml"  # seeded from the example; never the checkout's

    monkeypatch.setattr(
        controller,
        "_codex_login_status",
        lambda: module.subprocess.CompletedProcess(["codex", "login", "status"], 1, stdout="", stderr=""),
    )
    monkeypatch.setattr(controller, "_prompt_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(controller, "_prompt_yes_no", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("configure should not inline-run auth")))

    with pytest.raises(RuntimeError) as excinfo:
        controller.configure(
            preset="codex-primary",
            non_interactive=False,
            codex_model="gpt-5.1-codex-max",
            codex_profile=None,
            skip_doctor=True,
        )

    assert f"Then rerun `{module.ctl_prefix()} configure`" in str(excinfo.value)


def test_run_codex_command_preserves_console_modes(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()
    events: list[str] = []

    @contextlib.contextmanager
    def _fake_preserver():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(controller, "_codex_executable", lambda: "codex")
    monkeypatch.setattr(module, "_preserve_windows_console_modes", _fake_preserver)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: module.subprocess.CompletedProcess(["codex", "login", "status"], 0, stdout="", stderr=""),
    )

    completed = controller._run_codex_command(["login", "status"], capture_output=True, timeout_seconds=5.0)

    assert completed.returncode == 0
    assert events == ["enter", "exit"]


def test_configure_supports_subset_sections_without_model_preset(monkeypatch, tmp_path, capsys):
    module = _load_module()
    controller = module.LauncherController()
    env_local_path = tmp_path / ".env.local"
    controller.env_local_path = env_local_path
    monkeypatch.setattr(controller, "_sync_structured_settings", lambda updates: "")

    monkeypatch.setattr(module.config, "LOCAL_OWNER_EMAIL", "owner@example.com", raising=False)
    monkeypatch.setattr(module.config, "LOCAL_OWNER_DISPLAY_NAME", "Owner", raising=False)
    monkeypatch.setattr(module.config, "ENABLE_IDENTITY_ARCHITECTURE", True, raising=False)
    monkeypatch.setattr(module.config, "IDENTITY_ENABLE_PRIVILEGED_AUTONOMY", False, raising=False)
    monkeypatch.setattr(module.config, "ENABLE_VOICE_FEATURES", True, raising=False)
    monkeypatch.setattr(module.config, "VOICE_ENGINE_DEFAULT", "classic", raising=False)
    monkeypatch.setattr(module.config, "VOICE_LOCAL_TTS_VOICE", "alloy-local", raising=False)
    monkeypatch.setattr(module.config, "OPENAI_API_KEY", "", raising=False)

    assert controller.configure(
        preset=None,
        non_interactive=True,
        codex_model=None,
        codex_profile=None,
        skip_doctor=True,
        sections=["identity", "voice"],
    ) == 0

    contents = env_local_path.read_text(encoding="utf-8")
    assert "LOCAL_OWNER_EMAIL=owner@example.com" in contents
    assert "LOCAL_OWNER_DISPLAY_NAME=Owner" in contents
    assert "VOICE_ENGINE_DEFAULT=classic" in contents
    assert "LLM_PROVIDER=" not in contents

    captured = capsys.readouterr()
    assert "Configured sections: identity, voice" in captured.out


def test_configure_supports_companion_section(monkeypatch, tmp_path, capsys):
    """T17 (2026-05-12): the Open-Threads Agenda is unconditional — the
    companion section no longer writes ENABLE_COMPANION_AGENDA or
    COMPANION_AGENDA_* env knobs. It must still be a valid section
    choice (exit 0, reported as configured) and must write no legacy
    companion keys to .env.local."""
    module = _load_module()
    controller = module.LauncherController()
    env_local_path = tmp_path / ".env.local"
    controller.env_local_path = env_local_path
    monkeypatch.setattr(controller, "_sync_structured_settings", lambda updates: "")

    assert controller.configure(
        preset=None,
        non_interactive=True,
        codex_model=None,
        codex_profile=None,
        skip_doctor=True,
        sections=["companion"],
    ) == 0

    contents = env_local_path.read_text(encoding="utf-8") if env_local_path.exists() else ""
    assert "ENABLE_COMPANION_AGENDA" not in contents
    assert "COMPANION_AGENDA_CAPTURE_ENABLED" not in contents
    assert "COMPANION_AGENDA_PROACTIVE_ENABLED" not in contents

    captured = capsys.readouterr()
    assert "Configured sections: companion" in captured.out


def test_build_structured_settings_updates_syncs_llm_voice_and_feature_flags(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()

    monkeypatch.setattr(module.config, "LLM_VISION_PROVIDER", "openai", raising=False)
    monkeypatch.setattr(module.config, "LLM_VISION_MODEL", "gpt-4o", raising=False)

    updates = controller._build_structured_settings_updates(
        {
            "LLM_PROVIDER": "codex",
            "LLM_RESPONSE_PROVIDER": "codex",
            "LLM_RESPONSE_MODEL": "gpt-5.3-codex",
            "LLM_REFLECTION_PROVIDER": "codex",
            "LLM_REFLECTION_MODEL": "gpt-5.3-codex",
            "VOICE_ENGINE_DEFAULT": "classic",
            "ENABLE_DISCORD_BOT": True,
            "ENABLE_COMPANION_AGENDA": False,
            "ENABLE_WEB_SEARCH": True,
        }
    )

    assert updates["llm.default_provider"] == "codex"
    assert updates["llm.routing.response"] == {
        "provider": "codex",
        "model": "gpt-5.3-codex",
    }
    assert updates["llm.routing.reflection"] == {
        "provider": "codex",
        "model": "gpt-5.3-codex",
    }
    assert updates["voice.engine"] == "classic"
    assert updates["channels.discord.enabled"] is True
    # T17 deleted features.companion_agenda from the schema; the
    # builder must ignore the legacy env key rather than map it.
    assert "features.companion_agenda" not in updates
    assert updates["features.web_search"] is True


def test_onboard_operator_defaults_to_codex_primary_and_can_skip_start(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()
    captured: dict[str, object] = {}

    def _fake_configure(**kwargs):
        captured["configure"] = kwargs
        return 0

    monkeypatch.setattr(controller, "configure", _fake_configure)
    monkeypatch.setattr(controller, "start", lambda: (_ for _ in ()).throw(AssertionError("onboard should skip start")))

    assert controller.onboard(
        flow="operator",
        preset=None,
        non_interactive=True,
        codex_model=None,
        codex_profile=None,
        skip_doctor=True,
        skip_start=True,
    ) == 0

    assert captured["configure"]["preset"] == "codex-primary"
    assert captured["configure"]["sections"] == list(module.CONFIG_SECTION_CHOICES)


# --- chat talks to the route that exists (week 1 task 5) ---------------------
# `chat` POSTed to /api/web/runtime/local/probe/message, which 404s: that probe
# route does not exist. The real one is POST /api/chat/message, and it is CSRF
# protected even in local-no-auth mode — the double-submit cookie is the only
# thing between a webpage on the LAN and her shell tool. So the CLI does the
# same dance the dashboard does: GET /api/web/auth/me mints the cookie, the POST
# echoes it back in the X-CSRF-Token header.


def test_chat_posts_to_the_real_route_with_the_csrf_header(monkeypatch, capsys):
    module = _load_module()
    controller = module.LauncherController()
    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs)
        if kwargs["path"] == module.AUTH_ME_PATH:
            return {"user": {"id": 1}}
        return {"response": "hey there", "beats": [{"kind": "thought"}]}

    monkeypatch.setattr(controller, "_runtime_request", fake_request)
    monkeypatch.setattr(controller, "_csrf_token", lambda: "token-abc")

    assert controller.chat(message="hello", show_events=True) == 0

    post = [call for call in calls if call["method"] == "POST"][0]
    assert post["path"] == "/api/chat/message"
    assert post["payload"] == {"message": "hello"}
    assert post["headers"]["X-CSRF-Token"] == "token-abc"
    assert "token-abc" in post["headers"]["Cookie"]

    out = capsys.readouterr().out
    assert "PaLLi: hey there" in out
    assert '"kind": "thought"' in out


def test_chat_bootstraps_the_csrf_cookie_from_auth_me(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()

    class _Response:
        @staticmethod
        def getheaders():
            return [("Set-Cookie", "palli_csrf=minted-here; Path=/; SameSite=lax")]

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: _Response())

    assert controller._csrf_token() == "minted-here"


# --- backup: a snapshot she can survive losing the disk over (week 1 task 5) --


def test_backup_archives_the_databases_and_never_the_secrets(tmp_path, monkeypatch):
    import sqlite3
    import tarfile

    module = _load_module()
    controller = module.LauncherController()

    source = tmp_path / "tree"
    (source / "data").mkdir(parents=True)
    (source / "identity").mkdir()
    connection = sqlite3.connect(source / "data" / "palli.sqlite3")
    connection.execute("CREATE TABLE t (a TEXT)")
    connection.execute("INSERT INTO t VALUES ('kept')")
    connection.commit()
    connection.close()
    (source / "data" / "palli.sqlite3-wal").write_text("wal", encoding="utf-8")
    (source / "data" / "palli.sqlite3-shm").write_text("shm", encoding="utf-8")
    (source / "data" / "scratch.tmp").write_text("tmp", encoding="utf-8")
    (source / "data" / ".env.local").write_text("DEEPSEEK_API_KEY=secret", encoding="utf-8")
    (source / "data" / "delegations").mkdir()
    (source / "data" / "delegations" / "creds.txt").write_text(
        "root-password", encoding="utf-8"
    )
    (source / "data" / "palli.yaml").write_text("features: {}\n", encoding="utf-8")
    (source / "identity" / "MY_STORY.md").write_text("hers", encoding="utf-8")

    controller.root_dir = source

    out_dir = tmp_path / "out"
    assert controller.backup(out_dir=str(out_dir)) == 0

    archives = sorted(out_dir.glob("palli-backup-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0], "r:gz") as handle:
        names = sorted(handle.getnames())

    assert any(name.endswith("data/palli.sqlite3") for name in names)
    assert any(name.endswith("data/palli.yaml") for name in names)
    assert any(name.endswith("identity/MY_STORY.md") for name in names)
    for forbidden in ("-wal", "-shm", ".tmp", ".env.local"):
        assert not any(name.endswith(forbidden) for name in names), (forbidden, names)
    # data/delegations/ holds HOST credentials; a backup gets handed around.
    assert not any("delegations" in name for name in names), names


def test_backup_copies_a_live_database_rather_than_the_file(tmp_path, monkeypatch):
    """VACUUM INTO, not cp: she is running while this takes the snapshot."""
    import sqlite3
    import tarfile

    module = _load_module()
    controller = module.LauncherController()

    source = tmp_path / "tree"
    (source / "data").mkdir(parents=True)
    database = source / "data" / "live.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE t (a TEXT)")
    connection.execute("INSERT INTO t VALUES ('committed')")
    connection.commit()
    # An open write that never commits: a raw copy could catch it, a VACUUM INTO
    # snapshot of the committed state cannot.
    connection.execute("BEGIN")
    connection.execute("INSERT INTO t VALUES ('uncommitted')")

    controller.root_dir = source
    out_dir = tmp_path / "out"
    assert controller.backup(out_dir=str(out_dir)) == 0
    connection.close()

    archive = sorted(out_dir.glob("palli-backup-*.tar.gz"))[0]
    with tarfile.open(archive, "r:gz") as handle:
        member = [n for n in handle.getnames() if n.endswith("live.sqlite3")][0]
        handle.extract(member, path=tmp_path / "restored", filter="data")
    restored = sqlite3.connect(tmp_path / "restored" / member)
    rows = [row[0] for row in restored.execute("SELECT a FROM t")]
    restored.close()
    assert rows == ["committed"]


# --- backup, part two: encrypted, with ~/.palli, kept to N, never into an ------
# unmounted NAS (stop the leaks, task 6). The nightly run writes to a share the
# whole LAN can read, so only ciphertext may ever land there. Every secret below
# is a stand-in, and HOME points at tmp so Path.home() never reaches the real one.

_OPENSSL = shutil.which("openssl")
_needs_openssl = pytest.mark.skipif(_OPENSSL is None, reason="openssl not installed")


def _backup_tree(tmp_path):
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "identity").mkdir()
    (root / "data" / "a.txt").write_text("a", encoding="utf-8")
    (root / "identity" / "b.md").write_text("b", encoding="utf-8")
    home = tmp_path / "home"
    palli_home = home / ".palli"
    vault = palli_home / "vault"
    vault.mkdir(parents=True)
    (vault / "vault.key").write_text("k" * 64 + "\n", encoding="utf-8")
    (vault / ".passphrase").write_text("p" * 50 + "\n", encoding="utf-8")
    (palli_home / "logs").mkdir()
    (palli_home / "logs" / "noise.log").write_text("log", encoding="utf-8")
    (palli_home / "models").mkdir()
    (palli_home / "models" / "weights.bin").write_bytes(b"\0" * 16)
    (palli_home / "browser").mkdir()
    (palli_home / "browser" / "Preferences").write_text("{}", encoding="utf-8")
    (palli_home / "helper.sock").write_text("", encoding="utf-8")
    passfile = palli_home / "backup.passphrase"
    passfile.write_text("fake-" + "backup-phrase\n", encoding="utf-8")
    os.chmod(passfile, 0o600)
    return root, home, passfile


def _backup_controller(root):
    module = _load_module()
    controller = module.LauncherController()
    controller.root_dir = root
    return module, controller


def _decrypted_names(enc, passfile, tmp_path):
    plain = tmp_path / (enc.name + ".plain")
    subprocess.run(
        [_OPENSSL, "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
         "-in", str(enc), "-out", str(plain), "-pass", f"file:{passfile}"],
        check=True,
    )
    with tarfile.open(plain, "r:gz") as handle:
        return handle.getnames()


@_needs_openssl
def test_encrypted_backup_carries_home_and_keeps_the_newest_two(tmp_path, monkeypatch, capsys):
    root, home, passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    _module, controller = _backup_controller(root)
    # Three runs inside one second (the cron line and a hand run can collide):
    # all three must survive as three names, and keep must drop the one made
    # FIRST — not whichever sorts first ("-2" sorts before ".tar", by accident).
    monkeypatch.setattr(controller, "_backup_stamp", lambda: "20260926-030000")
    out = tmp_path / "nas"
    written = []
    for _ in range(3):
        assert controller.backup(
            out_dir=str(out), include_home=True, encrypt=True, passfile=str(passfile), keep=2
        ) == 0
        captured = capsys.readouterr()
        written.append(Path(captured.out.splitlines()[0]))
        assert "lock" not in captured.err

    assert len(set(written)) == 3
    kept = sorted(out.glob("palli-backup-*.tar.gz.enc"))
    assert set(kept) == set(written[1:]), (kept, written)
    assert not list(out.glob("*.tar.gz"))

    names = _decrypted_names(written[-1], passfile, tmp_path)
    assert "home/.palli/vault/vault.key" in names and "data/a.txt" in names
    assert "home/.palli/vault/.passphrase" in names
    assert "home/.palli/browser/Preferences" in names
    # The archive never carries its own key; logs/, models/ and sockets stay home.
    assert not any(name.endswith("backup.passphrase") for name in names), names
    assert not any(name.startswith(("home/.palli/logs", "home/.palli/models")) for name in names)
    assert not any(name.endswith(".sock") for name in names)
    # The vault was read under the shared lock her saves take exclusively.
    lock = home / ".palli" / "vault" / ".lock"
    assert lock.exists() and (lock.stat().st_mode & 0o777) == 0o600


def test_plain_backup_keeps_the_newest_and_runs_on_a_real_mount(tmp_path, monkeypatch, capsys):
    root, home, _passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    _module, controller = _backup_controller(root)
    monkeypatch.setattr(controller, "_backup_stamp", lambda: "20260926-030000")
    out = tmp_path / "out"
    written = []
    for _ in range(2):
        assert controller.backup(out_dir=str(out), keep=1, require_mount="/") == 0
        written.append(Path(capsys.readouterr().out.splitlines()[0]))
    assert sorted(out.iterdir()) == [written[-1]]
    assert written[-1].name == "palli-backup-20260926-030000-2.tar.gz"


def test_backup_refuses_an_unmounted_target(tmp_path, capsys):
    root, _home, _passfile = _backup_tree(tmp_path)
    _module, controller = _backup_controller(root)
    not_mounted = tmp_path / "not-a-mount"
    assert controller.backup(out_dir=str(tmp_path / "nas"), require_mount=str(not_mounted)) == 2
    assert not (tmp_path / "nas").exists()
    assert f"{not_mounted} is not mounted" in capsys.readouterr().out


def test_encrypt_refuses_a_passfile_others_can_read_or_one_that_is_missing(tmp_path, monkeypatch):
    root, home, passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    _module, controller = _backup_controller(root)
    nas = tmp_path / "nas"
    os.chmod(passfile, 0o644)
    with pytest.raises(RuntimeError):
        controller.backup(out_dir=str(nas), encrypt=True, passfile=str(passfile))
    passfile.unlink()
    with pytest.raises(RuntimeError):
        controller.backup(out_dir=str(nas), encrypt=True)  # the default: ~/.palli/backup.passphrase
    assert not nas.exists()


def test_home_never_goes_into_an_unencrypted_archive(tmp_path, monkeypatch):
    """~/.palli holds the vault's own key. Keys never ride an archive someone could hand around."""
    root, home, _passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    _module, controller = _backup_controller(root)
    nas = tmp_path / "nas"
    with pytest.raises(RuntimeError):
        controller.backup(out_dir=str(nas), include_home=True)
    assert not nas.exists()


@_needs_openssl
def test_an_encrypted_backup_puts_only_ciphertext_in_the_target(tmp_path, monkeypatch):
    import tempfile

    root, home, passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    module, controller = _backup_controller(root)
    out = tmp_path / "nas"

    seen_at_encrypt = []
    real_run = subprocess.run

    def _spy(command, *args, **kwargs):
        # The moment openssl starts, the plaintext tar is complete: it must be
        # somewhere private, and the share must still be empty.
        seen_at_encrypt.append((sorted(p.name for p in out.iterdir()), command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", _spy)
    assert controller.backup(
        out_dir=str(out), include_home=True, encrypt=True, passfile=str(passfile)
    ) == 0

    assert len(seen_at_encrypt) == 1
    listing, command = seen_at_encrypt[0]
    assert listing == []
    plaintext_in = Path(command[command.index("-in") + 1])
    assert out not in plaintext_in.parents
    assert [p.name.endswith(".tar.gz.enc") for p in out.iterdir()] == [True]
    assert list(scratch.iterdir()) == []  # no plaintext left behind locally either


def test_a_failed_encryption_leaves_nothing_behind(tmp_path, monkeypatch):
    import tempfile

    root, home, passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    # An openssl that writes half an output file and then fails.
    fake = tmp_path / "fake-openssl"
    fake.write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "-out" ]; then echo half > "$2"; fi\n'
        "  shift\n"
        "done\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    _module, controller = _backup_controller(root)
    controller.BACKUP_OPENSSL = str(fake)
    out = tmp_path / "nas"
    with pytest.raises(RuntimeError):
        controller.backup(out_dir=str(out), include_home=True, encrypt=True, passfile=str(passfile))
    assert list(out.iterdir()) == []
    assert list(scratch.iterdir()) == []
    # A failed backup is never recorded as a good one.
    assert not (home / ".palli" / "backup.last-ok").exists()


@_needs_openssl
def test_a_vault_save_in_progress_is_waited_for_then_warned_about(tmp_path, monkeypatch, capsys):
    import fcntl

    root, home, passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    _module, controller = _backup_controller(root)
    controller.BACKUP_VAULT_LOCK_WAIT_SECONDS = 0.2
    lock = home / ".palli" / "vault" / ".lock"
    held = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(held, fcntl.LOCK_EX)  # her save, mid-write
        assert controller.backup(
            out_dir=str(tmp_path / "nas"), include_home=True, encrypt=True, passfile=str(passfile)
        ) == 0
    finally:
        os.close(held)
    err = capsys.readouterr().err
    assert "vault" in err and "lock" in err


@_needs_openssl
def test_a_home_file_that_vanishes_mid_walk_is_skipped_and_counted(tmp_path, monkeypatch, capsys):
    root, home, passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    doomed = home / ".palli" / "browser" / "Cookies-journal"
    doomed.write_text("x", encoding="utf-8")
    _module, controller = _backup_controller(root)
    listed = controller._backup_home_files

    def _then_vanish(*args, **kwargs):
        files = listed(*args, **kwargs)
        # The browser deleted it between the walk and the read. (The vault is
        # listed on its own, under its lock, so this runs twice.)
        doomed.unlink(missing_ok=True)
        return files

    monkeypatch.setattr(controller, "_backup_home_files", _then_vanish)
    out = tmp_path / "nas"
    assert controller.backup(
        out_dir=str(out), include_home=True, encrypt=True, passfile=str(passfile)
    ) == 0
    assert "1 skipped" in capsys.readouterr().out
    names = _decrypted_names(next(out.glob("*.tar.gz.enc")), passfile, tmp_path)
    assert "home/.palli/vault/vault.key" in names
    assert not any(name.endswith("Cookies-journal") for name in names)


def test_a_home_file_that_shrinks_mid_read_does_not_tear_the_archive(tmp_path):
    """tarfile writes the header's size before it reads a byte. A live browser file
    that shrinks mid-read used to leave the stream short — every member after it
    misaligned. The reader pads to the promised size; the archive stays whole."""
    import io

    module = _load_module()
    path = tmp_path / "t.tar"
    with tarfile.open(path, "w") as archive:
        shrunk = tarfile.TarInfo("shrunk")
        shrunk.size = 10
        reader = module._FixedLengthReader(io.BytesIO(b"abc"), 10)
        archive.addfile(shrunk, reader)
        after = tarfile.TarInfo("after")
        after.size = 2
        archive.addfile(after, io.BytesIO(b"ok"))
    assert reader.torn
    with tarfile.open(path) as archive:
        assert archive.getnames() == ["shrunk", "after"]
        assert archive.extractfile("after").read() == b"ok"
        assert archive.extractfile("shrunk").read() == b"abc" + b"\0" * 7


@pytest.mark.parametrize("first_line", ["", "eightchr"])
def test_encrypt_refuses_a_passphrase_too_short_to_mean_anything(tmp_path, monkeypatch, first_line):
    """OpenSSL encrypts with an EMPTY password when the first line is empty, and
    exits 0. A ciphertext anyone can open is worse than none: it looks safe."""
    root, home, passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    passfile.write_text(first_line + "\n" + "a-long-second-line-openssl-never-reads\n", encoding="utf-8")
    os.chmod(passfile, 0o600)
    _module, controller = _backup_controller(root)
    nas = tmp_path / "nas"
    with pytest.raises(RuntimeError) as refused:
        controller.backup(out_dir=str(nas), include_home=True, encrypt=True, passfile=str(passfile))
    assert f"{len(first_line)} characters" in str(refused.value)
    if first_line:
        assert first_line not in str(refused.value)  # the length, never the content
    assert not nas.exists()


def test_out_must_sit_inside_the_required_mount(tmp_path, monkeypatch):
    """A typo in --out or --require-mount would otherwise pass the mount check
    and write to the local disk anyway: the guard defeated in silence."""
    root, _home, _passfile = _backup_tree(tmp_path)
    module, controller = _backup_controller(root)
    share = tmp_path / "share"
    share.mkdir()
    monkeypatch.setattr(
        module.os.path, "ismount", lambda p: os.path.realpath(p) == os.path.realpath(share)
    )
    elsewhere = tmp_path / "local-disk"
    with pytest.raises(RuntimeError):
        controller.backup(out_dir=str(elsewhere), require_mount=str(share))
    assert not elsewhere.exists()
    assert controller.backup(out_dir=str(share / "palli"), require_mount=str(share)) == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root opens a mode-400 file for writing anyway")
@_needs_openssl
def test_the_vault_lock_needs_only_a_read_descriptor(tmp_path, monkeypatch, capsys):
    """A .lock she cannot write (created by root, say) must still lock: flock(2)
    needs only a read descriptor. Opening it O_RDWR copied the vault unlocked."""
    root, home, passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    lock = home / ".palli" / "vault" / ".lock"
    lock.write_text("", encoding="utf-8")
    os.chmod(lock, 0o400)
    _module, controller = _backup_controller(root)
    assert controller.backup(
        out_dir=str(tmp_path / "nas"), include_home=True, encrypt=True, passfile=str(passfile)
    ) == 0
    assert "lock" not in capsys.readouterr().err


def test_a_data_file_that_vanishes_mid_walk_is_skipped_and_counted(tmp_path, monkeypatch, capsys):
    """data/ is live too: settings saves write palli.yaml.tmp.XXXX and rename it
    away. One file vanishing used to fail the whole nightly run."""
    root, _home, _passfile = _backup_tree(tmp_path)
    doomed = root / "data" / "doomed.json"
    doomed.write_text("{}", encoding="utf-8")
    (root / "data" / "palli.yaml.tmp.ab12").write_text("half", encoding="utf-8")
    _module, controller = _backup_controller(root)
    listed = controller._backup_tree_files

    def _then_vanish(*args, **kwargs):
        paths = listed(*args, **kwargs)
        doomed.unlink(missing_ok=True)  # gone between the walk and the read
        return paths

    monkeypatch.setattr(controller, "_backup_tree_files", _then_vanish)
    out = tmp_path / "out"
    assert controller.backup(out_dir=str(out)) == 0
    assert "1 skipped" in capsys.readouterr().out
    with tarfile.open(next(out.glob("palli-backup-*.tar.gz")), "r:gz") as handle:
        names = handle.getnames()
    assert "data/a.txt" in names and "identity/b.md" in names
    assert not any(name.endswith("doomed.json") for name in names)
    assert not any(".tmp." in name for name in names)


# --- backup, part three: a failure you can see (final fix wave, 2026-09-26) --
# The nightly run is a cron line writing to logs/backup.log that nobody reads.
# A good run records itself in ~/.palli/backup.last-ok; doctor warns when that
# record is more than 36 h old. HOME is tmp in every test (autouse above).


def _last_ok(home: Path) -> Path:
    return home / ".palli" / "backup.last-ok"


def test_a_successful_backup_records_when_it_happened(tmp_path, monkeypatch, capsys):
    import json
    import stat
    from datetime import datetime, timedelta, timezone

    root, home, _passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    _module, controller = _backup_controller(root)
    out = tmp_path / "out"
    before = datetime.now(timezone.utc)
    assert controller.backup(out_dir=str(out)) == 0
    archive = Path(capsys.readouterr().out.splitlines()[0])

    record = _last_ok(home)
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    data = json.loads(record.read_text(encoding="utf-8"))
    assert set(data) == {"at", "archive"}
    assert data["archive"] == archive.name  # the file name only, never a path
    at = datetime.fromisoformat(data["at"])
    assert at.tzinfo is not None and at.utcoffset() == timedelta(0)
    assert before - timedelta(seconds=1) <= at <= datetime.now(timezone.utc) + timedelta(seconds=1)
    # Atomic: nothing but the record (and the tree the test built) is left.
    assert not [p for p in record.parent.iterdir() if p.name.startswith(".backup.last-ok")]


def test_a_refused_or_failed_backup_leaves_the_old_record_alone(tmp_path, monkeypatch, capsys):
    root, home, _passfile = _backup_tree(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    _module, controller = _backup_controller(root)
    record = _last_ok(home)
    record.write_text('{"at": "2026-09-01T03:20:00+00:00", "archive": "old.tar.gz"}\n', encoding="utf-8")
    before = record.read_bytes()

    # The cron line's "skipped" exit: an unmounted NAS.
    assert controller.backup(
        out_dir=str(tmp_path / "nas"), require_mount=str(tmp_path / "not-a-mount")
    ) == 2
    # A refused run: --include-home without --encrypt.
    with pytest.raises(RuntimeError):
        controller.backup(out_dir=str(tmp_path / "out"), include_home=True)

    assert record.read_bytes() == before


def test_no_backup_record_is_a_note_not_a_failure(monkeypatch, capsys):
    module = _load_module()
    controller = module.LauncherController()
    report = {"summary": "Environment is ready.", "status": "ok",
              "counts": {"core_blockers": 0, "warnings": 0, "ok": 0}, "checks": []}
    monkeypatch.setattr(module, "build_doctor_report", lambda **_kwargs: report)
    monkeypatch.setattr(controller, "_save_last_doctor", lambda report: None)

    assert controller.doctor(as_json=False) == 0

    out = capsys.readouterr().out
    assert "[INFO] backup_none_recorded: No backup recorded yet" in out
    assert "Environment is ready." in out


def _doctor_with_record(module, controller, monkeypatch, home, payload_text):
    record = _last_ok(home)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(payload_text, encoding="utf-8")
    report = {"summary": "Environment is ready.", "status": "ok",
              "counts": {"core_blockers": 0, "warnings": 0, "ok": 1},
              "checks": [{"code": "config_valid", "severity": "ok",
                          "message": "Config validation passed.", "details": {}}]}
    monkeypatch.setattr(module, "build_doctor_report", lambda **_kwargs: report)
    monkeypatch.setattr(controller, "_save_last_doctor", lambda report: None)
    return report


def test_a_stale_backup_record_is_a_warning_naming_the_age_and_the_log(tmp_path, monkeypatch, capsys):
    import json
    from datetime import datetime, timedelta, timezone

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    module = _load_module()
    controller = module.LauncherController()
    at = (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat()
    _doctor_with_record(module, controller, monkeypatch, home,
                        json.dumps({"at": at, "archive": "palli-backup-x.tar.gz.enc"}))

    assert controller.doctor(as_json=False) == 0  # never a failure

    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "backup_stale" in ln)
    assert line.startswith("[WARNING] backup_stale:")
    assert "40 h" in line and "tail logs/backup.log" in line
    assert "Ready with 1 warning(s)." in out


def test_a_stale_backup_record_reaches_the_json_form(tmp_path, monkeypatch, capsys):
    import json
    from datetime import datetime, timedelta, timezone

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    module = _load_module()
    controller = module.LauncherController()
    at = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    _doctor_with_record(module, controller, monkeypatch, home,
                        json.dumps({"at": at, "archive": "palli-backup-x.tar.gz.enc"}))

    assert controller.doctor(as_json=True) == 0

    payload = json.loads(capsys.readouterr().out)
    check = next(c for c in payload["checks"] if c["code"] == "backup_stale")
    assert check["severity"] == "warning"
    assert "5 days" in check["message"] and "tail logs/backup.log" in check["message"]
    assert check["details"]["archive"] == "palli-backup-x.tar.gz.enc"
    assert payload["counts"]["warnings"] == 1 and payload["status"] == "warning"


def test_a_recent_backup_record_is_ok(tmp_path, monkeypatch, capsys):
    import json
    from datetime import datetime, timedelta, timezone

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    module = _load_module()
    controller = module.LauncherController()
    at = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _doctor_with_record(module, controller, monkeypatch, home,
                        json.dumps({"at": at, "archive": "palli-backup-x.tar.gz.enc"}))

    assert controller.doctor(as_json=False) == 0

    out = capsys.readouterr().out
    assert "[OK] backup_recent:" in out and "[WARNING]" not in out
    assert "Environment is ready." in out


def test_an_unreadable_backup_record_is_a_warning_not_a_failure(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    module = _load_module()
    controller = module.LauncherController()
    _doctor_with_record(module, controller, monkeypatch, home, "not json at all")

    assert controller.doctor(as_json=False) == 0

    out = capsys.readouterr().out
    assert "[WARNING] backup_record_unreadable:" in out and "tail logs/backup.log" in out


def test_backup_cli_passes_every_new_flag_through(monkeypatch):
    module = _load_module()
    seen = {}
    monkeypatch.setattr(
        module.LauncherController, "backup", lambda self, **kwargs: seen.update(kwargs) or 0
    )
    assert module.main([
        "backup", "--out", "/srv/nas", "--include-home", "--encrypt",
        "--passfile", "/srv/pass", "--keep", "14", "--require-mount", "/srv/nas",
    ]) == 0
    assert seen == {
        "out_dir": "/srv/nas", "include_home": True, "encrypt": True,
        "passfile": "/srv/pass", "keep": 14, "require_mount": "/srv/nas",
    }


# test_tui_once_renders_runtime_console_snapshot deleted in Wave 0:
# the palli_ctl tui subcommand is gone (CLI/TUI surface dropped per design spec).


# --- doctor says WHY a config is invalid (step 28 task 4 fix 1) --------------
# `config_invalid` used to be the whole message: "Config validation failed." The
# reasons were logged and then thrown away by a logging.disable() around the call,
# so a stranger's first doctor named the blocker and refused to say what caused it.


def test_doctor_prints_the_reasons_a_config_is_invalid(monkeypatch, capsys):
    module = _load_module()
    controller = module.LauncherController()
    report = {
        "summary": "Environment has core blockers.",
        "counts": {"core_blockers": 1},
        "checks": [
            {
                "code": "config_invalid",
                "severity": "core_blocker",
                "message": "Config validation failed.",
                "details": {
                    "reasons": [
                        "EXA_API_KEY is required when web search features are enabled",
                        "DEEPGRAM_API_KEY is required when STT_PROVIDER=deepgram",
                    ]
                },
            }
        ],
    }
    # **_kwargs: doctor asks for the live brain/balance probes now (week 1 task 1b).
    monkeypatch.setattr(module, "build_doctor_report", lambda **_kwargs: report)
    monkeypatch.setattr(controller, "_save_last_doctor", lambda report: None)

    assert controller.doctor(as_json=False) == 1

    out = capsys.readouterr().out
    # the summary line is unchanged — the reasons are added under it, not instead of it
    assert "[CORE_BLOCKER] config_invalid: Config validation failed." in out
    assert "EXA_API_KEY is required when web search features are enabled" in out
    assert "DEEPGRAM_API_KEY is required when STT_PROVIDER=deepgram" in out


def test_doctor_asks_for_the_live_checks_and_prints_them(monkeypatch, capsys):
    """Week 1 task 1b. Two things at once: `doctor` opts into the live brain and
    balance probes (the health endpoint that shares the builder must not), and
    their answers reach the person reading the terminal, not only --json."""
    module = _load_module()
    controller = module.LauncherController()
    asked: list[dict] = []
    report = {
        "summary": "Ready with 1 warning(s).",
        "counts": {"core_blockers": 0},
        "checks": [
            {
                "code": "llm_brain_answers",
                "severity": "ok",
                "message": "The brain (deepseek) answered in 42 ms.",
            },
            {
                "code": "llm_balance_low",
                "severity": "warning",
                "message": "Balance is low (0.42 USD, floor 2.00) — top up before she runs dry.",
            },
        ],
    }

    def _report(**kwargs):
        asked.append(kwargs)
        return report

    monkeypatch.setattr(module, "build_doctor_report", _report)
    monkeypatch.setattr(controller, "_save_last_doctor", lambda report: None)

    assert controller.doctor(as_json=False) == 0

    assert asked == [{"probe_llm": True}]
    out = capsys.readouterr().out
    assert "[OK] llm_brain_answers: The brain (deepseek) answered in 42 ms." in out
    assert "[WARNING] llm_balance_low: Balance is low" in out


def test_config_validation_reasons_names_the_key_that_is_missing(monkeypatch):
    """The reasons come from the schema itself, not from scraping the log: the doctor can
    ask for them without unmuting logging inside a live process."""
    from core import launcher_support

    # The planted gap has to be one that still BLOCKS. Week 1 task 1 moved
    # EXA_API_KEY into the warnings, and week 2 task 7 moved the blank Discord
    # token there too (a stranger meets her on the dashboard first), so the gap
    # is task 4's lock: a wide bind whose list of who may reach her parses to
    # nothing.
    monkeypatch.setattr(launcher_support.config, "BACKEND_HOST", "0.0.0.0", raising=False)
    monkeypatch.setattr(launcher_support.config, "BACKEND_ALLOWED_NETWORKS", "nonsense", raising=False)

    reasons = launcher_support.config_validation_reasons()

    assert any("BACKEND_ALLOWED_NETWORKS" in reason for reason in reasons), reasons


def test_config_validation_reasons_is_empty_when_the_config_is_fine(monkeypatch):
    """Non-vacuous partner to the test above: without the planted gap there is nothing to
    report, so a green doctor cannot start printing phantom reasons."""
    from core import launcher_support

    monkeypatch.setattr(launcher_support.config, "ENABLE_WEB_SEARCH", False, raising=False)
    monkeypatch.setattr(launcher_support.config, "EXA_API_KEY", "", raising=False)
    monkeypatch.setattr(launcher_support.config, "ENABLE_DISCORD_BOT", False, raising=False)
    # "fine" is what THIS test supplies (a public clone has no .env.local to lean on).
    monkeypatch.setattr(launcher_support.config, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(launcher_support.config, "DEEPSEEK_API_KEY", "sk-test-not-a-real-key", raising=False)

    assert launcher_support.config_validation_reasons() == []


# --- .env.template must actually be usable (step 28 task 4 fix 1) -----------


def _template_value(key: str) -> str:
    """One uncommented value from the shipped .env.template."""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / ".env.template").read_text(encoding="utf-8")
    match = re.search(rf"(?m)^{key}=(.*)$", text)
    assert match is not None, f"{key} is missing from .env.template"
    return match.group(1).strip()


def test_a_fresh_env_copied_from_the_template_imports_the_config(tmp_path):
    """`cp .env.template .env` is step one of every install, and for twelve keys the
    template shipped a BLANK where core/config.py does int(os.getenv(KEY, "3000")) at
    class-body time. A blank is not a missing value — os.getenv returns "", the default
    never applies, and int("") raises before a single line of PaLLi runs. The fresh-clone
    doctor died here, on `PALLI_DEBOUNCE_MS`, with a traceback and no message.

    Run in a SUBPROCESS: core.config is a module-level singleton evaluated once at import,
    so the failure this guards against cannot be reached from inside a process that has
    already imported it. PALLI_TEST_DATA_ROOT is set so core/config.py loads its dotenv
    files with override=False and the owner's real .env cannot paper over a blank here."""
    import os
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    template = repo / ".env.template"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    for line in template.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.split("#")[0].strip().strip('"')
    # LAST, not first: the template ships `PALLI_TEST_DATA_ROOT=` blank, and the loop
    # above would set it back to "" — which flips core/config.py's _ENV_OVERRIDE to True
    # and lets the OWNER's .env.local win over the template values this test is checking.
    env["PALLI_TEST_DATA_ROOT"] = str(tmp_path)
    # The template ships the bot enabled with a blank token, and since week 2
    # task 7 that is a WARNING, not a blocker: Discord is off for that boot and
    # she starts anyway, because the dashboard is the first door. This test is
    # about the API KEYS, so it turns the bot off — one less line in the output
    # it reads, and nothing here depends on Discord either way.
    env["ENABLE_DISCORD_BOT"] = "false"

    # A fresh clone the way it really is: .env from the template, and data/palli.yaml
    # seeded from the shipped example — into tmp_path, never the owner's copy. Mirrors
    # tests/conftest.py's own redirect, which is the shape a public clone boots in.
    target = tmp_path / "palli.yaml"
    probe = (
        "import pathlib;"
        "import core.settings_boot as sb;"
        "from core.settings_service import SettingsService;"
        f"t = pathlib.Path({str(target)!r});"
        "sb._DEFAULT_YAML_PATH = t;"
        "sb.settings_service = SettingsService(yaml_path=t);"
        "sb.seed_from_example(t);"
        "from core.config import config;"
        "print(config.LLM_PROVIDER);"
        "print('VALID' if config.validate() else 'INVALID')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, (
        "a fresh .env copied from .env.template cannot even be imported:\n"
        + result.stderr[-2000:]
    )
    lines = result.stdout.split()
    # PREMISE UPDATED 2026-09-18 (owner: a stranger picks at the door). The
    # template's brain is the keyless cloud one that puts them on the door; it
    # used to be the local server, which skipped the door entirely. What this
    # test is really about is the line below — a fresh clone with NO key set
    # still VALIDATES — and that is unchanged.
    assert lines[0] == _template_value("LLM_PROVIDER"), result.stdout
    # The whole keyless claim in one assertion (review I3). web_search off is guarded in
    # the yaml by KEY_BOUND_FLAGS; its twin — ENABLE_VOICE_FEATURES=false in the template
    # — had no guard at all, and flipping it back sends a fresh clone straight to
    # "DEEPGRAM_API_KEY is required when STT_PROVIDER=deepgram" with the suite still green.
    assert lines[1] == "VALID", (
        "the shipped .env.template + data/palli.example.yaml do not validate with NO key "
        "set — the keyless first run is broken:\n" + result.stderr[-2000:]
    )


# --- the first command a stranger runs (step 28 task 4 fix 2, review C1) -----
# A fresh clone has NO data/palli.yaml — Task 5's public tree ships only the example —
# and palli_ctl's settings readers load that file DIRECTLY rather than through
# core.settings_boot.boot_settings(), which is where the seed lives. So `onboard`, the
# first command both docs give, answered "settings file not found" and wrote nothing.
# Every test below works in tmp_path only; the owner's data/palli.yaml is never a target.


def _example_source() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "palli.example.yaml"


def test_yaml_settings_seeds_the_config_a_fresh_clone_does_not_have(tmp_path):
    module = _load_module()
    controller = module.LauncherController()
    target = tmp_path / "data" / "palli.yaml"
    controller.palli_yaml_path = target
    assert not target.exists()

    settings = controller._yaml_settings()

    assert target.is_file(), "the settings reader did not seed the file it then read"
    assert settings.presets, "the seeded config carries no presets for onboard to offer"
    assert target.read_bytes() == _example_source().read_bytes()


def test_the_seed_never_overwrites_a_config_that_is_already_there(tmp_path):
    """The owner's live file is the truth, always — this runs on his machine too."""
    module = _load_module()
    controller = module.LauncherController()
    target = tmp_path / "data" / "palli.yaml"
    target.parent.mkdir(parents=True)
    target.write_bytes(_example_source().read_bytes())
    target.write_text(target.read_text(encoding="utf-8") + "\n# a hand edit\n", encoding="utf-8")
    before = target.read_bytes()
    controller.palli_yaml_path = target

    controller._yaml_settings()

    assert target.read_bytes() == before


@pytest.mark.parametrize("argv", [["doctor"], ["prepare"], ["start"], ["restart"], ["status"]])
def test_the_launcher_commands_seed_the_config_before_the_doctor_reads_it(tmp_path, argv):
    """The stranger test (2026-10-10): on a fresh clone `prepare`, the README's first
    command after the install, stopped with "3 core blocker(s)". Nothing on that path
    had seeded data/palli.yaml (only main.py's boot, `brain` and the settings reader
    do), so the lane routing fell back to the template's LLM_PROVIDER (a cloud provider
    with no key yet) for all ten lanes, and the three core lanes read as blockers
    before the door could ask for a brain. With the example in place the same doctor
    reports only the setup-mode warning. Each launcher command now seeds first."""
    module = _load_module()
    target = tmp_path / "data" / "palli.yaml"
    seen: list[bool] = []

    class Seeded(module.LauncherController):
        def __init__(self):
            super().__init__()
            self.palli_yaml_path = target

        def _ran(self, *a, **k):
            seen.append(target.is_file())
            return 0

        doctor = prepare = start = restart = status = _ran

    original = module.LauncherController
    module.LauncherController = Seeded
    try:
        assert module.main(argv) == 0
    finally:
        module.LauncherController = original
    assert seen == [True], "the command ran before its settings file was seeded"
    assert target.read_bytes() == _example_source().read_bytes()



def test_brain_seeds_the_config_before_reading_it(tmp_path, capsys):
    """`brain --pick` printed "Cannot read …: settings file not found" on a fresh clone.
    run_brain_command builds its OWN SettingsService, so it needs the seed too."""
    import argparse

    module = _load_module()
    target = tmp_path / "data" / "palli.yaml"
    (tmp_path / "data").mkdir()
    assert not target.exists()
    args = argparse.Namespace(
        preset=None, show=True, pick=False, except_lanes=[], lane=None, to=None,
        undo=False, dry_run=False, verbose=False, force=False, yes=False,
    )
    lines: list[str] = []

    code = module.run_brain_command(
        args,
        env_local=tmp_path / ".env.local",
        env_base=tmp_path / ".env",
        palli_yaml=target,
        prober=lambda *a, **k: {},
        git_status_fn=lambda: "",
        out=lines.append,
    )

    printed = "\n".join(lines)
    assert "Cannot read" not in printed, printed
    assert code == 0, printed
    assert target.is_file(), "brain did not seed the config it reads"


def test_the_documented_first_command_reaches_its_own_work_on_a_fresh_tree(tmp_path):
    """C1 end to end, in-process: `onboard` resolves a preset from the seeded config
    instead of dying on the read. _infer_runtime_preset is the first thing it does with
    the settings, and it is what chooses the answer the docs call "needs nothing from
    you"."""
    module = _load_module()
    controller = module.LauncherController()
    controller.palli_yaml_path = tmp_path / "data" / "palli.yaml"

    presets = controller._yaml_presets()

    assert module.LauncherController._infer_runtime_preset(controller) in presets


# --- the seam: build_doctor_report actually attaches the reasons (review I2) --


def test_build_doctor_report_attaches_the_reasons_to_the_config_check(monkeypatch):
    """The three fix-round-1 tests pinned the helper and the printer and MISSED the wiring
    between them: deleting `details={"reasons": reasons}` from build_doctor_report left the
    whole suite green while the live doctor went back to saying "Config validation failed."
    and nothing else. This is the seam — a real gap in, the sentences out."""
    from core import launcher_support

    # The planted gap is a blocker again (week 1 task 1 softened EXA_API_KEY;
    # week 2 task 7 softened the blank Discord token). This one is task 4's
    # lock — a wide bind with an allowed-networks list that parses to nothing.
    monkeypatch.setattr(launcher_support.config, "BACKEND_HOST", "0.0.0.0", raising=False)
    monkeypatch.setattr(launcher_support.config, "BACKEND_ALLOWED_NETWORKS", "nonsense", raising=False)

    report = launcher_support.build_doctor_report()

    check = next(c for c in report["checks"] if c["code"] == "config_invalid")
    reasons = (check.get("details") or {}).get("reasons") or []
    assert reasons, "config_invalid carries no reasons — the doctor cannot say why"
    assert any("BACKEND_ALLOWED_NETWORKS" in reason for reason in reasons), reasons
    # the summary line is unchanged; the reasons are additional, never a replacement
    assert check["message"] == "Config validation failed."


def test_a_valid_config_produces_no_config_invalid_check(monkeypatch):
    """Non-vacuous partner: the check above must be absent when nothing is wrong, so the
    test cannot be satisfied by a doctor that always reports a blocker."""
    from core import launcher_support

    monkeypatch.setattr(launcher_support.config, "ENABLE_WEB_SEARCH", False, raising=False)
    monkeypatch.setattr(launcher_support.config, "ENABLE_DISCORD_BOT", False, raising=False)
    # A valid config is one THIS test supplies, not one the checkout's .env.local
    # happens to hold (2026-09-09, the first CI run: no key there, so "valid" failed).
    monkeypatch.setattr(launcher_support.config, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(launcher_support.config, "DEEPSEEK_API_KEY", "sk-test-not-a-real-key", raising=False)

    report = launcher_support.build_doctor_report()

    assert [c for c in report["checks"] if c["code"] == "config_invalid"] == []


def test_the_reasons_are_computed_while_the_log_is_still_muted(monkeypatch):
    """Review I1. build_doctor_report() is the body of GET /api/health/detail, so a second
    validation pass outside the logging mute writes the validators' provider and plugin
    warnings into a live backend's log on every health poll — the exact thing the helper's
    docstring says it exists to avoid.

    Asserted as STATE, not as captured output: pytest installs its own root handlers, so a
    log line emitted during the call is swallowed by the logging plugin and capsys sees
    nothing either way. What cannot be faked is whether logging was disabled at the moment
    the helper ran."""
    import logging as _logging

    from core import launcher_support

    real = launcher_support.config_validation_reasons
    seen: dict[str, int] = {}

    def watched():
        seen["disable_level"] = _logging.root.manager.disable
        return real()

    monkeypatch.setattr(launcher_support, "config_validation_reasons", watched)
    # A blocker, not a warning: config_validation_reasons() only runs when
    # validate() said no. Week 1 task 1 made a missing EXA_API_KEY a warning,
    # week 2 task 7 a missing Discord token, and the first door (task 1,
    # premise update with the owner's yes 2026-09-17 22:36) the blank brain
    # key itself — setup mode, she starts. So the planted gap is task 4's
    # lock, like its two siblings above: a wide bind whose list of who may
    # reach her parses to nothing.
    monkeypatch.setattr(launcher_support.config, "BACKEND_HOST", "0.0.0.0", raising=False)
    monkeypatch.setattr(launcher_support.config, "BACKEND_ALLOWED_NETWORKS", "nonsense", raising=False)
    before = _logging.root.manager.disable

    launcher_support.build_doctor_report()

    assert seen.get("disable_level") == _logging.CRITICAL, (
        "config_validation_reasons() ran with logging live — move the call back inside "
        "the same `try:` as config.validate()"
    )
    assert _logging.root.manager.disable == before, "the mute was not put back"


# --- onboard's last step, and what counts as a blocker (review I5 / I6) ------


def _dashboard_blocker(code: str) -> dict:
    return {"code": code, "severity": "core_blocker", "message": "the dashboard is not built"}


def test_configure_does_not_call_a_dashboard_blocker_a_failure(monkeypatch, tmp_path, capsys):
    """Every fresh clone has all four dashboard checks red — nobody has run `prepare` —
    and configure raised on ANY core blocker, so a setup that succeeded exited 1 with a
    traceback. `main.py`, the Discord path both docs lead with, never touches Node."""
    module = _load_module()
    controller = module.LauncherController()
    controller.env_local_path = tmp_path / ".env.local"
    monkeypatch.setattr(controller, "_sync_structured_settings", lambda updates: "")
    monkeypatch.setattr(
        controller, "_run_fresh_doctor_report",
        lambda: {
            "summary": "2 core blocker(s) need attention before full start.",
            "_exit_code": 1,
            "checks": [
                _dashboard_blocker("frontend_deps_missing"),
                _dashboard_blocker("frontend_build_missing"),
            ],
        },
    )

    assert controller.configure(
        preset=None, non_interactive=True, codex_model=None, codex_profile=None,
        skip_doctor=False, sections=["identity"],
    ) == 0

    out = capsys.readouterr().out
    assert "venv/bin/python main.py" in out
    assert "prepare" in out


def test_configure_still_fails_on_a_blocker_that_is_not_the_dashboard(monkeypatch, tmp_path):
    """Non-vacuous partner: an invalid config must still stop the run."""
    module = _load_module()
    controller = module.LauncherController()
    controller.env_local_path = tmp_path / ".env.local"
    monkeypatch.setattr(controller, "_sync_structured_settings", lambda updates: "")
    monkeypatch.setattr(
        controller, "_run_fresh_doctor_report",
        lambda: {
            "summary": "1 core blocker(s) need attention before full start.",
            "_exit_code": 1,
            "checks": [
                _dashboard_blocker("frontend_build_missing"),
                {"code": "config_invalid", "severity": "core_blocker", "message": "no"},
            ],
        },
    )

    with pytest.raises(RuntimeError, match="doctor still reports blockers"):
        controller.configure(
            preset=None, non_interactive=True, codex_model=None, codex_profile=None,
            skip_doctor=False, sections=["identity"],
        )


def test_onboard_does_not_attempt_a_start_the_launcher_would_refuse(monkeypatch, capsys):
    """`Start PaLLi now?` defaults to YES and calls start(), which _assert_startable
    refuses while the website deps are missing — the state of every fresh clone. The
    successful setup ended in a RuntimeError."""
    module = _load_module()
    controller = module.LauncherController()
    monkeypatch.setattr(module, "website_node_modules_present", lambda: False)
    monkeypatch.setattr(controller, "configure", lambda **kwargs: 0)
    started = []
    monkeypatch.setattr(controller, "start", lambda: started.append(True) or 0)
    monkeypatch.setattr(controller, "_prompt_yes_no", lambda *a, **k: True)

    code = controller.onboard(
        flow="companion", preset="local-only", non_interactive=True, codex_model=None,
        codex_profile=None, skip_doctor=True, skip_start=False,
    )

    assert code == 0
    assert started == [], "onboard tried to start with the dashboard deps missing"
    out = capsys.readouterr().out
    assert "venv/bin/python main.py" in out
    assert "prepare" in out


def test_onboard_still_starts_when_the_dashboard_is_ready(monkeypatch):
    """Non-vacuous partner: the guard must not swallow the start on a prepared install."""
    module = _load_module()
    controller = module.LauncherController()
    monkeypatch.setattr(module, "website_node_modules_present", lambda: True)
    monkeypatch.setattr(controller, "configure", lambda **kwargs: 0)
    started = []
    monkeypatch.setattr(controller, "start", lambda: started.append(True) or 0)

    controller.onboard(
        flow="companion", preset="local-only", non_interactive=True, codex_model=None,
        codex_profile=None, skip_doctor=True, skip_start=False,
    )

    assert started == [True]


def test_a_missing_dashboard_build_is_not_a_blocker_without_the_deps(monkeypatch):
    """Review I6. One problem, one check: `frontend_deps_missing` already reports that
    state, and the build cannot exist before them. Both firing as CORE_BLOCKER opened a
    correctly-configured keyless clone's first doctor with "3 core blocker(s)"."""
    from core import launcher_support

    monkeypatch.setattr(launcher_support, "website_build_present", lambda: False)
    monkeypatch.setattr(launcher_support, "website_node_modules_present", lambda: False)
    monkeypatch.setattr(launcher_support, "resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr(launcher_support, "resolve_npm_binary", lambda: "/usr/bin/npm")

    report = launcher_support.build_doctor_report()

    check = next(c for c in report["checks"] if c["code"] == "frontend_build_missing")
    assert check["severity"] == "warning", check
    assert not any(
        c["code"] == "frontend_build_missing" and c["severity"] == "core_blocker"
        for c in report["checks"]
    )


# ── one env writer for the CLI and the door (first door, task 2, 2026-09-17) ──


def test_configure_writes_through_the_shared_writer_and_keeps_crlf(tmp_path):
    """`_write_env_overrides` used to re-join every line with "\\n".

    `.env.local` is CRLF, so one `configure` run rewrote the whole file and put
    every untouched line in the owner's diff. It now goes through
    `palli_config.envfile`, the writer `palli_ctl brain` and the setup API
    already share, which takes the newline style from the text it is given.
    """
    module = _load_module()
    path = tmp_path / ".env.local"
    path.write_bytes(
        b"# her brain\r\nLLM_PROVIDER=deepseek\r\nDEEPSEEK_MODEL=deepseek-v4-pro\r\n"
    )

    module._write_env_overrides(path, {"LLM_PROVIDER": "openai"})

    raw = path.read_bytes()
    assert raw == (
        b"# her brain\r\nLLM_PROVIDER=openai\r\nDEEPSEEK_MODEL=deepseek-v4-pro\r\n"
    )


def test_configure_still_opens_a_file_it_creates_with_its_two_header_lines(tmp_path):
    module = _load_module()
    path = tmp_path / ".env.local"

    module._write_env_overrides(path, {"LLM_PROVIDER": "openai"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# Local overrides written by")
    assert lines[1].startswith("# This file is loaded after `.env`")
    assert "LLM_PROVIDER=openai" in lines


def test_configure_still_renders_bools_numbers_and_values_needing_quotes(tmp_path):
    """The types `configure` collects survive the move: the shared writer takes
    strings, so the coercion stays here and the quoting moves there."""
    module = _load_module()
    path = tmp_path / ".env.local"

    module._write_env_overrides(
        path,
        {
            "ENABLE_VOICE_FEATURES": True,
            "ENABLE_DISCORD_BOT": False,
            "BACKEND_PORT": 8000,
            "LOCAL_OWNER_DISPLAY_NAME": "Some Owner",
            "EMPTY_ON_PURPOSE": None,
        },
    )

    written = path.read_text(encoding="utf-8")
    assert "ENABLE_VOICE_FEATURES=true" in written
    assert "ENABLE_DISCORD_BOT=false" in written
    assert "BACKEND_PORT=8000" in written
    assert 'LOCAL_OWNER_DISPLAY_NAME="Some Owner"' in written
    assert "EMPTY_ON_PURPOSE=" in written


def test_configure_never_writes_a_key_twice(tmp_path):
    module = _load_module()
    path = tmp_path / ".env.local"
    path.write_text("LLM_PROVIDER=deepseek\nLLM_PROVIDER=openai\n", encoding="utf-8")

    module._write_env_overrides(path, {"LLM_PROVIDER": "local_openai"})

    assert path.read_text(encoding="utf-8").count("LLM_PROVIDER=") == 1


# --- Batch 8 Task 11 A3: `start` never launches a second her ------------------
#
# Started from tmux she has no launcher record; the launcher now reads her
# :8000 listener as "self" (core/launcher_support.her_listener_pid). `start`
# refuses — a second main.py would run on the same tokens — `stop` leaves her
# alone (no launcher pid to stop), and `status` says who she is.


def _her_snapshot(service_name: str) -> dict:
    if service_name == "backend":
        return {
            "service": "backend", "status": "running", "ownership": "self",
            "host": "127.0.0.1", "port": 8000, "url": "http://127.0.0.1:8000",
            "log_path": "logs/launcher/backend.log", "probe": {},
            "pid": 4242, "pid_running": False, "port_open": True, "managed": False,
        }
    return {
        "service": "frontend", "status": "stopped", "ownership": "none",
        "host": "127.0.0.1", "port": 3000, "url": "http://127.0.0.1:3000",
        "log_path": "logs/launcher/frontend.log", "probe": {},
        "pid": None, "pid_running": False, "port_open": False, "managed": False,
    }


def _never(*args, **kwargs):
    raise AssertionError("a second main.py would run on her tokens")


@pytest.mark.parametrize("doctor_passes", [True, False])
def test_start_refuses_while_she_is_already_running(monkeypatch, doctor_passes):
    module = _load_module()
    controller = module.LauncherController()

    if doctor_passes:
        monkeypatch.setattr(controller, "_assert_startable", lambda: None)
    else:
        def _blocked():
            raise RuntimeError("1 core blocker(s) need attention.")
        monkeypatch.setattr(controller, "_assert_startable", _blocked)
    monkeypatch.setattr(module, "resolve_repo_python_path",
                        lambda: {"exists": True, "executable": True, "path": "venv/bin/python"})
    monkeypatch.setattr(module, "website_build_present", lambda: True)
    monkeypatch.setattr(module, "inspect_launcher_service", _her_snapshot)
    monkeypatch.setattr(module, "append_launcher_log", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_spawn", _never)
    monkeypatch.setattr(controller, "_run_frontend_command", _never)

    with pytest.raises(RuntimeError) as excinfo:
        controller.start()

    assert "she is already running (pid 4242)" in str(excinfo.value)


def test_stop_leaves_her_alone(monkeypatch):
    module = _load_module()
    controller = module.LauncherController()

    monkeypatch.setattr(controller, "_load_state", lambda: {"services": {}})
    monkeypatch.setattr(controller, "_remove_service_state", lambda *a, **k: None)
    monkeypatch.setattr(module, "inspect_launcher_service", _her_snapshot)
    monkeypatch.setattr(module, "append_launcher_log", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_terminate_pid", _never)
    monkeypatch.setattr(controller, "_kill_pid", _never)

    assert controller.stop() == 0


def test_status_names_her_pid_when_she_runs_outside_the_launcher(monkeypatch, capsys):
    module = _load_module()
    controller = module.LauncherController()

    monkeypatch.setattr(
        module,
        "launcher_snapshot",
        lambda: {
            "services": {"backend": _her_snapshot("backend"), "frontend": _her_snapshot("frontend")},
            "last_doctor": None,
        },
    )

    assert controller.status(as_json=False) == 0

    out = capsys.readouterr().out
    assert "Backend: running (self, pid 4242, started outside the launcher)" in out, out


# The stranger test (2026-10-10), the second find: `restart` after `brain` left the
# web page's server running. The launcher starts each service in a session of its own
# (start_new_session=True), but `stop` signalled only the pid it recorded: npm, which
# exits, while next-server under it kept port 3400 and `start` then refused ("occupied
# by a foreign process"). Windows already stops the tree (taskkill /T); POSIX now
# signals the process group the launcher made, and only when the pid leads one.


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
@pytest.mark.parametrize("how", ["_terminate_pid", "_kill_pid"])
def test_stop_takes_down_the_web_servers_whole_group(tmp_path, how):
    module = _load_module()
    controller = module.LauncherController()
    child_file = tmp_path / "child.pid"
    parent = subprocess.Popen(
        ["/bin/sh", "-c", f"sleep 300 & echo $! > {child_file}; wait"], start_new_session=True,
    )
    try:
        for _ in range(100):
            if child_file.exists() and child_file.read_text().strip():
                break
            import time
            time.sleep(0.05)
        child = int(child_file.read_text().strip())
        getattr(controller, how)(parent.pid, group=True)
        parent.wait(timeout=5)
        assert _wait_gone(child), "the parent stopped and its child kept running"
    finally:
        with contextlib.suppress(Exception):
            os.killpg(parent.pid, 9)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_a_pid_that_leads_no_group_is_signalled_alone(tmp_path):
    """Never a group the launcher did not make: this process shares the test
    runner's group, and signalling that group would stop the runner itself."""
    module = _load_module()
    controller = module.LauncherController()
    lone = subprocess.Popen(["sleep", "300"])
    try:
        assert os.getpgid(lone.pid) != lone.pid
        controller._terminate_pid(lone.pid, group=True)
        lone.wait(timeout=5)
        assert lone.returncode is not None
    finally:
        with contextlib.suppress(Exception):
            lone.kill()


# Fable's review of the fix above (2026-10-10): `stop` signalled whatever pid the state
# file named. A record left by a crash or a reboot can name a pid the system has since
# given to something else, and with the group signal that could be a whole tmux pane.
# A recorded pid is the launcher's only if it started within seconds of the record
# (Linux /proc); a mismatch is never signalled, only forgotten.


def _session_with_child(tmp_path):
    import time

    child_file = tmp_path / "child.pid"
    parent = subprocess.Popen(
        ["/bin/sh", "-c", f"sleep 300 & echo $! > {child_file}; wait"], start_new_session=True,
    )
    for _ in range(100):
        if child_file.exists() and child_file.read_text().strip():
            break
        time.sleep(0.05)
    return parent, int(child_file.read_text().strip())


def _stop_with_record(module, controller, monkeypatch, record):
    removed: list[str] = []
    monkeypatch.setattr(controller, "_load_state", lambda: {"services": {"frontend": record}})
    monkeypatch.setattr(controller, "_remove_service_state", lambda name: removed.append(name))
    monkeypatch.setattr(module, "append_launcher_log", lambda *a, **k: None)
    monkeypatch.setattr(
        module, "inspect_launcher_service",
        lambda name, **k: {"pid_running": module.process_is_running(record["pid"]), "port_open": False},
    )
    assert controller.stop() == 0
    return removed


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads start times from Linux /proc")
def test_stop_never_signals_a_pid_that_started_long_after_its_record(tmp_path, monkeypatch):
    import time

    module = _load_module()
    controller = module.LauncherController()
    parent, child = _session_with_child(tmp_path)
    try:
        record = {"pid": parent.pid, "started_at": time.time() - 3600, "command": ["npm", "run", "start"]}
        removed = _stop_with_record(module, controller, monkeypatch, record)
        assert "frontend" in removed, "the stale record is forgotten"
        assert parent.poll() is None and not _wait_gone(child, 0.3), "a stranger's process was signalled"
    finally:
        with contextlib.suppress(Exception):
            os.killpg(parent.pid, 9)


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads start times from Linux /proc")
def test_stop_takes_down_the_group_it_started(tmp_path, monkeypatch):
    import time

    module = _load_module()
    controller = module.LauncherController()
    parent, child = _session_with_child(tmp_path)
    try:
        record = {"pid": parent.pid, "started_at": time.time(), "command": ["npm", "run", "start"]}
        _stop_with_record(module, controller, monkeypatch, record)
        parent.wait(timeout=5)
        assert _wait_gone(child), "next-server's stand-in outlived stop"
    finally:
        with contextlib.suppress(Exception):
            os.killpg(parent.pid, 9)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_stop_reaches_the_orphans_of_a_leader_that_already_died(tmp_path, monkeypatch):
    """npm gone on its own, next-server still holding the port: the group outlives
    its leader, and Linux never hands that number to a new process while it does."""
    import time

    module = _load_module()
    controller = module.LauncherController()
    child_file = tmp_path / "child.pid"
    leader = subprocess.Popen(
        ["/bin/sh", "-c", f"sleep 300 & echo $! > {child_file}"], start_new_session=True,
    )
    leader.wait(timeout=5)
    for _ in range(100):
        if child_file.exists() and child_file.read_text().strip():
            break
        time.sleep(0.05)
    child = int(child_file.read_text().strip())
    try:
        assert os.getpgid(child) == leader.pid
        record = {"pid": leader.pid, "started_at": time.time(), "command": ["npm", "run", "start"]}
        _stop_with_record(module, controller, monkeypatch, record)
        assert _wait_gone(child), "the orphan kept running"
    finally:
        with contextlib.suppress(Exception):
            os.kill(child, 9)


# ── presence (the presence work, step one, 2026-10-11) ───────────────────────


def test_presence_prints_the_report_for_the_installs_own_data(monkeypatch, capsys):
    """`palli_ctl presence --days 14`: the report over this install's data dir and
    database, read-only; --save writes the baseline under data/presence."""
    module = _load_module()
    from core import presence_report

    seen: dict = {}
    counts = {"window_days": 14}

    def fake_collect(**kw):
        seen.update(kw)
        seen["collects"] = seen.get("collects", 0) + 1
        return counts

    def fake_baseline(out_dir, **kw):
        seen["out_dir"] = out_dir
        seen["saved"] = kw.get("counts")
        return out_dir / "2026-10-11.json"

    monkeypatch.setattr(presence_report, "collect", fake_collect)
    monkeypatch.setattr(presence_report, "lines_for", lambda c: ["line one", "line two"])
    monkeypatch.setattr(presence_report, "write_baseline", fake_baseline)
    assert module.main(["presence", "--days", "14", "--save"]) == 0
    out = capsys.readouterr().out
    assert "line one" in out and "line two" in out and "Saved:" in out
    assert seen["days"] == 14 and "judge" not in seen
    assert seen["data_dir"] == module.ROOT_DIR / "data" and seen["repo_root"] == module.ROOT_DIR
    assert Path(seen["db_path"]).is_absolute()
    assert seen["out_dir"] == module.ROOT_DIR / "data" / "presence"
    assert seen["collects"] == 1 and seen["saved"] is counts     # printed and saved from one run


def test_presence_judge_passes_the_model_judge_and_its_limit(monkeypatch, capsys):
    """`palli_ctl presence --judge --judge-limit 20` (2026-10-12): the model
    judge and the cap reach collect; without --judge neither does."""
    module = _load_module()
    from core import presence_report

    seen: dict = {}
    monkeypatch.setattr(presence_report, "collect", lambda **kw: seen.update(kw) or {"window_days": 30})
    monkeypatch.setattr(presence_report, "lines_for", lambda c: ["judged line"])
    assert module.main(["presence", "--judge", "--judge-limit", "20"]) == 0
    assert "judged line" in capsys.readouterr().out
    assert seen["judge"] is presence_report.model_judge and seen["judge_limit"] == 20

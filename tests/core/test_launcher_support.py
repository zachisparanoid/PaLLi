from __future__ import annotations

from pathlib import Path

from core.config import config
from core.launcher_support import build_doctor_report, build_public_health_detail, database_writability
from palli_config.ctl_prefix import ctl_prefix


def test_build_doctor_report_flags_foreign_backend_port(monkeypatch):
    monkeypatch.setattr("core.launcher_support.resolve_repo_python_path", lambda: {"path": "/tmp/venv/bin/python", "exists": True, "executable": True})
    monkeypatch.setattr("core.launcher_support.resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr("core.launcher_support.resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr("core.launcher_support.website_node_modules_present", lambda: True)
    monkeypatch.setattr("core.launcher_support.website_build_present", lambda: True)
    monkeypatch.setattr("core.launcher_support.database_writability", lambda: {"path": "data/test.sqlite3", "absolute_path": "/tmp/test.sqlite3", "writable": True, "error": ""})
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"ownership": "external", "port": 8000, "status": "degraded"},
                "frontend": {"ownership": "none", "port": 3000, "status": "stopped"},
            },
            "last_doctor": None,
        },
    )
    monkeypatch.setattr("core.launcher_support._check_optional_capabilities", lambda: [])
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": [], "task_routing": {}, "route_issues": []})
    monkeypatch.setattr(config, "validate", lambda: True)
    # The doors are the owner's env, not this test's: with ENABLE_TELEGRAM_BOT on in .env the
    # doctor adds its door line and the warning count drifts (2026-09-17). Pin them off here.
    monkeypatch.setattr(config, "ENABLE_TELEGRAM_BOT", False, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)

    report = build_doctor_report()

    assert report["status"] == "blocked"
    assert report["counts"]["core_blockers"] == 1
    assert any(item["code"] == "backend_port_external" for item in report["checks"])


def test_build_public_health_detail_exposes_doctor_and_launcher(monkeypatch):
    monkeypatch.setattr(
        "core.launcher_support.build_doctor_report",
        lambda: {
            "status": "warning",
            "summary": "Ready with warnings.",
            "counts": {"core_blockers": 0, "warnings": 2, "ok": 5},
            "checks": [{"code": "voice_bootstrap_degraded", "severity": "warning", "message": "Voice bootstrap is degraded on this host."}],
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"status": "running", "host": "127.0.0.1", "port": 8000, "probe": {}},
                "frontend": {"status": "stopped", "host": "127.0.0.1", "port": 3000, "probe": {}},
            },
            "last_doctor": {"summary": "Ready with warnings."},
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.backend_bootstrap.snapshot",
        lambda: {
            "status": "degraded",
            "counts": {"ready": 3, "skipped": 0, "degraded": 1, "failed": 0},
            "stages": [
                {
                    "stage_id": "runtime_recovery",
                    "status": "degraded",
                    "required": False,
                    "message": "Runtime recovery failed; backend remains available in degraded mode. (RuntimeError: boom)",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.backend_bootstrap.warning_items",
        lambda: [
            {
                "code": "backend_startup_runtime_recovery_degraded",
                "severity": "warning",
                "message": "Runtime recovery failed; backend remains available in degraded mode. (RuntimeError: boom)",
            }
        ],
    )
    monkeypatch.setattr("core.launcher_support.database_writability", lambda: {"path": "data/test.sqlite3", "absolute_path": "/tmp/test.sqlite3", "writable": True, "error": ""})
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": []})

    class _VoiceStatus:
        ok = False
        messages = ["missing opus"]
        details = {"opus": "missing"}

    monkeypatch.setattr("voice.bootstrap.voice_bootstrap_status", lambda: _VoiceStatus())

    detail = build_public_health_detail()

    assert detail["status"] == "degraded"
    assert detail["doctor"]["counts"]["warnings"] == 2
    assert detail["launcher"]["last_doctor"]["summary"] == "Ready with warnings."
    assert detail["backend"]["status"] == "degraded"
    assert detail["backend"]["startup"]["status"] == "degraded"
    assert detail["frontend"]["status"] == "stopped"
    assert any(item["code"] == "backend_startup_runtime_recovery_degraded" for item in detail["warnings"])


def test_build_doctor_report_surfaces_current_last_doctor_summary(monkeypatch):
    monkeypatch.setattr("core.launcher_support.resolve_repo_python_path", lambda: {"path": "/tmp/venv/bin/python", "exists": True, "executable": True})
    monkeypatch.setattr("core.launcher_support.resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr("core.launcher_support.resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr("core.launcher_support.website_node_modules_present", lambda: True)
    monkeypatch.setattr("core.launcher_support.website_build_present", lambda: True)
    monkeypatch.setattr(
        "core.launcher_support.database_writability",
        lambda: {
            "path": "data/test.sqlite3",
            "absolute_path": "/tmp/test.sqlite3",
            "writable": True,
            "error": "",
            "summary": "Database path is writable.",
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"ownership": "none", "port": 8000, "status": "stopped"},
                "frontend": {"ownership": "none", "port": 3000, "status": "stopped"},
            },
            "last_doctor": {
                "generated_at": 10.0,
                "status": "warning",
                "summary": "Old launcher summary.",
                "counts": {"core_blockers": 0, "warnings": 1, "ok": 7},
            },
        },
    )
    monkeypatch.setattr("core.launcher_support._check_optional_capabilities", lambda: [])
    monkeypatch.setattr("core.launcher_support.time.time", lambda: 1234.5)
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": [], "task_routing": {}, "route_issues": []})
    monkeypatch.setattr(config, "validate", lambda: True)

    report = build_doctor_report()

    assert report["generated_at"] == 1234.5
    assert report["launcher"]["last_doctor"]["generated_at"] == 1234.5
    assert report["launcher"]["last_doctor"]["status"] == report["status"]
    assert report["launcher"]["last_doctor"]["summary"] == report["summary"]
    assert report["launcher"]["last_doctor"]["counts"] == report["counts"]


def test_build_public_health_detail_prefers_current_doctor_summary_for_last_report(monkeypatch):
    monkeypatch.setattr(
        "core.launcher_support.build_doctor_report",
        lambda: {
            "generated_at": 200.0,
            "status": "warning",
            "summary": "Current doctor summary.",
            "counts": {"core_blockers": 0, "warnings": 2, "ok": 5},
            "checks": [],
            "launcher": {
                "services": {
                    "backend": {"status": "running", "host": "127.0.0.1", "port": 8000, "probe": {}},
                    "frontend": {"status": "stopped", "host": "127.0.0.1", "port": 3000, "probe": {}},
                },
                "last_doctor": {
                    "generated_at": 200.0,
                    "status": "warning",
                    "summary": "Current doctor summary.",
                    "counts": {"core_blockers": 0, "warnings": 2, "ok": 5},
                },
            },
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"status": "running", "host": "127.0.0.1", "port": 8000, "probe": {}},
                "frontend": {"status": "stopped", "host": "127.0.0.1", "port": 3000, "probe": {}},
            },
            "last_doctor": {
                "generated_at": 100.0,
                "status": "ok",
                "summary": "Older stored summary.",
                "counts": {"core_blockers": 0, "warnings": 0, "ok": 6},
            },
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.backend_bootstrap.snapshot",
        lambda: {
            "status": "ready",
            "counts": {"ready": 1, "skipped": 0, "degraded": 0, "failed": 0},
            "stages": [],
        },
    )
    monkeypatch.setattr("core.launcher_support.backend_bootstrap.warning_items", lambda: [])
    monkeypatch.setattr("core.launcher_support.database_writability", lambda: {"path": "data/test.sqlite3", "absolute_path": "/tmp/test.sqlite3", "writable": True, "error": ""})
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": []})

    class _VoiceStatus:
        ok = True
        messages = []
        details = {}

    monkeypatch.setattr("voice.bootstrap.voice_bootstrap_status", lambda: _VoiceStatus())

    detail = build_public_health_detail()

    assert detail["launcher"]["last_doctor"]["generated_at"] == 200.0
    assert detail["launcher"]["last_doctor"]["summary"] == "Current doctor summary."
    assert detail["doctor"]["last_report"]["generated_at"] == 200.0
    assert detail["doctor"]["last_report"]["summary"] == "Current doctor summary."


def test_database_writability_uses_effective_default_path_when_config_blank(monkeypatch):
    captured: dict[str, Path] = {}

    def fake_inspect(path: Path):
        captured["path"] = path
        return {
            "writable": True,
            "error": "",
            "summary": "Database path is writable.",
            "journal_mode_fallback_active": False,
        }

    monkeypatch.setattr(config, "DATABASE_PATH", "")
    monkeypatch.setattr("core.launcher_support.inspect_sqlite_database_path", fake_inspect)

    info = database_writability()

    assert info["path"] == "data/palli-local.sqlite3"
    assert info["absolute_path"].endswith(str(Path("data") / "palli-local.sqlite3"))
    assert captured["path"] == Path(info["absolute_path"])


def test_database_writability_reports_directory_target(monkeypatch, tmp_path):
    directory_target = tmp_path / "db-dir"
    directory_target.mkdir()
    monkeypatch.setattr(config, "DATABASE_PATH", str(directory_target))

    info = database_writability()

    assert info["writable"] is False
    assert info["target_kind"] == "directory"
    assert "directory" in info["error"].lower()


def test_build_doctor_report_warns_when_database_journal_falls_back(monkeypatch):
    monkeypatch.setattr("core.launcher_support.resolve_repo_python_path", lambda: {"path": "/tmp/venv/bin/python", "exists": True, "executable": True})
    monkeypatch.setattr("core.launcher_support.resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr("core.launcher_support.resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr("core.launcher_support.website_node_modules_present", lambda: True)
    monkeypatch.setattr("core.launcher_support.website_build_present", lambda: True)
    monkeypatch.setattr(
        "core.launcher_support.database_writability",
        lambda: {
            "path": "data/test.sqlite3",
            "absolute_path": "/tmp/test.sqlite3",
            "writable": True,
            "error": "",
            "summary": "Database path is writable.",
            "journal_mode_fallback_active": True,
            "journal_mode_warning": "SQLite WAL mode is unavailable for the configured database filesystem; runtime will use DELETE journal mode.",
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"ownership": "none", "port": 8000, "status": "stopped"},
                "frontend": {"ownership": "none", "port": 3000, "status": "stopped"},
            },
            "last_doctor": None,
        },
    )
    monkeypatch.setattr("core.launcher_support._check_optional_capabilities", lambda: [])
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": [], "task_routing": {}, "route_issues": []})
    monkeypatch.setattr(config, "validate", lambda: True)
    # The doors are the owner's env, not this test's: with ENABLE_TELEGRAM_BOT on in .env the
    # doctor adds its door line and the warning count drifts (2026-09-17). Pin them off here.
    monkeypatch.setattr(config, "ENABLE_TELEGRAM_BOT", False, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)

    report = build_doctor_report()

    assert report["status"] == "warning"
    assert report["counts"]["core_blockers"] == 0
    assert report["counts"]["warnings"] == 1


def test_build_doctor_report_surfaces_prompt_readiness_warning(monkeypatch):
    monkeypatch.setattr("core.launcher_support.resolve_repo_python_path", lambda: {"path": "/tmp/venv/bin/python", "exists": True, "executable": True})
    monkeypatch.setattr("core.launcher_support.resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr("core.launcher_support.resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr("core.launcher_support.website_node_modules_present", lambda: True)
    monkeypatch.setattr("core.launcher_support.website_build_present", lambda: True)
    monkeypatch.setattr(
        "core.launcher_support.collect_prompt_loading_status",
        lambda: {
            "status": "warning",
            "summary": (
                "Critical prompt readiness needs attention: combined_decision is missing or unreadable. "
                "Some runtime features may not work correctly."
            ),
            "critical_prompts": ["combined_decision", "vision_analysis"],
            "critical_missing": ["combined_decision"],
            "loaded_count": 36,
            "total_count": 37,
            "missing": ["combined_decision"],
            "missing_count": 1,
            "empty": [],
            "empty_count": 0,
            "errors": {},
            "error_count": 0,
            "prompt_status": {
                "combined_decision": False,
                "vision_analysis": True,
            },
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.database_writability",
        lambda: {
            "path": "data/test.sqlite3",
            "absolute_path": "/tmp/test.sqlite3",
            "writable": True,
            "error": "",
            "summary": "Database path is writable.",
            "journal_mode_fallback_active": False,
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"ownership": "none", "port": 8000, "status": "stopped"},
                "frontend": {"ownership": "none", "port": 3000, "status": "stopped"},
            },
            "last_doctor": None,
        },
    )
    monkeypatch.setattr("core.launcher_support._check_optional_capabilities", lambda: [])
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": [], "task_routing": {}, "route_issues": []})
    monkeypatch.setattr(config, "validate", lambda: True)
    # The doors are the owner's env, not this test's: with ENABLE_TELEGRAM_BOT on in .env the
    # doctor adds its door line and the warning count drifts (2026-09-17). Pin them off here.
    monkeypatch.setattr(config, "ENABLE_TELEGRAM_BOT", False, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)

    report = build_doctor_report()

    prompt_check = next(item for item in report["checks"] if item["code"] == "prompt_readiness_warning")
    assert report["status"] == "warning"
    assert report["counts"]["warnings"] == 1
    assert prompt_check["severity"] == "warning"
    assert "combined_decision" in prompt_check["message"]
    assert report["prompts"]["critical_missing"] == ["combined_decision"]


def test_build_public_health_detail_exposes_prompt_readiness_warning(monkeypatch):
    monkeypatch.setattr(
        "core.launcher_support.build_doctor_report",
        lambda: {
            "generated_at": 200.0,
            "status": "warning",
            "summary": "Ready with 1 warning(s).",
            "counts": {"core_blockers": 0, "warnings": 1, "ok": 6},
            "checks": [
                {
                    "code": "prompt_readiness_warning",
                    "severity": "warning",
                    "message": (
                        "Critical prompt readiness needs attention: combined_decision is missing or unreadable. "
                        "Some runtime features may not work correctly."
                    ),
                }
            ],
            "launcher": {
                "services": {
                    "backend": {"status": "running", "host": "127.0.0.1", "port": 8000, "probe": {}},
                    "frontend": {"status": "stopped", "host": "127.0.0.1", "port": 3000, "probe": {}},
                },
                "last_doctor": {
                    "generated_at": 200.0,
                    "status": "warning",
                    "summary": "Ready with 1 warning(s).",
                    "counts": {"core_blockers": 0, "warnings": 1, "ok": 6},
                },
            },
            "prompts": {
                "status": "warning",
                "summary": (
                    "Critical prompt readiness needs attention: combined_decision is missing or unreadable. "
                    "Some runtime features may not work correctly."
                ),
                "critical_prompts": ["combined_decision", "vision_analysis"],
                "critical_missing": ["combined_decision"],
                "loaded_count": 36,
                "total_count": 37,
                "missing": ["combined_decision"],
                "missing_count": 1,
                "empty": [],
                "empty_count": 0,
                "errors": {},
                "error_count": 0,
                "prompt_status": {
                    "combined_decision": False,
                    "vision_analysis": True,
                },
            },
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"status": "running", "host": "127.0.0.1", "port": 8000, "probe": {}},
                "frontend": {"status": "stopped", "host": "127.0.0.1", "port": 3000, "probe": {}},
            },
            "last_doctor": {
                "generated_at": 100.0,
                "status": "ok",
                "summary": "Older stored summary.",
                "counts": {"core_blockers": 0, "warnings": 0, "ok": 6},
            },
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.backend_bootstrap.snapshot",
        lambda: {
            "status": "ready",
            "counts": {"ready": 1, "skipped": 0, "degraded": 0, "failed": 0},
            "stages": [],
        },
    )
    monkeypatch.setattr("core.launcher_support.backend_bootstrap.warning_items", lambda: [])
    monkeypatch.setattr(
        "core.launcher_support.database_writability",
        lambda: {
            "path": "data/test.sqlite3",
            "absolute_path": "/tmp/test.sqlite3",
            "writable": True,
            "error": "",
        },
    )
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": []})

    class _VoiceStatus:
        ok = True
        messages = []
        details = {}

    monkeypatch.setattr("voice.bootstrap.voice_bootstrap_status", lambda: _VoiceStatus())

    detail = build_public_health_detail()

    assert detail["prompts"]["status"] == "warning"
    assert detail["prompts"]["critical_missing"] == ["combined_decision"]
    assert any(item["code"] == "prompt_readiness_warning" for item in detail["warnings"])


def test_build_doctor_report_uses_python_prepare_hint_for_missing_frontend_artifacts(monkeypatch):
    monkeypatch.setattr("core.launcher_support.resolve_repo_python_path", lambda: {"path": "/tmp/venv/bin/python", "exists": True, "executable": True})
    monkeypatch.setattr("core.launcher_support.resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr("core.launcher_support.resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr("core.launcher_support.website_node_modules_present", lambda: False)
    monkeypatch.setattr("core.launcher_support.website_build_present", lambda: False)
    monkeypatch.setattr(
        "core.launcher_support.database_writability",
        lambda: {
            "path": "data/test.sqlite3",
            "absolute_path": "/tmp/test.sqlite3",
            "writable": True,
            "error": "",
            "summary": "Database path is writable.",
            "journal_mode_fallback_active": False,
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"ownership": "none", "port": 8000, "status": "stopped"},
                "frontend": {"ownership": "none", "port": 3000, "status": "stopped"},
            },
            "last_doctor": None,
        },
    )
    monkeypatch.setattr("core.launcher_support._check_optional_capabilities", lambda: [])
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": [], "task_routing": {}, "route_issues": []})
    monkeypatch.setattr(config, "validate", lambda: True)
    # The doors are the owner's env, not this test's: with ENABLE_TELEGRAM_BOT on in .env the
    # doctor adds its door line and the warning count drifts (2026-09-17). Pin them off here.
    monkeypatch.setattr(config, "ENABLE_TELEGRAM_BOT", False, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)

    report = build_doctor_report()

    messages = {
        item["code"]: item["message"]
        for item in report["checks"]
        if item["code"] in {"frontend_deps_missing", "frontend_build_missing"}
    }
    # Week 1 task 5: launcher_command_hint() asks the PATH now, so the
    # expectation asks the same question rather than pinning one of two forms.
    assert messages["frontend_deps_missing"] == f"Website dependencies are missing; run `{ctl_prefix()} prepare`."
    assert messages["frontend_build_missing"] == f"Website production build is missing; run `{ctl_prefix()} prepare`."


def test_build_doctor_report_warns_when_codex_uses_api_key_auth(monkeypatch):
    monkeypatch.setattr("core.launcher_support.resolve_repo_python_path", lambda: {"path": "/tmp/venv/bin/python", "exists": True, "executable": True})
    monkeypatch.setattr("core.launcher_support.resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr("core.launcher_support.resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr("core.launcher_support.website_node_modules_present", lambda: True)
    monkeypatch.setattr("core.launcher_support.website_build_present", lambda: True)
    monkeypatch.setattr(
        "core.launcher_support.database_writability",
        lambda: {
            "path": "data/test.sqlite3",
            "absolute_path": "/tmp/test.sqlite3",
            "writable": True,
            "error": "",
            "summary": "Database path is writable.",
            "journal_mode_fallback_active": False,
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"ownership": "none", "port": 8000, "status": "stopped"},
                "frontend": {"ownership": "none", "port": 3000, "status": "stopped"},
            },
            "last_doctor": None,
        },
    )
    monkeypatch.setattr("core.launcher_support._check_optional_capabilities", lambda: [])
    monkeypatch.setattr("core.launcher_support.plugin_runtime.doctor_checks", lambda: [])
    monkeypatch.setattr(config, "validate", lambda: True)
    monkeypatch.setattr(
        "ai.llm_client.llm.get_runtime_status",
        lambda: {
            "default_provider": "codex",
            "providers": [
                {
                    "provider": "codex",
                    "configured": True,
                    "auth_mode": "apikey",
                    "login_status": "authenticated",
                    "login_status_text": "Logged in using API key",
                    "codex_executable": "C:\\Users\\Sam\\AppData\\Roaming\\npm\\codex.cmd",
                    "profile": "",
                }
            ],
            "task_routing": {},
            "route_issues": [],
        },
    )

    report = build_doctor_report()

    assert report["llm"]["default_provider"] == "codex"
    assert any(item["code"] == "codex_auth_api_key" for item in report["checks"])


def _quiet_health_detail_environment(monkeypatch) -> None:
    """The stubs build_public_health_detail needs to run at all, so a test can
    be about one field of it instead of about the whole host."""
    monkeypatch.setattr(
        "core.launcher_support.build_doctor_report",
        lambda: {
            "status": "ok",
            "summary": "Ready.",
            "counts": {"core_blockers": 0, "warnings": 0, "ok": 5},
            "checks": [],
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"status": "running", "host": "127.0.0.1", "port": 8000, "probe": {}},
                "frontend": {"status": "stopped", "host": "127.0.0.1", "port": 3000, "probe": {}},
            },
            "last_doctor": None,
        },
    )
    monkeypatch.setattr(
        "core.launcher_support.backend_bootstrap.snapshot",
        lambda: {"status": "ready", "counts": {"ready": 4, "skipped": 0, "degraded": 0, "failed": 0}, "stages": []},
    )
    monkeypatch.setattr("core.launcher_support.backend_bootstrap.warning_items", lambda: [])
    monkeypatch.setattr(
        "core.launcher_support.database_writability",
        lambda: {"path": "data/test.sqlite3", "absolute_path": "/tmp/test.sqlite3", "writable": True, "error": ""},
    )
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": []})

    class _VoiceStatus:
        ok = True
        messages: list[str] = []
        details: dict[str, str] = {}

    monkeypatch.setattr("voice.bootstrap.voice_bootstrap_status", lambda: _VoiceStatus())


def test_health_detail_discord_ready_follows_boots_verdict(monkeypatch):
    """Week 2 task 7: a blank token turns Discord off for the boot instead of
    stopping it, so `ready` is the verdict validate() recorded — not a fourth
    place that re-derives "enabled AND holding a token". `enabled` and
    `token_configured` still report the raw settings; only `ready` is the
    answer to "is she on Discord right now"."""
    _quiet_health_detail_environment(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_DISCORD_BOT", True, raising=False)
    monkeypatch.setattr(config, "DISCORD_TOKEN", "x" * 40, raising=False)

    monkeypatch.setattr(config, "effective_discord_enabled", False, raising=False)
    detail = build_public_health_detail()
    assert detail["discord"]["ready"] is False
    # The raw facts are unchanged — a reader can still see WHY.
    assert detail["discord"]["enabled"] is True
    assert detail["discord"]["token_configured"] is True

    monkeypatch.setattr(config, "effective_discord_enabled", True, raising=False)
    assert build_public_health_detail()["discord"]["ready"] is True


def test_health_detail_is_never_ready_on_a_blank_token(monkeypatch):
    """Belt and braces, the same as the channel map: Config declares the flag
    True at class level, so a process that reports health before validate()
    has run must not claim she is on Discord with an empty token."""
    _quiet_health_detail_environment(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_DISCORD_BOT", True, raising=False)
    monkeypatch.setattr(config, "DISCORD_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "effective_discord_enabled", True, raising=False)

    detail = build_public_health_detail()

    assert detail["discord"]["ready"] is False
    assert detail["discord"]["token_configured"] is False


def test_health_detail_lists_every_door_the_channel_runtime_holds_open(monkeypatch):
    """Telegram door (2026-09-14, task 6): `/api/health/detail` answers "which
    doors are open right now" from the channel runtime's own snapshot, not from
    a list of platform names written into the builder. So the second door shows
    up there because it registers an adapter — and if that ever stopped being
    true, this readout would quietly be about Discord forever.

    The registry is process-wide, so the two rows are put back exactly as they
    were found: `unregister_adapter` leaves a `disconnected` row behind by
    design (a door that shut is news), which would otherwise leak into another
    module's snapshot."""
    import asyncio

    from core.channel_runtime import channel_runtime

    _quiet_health_detail_environment(monkeypatch)

    class _FakeAdapter:
        async def send_envelope(self, *, surface_id, envelope):  # pragma: no cover
            return {}

    saved_adapters = dict(channel_runtime._adapters)
    saved_health = dict(channel_runtime._health)

    async def _with_both_doors_open():
        await channel_runtime.register_adapter("discord", _FakeAdapter(),
                                               metadata={"label": "Discord"})
        await channel_runtime.register_adapter("telegram", _FakeAdapter(),
                                               metadata={"label": "Telegram"})
        try:
            return build_public_health_detail()
        finally:
            await channel_runtime.unregister_adapter("discord", reason="test")
            await channel_runtime.unregister_adapter("telegram", reason="test")

    try:
        detail = asyncio.run(_with_both_doors_open())
    finally:
        channel_runtime._adapters.clear()
        channel_runtime._adapters.update(saved_adapters)
        channel_runtime._health.clear()
        channel_runtime._health.update(saved_health)

    runtime = detail["channels"]["runtime"]
    assert set(runtime["connected_platforms"]) >= {"discord", "telegram"}
    assert {str(item.get("platform")) for item in runtime["platforms"]} >= {"discord", "telegram"}
    # And the configured-vs-ready half of the same block names both doors, so a
    # reader can tell "open right now" from "turned on in the settings".
    assert {"discord", "telegram"} <= set(detail["channels"])


def _quiet_doctor_environment(monkeypatch) -> None:
    """The stubs build_doctor_report needs to run at all, so a test can be
    about one check of it instead of about this machine's node, ports and
    database. The same set the module's first test spells out inline."""
    monkeypatch.setattr("core.launcher_support.resolve_repo_python_path",
                        lambda: {"path": "/tmp/venv/bin/python", "exists": True, "executable": True})
    monkeypatch.setattr("core.launcher_support.resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr("core.launcher_support.resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr("core.launcher_support.website_node_modules_present", lambda: True)
    monkeypatch.setattr("core.launcher_support.website_build_present", lambda: True)
    monkeypatch.setattr(
        "core.launcher_support.database_writability",
        lambda: {"path": "data/test.sqlite3", "absolute_path": "/tmp/test.sqlite3",
                 "writable": True, "error": ""},
    )
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": {"ownership": "launcher", "port": 8000, "status": "running"},
                "frontend": {"ownership": "none", "port": 3000, "status": "stopped"},
            },
            "last_doctor": None,
        },
    )
    monkeypatch.setattr("core.launcher_support._check_optional_capabilities", lambda: [])
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status",
                        lambda: {"default_provider": "openai", "providers": [],
                                 "task_routing": {}, "route_issues": []})
    monkeypatch.setattr(config, "validate", lambda: True)


def _telegram_check(report) -> dict | None:
    return next((item for item in report["checks"]
                 if item["code"] == "telegram_token_missing"), None)


def test_the_doctor_names_a_telegram_door_boot_closed_on_a_missing_library(monkeypatch):
    """Task 6 review round 1, important 3: the doctor read the raw token while
    its Discord twin read BOOT'S VERDICT, so the one failure Task 2 invented
    the library check for — a token set on a tree where python-telegram-bot
    was never installed — was named by the preflight and by
    /api/health/detail and by nothing a person actually runs. `doctor` is
    what a stranger runs."""
    _quiet_doctor_environment(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_TELEGRAM_BOT", True, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABC-fictional-token", raising=False)
    monkeypatch.setattr(config, "effective_telegram_enabled", False, raising=False)

    check = _telegram_check(build_doctor_report())

    assert check is not None, "the doctor said nothing about a door boot closed"
    assert check["severity"] == "warning"
    assert "closed for this boot" in check["message"]
    assert "python-telegram-bot" in check["message"]


def test_the_doctor_still_names_a_blank_telegram_token(monkeypatch):
    """The other half, unchanged in words: a blank token with the flag on is
    the same warning it always was."""
    _quiet_doctor_environment(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_TELEGRAM_BOT", True, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "effective_telegram_enabled", False, raising=False)

    check = _telegram_check(build_doctor_report())

    assert check is not None
    assert "TELEGRAM_BOT_TOKEN" in check["message"]


def test_the_doctor_is_quiet_about_a_telegram_door_that_opened(monkeypatch):
    """Non-vacuous: boot said the door is open, so there is nothing to say.
    Without this the check could fire on every boot and still pass the two
    above."""
    _quiet_doctor_environment(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_TELEGRAM_BOT", True, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:ABC-fictional-token", raising=False)
    monkeypatch.setattr(config, "effective_telegram_enabled", True, raising=False)

    assert _telegram_check(build_doctor_report()) is None


# --- Batch 8 Task 11 A3: her own :8000 is "self", never "external" ----------
#
# Started from tmux, she has no launcher record, so an open :8000 used to read
# "occupied by a foreign process" — a core blocker in her own doctor and in
# /api/health/detail. The listener is hers when it is THIS process, or a
# process whose cwd is this repo root and whose argv[1] is main.py. A process
# from another checkout stays "external". Every /proc read below is a fake
# tree under tmp_path — never her real running process.

import os

import pytest

import core.launcher_support as launcher_support_module
from core.launcher_support import her_listener_pid, inspect_launcher_service

_PORT = 8000
_INODE = "424242"
_REAL_LAUNCHER_SNAPSHOT = launcher_support_module.launcher_snapshot


def _fake_proc(tmp_path: Path, *, port: int = _PORT, inode: str = _INODE, family: str = "tcp") -> Path:
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    listen = (
        f"   0: 00000000:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 {inode} 1 0000000000000000 100 0 0 10 0\n"
    )
    # An ESTABLISHED socket on the same port (a client talking to her) is not
    # the listener and must never be taken for it.
    established = (
        f"   1: 0100007F:{port:04X} 0100007F:D431 01 00000000:00000000 00:00000000 00000000  1000        0 999999 1 0000000000000000 20 4 30 10 -1\n"
    )
    (proc / "net" / family).write_text(header + listen + established)
    return proc


def _fake_pid(proc: Path, pid: int, *, cwd: Path, argv: list[str], sockets: tuple[str, ...] = ()) -> None:
    base = proc / str(pid)
    (base / "fd").mkdir(parents=True)
    os.symlink(str(cwd), base / "cwd")
    (base / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    os.symlink("/dev/null", base / "fd" / "0")
    for n, ino in enumerate(sockets, start=3):
        os.symlink(f"socket:[{ino}]", base / "fd" / str(n))


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "main.py").write_text("")
    return root


def test_her_listener_is_a_main_py_started_in_this_repo_root(tmp_path):
    proc = _fake_proc(tmp_path)
    root = _repo(tmp_path)
    _fake_pid(proc, 4242, cwd=root, argv=[str(root / "venv/bin/python"), "main.py"], sockets=(_INODE,))

    assert her_listener_pid(_PORT, proc_root=proc, project_root=root, self_pid=1) == 4242


def test_this_process_holding_the_port_is_her(tmp_path):
    """/api/health/detail runs the doctor inside her own process."""
    proc = _fake_proc(tmp_path, family="tcp6")
    root = _repo(tmp_path)
    other = _repo(tmp_path, "elsewhere")
    _fake_pid(proc, 7777, cwd=other, argv=["python", "-m", "uvicorn"], sockets=(_INODE,))

    assert her_listener_pid(_PORT, proc_root=proc, project_root=root, self_pid=7777) == 7777


def test_a_main_py_from_another_checkout_stays_external(tmp_path):
    proc = _fake_proc(tmp_path)
    root = _repo(tmp_path)
    other = _repo(tmp_path, "another-checkout")
    _fake_pid(proc, 5151, cwd=other, argv=["python", "main.py"], sockets=(_INODE,))

    assert her_listener_pid(_PORT, proc_root=proc, project_root=root, self_pid=1) is None


def test_her_main_py_not_holding_the_port_does_not_claim_it(tmp_path):
    """She may be running while something else holds :8000 — that is still a
    foreign process, and the doctor must keep saying so."""
    proc = _fake_proc(tmp_path)
    root = _repo(tmp_path)
    _fake_pid(proc, 4242, cwd=root, argv=["python", "main.py"], sockets=("111",))
    _fake_pid(proc, 6000, cwd=tmp_path, argv=["nginx"], sockets=(_INODE,))

    assert her_listener_pid(_PORT, proc_root=proc, project_root=root, self_pid=1) is None


def test_a_script_other_than_main_py_in_her_root_is_not_her(tmp_path):
    proc = _fake_proc(tmp_path)
    root = _repo(tmp_path)
    _fake_pid(proc, 4300, cwd=root, argv=["python", "scripts/palli_ctl.py", "start"], sockets=(_INODE,))

    assert her_listener_pid(_PORT, proc_root=proc, project_root=root, self_pid=1) is None


def test_no_proc_table_means_no_verdict(tmp_path):
    """Not Linux (or /proc unreadable): the old answer, "external", stands."""
    assert her_listener_pid(_PORT, proc_root=tmp_path / "no-proc", project_root=tmp_path, self_pid=1) is None


def _probe_open(name: str, *, http_ok: bool = True) -> dict:
    return {
        "name": name, "host": "127.0.0.1", "port": _PORT if name == "backend" else 3000,
        "url": "http://127.0.0.1:8000", "health_url": "", "log_path": "logs/launcher/backend.log",
        "reachable": True, "status_code": 200, "http_ok": http_ok, "payload": {},
        "error": "", "port_open": True,
    }


@pytest.mark.parametrize("http_ok, status", [(True, "running"), (False, "degraded")])
def test_inspect_calls_her_own_listener_self(monkeypatch, http_ok, status):
    monkeypatch.setattr(launcher_support_module, "_probe_service", lambda name: _probe_open(name, http_ok=http_ok))
    monkeypatch.setattr(launcher_support_module, "her_listener_pid", lambda port, **kw: 4242)

    service = inspect_launcher_service("backend", state={"services": {}})

    assert service["ownership"] == "self"
    assert service["status"] == status
    assert service["pid"] == 4242
    assert service["managed"] is False


def test_inspect_keeps_a_foreign_listener_external(monkeypatch):
    monkeypatch.setattr(launcher_support_module, "_probe_service", lambda name: _probe_open(name))
    monkeypatch.setattr(launcher_support_module, "her_listener_pid", lambda port, **kw: None)

    service = inspect_launcher_service("backend", state={"services": {}})

    assert service["ownership"] == "external"
    assert service["status"] == "degraded"


def _doctor_with_backend(monkeypatch, backend: dict) -> None:
    monkeypatch.setattr("core.launcher_support.resolve_repo_python_path", lambda: {"path": "/tmp/venv/bin/python", "exists": True, "executable": True})
    monkeypatch.setattr("core.launcher_support.resolve_node_binary", lambda: "/usr/bin/node")
    monkeypatch.setattr("core.launcher_support.resolve_npm_binary", lambda: "/usr/bin/npm")
    monkeypatch.setattr("core.launcher_support.website_node_modules_present", lambda: True)
    monkeypatch.setattr("core.launcher_support.website_build_present", lambda: True)
    monkeypatch.setattr("core.launcher_support.database_writability", lambda: {"path": "data/test.sqlite3", "absolute_path": "/tmp/test.sqlite3", "writable": True, "error": ""})
    monkeypatch.setattr(
        "core.launcher_support.launcher_snapshot",
        lambda: {
            "services": {
                "backend": backend,
                "frontend": {"ownership": "none", "port": 3000, "status": "stopped"},
            },
            "last_doctor": None,
        },
    )
    monkeypatch.setattr("core.launcher_support._check_optional_capabilities", lambda: [])
    monkeypatch.setattr("ai.llm_client.llm.get_runtime_status", lambda: {"default_provider": "openai", "providers": [], "task_routing": {}, "route_issues": []})
    monkeypatch.setattr(config, "validate", lambda: True)
    monkeypatch.setattr(config, "ENABLE_TELEGRAM_BOT", False, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)


def test_the_doctor_says_she_is_running_when_the_port_is_hers(monkeypatch):
    _doctor_with_backend(
        monkeypatch,
        {"ownership": "self", "pid": 4242, "port": _PORT, "status": "running"},
    )

    report = build_doctor_report()

    codes = {item["code"]: item for item in report["checks"]}
    assert "backend_port_external" not in codes
    assert report["counts"]["core_blockers"] == 0
    # Never "available for launcher control" — the port is taken, by her.
    assert "backend_port_clear" not in codes
    ok = codes["backend_running_outside_launcher"]
    assert ok["severity"] == "ok"
    assert "she is running (pid 4242, started outside the launcher)" in ok["message"].lower()


def test_health_detail_stops_calling_her_foreign(monkeypatch):
    """End to end through the real inspect_launcher_service: only the port
    probe, the /proc reader and the launcher state file are stood in for."""
    _doctor_with_backend(monkeypatch, {})
    monkeypatch.setattr("core.launcher_support.launcher_snapshot", _REAL_LAUNCHER_SNAPSHOT)
    monkeypatch.setattr(launcher_support_module, "load_launcher_state", lambda: {"services": {}})

    def _probe(name):
        probe = _probe_open(name)
        if name != "backend":
            probe.update(port_open=False, reachable=False, http_ok=False, status_code=None)
        return probe

    monkeypatch.setattr(launcher_support_module, "_probe_service", _probe)
    monkeypatch.setattr(launcher_support_module, "her_listener_pid", lambda port, **kw: 4242)
    monkeypatch.setattr("core.launcher_support.backend_bootstrap.snapshot", lambda: {"status": "ready", "counts": {}, "stages": []})
    monkeypatch.setattr("core.launcher_support.backend_bootstrap.warning_items", lambda: [])

    class _VoiceStatus:
        ok = True
        messages: list = []
        details: dict = {}

    monkeypatch.setattr("voice.bootstrap.voice_bootstrap_status", lambda: _VoiceStatus())

    detail = build_public_health_detail()

    assert not any("foreign" in item["message"] for item in detail["warnings"]), detail["warnings"]
    assert not any(item["code"] == "backend_port_external" for item in detail["warnings"])
    assert detail["doctor"]["counts"]["core_blockers"] == 0


# --- second read F7 (2026-10-04): the detail runs in a worker thread -------
#
# /api/health/detail now builds the doctor's report through asyncio.to_thread
# (core/main.py). The doctor mutes logging around config.validate() so a poll
# never sprays the validators' warnings into her log; logging.disable() is
# process-wide, so off the main thread it would drop HER loop's lines for that
# window, and two overlapping polls could put back each other's level and
# leave logging off. Off the main thread the mute is this thread's records
# only; on the main thread (palli_ctl doctor) it is the old global mute.

import logging as _logging
import threading as _threading


class _Collect(_logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=_logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: _logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_the_doctors_mute_off_the_main_thread_drops_only_its_own_lines(monkeypatch):
    collect = _Collect()
    root = _logging.getLogger()
    root.addHandler(collect)
    old_level = root.level
    root.setLevel(_logging.DEBUG)
    log = _logging.getLogger("tests.f7")
    seen: dict[str, object] = {}

    def validate() -> bool:
        log.warning("from the doctor's own thread")
        other = _threading.Thread(target=lambda: log.warning("from her loop meanwhile"))
        other.start()
        other.join()
        seen["disable"] = _logging.root.manager.disable
        return True

    monkeypatch.setattr(launcher_support_module.config, "validate", validate, raising=False)
    before = _logging.root.manager.disable
    try:
        worker = _threading.Thread(target=launcher_support_module.build_doctor_report)
        worker.start()
        worker.join()
        log.warning("after the doctor")
    finally:
        root.removeHandler(collect)
        root.setLevel(old_level)

    assert "from her loop meanwhile" in collect.messages
    assert "from the doctor's own thread" not in collect.messages
    assert "after the doctor" in collect.messages
    assert seen["disable"] == before and _logging.root.manager.disable == before
    assert not any(isinstance(f, launcher_support_module._ThreadMute)
                   for h in root.handlers for f in h.filters)

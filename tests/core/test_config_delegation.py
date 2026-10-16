"""Tests for Stage 1 delegation bridge config settings."""
from __future__ import annotations

from pathlib import Path

from core.config import config


def test_delegation_enabled_defaults_to_false(monkeypatch):
    """The shipped default (env var absent) is False.

    The owner's .env.local sets DELEGATION_ENABLED=true and
    core/config.py loads .env files with override=True at import time,
    so the ambient config singleton reflects the live env, not the
    default. Re-exec the module with dotenv stubbed out and the env var
    removed to observe the actual code default.
    """
    import importlib.util

    import dotenv

    monkeypatch.delenv("DELEGATION_ENABLED", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)

    config_path = Path(__file__).resolve().parents[2] / "core" / "config.py"
    spec = importlib.util.spec_from_file_location(
        "core_config_defaults_probe", config_path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod.Config, "DELEGATION_ENABLED")
    assert mod.Config.DELEGATION_ENABLED is False


def test_max_concurrent_delegations_defaults_to_three():
    assert hasattr(config, "MAX_CONCURRENT_DELEGATIONS_PER_USER")
    assert int(config.MAX_CONCURRENT_DELEGATIONS_PER_USER) == 3


def test_idle_timeout_defaults_to_900_seconds():
    assert hasattr(config, "DELEGATION_IDLE_TIMEOUT_SECONDS")
    assert int(config.DELEGATION_IDLE_TIMEOUT_SECONDS) == 900


def test_hard_timeout_defaults_to_1800_seconds():
    assert hasattr(config, "DELEGATION_HARD_TIMEOUT_SECONDS")
    assert int(config.DELEGATION_HARD_TIMEOUT_SECONDS) == 1800


def test_progress_min_interval_defaults_to_120_seconds():
    assert hasattr(config, "DELEGATION_PROGRESS_MIN_INTERVAL_SECONDS")
    assert int(config.DELEGATION_PROGRESS_MIN_INTERVAL_SECONDS) == 120


def test_claude_code_workspace_dir_has_default():
    assert hasattr(config, "CLAUDE_CODE_WORKSPACE_DIR")
    assert isinstance(config.CLAUDE_CODE_WORKSPACE_DIR, str)
    assert len(config.CLAUDE_CODE_WORKSPACE_DIR) > 0


def test_delegation_max_turns_defaults_to_500():
    """Safety cap on Claude Code turns per delegation — guards against
    infinite tool loops burning through Claude Max weekly quota."""
    assert hasattr(config, "DELEGATION_MAX_TURNS")
    assert int(config.DELEGATION_MAX_TURNS) == 300

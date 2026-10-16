"""Shared fixtures for palli_config tests.

Provides isolated tmp_path-based copies of .env.local and data/palli.yaml
so tests don't touch the real files. Each test that needs them takes the
fixture and operates on the temp copy.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_palli_yaml(tmp_path: Path) -> Path:
    """Copy data/palli.yaml into tmp_path and return the copy's path."""
    src = REPO_ROOT / "data" / "palli.yaml"
    dst = tmp_path / "palli.yaml"
    shutil.copyfile(src, dst)
    return dst


@pytest.fixture
def isolated_env_local(tmp_path: Path) -> Path:
    """Create a tmp .env.local with representative content."""
    dst = tmp_path / ".env.local"
    dst.write_text(
        "LLM_PROVIDER=deepseek\n"
        "LLM_RESPONSE_PROVIDER=deepseek\n"
        "LLM_RESPONSE_MODEL=deepseek-v4-pro\n"
        "OPENAI_API_KEY=sk-test-key\n"
        "ANTHROPIC_API_KEY=sk-ant-test-key\n",
        encoding="utf-8",
    )
    return dst

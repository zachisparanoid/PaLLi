"""Fixtures shared by all agent tests."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_module(relative_path: str, name: str | None = None):
    """Load a module from a path under the repo root via importlib.

    Avoids triggering core.config side effects when testing isolated
    components.
    """
    module_path = REPO_ROOT / relative_path
    module_name = name or module_path.stem
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def agent_session_root(tmp_path: Path) -> Path:
    """A throwaway sessions directory for tests that need on-disk transcripts."""
    root = tmp_path / "agent_sessions"
    root.mkdir()
    return root

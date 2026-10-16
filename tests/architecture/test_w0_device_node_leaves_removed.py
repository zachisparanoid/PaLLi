"""
Wave 0: assert the leaf-level device-node API surface is gone.

The full device-coordination stack (core/device_node_*, core/cross_device_*,
core/home_presence) is heavily entangled with core/database.py (121KB)
and core/runtime_authority.py (247KB), both being decomposed in Wave 5.
So Wave 0 only deletes the isolated leaves that have no production callers
beyond the plugin_runtime registration.
"""
from __future__ import annotations

import os

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_api_nodes_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        import api.nodes  # noqa: F401


def test_palli_node_client_script_is_gone() -> None:
    path = os.path.join(REPO_ROOT, "scripts", "palli_node_client.py")
    assert not os.path.exists(path), f"{path} should be deleted"


def test_plugin_runtime_does_not_register_nodes_router() -> None:
    """The builtin.api.nodes plugin block is removed from plugin_runtime."""
    path = os.path.join(REPO_ROOT, "core", "plugin_runtime.py")
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "builtin.api.nodes" not in text
    assert "from api import nodes" not in text

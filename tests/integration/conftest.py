"""Pytest config for integration tests.

Forces a single asyncio loop per module so the LLM client's persistent
aiohttp session survives between tests in a file. Without this, each
asyncio test function gets a fresh loop and the cross-test session is
bound to a closed loop on the second test.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(scope="module")
def event_loop():
    """Single loop per module — overrides pytest-asyncio's per-function default."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def pytest_collection_modifyitems(config, items):
    """Auto-mark all integration tests with asyncio mode."""
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.asyncio(loop_scope="module"))

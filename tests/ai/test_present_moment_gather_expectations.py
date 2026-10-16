# tests/ai/test_present_moment_gather_expectations.py
"""The expectations sense (spec §2): flag-conditional exactly like the wants
board — a surprise-off cycle carries neither the block nor its health key."""
import asyncio
from unittest.mock import patch

import ai.present_moment_gather as pmg


def _gather():
    return asyncio.run(pmg.gather_moment_inputs(owner_user_id=1))


def test_on_carries_block_and_health_key():
    with patch("ai.expectations.surprise_enabled", return_value=True), \
         patch("ai.expectations.render_expectations_block",
               return_value="EXPECTATIONS — x:\n- him: y\n\n"):
        out = _gather()
    assert out.expectations_block.startswith("EXPECTATIONS")
    assert out.expectations_block.endswith("\n\n")   # the zero-byte-collapse contract
    assert out.sense_health.get("expectations") == "ok"


def test_off_carries_neither():
    with patch("ai.expectations.surprise_enabled", return_value=False):
        out = _gather()
    assert out.expectations_block == ""
    assert "expectations" not in out.sense_health

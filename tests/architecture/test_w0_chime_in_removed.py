"""
Wave 0: assert Phase 5 chime-in scorer is gone.

The scorer was a 6-signal heuristic gate intended to filter ambient
group-channel messages out of the LLM decision step. Per the new design,
"cost-blind LLM-mediated judgment" replaces this — the chime-in question
becomes one field of the consolidated turn_decide prompt instead of a
separate heuristic engine.
"""
from __future__ import annotations

import os

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_chime_in_scorer_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        import ai.chime_in_scorer  # noqa: F401


def test_chime_in_scorer_file_is_gone() -> None:
    path = os.path.join(REPO_ROOT, "ai", "chime_in_scorer.py")
    assert not os.path.exists(path), f"{path} should be deleted"


def test_message_should_respond_module_is_gone() -> None:
    """C7 (Phase C, 2026-05-13) deleted the entire message_processor
    cluster including core/message_should_respond.py — the file that
    once invoked the chime-in scorer is gone, so the gate it housed
    cannot regress.
    """
    path = os.path.join(REPO_ROOT, "core", "message_should_respond.py")
    assert not os.path.exists(path), f"{path} should be deleted"


def test_settings_schema_has_no_chime_in_block() -> None:
    """The chime_in Pydantic block on PalliSettings is removed."""
    from core.settings_schema import PalliSettings
    assert "chime_in" not in PalliSettings.model_fields, (
        "PalliSettings still has a chime_in field; should be removed in Wave 0"
    )

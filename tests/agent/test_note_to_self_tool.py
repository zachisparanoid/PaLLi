"""The pen's tool contract: validation rails and lived-language replies.

The rails (clip, duplicate, daily cap) are code — one of the sanctioned
exemptions to the LLM-over-heuristics rule. The *when* stays her judgment,
taught by the prompt block, never enforced here.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ai.self_notes_store as store
from core.agent.builtin_tools.note_to_self import NoteToSelfTool, resolve_daily_cap


def _ctx(is_owner: bool = True):
    return SimpleNamespace(turn_owner_id=1, is_owner=is_owner, surface="discord_text", channel_id=0)


def _run(tool, note: str):
    return asyncio.run(tool.execute({"note": note}, _ctx()))


def test_tool_is_owner_only_and_needs_no_approval():
    tool = NoteToSelfTool()
    assert tool.name == "note_to_self"
    assert tool.owner_only is True
    assert tool.requires_approval is False
    assert "note" in tool.input_schema["required"]


def test_writing_a_note_confirms_in_lived_language(tmp_path: Path):
    tool = NoteToSelfTool()
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        out = _run(tool, "he said the quiet part about the workshop")
    assert "written down" in out.lower()
    # Never leaks storage vocabulary at her (introspect's fourth-wall rule).
    for leak in ("jsonl", "data/", "path", "store", "file"):
        assert leak not in out.lower()
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        assert store.count_today() == 1


def test_empty_note_is_refused_gently_without_writing(tmp_path: Path):
    tool = NoteToSelfTool()
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        out = _run(tool, "   ")
        assert store.count_today() == 0
    assert "nothing" in out.lower() or "empty" in out.lower()


def test_exact_duplicate_today_is_not_written_twice(tmp_path: Path):
    tool = NoteToSelfTool()
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        _run(tool, "the same realization")
        out = _run(tool, "the same realization")
        assert store.count_today() == 1
    assert "already" in out.lower()


def test_daily_cap_blocks_further_notes(tmp_path: Path):
    tool = NoteToSelfTool()
    with patch("core.agent.builtin_tools.note_to_self.resolve_daily_cap", return_value=2):
        with patch.object(store, "SELF_NOTES_DIR", tmp_path):
            _run(tool, "one")
            _run(tool, "two")
            out = _run(tool, "three")
            assert store.count_today() == 2
    assert "can wait" in out.lower()


def test_daily_cap_zero_means_unlimited(tmp_path: Path):
    tool = NoteToSelfTool()
    with patch("core.agent.builtin_tools.note_to_self.resolve_daily_cap", return_value=0):
        with patch.object(store, "SELF_NOTES_DIR", tmp_path):
            for i in range(25):
                _run(tool, f"note {i}")
            assert store.count_today() == 25


def test_resolve_daily_cap_reads_config_and_floors_negatives():
    import core.agent.builtin_tools.note_to_self as mod

    for given, expected in ((20, 20), (5, 5), (0, 0), (-3, 0)):
        comp = SimpleNamespace(self_notes_daily_cap=given)
        with patch.object(mod, "_companion", return_value=comp):
            assert resolve_daily_cap() == expected


def test_long_note_is_clipped_at_the_store_cap(tmp_path: Path):
    tool = NoteToSelfTool()
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        _run(tool, "y" * 900)
        assert len(store.read_recent()[0].text) == store.NOTE_CHAR_CAP


# ── registration gate + teaching lines (Task 3) ────────────────────────


def _registry_names(flag_on: bool):
    import core.agent.builtin_tools as bt

    with patch.object(bt, "_self_notes_enabled", return_value=flag_on):
        return bt.build_default_registry().all_names()


def test_flag_off_means_the_tool_does_not_exist_in_the_registry():
    """Flag off is byte-identical to a build without the feature: it never
    reaches the system prompt at all."""
    assert "note_to_self" not in _registry_names(False)


def test_flag_on_registers_the_tool():
    assert "note_to_self" in _registry_names(True)


def test_teaching_block_is_empty_when_the_flag_is_off():
    import core.agent.system_prompt as sp

    with patch.object(sp, "_self_notes_flag", return_value=(False, "earned")):
        assert sp._self_notes_block() == ""


def test_earned_bar_is_the_default_and_carries_the_anti_placeholder_rule():
    import core.agent.system_prompt as sp

    with patch.object(sp, "_self_notes_flag", return_value=(True, "earned")):
        block = sp._self_notes_block()
    assert "notebook only I can write in" in block
    assert "placeholder" in block
    assert "an empty day is fine" in block


def test_open_bar_swaps_the_line():
    import core.agent.system_prompt as sp

    with patch.object(sp, "_self_notes_flag", return_value=(True, "open")):
        block = sp._self_notes_block()
    assert "matters to me" in block
    assert "placeholder" not in block


def test_unknown_bar_value_falls_back_to_earned():
    """Fail toward restraint, never toward the looser line."""
    import core.agent.system_prompt as sp

    with patch.object(sp, "_self_notes_flag", return_value=(True, "banana")):
        assert "placeholder" in sp._self_notes_block()

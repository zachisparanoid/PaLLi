"""recall reads her deeds ring for the reply prompt (grounding rule,
2026-08-17): newest-window rows, deterministic phrasing, best-effort, and
the value flows through TurnContext into the agent-loop context."""
from __future__ import annotations

import time

import core.turn.recall as recall


def test_field_exists_default_empty():
    from core.turn.types import TurnContext

    assert TurnContext.__dataclass_fields__["recent_deeds_text"].default == ""


def test_adapter_carries_the_band():
    from core.agent.context_adapter import AgentLoopContext

    assert AgentLoopContext.__dataclass_fields__["recent_deeds_text"].default == ""


def test_band_renders_rows_with_ages(monkeypatch, tmp_path):
    import ai.recent_deeds as rd

    monkeypatch.setattr(rd, "DEEDS_PATH", tmp_path / "deeds.jsonl")
    rd.record_deed("note_to_self", "wrote a note to yourself")
    rd.record_deed("hands", "joined voice channel General")
    out = recall._read_recent_deeds_band()
    assert "joined voice channel General" in out
    assert out.splitlines()[0].startswith("- ~0m ago —")


def test_band_empty_when_ring_is_empty(monkeypatch, tmp_path):
    import ai.recent_deeds as rd

    monkeypatch.setattr(rd, "DEEDS_PATH", tmp_path / "absent.jsonl")
    assert recall._read_recent_deeds_band() == ""


def test_band_empty_when_reader_raises(monkeypatch):
    import ai.recent_deeds as rd

    def boom(**kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(rd, "read_recent_deeds", boom)
    assert recall._read_recent_deeds_band() == ""


def test_band_windows_out_old_deeds(monkeypatch, tmp_path):
    import json

    import ai.recent_deeds as rd

    path = tmp_path / "deeds.jsonl"
    old = {"at": time.time() - 3 * 86400, "kind": "hands", "detail": "ancient act"}
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")
    monkeypatch.setattr(rd, "DEEDS_PATH", path)
    assert "ancient act" not in recall._read_recent_deeds_band()

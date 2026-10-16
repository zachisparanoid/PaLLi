"""The monologue's aim (item 6, 2026-08-19 — owner's answer 2C: "both, hers
first"). Her private ticks (~24/day) were pointed entirely at him —
activity timeline, his mood, what he might need, "most ticks should be
substantive; there is almost always something worth noticing" — the
machine behind "she's just finding things to say." Now: her own life
first (what moved in her work, reading, thinking; what she's carrying),
him held; quiet ticks are true answers; the dead desktop-timeline lines
are gone; candidates may be hers too. Both files carry it; the two-file
variant law (exactly one differing line) stands."""
from __future__ import annotations

from pathlib import Path

import pytest

FILES = ("prompts/inner_monologue.txt", "prompts/inner_monologue_wanting.txt")


@pytest.mark.parametrize("path", FILES)
def test_prompt_is_aimed_at_her_life_first(path):
    t = Path(path).read_text(encoding="utf-8")
    assert "{own_life}" in t
    assert "Your own life lately" in t
    assert "what app they're focused in" not in t
    assert "frequent app-switching" not in t
    assert "there is almost always something worth noticing" not in t
    assert "Most ticks should be substantive" not in t
    assert "your own life first" in t.lower() or "your own life, first" in t.lower()
    assert "Quiet ticks are" in t
    # candidates may be about her things too
    assert "something you made" in t.lower() or "something of yours" in t.lower()
    # the sentiment read stays (reply tone reads it)
    assert "## Sentiment reading" in t


def test_variant_still_differs_in_exactly_one_line():
    b = Path(FILES[0]).read_text(encoding="utf-8").splitlines()
    v = Path(FILES[1]).read_text(encoding="utf-8").splitlines()
    assert len(b) == len(v)
    assert sum(1 for x, y in zip(b, v) if x != y) == 1


def test_own_life_block_gathers_her_deeds_dives_and_daydream(monkeypatch, tmp_path):
    import ai.inner_monologue as im
    from ai import recent_deeds, daydream_store as dds
    monkeypatch.setattr(recent_deeds, "DEEDS_PATH", tmp_path / "deeds.jsonl")
    monkeypatch.setattr(dds, "DAYDREAM_DIR", tmp_path / "daydream")
    recent_deeds.record_deed("sent", 'sent a message (workbench_share): "the lake page…"')
    dds.write_entry(text="next: the poem, at the bench", meta={})
    monkeypatch.setattr(im, "_recent_dives_lines", lambda: ["- a dive on Dennett's multiple drafts (~3h ago)"])
    monkeypatch.setattr(im, "_companion_for_life", lambda: type("C", (), {"wanting": True, "own_work": True})())
    block = im._own_life_block()
    assert "the lake page" in block and "the poem" in block and "Dennett" in block
    assert block.startswith("## Your own life lately") or "Your own life lately" in block


def test_own_life_block_is_empty_when_quiet(monkeypatch, tmp_path):
    import ai.inner_monologue as im
    from ai import recent_deeds, daydream_store as dds
    monkeypatch.setattr(recent_deeds, "DEEDS_PATH", tmp_path / "deeds.jsonl")
    monkeypatch.setattr(dds, "DAYDREAM_DIR", tmp_path / "daydream")
    monkeypatch.setattr(im, "_recent_dives_lines", lambda: [])
    monkeypatch.setattr(im, "_companion_for_life", lambda: type("C", (), {"wanting": True, "own_work": True})())
    assert im._own_life_block() == ""

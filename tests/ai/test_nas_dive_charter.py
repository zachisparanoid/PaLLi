"""Step 23 · The NAS charter (Task 3).

Spec §0.1: denylist mode and the list is EMPTY by the owner's explicit call —
the whole drive is hers to read; the knob is quiet machinery. §0.3: NAS
wandering is unrationed (own cap, 0 = unlimited, the 07c convention) and
never spends the web-research budget. Each dive stays bounded as built.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

from ai import curiosity_research as cr


def _comp(**kw):
    base = {"curiosity_research": True, "curiosity_nas_dives": True,
            "curiosity_nas_daily_cap": 0, "research_daily_cap": 3, "nas_deny": []}
    base.update(kw)
    return SimpleNamespace(**base)


# ── the deny knob ────────────────────────────────────────────────────────

def test_nas_deny_names_do_not_exist_to_the_walk(tmp_path):
    # the walker matches the topic against NAMES (a bounded, read-only walk)
    (tmp_path / "open_folder").mkdir(); (tmp_path / "open_folder" / "cypress-note.md").write_text("a note")
    (tmp_path / "private").mkdir(); (tmp_path / "private" / "cypress-secret.md").write_text("a note")
    (tmp_path / "cypress-root-note.md").write_text("a root-level file, also denied by name")
    with patch.object(cr, "_nas_deny", lambda: ["private", "cypress-root-note.md"]):
        out = cr._nas_probe("cypress", root=str(tmp_path))
    text = str(out)
    assert "open_folder" in text
    assert "private" not in text and "root-note" not in text


def test_deny_is_top_level_only_a_nested_namesake_is_still_hers(tmp_path):
    (tmp_path / "open").mkdir(); (tmp_path / "open" / "private").mkdir()
    (tmp_path / "open" / "private" / "cypress.md").write_text("nested namesake")
    with patch.object(cr, "_nas_deny", lambda: ["private"]):
        out = cr._nas_probe("cypress", root=str(tmp_path))
    assert "open/private/cypress.md" in str(out)


def test_nas_deny_reader_defaults_empty_by_his_call():
    with patch.object(cr, "companion_settings", lambda: None):
        assert cr._nas_deny() == []                        # unbooted: nothing hidden
    with patch.object(cr, "companion_settings", lambda: _comp(nas_deny=["taxes", " x "])):
        assert cr._nas_deny() == ["taxes", "x"]
    with patch.object(cr, "companion_settings", lambda: _comp(nas_deny="garbage")):
        assert cr._nas_deny() == []


def test_whole_drive_readable_when_nothing_is_denied(tmp_path):
    (tmp_path / "anything").mkdir(); (tmp_path / "anything" / "cypress.txt").write_text("hers to read")
    with patch.object(cr, "_nas_deny", lambda: []):
        out = cr._nas_probe("cypress", root=str(tmp_path))
    assert "anything/cypress.txt" in str(out)


# ── the un-rationing ─────────────────────────────────────────────────────

def test_nas_dives_are_unrationed_and_do_not_touch_the_web_budget(monkeypatch):
    monkeypatch.setattr(cr, "companion_settings", lambda: _comp(curiosity_nas_daily_cap=0))
    monkeypatch.setattr(cr, "faculty_enabled", lambda name: True)
    monkeypatch.setattr(cr, "count_research_today", lambda: 99)      # web budget exhausted
    monkeypatch.setattr(cr, "count_nas_research_today", lambda: 500)  # she wandered a lot
    allowed, reason = cr.research_allowed("nas")
    assert allowed is True, reason


def test_a_nonzero_nas_cap_still_binds(monkeypatch):
    monkeypatch.setattr(cr, "companion_settings", lambda: _comp(curiosity_nas_daily_cap=2))
    monkeypatch.setattr(cr, "faculty_enabled", lambda name: True)
    monkeypatch.setattr(cr, "count_nas_research_today", lambda: 2)
    allowed, reason = cr.research_allowed("nas")
    assert allowed is False and "cap" in reason


def test_nas_cap_reader_zero_default_and_garbage():
    with patch.object(cr, "companion_settings", lambda: None):
        assert cr.curiosity_nas_daily_cap() == 0           # unlimited by default (§0.3)
    with patch.object(cr, "companion_settings", lambda: _comp(curiosity_nas_daily_cap=5)):
        assert cr.curiosity_nas_daily_cap() == 5
    with patch.object(cr, "companion_settings", lambda: _comp(curiosity_nas_daily_cap="lots")):
        assert cr.curiosity_nas_daily_cap() == 0
    with patch.object(cr, "companion_settings", lambda: _comp(curiosity_nas_daily_cap=-4)):
        assert cr.curiosity_nas_daily_cap() == 0


def test_web_budget_does_not_count_nas_wandering():
    now = time.time()
    web = SimpleNamespace(timestamp=now - 50,
                          tick_metadata={"entry_kind": "curiosity_research", "modality": "web", "rounds": 2})
    legacy = SimpleNamespace(timestamp=now - 60,
                             tick_metadata={"entry_kind": "curiosity_research"})   # pre-modality = web
    nas1 = SimpleNamespace(timestamp=now - 70,
                           tick_metadata={"entry_kind": "curiosity_research", "modality": "nas"})
    nas2 = SimpleNamespace(timestamp=now - 80,
                           tick_metadata={"entry_kind": "curiosity_research", "modality": "nas"})
    old_nas = SimpleNamespace(timestamp=now - 30 * 3600,
                              tick_metadata={"entry_kind": "curiosity_research", "modality": "nas"})
    other = SimpleNamespace(timestamp=now - 90, tick_metadata={"entry_kind": "tick"})
    with patch.object(cr, "read_entries", lambda **kw: [web, legacy, nas1, nas2, old_nas, other]):
        assert cr.count_research_today() == 3       # 2 web rounds + 1 legacy; nas never counted
        assert cr.count_nas_research_today() == 2   # today's nas dives only

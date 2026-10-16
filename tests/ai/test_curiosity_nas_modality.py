# tests/ai/test_curiosity_nas_modality.py
"""§0.10: the read-only NAS dive — its own flag, the shared belt, the
exa result shape, never a write."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import ai.curiosity_research as cr


def test_nas_modality_requires_its_own_flag():
    with patch.object(cr, "faculty_enabled", return_value=True), \
         patch.object(
             cr, "companion_settings",
             return_value=SimpleNamespace(curiosity_nas_dives=False,
                                          research_daily_cap=3),
         ), \
         patch.object(cr, "count_research_today", return_value=0):
        allowed, reason = cr.research_allowed(modality="nas")
    assert allowed is False
    assert "curiosity_nas_dives" in reason


def test_nas_modality_skips_the_web_flag():
    with patch.object(cr, "faculty_enabled", return_value=True), \
         patch.object(
             cr, "companion_settings",
             return_value=SimpleNamespace(curiosity_nas_dives=True,
                                          research_daily_cap=3),
         ), \
         patch.object(cr, "count_research_today", return_value=0):
        allowed, reason = cr.research_allowed(modality="nas")
    assert allowed is True, reason


def test_web_default_is_byte_identical_behavior():
    with patch.object(cr, "faculty_enabled", return_value=True), \
         patch.object(cr, "companion_settings",
                      return_value=SimpleNamespace(research_daily_cap=3)), \
         patch.object(cr, "count_research_today", return_value=0):
        allowed_default, _ = cr.research_allowed()
        allowed_web, _ = cr.research_allowed(modality="web")
    assert allowed_default == allowed_web


def test_nas_probe_returns_exa_shaped_results(tmp_path):
    (tmp_path / "music").mkdir()
    (tmp_path / "music" / "mixtape-notes.txt").write_text(
        "songs he played in july", encoding="utf-8",
    )
    (tmp_path / "unrelated.bin").write_bytes(b"\x00" * 64)
    payload = cr._nas_probe("mixtape", root=str(tmp_path))
    results = payload["results"]
    assert len(results) == 1
    r = results[0]
    assert set(r) >= {"title", "url", "summary"}
    assert "mixtape-notes.txt" in r["title"]
    assert "songs he played" in r["summary"]


def test_nas_probe_never_writes(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    cr._nas_probe("anything", root=str(tmp_path))
    after = sorted(p.name for p in tmp_path.rglob("*"))
    assert before == after


def test_nas_probe_bounds_hold(tmp_path):
    for i in range(30):
        (tmp_path / f"topic-match-{i}.txt").write_text("x", encoding="utf-8")
    payload = cr._nas_probe("topic", root=str(tmp_path))
    assert len(payload["results"]) <= 5


def test_no_nas_configured_dives_nowhere(monkeypatch, caplog):
    """Step 28 task 2 — the NAS is ONE knob (PALLI_NAS_ROOT, with this
    feature's own CURIOSITY_NAS_ROOT still overriding it) and EMPTY means
    there is no NAS. The dive says so and walks nothing: it must NOT fall
    through to Path("") — the process CWD, which is her own substrate."""
    monkeypatch.delenv("CURIOSITY_NAS_ROOT", raising=False)
    monkeypatch.setenv("PALLI_NAS_ROOT", "")
    with caplog.at_level(logging.INFO, logger="ai.curiosity_research"):
        payload = cr._nas_probe("anything")
    assert payload == {"results": []}
    assert "no NAS configured" in caplog.text


def test_the_curiosity_override_beats_the_house_knob(tmp_path, monkeypatch):
    """I2 — CURIOSITY_NAS_ROOT is this feature's own override and it PREDATES
    the shared knob (core/lan_presence.py cites it as the precedent). Nothing
    pinned it: dropping the override left the suite green. With both set to
    different roots, the dive walks CURIOSITY's."""
    house = tmp_path / "house"
    house.mkdir()
    (house / "topic-house.txt").write_text("the house drive", encoding="utf-8")
    own = tmp_path / "own"
    own.mkdir()
    (own / "topic-own.txt").write_text("the override drive", encoding="utf-8")

    monkeypatch.setenv("PALLI_NAS_ROOT", str(house))
    monkeypatch.setenv("CURIOSITY_NAS_ROOT", str(own))
    payload = cr._nas_probe("topic")
    assert [r["title"] for r in payload["results"]] == ["topic-own.txt"]
    assert "the override drive" in payload["results"][0]["summary"]

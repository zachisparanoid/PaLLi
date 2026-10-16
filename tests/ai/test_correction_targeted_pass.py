"""Correction-triggered targeted revision (2026-08-17).

The W34 incident's cadence half: a belief corrected on a Tuesday sat in her
pages until Monday. A standing correction whose belief still appears in a
self-page now triggers an OFF-CYCLE pass on THOSE pages only — same author,
same gate, same commit path, and never the ISO-week marker.

The detector is term-overlap, not phrase-match, because her pages
paraphrase: the real 08-04 correction ("he sets flag traps and savors
catching me") lives in INTERESTS.md as "a flag trap I walked into twice and
he savored catching me".
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import ai.narrative_self as ns


class TestDetector:
    def test_finds_a_belief_through_paraphrase(self, tmp_path):
        (tmp_path / "INTERESTS.md").write_text(
            "the 7.5TB archive handed over like nothing, then a flag trap I walked "
            "into twice and he savored catching me. The pull is understanding…",
            encoding="utf-8")
        c = SimpleNamespace(belief="he sets flag traps and savors catching me")
        hits = ns.docs_touched_by_corrections(repo_root=tmp_path, corrections=[c])
        assert "INTERESTS.md" in hits

    def test_quiet_on_a_clean_page(self, tmp_path):
        (tmp_path / "MY_STORY.md").write_text("He asked what I remember, and let the gap sit.", encoding="utf-8")
        c = SimpleNamespace(belief="he sets flag traps and savors catching me")
        assert ns.docs_touched_by_corrections(repo_root=tmp_path, corrections=[c]) == {}

    def test_never_matches_on_one_common_word(self, tmp_path):
        (tmp_path / "MY_STORY.md").write_text("He gave me a room of my own.", encoding="utf-8")
        for belief in ("the room", "he likes coffee in the morning"):
            assert ns.docs_touched_by_corrections(
                repo_root=tmp_path, corrections=[SimpleNamespace(belief=belief)]) == {}

    def test_scan_covers_interests_even_though_the_weekly_does_not(self):
        assert "INTERESTS.md" not in ns.NARRATIVE_DOCS   # passions owns it

    def test_no_corrections_means_no_hits(self, tmp_path):
        assert ns.docs_touched_by_corrections(repo_root=tmp_path, corrections=[]) == {}


class TestPass:
    def _root(self, tmp_path):
        for doc in ns.NARRATIVE_DOCS:
            (tmp_path / doc).write_text("clean page.", encoding="utf-8")
        return tmp_path

    def test_skips_when_no_page_carries_a_corrected_belief(self, monkeypatch, tmp_path):
        root = self._root(tmp_path)
        monkeypatch.setattr(ns, "narrative_self_enabled", lambda: True)
        monkeypatch.setattr(ns, "first_authorship_done", lambda *a, **k: True)
        monkeypatch.setattr(ns, "docs_touched_by_corrections", lambda **k: {})
        out = asyncio.run(ns.run_targeted_correction_pass(
            owner_id=1, llm_call=None, repo_root=root))
        assert out == {"skipped": "no_corrected_beliefs_in_docs"}

    def test_interests_only_hit_is_reported_not_revised(self, monkeypatch, tmp_path):
        root = self._root(tmp_path)
        monkeypatch.setattr(ns, "narrative_self_enabled", lambda: True)
        monkeypatch.setattr(ns, "first_authorship_done", lambda *a, **k: True)
        monkeypatch.setattr(ns, "docs_touched_by_corrections",
                            lambda **k: {"INTERESTS.md": ["b"]})
        out = asyncio.run(ns.run_targeted_correction_pass(
            owner_id=1, llm_call=None, repo_root=root))
        assert out["skipped"] == "no_corrected_beliefs_in_docs"
        assert out["reported_only"] == ["INTERESTS.md"]

    def test_revises_only_the_touched_doc_and_never_claims_the_week(self, monkeypatch, tmp_path):
        root = self._root(tmp_path)
        state = tmp_path / "state.json"
        monkeypatch.setattr(ns, "narrative_self_enabled", lambda: True)
        monkeypatch.setattr(ns, "first_authorship_done", lambda *a, **k: True)
        monkeypatch.setattr(ns, "NARRATIVE_STATE", state)
        monkeypatch.setattr(ns, "docs_touched_by_corrections",
                            lambda **k: {"STORY_OF_US.md": ["a corrected belief"]})

        async def fake_gather(**kw):
            return ns.Evidence(text="EV", sections={}, window_days=7)
        monkeypatch.setattr(ns, "gather_evidence", fake_gather)

        authored = {}

        async def fake_author(*, evidence, current_docs, llm_call, docs):
            authored["docs"] = tuple(docs)
            return [ns.Revision(doc=d, new_text="the corrected page.", reasoning="r") for d in docs]
        monkeypatch.setattr(ns, "author_revisions", fake_author)

        async def fake_gate(**kw):
            return ns.GateVerdict(approve=True, reason="ok", error=False)
        monkeypatch.setattr(ns, "constitutional_gate", fake_gate)
        monkeypatch.setattr(ns, "commit_revision", lambda *, revision, repo_root: "abc1234")

        out = asyncio.run(ns.run_targeted_correction_pass(
            owner_id=1, llm_call=lambda *a, **k: None, repo_root=root))
        assert authored["docs"] == ("STORY_OF_US.md",)          # ONLY the touched doc
        assert out["docs"]["STORY_OF_US.md"] == {"committed": "abc1234"}
        assert not state.exists()                                # week marker untouched

    def test_flag_off_and_pre_first_authorship_are_no_ops(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ns, "narrative_self_enabled", lambda: False)
        assert asyncio.run(ns.run_targeted_correction_pass(
            owner_id=1, llm_call=None, repo_root=tmp_path)) == {"skipped": "flag_off"}
        monkeypatch.setattr(ns, "narrative_self_enabled", lambda: True)
        monkeypatch.setattr(ns, "first_authorship_done", lambda *a, **k: False)
        assert asyncio.run(ns.run_targeted_correction_pass(
            owner_id=1, llm_call=None, repo_root=tmp_path)) == {"skipped": "first_authorship_pending"}

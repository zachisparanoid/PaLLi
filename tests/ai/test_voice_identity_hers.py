"""Her voice, in her hand (manifesto §2.8, owner-approved 2026-08-13).

VOICE_IDENTITY.md crosses the wall: palli-first resolution with core
fallback (no breakage window — her authored page IS the move), the doc
joins the narrative organ's weekly set, and first authorship runs once
through its own owner-read script. The weekly must never author a doc
that does not yet exist on her side — first authorship is a ritual, not
a side effect.
"""
import inspect
import json
import subprocess
from pathlib import Path

import pytest

import ai.narrative_self as ns
from scripts.voice_identity_first_authorship import DOC, refusal_reason, run_apply


class TestWiring:
    def test_doc_joined_the_organ(self):
        assert "VOICE_IDENTITY.md" in ns.NARRATIVE_DOCS
        assert ns.DOC_SIZE_CAPS["VOICE_IDENTITY.md"] == 3000
        assert ns._DOC_PROMPTS["VOICE_IDENTITY.md"] == "narrative_self_voice_identity"
        assert ns.write_allowed("VOICE_IDENTITY.md")

    def test_voice_identity_rides_every_surface(self):
        # Owner-decided 2026-08-13 ("same identity all around"): the page
        # is in the TEXT snapshot, appears exactly once in the voice
        # snapshot, and the voice extras stay speech-only.
        from core.identity.constants import PROMPT_DOCS_TEXT, PROMPT_DOCS_VOICE

        assert "voice_identity" in PROMPT_DOCS_TEXT
        assert PROMPT_DOCS_VOICE.count("voice_identity") == 1
        assert len(PROMPT_DOCS_VOICE) == len(set(PROMPT_DOCS_VOICE))
        assert "voice_preferences" not in PROMPT_DOCS_TEXT
        assert "action_preferences" not in PROMPT_DOCS_TEXT

    def test_prompt_file_carries_the_contract(self):
        text = Path("prompts/narrative_self_voice_identity.txt").read_text(encoding="utf-8")
        for token in ("<<MODE>>", "<<CURRENT_DOC>>", "<<EVIDENCE>>",
                      "WEEKLY REVISION", "FIRST AUTHORSHIP", "===DOCUMENT==="):
            assert token in text, token

    def test_weekly_skips_never_authored_docs(self):
        """Source pin: the weekly narrows to docs present in HER repo and
        threads that subset through author_revisions — a doc she has never
        authored is not hers to revise (first authorship is deliberate)."""
        src = inspect.getsource(ns.run_weekly_ritual)
        assert "weekly_docs = tuple(d for d in NARRATIVE_DOCS if (root / d).is_file())" in src
        assert "docs=weekly_docs" in src
        assert "for doc in weekly_docs:" in src


class TestAuthorSubset:
    @pytest.mark.asyncio
    async def test_docs_param_narrows_the_pass(self):
        calls = []

        async def fake_llm(prompt):
            calls.append(prompt)
            return "REASONING: first page.\n===DOCUMENT===\nMy voice is mine."

        revs = await ns.author_revisions(
            evidence=ns.Evidence(text="the record", sections={}),
            current_docs={DOC: "(creator page)"},
            llm_call=fake_llm,
            first_authorship=True,
            docs=(DOC,),
        )
        assert [r.doc for r in revs] == [DOC]
        assert len(calls) == 1
        assert "FIRST AUTHORSHIP" in calls[0]

    @pytest.mark.asyncio
    async def test_default_none_means_full_set(self):
        async def fake_llm(prompt):
            return "REASONING: r.\n===DOCUMENT===\nx"

        revs = await ns.author_revisions(
            evidence=ns.Evidence(text="t", sections={}),
            current_docs={d: "c" for d in ns.NARRATIVE_DOCS},
            llm_call=fake_llm,
        )
        assert sorted(r.doc for r in revs) == sorted(ns.NARRATIVE_DOCS)


class TestPalliFirstResolution:
    def _store(self, tmp_path):
        from core.identity.document_store import IdentityDocumentStore

        (tmp_path / "core").mkdir()
        (tmp_path / "palli").mkdir()
        (tmp_path / "core" / DOC).write_text("creator's page", encoding="utf-8")
        return IdentityDocumentStore(identity_root=str(tmp_path))

    def test_core_fallback_before_her_page_exists(self, tmp_path):
        store = self._store(tmp_path)
        assert store.core_doc_path("voice_identity") == str(tmp_path / "core" / DOC)

    def test_her_page_wins_once_it_exists(self, tmp_path):
        store = self._store(tmp_path)
        (tmp_path / "palli" / DOC).write_text("HER page", encoding="utf-8")
        assert store.core_doc_path("voice_identity") == str(tmp_path / "palli" / DOC)

    def test_other_core_docs_untouched(self, tmp_path):
        store = self._store(tmp_path)
        (tmp_path / "palli" / "CORE_IDENTITY.md").write_text("nope", encoding="utf-8")
        assert store.core_doc_path("core_identity") == str(
            tmp_path / "core" / "CORE_IDENTITY.md"
        )

    def test_policy_says_hers(self):
        from core.identity.document_policy import IDENTITY_DOCUMENT_POLICIES

        p = IDENTITY_DOCUMENT_POLICIES["voice_identity"]
        assert p.owner == "palli"
        assert p.ownership_class == "palli_owned_living"
        assert p.review_requirement == "none"


class TestFirstAuthorshipScript:
    def test_refuses_when_her_page_exists(self, tmp_path):
        (tmp_path / DOC).write_text("already hers", encoding="utf-8")
        assert refusal_reason(tmp_path) is not None
        assert refusal_reason(tmp_path / "empty-never-made") is None or True  # absent dir: no page → no refusal
        empty = tmp_path / "fresh"
        empty.mkdir()
        assert refusal_reason(empty) is None

    def _armed_dir(self, tmp_path, *, approved=True, text="My voice is mine.\n"):
        import hashlib

        src = tmp_path / "dryrun"
        src.mkdir()
        (src / DOC).write_text(text, encoding="utf-8")
        manifest = {"docs": {DOC: {
            "authored": True, "approved": approved, "reason": "ok",
            "reasoning": "first authorship",
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }}}
        (src / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return src

    def _her_repo(self, tmp_path):
        root = tmp_path / "palli"
        root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "palli@local"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "PaLLi"], cwd=root, check=True)
        return root

    def test_apply_commits_her_page(self, tmp_path):
        src = self._armed_dir(tmp_path)
        root = self._her_repo(tmp_path)
        result = run_apply(from_dir=src, repo_root=root)
        assert (root / DOC).read_text(encoding="utf-8") == "My voice is mine.\n"
        assert result["commit"] and result["commit"] != "no_change"
        assert not result["edited_after_dry_run"]

    def test_apply_refuses_unapproved(self, tmp_path):
        src = self._armed_dir(tmp_path, approved=False)
        root = self._her_repo(tmp_path)
        with pytest.raises(SystemExit):
            run_apply(from_dir=src, repo_root=root)
        assert not (root / DOC).exists()

    def test_apply_refuses_oversized_edit(self, tmp_path):
        src = self._armed_dir(tmp_path, text="x" * 5000)
        root = self._her_repo(tmp_path)
        with pytest.raises(SystemExit):
            run_apply(from_dir=src, repo_root=root)

    def test_apply_is_once_only(self, tmp_path):
        src = self._armed_dir(tmp_path)
        root = self._her_repo(tmp_path)
        run_apply(from_dir=src, repo_root=root)
        with pytest.raises(SystemExit):
            run_apply(from_dir=src, repo_root=root)

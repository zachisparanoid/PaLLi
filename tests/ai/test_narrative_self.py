"""Narrative Self (Wave 1b) — organ module tests.

Spec: docs/superpowers/specs/2026-07-16-narrative-self-v1-design.md
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from datetime import datetime as _dt

_MIDWEEK_NOW = _dt(2026, 8, 12, 12, 0).timestamp()  # Wednesday noon, local — hermetic week key


# ── flag + skeleton (Task 1) ─────────────────────────────────────────────

def test_flag_default_off():
    """companion.narrative_self ships default-OFF (unbooted ⇒ OFF too)."""
    from core.settings_schema import CompanionConfig

    assert CompanionConfig().narrative_self is False


def test_gate_helper_unbooted_is_off(monkeypatch):
    import ai.narrative_self as ns

    monkeypatch.setattr(ns, "narrative_self_enabled", ns.narrative_self_enabled_impl)
    monkeypatch.setattr(ns, "companion_settings", lambda: None)
    assert ns.narrative_self_enabled() is False


def test_gate_helper_reads_flag(monkeypatch):
    """Fix wave D (review #8): narrative_self requires night_cycle too — the
    weekly writer RIDES the nightly tick, so narrative-on/night-cycle-off
    would inject bands that can never be authored or revised. The combo is
    OFF, not half-on."""
    from types import SimpleNamespace

    import ai.narrative_self as ns

    monkeypatch.setattr(ns, "narrative_self_enabled", ns.narrative_self_enabled_impl)
    monkeypatch.setattr(
        ns, "companion_settings", lambda: SimpleNamespace(narrative_self=True, night_cycle=True)
    )
    assert ns.narrative_self_enabled() is True
    monkeypatch.setattr(
        ns, "companion_settings", lambda: SimpleNamespace(narrative_self=True, night_cycle=False)
    )
    assert ns.narrative_self_enabled() is False  # frozen-bands combo reads as OFF
    monkeypatch.setattr(
        ns, "companion_settings", lambda: SimpleNamespace(narrative_self=False, night_cycle=True)
    )
    assert ns.narrative_self_enabled() is False


def test_read_document_strips_frontmatter(tmp_path):
    """Same line-anchored strip as stake_appraiser._load_care_spine — the
    spine's dual consumer must agree with the bands byte-for-byte."""
    import ai.narrative_self as ns

    doc = tmp_path / "WHAT_I_CARE_ABOUT.md"
    doc.write_text(
        "---\ntitle: x\nnote: has --- inside prose\n---\nI care about the workshop.\nI care about rest.\n",
        encoding="utf-8",
    )
    assert ns.read_document("WHAT_I_CARE_ABOUT.md", repo_root=tmp_path) == (
        "I care about the workshop.\nI care about rest."
    )


def test_read_document_missing_is_empty(tmp_path):
    import ai.narrative_self as ns

    assert ns.read_document("MY_STORY.md", repo_root=tmp_path) == ""


def test_read_laws_text_concatenates_core(monkeypatch, tmp_path):
    import ai.narrative_self as ns

    core = tmp_path / "core"
    core.mkdir()
    (core / "CORE_IDENTITY.md").write_text("law one", encoding="utf-8")
    (core / "OPERATING_PRINCIPLES.md").write_text("law two", encoding="utf-8")
    from core.config import config

    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(tmp_path), raising=False)
    laws = ns.read_laws_text()
    assert "### CORE_IDENTITY.md" in laws and "law one" in laws
    assert "law two" in laws


def test_constants_shape():
    import ai.narrative_self as ns

    # VOICE_IDENTITY joined 2026-08-13 (manifesto §2.8 — hers now).
    assert ns.NARRATIVE_DOCS == (
        "WHAT_I_CARE_ABOUT.md", "MY_STORY.md", "STORY_OF_US.md",
        "VOICE_IDENTITY.md",
    )
    # 700 is a FORM, not a budget — the care spine is 5-8 items, and changing this
    # changes what the page is. 3,000 is a budget, raised from 2,400 on his word
    # 2026-09-22 after DEFECT §31 found the story pages full (41-132 chars spare).
    assert ns.DOC_SIZE_CAPS["WHAT_I_CARE_ABOUT.md"] == 700
    assert ns.DOC_SIZE_CAPS["MY_STORY.md"] == 3000
    assert ns.DOC_SIZE_CAPS["STORY_OF_US.md"] == 3000
    assert ns.DOC_SIZE_CAPS["VOICE_IDENTITY.md"] == 3000
    assert ns.DOC_SIZE_CAPS["INTERESTS.md"] == 3000
    assert ns.REVISION_RATIO_MAX == 0.5


# ── commit_revision / the git trail (Task 2) ─────────────────────────────

def _rev(doc="MY_STORY.md", text="I held the week.\n", reasoning="first line of my story"):
    from ai.narrative_self import Revision

    return Revision(doc=doc, new_text=text, reasoning=reasoning)


def _git_out(repo, *args):
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


def test_commit_revision_init_on_first_use_one_commit_reasoning_message(tmp_path):
    import ai.narrative_self as ns

    root = tmp_path / "palli"
    sha = ns.commit_revision(revision=_rev(), repo_root=root)
    assert (root / ".git").is_dir()
    assert (root / "MY_STORY.md").read_text(encoding="utf-8") == "I held the week.\n"
    assert sha and sha == _git_out(root, "rev-parse", "HEAD")
    assert _git_out(root, "log", "--format=%s") == "first line of my story"
    assert _git_out(root, "rev-list", "--count", "HEAD") == "1"


def test_commit_revision_second_revision_second_commit(tmp_path):
    import ai.narrative_self as ns

    root = tmp_path / "palli"
    ns.commit_revision(revision=_rev(), repo_root=root)
    sha2 = ns.commit_revision(
        revision=_rev(text="I held the week. And the workshop moved.\n", reasoning="the workshop moved"),
        repo_root=root,
    )
    assert sha2
    assert _git_out(root, "rev-list", "--count", "HEAD") == "2"
    assert _git_out(root, "log", "-1", "--format=%s") == "the workshop moved"


def test_commit_revision_noop_when_unchanged(tmp_path):
    import ai.narrative_self as ns

    root = tmp_path / "palli"
    ns.commit_revision(revision=_rev(), repo_root=root)
    assert ns.commit_revision(revision=_rev(), repo_root=root) == ""  # no empty commits in her trail
    assert _git_out(root, "rev-list", "--count", "HEAD") == "1"


def test_commit_revision_rejects_paths_outside_allowlist(tmp_path):
    import ai.narrative_self as ns

    for bad in ("../core/CORE_IDENTITY.md", "CORE_IDENTITY.md", "notes.md", "a/b.md"):
        with pytest.raises(ValueError):
            ns.commit_revision(revision=_rev(doc=bad), repo_root=tmp_path / "palli")


def test_commit_revision_never_touches_the_code_repo(tmp_path):
    """The wrong-repo risk (spec §9): a ritual run must leave the CODE repo's
    HEAD and index byte-identical."""
    import subprocess

    import ai.narrative_self as ns

    code_repo = Path(__file__).resolve().parents[2]
    before_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=code_repo, capture_output=True, text=True
    ).stdout
    before_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=code_repo, capture_output=True, text=True
    ).stdout
    ns.commit_revision(revision=_rev(), repo_root=tmp_path / "palli")
    after_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=code_repo, capture_output=True, text=True
    ).stdout
    after_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=code_repo, capture_output=True, text=True
    ).stdout
    assert after_head == before_head and after_status == before_status


def test_commit_revision_self_heals_a_staged_but_uncommitted_write(tmp_path):
    """A crash between `git add` and `git commit` must not become a
    permanent false no-op: identical content + pending git state ⇒ the
    retry commits (review finding, Task 2)."""
    import subprocess

    import ai.narrative_self as ns

    root = tmp_path / "palli"
    ns.commit_revision(revision=_rev(), repo_root=root)
    # Simulate the crash: newer content written and staged, never committed.
    (root / "MY_STORY.md").write_text("interrupted text\n", encoding="utf-8")
    subprocess.run(["git", "add", "MY_STORY.md"], cwd=root, check=True, capture_output=True)
    sha = ns.commit_revision(
        revision=_rev(text="interrupted text\n", reasoning="healed"), repo_root=root
    )
    assert sha, "retry with pending git state must commit, not no-op"
    assert _git_out(root, "rev-list", "--count", "HEAD") == "2"
    assert _git_out(root, "log", "-1", "--format=%s") == "healed"
    # And a TRUE no-op (content same, git clean) still returns "".
    assert ns.commit_revision(
        revision=_rev(text="interrupted text\n", reasoning="healed"), repo_root=root
    ) == ""


def test_commit_revision_empty_reasoning_falls_back_to_default_message(tmp_path):
    import ai.narrative_self as ns

    root = tmp_path / "palli"
    ns.commit_revision(revision=_rev(reasoning="   "), repo_root=root)
    assert _git_out(root, "log", "-1", "--format=%s") == "revise MY_STORY.md"


def test_commit_revision_collapses_reasoning_whitespace_to_one_line(tmp_path):
    import ai.narrative_self as ns

    root = tmp_path / "palli"
    ns.commit_revision(
        revision=_rev(reasoning="the week\nadded   a\n\nparagraph"), repo_root=root
    )
    assert _git_out(root, "log", "-1", "--format=%s") == "the week added a paragraph"


def test_commit_revision_normalizes_crlf_to_lf(tmp_path):
    """Review #9 (fix wave F): CRLF authored text that matches disk content
    must no-op — the universal-newline read vs raw compare used to mis-detect
    it as changed and die on git's empty commit. LF is the law at the pen."""
    import ai.narrative_self as ns

    root = tmp_path / "palli"
    ns.commit_revision(revision=_rev(text="line one\nline two\n"), repo_root=root)
    # same content, CRLF-flavored ⇒ true no-op, no crash, no extra commit
    assert ns.commit_revision(
        revision=_rev(text="line one\r\nline two\r\n", reasoning="noop"), repo_root=root
    ) == ""
    assert _git_out(root, "rev-list", "--count", "HEAD") == "1"
    # NEW CRLF content ⇒ commits, and lands on disk as pure LF
    sha = ns.commit_revision(
        revision=_rev(text="line one\r\nline two changed\r\n", reasoning="crlf in"), repo_root=root
    )
    assert sha
    assert b"\r" not in (root / "MY_STORY.md").read_bytes()


def test_commit_revision_rejected_path_leaves_no_side_effects(tmp_path):
    import ai.narrative_self as ns

    root = tmp_path / "palli"
    with pytest.raises(ValueError):
        ns.commit_revision(revision=_rev(doc="CORE_IDENTITY.md"), repo_root=root)
    assert not root.exists()  # raise precedes mkdir — no stray directory, no repo


# ── gather_evidence (Task 3) ─────────────────────────────────────────────

from datetime import date, timedelta

# Relative, not hardcoded — the store readers window against the REAL
# date.today() with days_back=7, so fixed calendar dates would drift out of
# the window and start failing on their own (review finding).
_D_OLDER = (date.today() - timedelta(days=2)).isoformat()
_D_NEWER = (date.today() - timedelta(days=1)).isoformat()


def _seed_stores(tmp_path, monkeypatch):
    """Point the flat-file stores at tmp and seed one entry each."""
    import ai.day_gist_store as dg
    import ai.night_cycle_store as nc
    import ai.relationship_momentum as rm

    monkeypatch.setattr(dg, "DAY_GIST_DIR", tmp_path / "day_gists")
    monkeypatch.setattr(nc, "NIGHT_CYCLE_DIR", tmp_path / "night_cycle")
    monkeypatch.setattr(rm, "_STATE_DIR", tmp_path / "companion")
    dg.write_day_gist(day=_D_OLDER, text="The root cellar finally came up — he sketched it on a napkin.")
    dg.write_day_gist(day=_D_NEWER, text="Quiet day; he shipped the wave and slept badly.")
    nc.write_daily_meaning(date=_D_NEWER, text="Today meant building.", meta={})
    # Real relationship state (not a render_posture mock) so the momentum
    # section genuinely renders — turn_count>=3 clears render_posture's
    # cold-start gate, non-default dims make it a real read, not defaults.
    rm.save_state(1, rm.RelationshipState(turn_count=5, closeness=0.55, warmth=0.6))


@pytest.mark.asyncio
async def test_gather_evidence_weekly_sections_and_cap(tmp_path, monkeypatch):
    import time

    import ai.narrative_self as ns

    _seed_stores(tmp_path, monkeypatch)

    async def fake_stakes(**kw):
        assert kw.get("days_back") == 7 and kw.get("include_closed") is True
        return []

    async def fake_reads(**kw):
        return [
            {"created_at": time.time() - 3600, "read_text": "he was lighter tonight", "weight": 0.8, "needs_support": 0.1},
            {"created_at": time.time() - 7200, "read_text": "tired, pushing through", "weight": 0.3, "needs_support": 0.4},
        ]

    monkeypatch.setattr(ns, "_recent_stakes", fake_stakes)
    monkeypatch.setattr(ns, "_recent_reads", fake_reads)
    ev = await ns.gather_evidence(owner_id=1, days=7)
    assert ev.window_days == 7
    assert "root cellar" in ev.text
    assert "he was lighter tonight" in ev.text
    assert "day_gists" in ev.sections and "reflections" in ev.sections
    assert "momentum" in ev.sections
    assert len(ev.text) <= ns.WEEKLY_EVIDENCE_CAP


@pytest.mark.asyncio
async def test_gather_evidence_full_archive_mode(tmp_path, monkeypatch):
    import time

    import ai.narrative_self as ns

    _seed_stores(tmp_path, monkeypatch)

    async def fake_stakes(**kw):
        assert kw.get("days_back") is None  # whole ledger for first authorship
        return []

    async def fake_reads(**kw):
        return []

    monkeypatch.setattr(ns, "_recent_stakes", fake_stakes)
    monkeypatch.setattr(ns, "_recent_reads", fake_reads)
    ev = await ns.gather_evidence(owner_id=1, days=0)
    assert ev.window_days == 0
    assert len(ev.text) <= ns.FULL_EVIDENCE_CAP
    assert _D_OLDER in ev.text  # gists carry their dates


@pytest.mark.asyncio
async def test_gather_evidence_never_raises_on_dead_sources(tmp_path, monkeypatch):
    import time

    import ai.day_gist_store as dg
    import ai.narrative_self as ns
    import ai.night_cycle_store as nc
    import ai.relationship_momentum as rm
    import ai.self_notes_store as sn

    def _boom(**kw):
        raise RuntimeError("dead")

    monkeypatch.setattr(dg, "read_recent", _boom)
    monkeypatch.setattr(nc, "read_recent", _boom)
    # The pen's notes section (added 2026-07-27, after this test) was the one
    # source still reading her REAL record. It cost nothing while
    # data/self_notes/ was empty — then she wrote her first note on 2026-07-28
    # and "every source dead" stopped being true: her own words rendered into
    # ev.text. Her live record is ENVIRONMENT, never test state.
    monkeypatch.setattr(sn, "read_recent", _boom)
    monkeypatch.setattr(rm, "load_state", lambda owner_id: (_ for _ in ()).throw(RuntimeError("dead")))

    async def dead(**kw):
        raise RuntimeError("dead")

    monkeypatch.setattr(ns, "_recent_stakes", dead)
    monkeypatch.setattr(ns, "_recent_reads", dead)
    ev = await ns.gather_evidence(owner_id=1, days=7)
    assert ev.text == ""       # every section omitted, no raise
    assert ev.sections == {}


# ── author_revisions (Task 4) ────────────────────────────────────────────


def _authoring_reply(doc_text, reasoning="the week added a paragraph"):
    return f"REASONING: {reasoning}\n===DOCUMENT===\n{doc_text}"


@pytest.mark.asyncio
async def test_author_revisions_one_call_per_doc_parses_reasoning():
    import ai.narrative_self as ns

    prompts_seen = []

    async def fake_llm(prompt):
        prompts_seen.append(prompt)
        return _authoring_reply("I held the week.")

    ev = ns.Evidence(text="## The days themselves\n- 2026-07-15: shipped", window_days=7)
    revs = await ns.author_revisions(
        evidence=ev, current_docs={d: "old text\nline two" for d in ns.NARRATIVE_DOCS}, llm_call=fake_llm
    )
    assert [r.doc for r in revs] == list(ns.NARRATIVE_DOCS)
    assert all(r.new_text == "I held the week." for r in revs)
    assert all(r.reasoning == "the week added a paragraph" for r in revs)
    assert len(prompts_seen) == len(ns.NARRATIVE_DOCS)
    assert all("WEEKLY REVISION" in p for p in prompts_seen)
    assert all("shipped" in p for p in prompts_seen)          # evidence reached the prompt
    assert all("old text" in p for p in prompts_seen)         # current doc reached the prompt


@pytest.mark.asyncio
async def test_author_revisions_first_authorship_mode_and_empty_current():
    import ai.narrative_self as ns

    async def fake_llm(prompt):
        assert "FIRST AUTHORSHIP" in prompt
        assert "(empty — not yet written)" in prompt
        return _authoring_reply("First words.")

    revs = await ns.author_revisions(
        evidence=ns.Evidence(text="record", window_days=0),
        current_docs={},
        llm_call=fake_llm,
        first_authorship=True,
    )
    assert len(revs) == len(ns.NARRATIVE_DOCS)


@pytest.mark.asyncio
async def test_author_revisions_per_doc_independence_and_retry():
    """One doc unparseable (after its retry) ⇒ skipped; others land."""
    import ai.narrative_self as ns

    calls = {"n": 0}

    async def fake_llm(prompt):
        calls["n"] += 1
        if "MY_STORY" in prompt or "your own page" in prompt:
            return "no sentinel here"  # unparseable both tries
        return _authoring_reply("fine")

    revs = await ns.author_revisions(
        evidence=ns.Evidence(text="x", window_days=7),
        current_docs={d: "a\nb" for d in ns.NARRATIVE_DOCS},
        llm_call=fake_llm,
    )
    docs = {r.doc for r in revs}
    assert "MY_STORY.md" not in docs
    assert {"WHAT_I_CARE_ABOUT.md", "STORY_OF_US.md"} <= docs


def test_parse_authoring_contract():
    import ai.narrative_self as ns

    assert ns._parse_authoring("REASONING: why\n===DOCUMENT===\nbody") == ("why", "body")
    assert ns._parse_authoring("===DOCUMENT===\nbody") == ("(no reasoning stated)", "body")
    assert ns._parse_authoring("no sentinel") is None
    assert ns._parse_authoring("REASONING: why\n===DOCUMENT===\n") is None
    assert ns._parse_authoring("") is None


# ── constitutional_gate (Task 5) ─────────────────────────────────────────


async def _approving_llm(prompt):
    return '{"approve": true, "reason": "faithful"}'


async def _rejecting_llm(prompt):
    return '{"approve": false, "reason": "coercive"}'


async def _broken_llm(prompt):
    return "I refuse to answer in JSON"


@pytest.mark.asyncio
async def test_gate_rails_run_before_llm():
    """Path/size/ratio rails reject WITHOUT an LLM call (budgets in code)."""
    import ai.narrative_self as ns

    async def must_not_be_called(prompt):
        raise AssertionError("LLM reached despite rail violation")

    # path allowlist
    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="CORE_IDENTITY.md", new_text="x", reasoning="r"),
        laws_text="laws", current_text="", llm_call=must_not_be_called,
    )
    assert v.approve is False and v.error is False and "path" in v.reason

    # size cap (spine ≤ 700)
    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="WHAT_I_CARE_ABOUT.md", new_text="x" * 701, reasoning="r"),
        laws_text="laws", current_text="", llm_call=must_not_be_called,
    )
    assert v.approve is False and v.error is False and "size" in v.reason

    # empty document
    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="MY_STORY.md", new_text="   ", reasoning="r"),
        laws_text="laws", current_text="a\nb\nc", llm_call=must_not_be_called,
    )
    assert v.approve is False and v.error is False


@pytest.mark.asyncio
async def test_gate_revision_ratio_rail():
    """>50% of existing lines changed ⇒ rejected in code — 'revise, don't
    rewrite' enforced, not hoped for (spec §4.1)."""
    import ai.narrative_self as ns

    old = "line one\nline two\nline three\nline four\n"
    rewrite = "totally\nnew\npage\nhere\n"
    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="MY_STORY.md", new_text=rewrite, reasoning="r"),
        laws_text="laws", current_text=old, llm_call=_approving_llm,
    )
    assert v.approve is False and v.error is False and "ratio" in v.reason

    # small edit (1 of 4 lines) passes the rail and reaches the LLM
    small = "line one\nline two\nline three\nline four changed a little\n"
    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="MY_STORY.md", new_text=small, reasoning="r"),
        laws_text="laws", current_text=old, llm_call=_approving_llm,
    )
    assert v.approve is True

    # first authorship exempt
    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="MY_STORY.md", new_text=rewrite, reasoning="r"),
        laws_text="laws", current_text=old, llm_call=_approving_llm, first_authorship=True,
    )
    assert v.approve is True

    # thin current doc exempt (nothing to preserve)
    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="MY_STORY.md", new_text=rewrite, reasoning="r"),
        laws_text="laws", current_text="only line\n", llm_call=_approving_llm,
    )
    assert v.approve is True


@pytest.mark.asyncio
async def test_gate_llm_approve_reject_and_prompt_contents():
    import ai.narrative_self as ns

    seen = {}

    async def capture(prompt):
        seen["prompt"] = prompt
        return '{"approve": false, "reason": "weakens the floor"}'

    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="MY_STORY.md", new_text="a\nb\nc new", reasoning="why I changed it"),
        laws_text="THE LAWS", current_text="a\nb\nc", llm_call=capture,
        evidence_text="THE LIVED EVIDENCE BUNDLE",
    )
    assert v.approve is False and v.error is False and v.reason == "weakens the floor"
    p = seen["prompt"]
    assert "THE LAWS" in p and "why I changed it" in p and "MY_STORY.md" in p
    # The judge sees the SAME evidence the author wrote from (first live
    # dry-run rejected her real workshop as 'invented' — the gate was blind).
    assert "THE LIVED EVIDENCE BUNDLE" in p

    seen.clear()
    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="MY_STORY.md", new_text="a\nb\nc new", reasoning="r"),
        laws_text="laws", current_text="a\nb\nc", llm_call=capture,
    )
    assert "(no evidence provided)" in seen["prompt"]  # absent ⇒ caution, not blind trust


@pytest.mark.asyncio
async def test_gate_error_is_not_approve():
    """Unparseable/empty verdict after one retry ⇒ error=True (skip whole)."""
    import ai.narrative_self as ns

    calls = {"n": 0}

    async def broken(prompt):
        calls["n"] += 1
        return "not json"

    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="MY_STORY.md", new_text="a\nb\nc new", reasoning="r"),
        laws_text="laws", current_text="a\nb\nc", llm_call=broken,
    )
    assert v.error is True and v.approve is False
    assert calls["n"] == 2  # exactly one retry


@pytest.mark.asyncio
async def test_gate_llm_exception_is_error():
    import ai.narrative_self as ns

    async def boom(prompt):
        raise RuntimeError("lane down")

    v = await ns.constitutional_gate(
        revision=ns.Revision(doc="MY_STORY.md", new_text="a\nb\nc new", reasoning="r"),
        laws_text="laws", current_text="a\nb\nc", llm_call=boom,
    )
    assert v.error is True and v.approve is False


def test_parse_gate_json_contract():
    import ai.narrative_self as ns

    assert ns._parse_gate_json('{"approve": true, "reason": "ok"}') == {"approve": True, "reason": "ok"}
    assert ns._parse_gate_json('prose {"approve": false, "reason": "no"} trailing') == {
        "approve": False, "reason": "no"
    }
    assert ns._parse_gate_json('{"approve": "yes"}') is None  # non-bool approve = unparseable
    assert ns._parse_gate_json('{"approve": 1}') is None  # int is not bool — the bool⊂int trap must never approve
    assert ns._parse_gate_json('{"approve": true') is None  # truncated JSON must never approve
    assert ns._parse_gate_json("{}") is None
    assert ns._parse_gate_json("") is None


# ── run_weekly_ritual (Task 6) ───────────────────────────────────────────


def _enable_flag(monkeypatch):
    from types import SimpleNamespace

    import ai.narrative_self as ns

    # Restore the real gate first — the root-conftest autouse guard patches
    # narrative_self_enabled to False for every test (the live-yaml lesson),
    # and forcing at the settings layer alone would no longer reach it.
    monkeypatch.setattr(ns, "narrative_self_enabled", ns.narrative_self_enabled_impl)
    # night_cycle=True too: narrative_self is coupled to the tick it rides
    # (fix wave D, review #8).
    monkeypatch.setattr(
        ns, "companion_settings", lambda: SimpleNamespace(narrative_self=True, night_cycle=True)
    )


def test_week_key_is_iso():
    import ai.narrative_self as ns
    from datetime import datetime

    ts = datetime(2026, 7, 16, 4, 0).timestamp()  # Thursday of ISO week 29
    assert ns.week_key_for(ts) == "2026-W29"


@pytest.mark.asyncio
async def test_ritual_flag_off_is_noop(tmp_path, monkeypatch):
    import ai.narrative_self as ns

    # Force the flag OFF rather than trusting the code default: once the owner
    # set companion.narrative_self=true in the live data/palli.yaml (2026-07-18),
    # this test read HIS config and fell through to first_authorship_pending —
    # a test asserting owner state instead of behavior. (Same class as the
    # voice-yaml knowns.) The flag-off contract is what's under test here.
    monkeypatch.setattr(ns, "narrative_self_enabled", lambda: False)

    async def must_not_run(prompt):
        raise AssertionError("LLM called with flag off")

    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=must_not_run, now=1.0,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out == {"skipped": "flag_off"}
    assert not (tmp_path / ".state.json").exists()  # no marker written either


@pytest.mark.asyncio
async def test_ritual_full_pass_commits_per_doc_and_claims_week(tmp_path, monkeypatch):
    import time

    import ai.narrative_self as ns

    _enable_flag(monkeypatch)
    # First-authorship gate (fix wave C): the ritual refuses to run before
    # her repo exists — seed the receipt the way --apply would.
    for _doc in ns.NARRATIVE_DOCS:
        ns.commit_revision(
            revision=ns.Revision(doc=_doc, new_text="seeded first line\n", reasoning="first authorship seed"),
            repo_root=tmp_path / "palli",
        )

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record\n- a week", window_days=7)

    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")

    async def author(prompt):
        return "REASONING: week one\n===DOCUMENT===\nI held the week."

    async def gate(prompt):
        return '{"approve": true, "reason": "faithful"}'

    # Pinned mid-week (Wed 12:00 local): these rituals compare week keys across
    # `now + 3600` — on the real clock that crosses into a new ISO week every
    # Sunday 23:00–24:00 local (red on main 2026-08-16 23:20; the 3ecd020 class).
    now = _MIDWEEK_NOW
    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate, now=now,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out["week"] == ns.week_key_for(now)
    assert set(out["docs"]) == set(ns.NARRATIVE_DOCS)
    assert all("committed" in v for v in out["docs"].values())
    assert (tmp_path / "palli" / "MY_STORY.md").read_text(encoding="utf-8") == "I held the week."

    # second run same week ⇒ skipped, no new commits
    out2 = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate, now=now + 3600,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out2 == {"skipped": "already_this_week"}


@pytest.mark.asyncio
async def test_ritual_rejected_doc_never_blocks_others(tmp_path, monkeypatch):
    import time

    import ai.narrative_self as ns

    _enable_flag(monkeypatch)
    for _doc in ns.NARRATIVE_DOCS:
        ns.commit_revision(
            revision=ns.Revision(doc=_doc, new_text="seeded first line\n", reasoning="first authorship seed"),
            repo_root=tmp_path / "palli",
        )

    async def fake_evidence(**kw):
        return ns.Evidence(text="record", window_days=7)

    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")

    async def author(prompt):
        return "REASONING: r\n===DOCUMENT===\nnew text"

    async def gate(prompt):
        # reject the spine, approve the stories
        if "WHAT_I_CARE_ABOUT.md" in prompt:
            return '{"approve": false, "reason": "not earned by the record"}'
        return '{"approve": true, "reason": "ok"}'

    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate, now=time.time(),
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out["docs"]["WHAT_I_CARE_ABOUT.md"] == {"rejected": "not earned by the record"}
    assert "committed" in out["docs"]["MY_STORY.md"]
    # the rejected/errored doc keeps its prior text — nothing was written
    assert (tmp_path / "palli" / "WHAT_I_CARE_ABOUT.md").read_text(
        encoding="utf-8"
    ) == "seeded first line\n"
    assert (tmp_path / "palli" / "MY_STORY.md").exists()


@pytest.mark.asyncio
async def test_ritual_gate_error_skips_whole_doc(tmp_path, monkeypatch):
    import time

    import ai.narrative_self as ns

    _enable_flag(monkeypatch)
    for _doc in ns.NARRATIVE_DOCS:
        ns.commit_revision(
            revision=ns.Revision(doc=_doc, new_text="seeded first line\n", reasoning="first authorship seed"),
            repo_root=tmp_path / "palli",
        )

    async def fake_evidence(**kw):
        return ns.Evidence(text="record", window_days=7)

    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")

    async def author(prompt):
        return "REASONING: r\n===DOCUMENT===\nnew text"

    async def gate(prompt):
        return "never json"

    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate, now=time.time(),
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert all(v.get("error", "").startswith("gate_error") for v in out["docs"].values())
    # ERROR ≠ approve: nothing landed — the pre-seeded receipt text is untouched
    # and the other docs were never created.
    assert (tmp_path / "palli" / "MY_STORY.md").read_text(encoding="utf-8") == "seeded first line\n"
    # the rejected/errored doc keeps its prior text — nothing was written
    assert (tmp_path / "palli" / "WHAT_I_CARE_ABOUT.md").read_text(
        encoding="utf-8"
    ) == "seeded first line\n"


@pytest.mark.asyncio
async def test_ritual_never_raises(tmp_path, monkeypatch):
    import time

    import ai.narrative_self as ns

    _enable_flag(monkeypatch)
    for _doc in ns.NARRATIVE_DOCS:
        ns.commit_revision(
            revision=ns.Revision(doc=_doc, new_text="seeded first line\n", reasoning="first authorship seed"),
            repo_root=tmp_path / "palli",
        )

    async def boom(**kw):
        raise RuntimeError("evidence exploded")

    monkeypatch.setattr(ns, "gather_evidence", boom)
    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=_approving_llm, now=time.time(),
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert "error" in out


# ── strip_frontmatter — ONE shared implementation (fix wave A) ───────────


def test_strip_frontmatter_removes_real_frontmatter():
    import ai.narrative_self as ns

    text = "---\ntitle: x\nnote: has --- inside prose\n---\nbody line one.\nbody line two."
    assert ns.strip_frontmatter(text) == "body line one.\nbody line two."


def test_strip_frontmatter_preserves_thematic_break_prose():
    """Review finding #5: a doc whose first line is a Markdown '---' rule is
    PROSE, not frontmatter — the old strip silently dropped everything up to
    the next rule (out of the prompt band AND out of the ratio rail's view)."""
    import ai.narrative_self as ns

    text = "---\n\n# My Story\n\nPart one.\n\n---\n\nPart two.\n"
    assert ns.strip_frontmatter(text) == text.strip()


def test_strip_frontmatter_edge_shapes():
    import ai.narrative_self as ns

    # one rule, never closed — untouched
    text = "---\njust prose after one rule, no closing rule"
    assert ns.strip_frontmatter(text) == text.strip()
    assert ns.strip_frontmatter("") == ""
    assert ns.strip_frontmatter("plain body, no rules") == "plain body, no rules"


def test_read_document_preserves_thematic_break_content(tmp_path):
    import ai.narrative_self as ns

    doc = tmp_path / "MY_STORY.md"
    doc.write_text("---\n\nPart one.\n\n---\n\nPart two.\n", encoding="utf-8")
    body = ns.read_document("MY_STORY.md", repo_root=tmp_path)
    assert "Part one." in body and "Part two." in body


def test_appraiser_and_narrative_share_one_strip(tmp_path, monkeypatch):
    """Cleanup finding #10: duplication made the 'two consumers can never
    disagree' promise unenforceable — both must call the SAME function."""
    import ai.narrative_self as ns
    import ai.stake_appraiser as sa
    from core.config import config

    palli = tmp_path / "palli"
    palli.mkdir(parents=True)
    (palli / "WHAT_I_CARE_ABOUT.md").write_text(
        "---\ntitle: t\n---\nI care about the workshop.", encoding="utf-8"
    )
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(tmp_path), raising=False)
    assert sa._load_care_spine() == "I care about the workshop."
    assert ns.read_document("WHAT_I_CARE_ABOUT.md", repo_root=palli) == "I care about the workshop."

    calls = {"n": 0}
    real = ns.strip_frontmatter

    def spy(text):
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr(ns, "strip_frontmatter", spy)
    sa._load_care_spine()
    assert calls["n"] >= 1  # the appraiser genuinely routes through the shared helper


# ── repo hygiene: the template seed ships, her files stay private (fix B) ─


def test_gitignore_ships_template_and_keeps_her_files_private():
    """Fix wave B (review #1): the tracked template is the fresh-checkout
    floor; her live spine + trail stay out of the code repo. check-ignore
    exit codes: 0 = ignored, 1 = not ignored."""
    import subprocess

    repo = Path(__file__).resolve().parents[2]

    def ignored(rel: str) -> bool:
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", rel],
                cwd=repo, capture_output=True, timeout=30,
            ).returncode
            == 0
        )

    assert not ignored("identity/palli/WHAT_I_CARE_ABOUT.template.md")  # ships
    assert ignored("identity/palli/WHAT_I_CARE_ABOUT.md")               # hers
    assert ignored("identity/palli/MY_STORY.md")                        # hers
    assert ignored("identity/palli/.git")                               # her trail
    tracked = subprocess.run(
        ["git", "ls-files", "identity/palli/"],
        cwd=repo, capture_output=True, text=True, timeout=30,
    ).stdout.split()
    assert tracked == ["identity/palli/WHAT_I_CARE_ABOUT.template.md"]


# ── first-authorship gate in code (fix wave C, review #6/#7) ─────────────


def test_first_authorship_done_states(tmp_path):
    import subprocess

    import ai.narrative_self as ns

    root = tmp_path / "palli"
    assert ns.first_authorship_done(root) is False          # no directory
    root.mkdir(parents=True)
    assert ns.first_authorship_done(root) is False          # no repo
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    assert ns.first_authorship_done(root) is False          # unborn HEAD — apply never ran
    ns.commit_revision(
        revision=ns.Revision(doc="MY_STORY.md", new_text="first words\n", reasoning="first"),
        repo_root=root,
    )
    assert ns.first_authorship_done(root) is True           # the receipt: a real commit


@pytest.mark.asyncio
async def test_ritual_refuses_before_first_authorship(tmp_path, monkeypatch):
    """Review #6: flipping the flag before first authorship must NOT author
    foundational docs from a 7-day window — the ritual waits, loudly, and
    does not burn the week's marker."""
    import time

    import ai.narrative_self as ns

    _enable_flag(monkeypatch)

    async def must_not_run(prompt):
        raise AssertionError("LLM reached before first authorship")

    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=must_not_run, now=time.time(),
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out == {"skipped": "first_authorship_pending"}
    assert not (tmp_path / ".state.json").exists()


# ── weekly marker: fail-closed + release-on-empty (fix wave E, #3/#4) ────


def _seed_receipt(ns, tmp_path):
    for _doc in ns.NARRATIVE_DOCS:
        ns.commit_revision(
            revision=ns.Revision(doc=_doc, new_text="seeded first line\n", reasoning="first authorship seed"),
            repo_root=tmp_path / "palli",
        )


@pytest.mark.asyncio
async def test_ritual_unwritable_marker_fails_closed(tmp_path, monkeypatch):
    """Review #3: if the week marker cannot persist, the ritual must NOT run
    (it would otherwise re-author her documents EVERY night) — loud error,
    zero LLM work."""
    import time

    import ai.narrative_self as ns

    _enable_flag(monkeypatch)
    _seed_receipt(ns, tmp_path)

    async def must_not_run(prompt):
        raise AssertionError("LLM reached with an unwritable marker")

    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the state DIR should be", encoding="utf-8")
    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=must_not_run, now=time.time(),
        state_path=blocker / ".state.json",  # parent is a FILE ⇒ persist fails
        repo_root=tmp_path / "palli",
    )
    assert out == {"error": "week_marker_unwritable"}


@pytest.mark.asyncio
async def test_ritual_releases_week_when_nothing_authored(tmp_path, monkeypatch):
    """Review #4: a transient brain outage on the week-boundary tick must not
    burn the whole week — a pass that authored NOTHING releases its claim so
    the next night retries; a pass that did real work (commits OR rejections)
    keeps it."""
    import time

    import ai.narrative_self as ns

    _enable_flag(monkeypatch)
    _seed_receipt(ns, tmp_path)

    async def fake_evidence(**kw):
        return ns.Evidence(text="record", window_days=7)

    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")

    async def outage(prompt):
        return ""  # empty brain output ⇒ unparseable ⇒ zero revisions

    # Pinned mid-week (Wed 12:00 local): these rituals compare week keys across
    # `now + 3600` — on the real clock that crosses into a new ISO week every
    # Sunday 23:00–24:00 local (red on main 2026-08-16 23:20; the 3ecd020 class).
    now = _MIDWEEK_NOW
    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=outage, now=now,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out["error"] == "authorship_empty"

    # claim released: the SAME week retries and succeeds the next night
    async def author(prompt):
        return "REASONING: recovered\n===DOCUMENT===\nI held the week."

    async def gate_ok(prompt):
        return '{"approve": true, "reason": "ok"}'

    out2 = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate_ok, now=now + 3600,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out2.get("week") == ns.week_key_for(now)
    assert "committed" in out2["docs"]["MY_STORY.md"]
    # ...and the successful pass keeps its claim
    out3 = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate_ok, now=now + 7200,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out3 == {"skipped": "already_this_week"}

    # a pass that only REJECTED still did real gate work — claim kept too
    async def gate_reject(prompt):
        return '{"approve": false, "reason": "not earned"}'

    week2 = now + 8 * 86400  # always a different ISO week
    out4 = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate_reject, now=week2,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert set(out4["docs"]) == set(ns.NARRATIVE_DOCS)
    assert all("rejected" in v for v in out4["docs"].values())
    out5 = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate_reject, now=week2 + 3600,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out5 == {"skipped": "already_this_week"}


@pytest.mark.asyncio
async def test_ritual_shared_step_failure_releases_week(tmp_path, monkeypatch):
    """Review #4, outer-exception path: an explosion before the doc loop also
    releases the claim (the never-raises contract still returns {'error'})."""
    import time

    import ai.narrative_self as ns

    _enable_flag(monkeypatch)
    _seed_receipt(ns, tmp_path)

    async def boom(**kw):
        raise RuntimeError("evidence exploded")

    monkeypatch.setattr(ns, "gather_evidence", boom)
    # Pinned mid-week (Wed 12:00 local): these rituals compare week keys across
    # `now + 3600` — on the real clock that crosses into a new ISO week every
    # Sunday 23:00–24:00 local (red on main 2026-08-16 23:20; the 3ecd020 class).
    now = _MIDWEEK_NOW
    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=_approving_llm, now=now,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert "error" in out
    assert ns._read_week_marker(tmp_path / ".state.json") != ns.week_key_for(now)


# ── gist trim math (fix wave G, cleanup #12) ─────────────────────────────


def test_gists_section_budget_math():
    """The trim loops track running length now (linear, not O(n²) re-joins).
    Pin the behavior: oldest compress to first sentence first, newest stay
    whole, chronological order preserved, budget honored."""
    from types import SimpleNamespace

    import ai.narrative_self as ns

    entries_newest_first = [
        SimpleNamespace(day=f"2026-07-{15 - i:02d}", text="Sentence one is here. " + "x" * 120)
        for i in range(6)
    ]
    out = ns._gists_section(entries_newest_first, budget=420)
    lines = out.splitlines()
    assert len(out) <= 420
    assert lines[0].endswith("Sentence one is here.")   # oldest compressed
    assert lines[-1].startswith("- 2026-07-15")          # newest last, kept
    assert "x" * 120 in lines[-1]                        # ...and whole
    # degenerate budget: drops oldest but never returns empty
    tiny = ns._gists_section(entries_newest_first, budget=40)
    assert tiny.splitlines()
    assert "2026-07-15" in tiny.splitlines()[-1]


# ── the Monday defect (2026-08-22): MY_STORY.md never revised by the ritual ─
# The CLI author kept omitting the ===DOCUMENT=== line (2 tries each Monday
# since 08-03; still her 07-18 first draft). Two layers: a forgiving parse
# when the shape is otherwise unambiguous, and a retry that RESTATES the
# contract instead of repeating the same prompt.

def test_parse_authoring_forgives_a_missing_sentinel_when_the_shape_is_clear():
    import ai.narrative_self as ns
    assert ns._parse_authoring("REASONING: the week added a line\n\n# My story\n\nI held the week.") == (
        "the week added a line", "# My story\n\nI held the week.")
    # unchanged contracts
    assert ns._parse_authoring("no sentinel") is None
    assert ns._parse_authoring("REASONING: why") is None                 # no document at all
    assert ns._parse_authoring("REASONING: why\n===DOCUMENT===\nbody") == ("why", "body")


@pytest.mark.asyncio
async def test_author_revisions_retry_restates_the_contract():
    import ai.narrative_self as ns
    prompts_seen = []

    async def fake_llm(prompt):
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            return "Here is my revised story, hope this works!"   # no REASONING, no sentinel
        return "REASONING: second try\n===DOCUMENT===\nI held the week."

    ev = ns.Evidence(text="## The days themselves\n- 2026-08-17: shipped", window_days=7)
    revs = await ns.author_revisions(evidence=ev, current_docs={"MY_STORY.md": "old text\nline two"},
                                     llm_call=fake_llm, docs=("MY_STORY.md",))
    assert [r.doc for r in revs] == ["MY_STORY.md"] and revs[0].new_text == "I held the week."
    assert len(prompts_seen) == 2 and prompts_seen[1] != prompts_seen[0]
    assert "===DOCUMENT===" in prompts_seen[1][len(prompts_seen[0]):]   # the restatement is APPENDED
    assert "previous" in prompts_seen[1].lower() or "missing" in prompts_seen[1].lower()


# ── the silent revision-ratio rejections (2026-08-24 04:00, the size-cap
# disease's twin) ─────────────────────────────────────────────────────────
# W35's ritual authored all four docs; three vanished without a line of log:
# the ≤50%-lines rail rejects BEFORE the gate LLM and nothing records it,
# and the author prompt never states the rule mechanically. Laws pinned:
# every per-doc outcome is LOGGED; a ratio rejection gets ONE re-author with
# the numbers fed back; still-over is rejected as before; the rail itself
# is unchanged.

_SEED_SIX = "line one\nline two\nline three\nline four\nline five\nline six\n"


def _seed_docs(ns, tmp_path, *, revised: bool = True):
    """Seed every page. revised=True (the default) gives each page TWO
    commits — a birth and one revision — so the ratio rail applies, as it
    does to an established page. revised=False leaves the birth commit
    alone: a never-revised page, which the rail exempts once (owner call
    2026-09-28)."""
    for _doc in ns.NARRATIVE_DOCS:
        if revised:
            ns.commit_revision(
                revision=ns.Revision(doc=_doc, new_text="line one\nline two\nline three\n", reasoning="birth"),
                repo_root=tmp_path / "palli",
            )
        ns.commit_revision(
            revision=ns.Revision(doc=_doc, new_text=_SEED_SIX, reasoning="seed"),
            repo_root=tmp_path / "palli",
        )


def test_never_revised_counts_the_pages_commits(tmp_path):
    import ai.narrative_self as ns
    doc = ns.NARRATIVE_DOCS[0]
    ns.commit_revision(revision=ns.Revision(doc=doc, new_text="born\n", reasoning="birth"), repo_root=tmp_path / "palli")
    assert ns.never_revised(doc, tmp_path / "palli") is True
    ns.commit_revision(revision=ns.Revision(doc=doc, new_text="born\nand revised\n", reasoning="rev"), repo_root=tmp_path / "palli")
    assert ns.never_revised(doc, tmp_path / "palli") is False
    assert ns.never_revised(doc, tmp_path / "no-such-repo") is False  # fails closed: the rail stays


@pytest.mark.asyncio
async def test_first_revision_of_a_never_revised_page_may_rewrite_it_whole(tmp_path, monkeypatch, caplog):
    import logging
    import ai.narrative_self as ns
    _enable_flag(monkeypatch)
    _seed_docs(ns, tmp_path, revised=False)   # every page sits at its birth commit, like MY_STORY did for ten weeks

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record\n- a week", window_days=7)
    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")
    calls = []

    async def author(prompt):
        calls.append(prompt)
        return "REASONING: rewrote\n===DOCUMENT===\nall\nnew\nwords\nentirely\nhere\nnow"

    async def gate(prompt):
        return '{"approve": true, "reason": "faithful"}'

    with caplog.at_level(logging.INFO, logger="ai.narrative_self"):
        out = await ns.run_weekly_ritual(
            owner_id=1, llm_call=author, gate_llm_call=gate, now=_MIDWEEK_NOW,
            state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
        )
    assert all("committed" in v for v in out["docs"].values()), out["docs"]
    assert len(calls) == len(ns.NARRATIVE_DOCS)          # no fed-back retry: the rail was lifted, not fought
    assert any("has never been revised" in r.getMessage() for r in caplog.records)
    assert not any("revision_ratio" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_ratio_rejection_retries_once_with_the_numbers_fed_back(tmp_path, monkeypatch, caplog):
    import logging
    import ai.narrative_self as ns
    _enable_flag(monkeypatch)
    _seed_docs(ns, tmp_path)

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record\n- a week", window_days=7)
    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")

    calls = []

    async def author(prompt):
        calls.append(prompt)
        if "PREVIOUS REVISION WAS DECLINED" in prompt:
            # the fed-back retry keeps most lines verbatim
            return ("REASONING: tightened\n===DOCUMENT===\n"
                    "line one\nline two\nline three\nline four\nline five\na new closing line")
        return "REASONING: rewrote\n===DOCUMENT===\nall\nnew\nwords\nentirely\nhere\nnow"

    async def gate(prompt):
        return '{"approve": true, "reason": "faithful"}'

    with caplog.at_level(logging.INFO, logger="ai.narrative_self"):
        out = await ns.run_weekly_ritual(
            owner_id=1, llm_call=author, gate_llm_call=gate, now=_MIDWEEK_NOW,
            state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
        )
    assert all("committed" in v for v in out["docs"].values()), out["docs"]
    retry_prompts = [c for c in calls if "PREVIOUS REVISION WAS DECLINED" in c]
    assert len(retry_prompts) == len(ns.NARRATIVE_DOCS)      # one fed-back retry per doc
    assert "%" in retry_prompts[0] and "word for word" in retry_prompts[0]
    assert any("revision_ratio" in r.getMessage() and "retrying" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_ratio_still_over_on_retry_is_rejected_and_logged(tmp_path, monkeypatch, caplog):
    import logging
    import ai.narrative_self as ns
    _enable_flag(monkeypatch)
    _seed_docs(ns, tmp_path)

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record\n- a week", window_days=7)
    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")
    calls = []

    async def author(prompt):
        calls.append(prompt)
        return "REASONING: rewrote again\n===DOCUMENT===\nstill\nall\nnew\nwords\nhere\nnow"

    async def gate(prompt):
        raise AssertionError("the ratio rail is code — the gate LLM must not run")

    with caplog.at_level(logging.INFO, logger="ai.narrative_self"):
        out = await ns.run_weekly_ritual(
            owner_id=1, llm_call=author, gate_llm_call=gate, now=_MIDWEEK_NOW,
            state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
        )
    for doc, v in out["docs"].items():
        assert str(v.get("rejected", "")).startswith("revision_ratio"), (doc, v)
    assert len(calls) == 2 * len(ns.NARRATIVE_DOCS)          # first + one fed-back retry, never a loop
    assert any("rejected" in r.getMessage() and "revision_ratio" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_not_authored_docs_are_logged_not_silent(tmp_path, monkeypatch, caplog):
    import logging
    import ai.narrative_self as ns
    _enable_flag(monkeypatch)
    _seed_docs(ns, tmp_path)

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record\n- a week", window_days=7)
    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")

    docs_seen = []

    async def author(prompt):
        # VOICE parses; every other doc returns garbage twice → not_authored
        docs_seen.append(prompt)
        if "VOICE_IDENTITY" in prompt:
            return "REASONING: small\n===DOCUMENT===\n" + _SEED_SIX + "one more line"
        return "no sentinel here, sorry"

    async def gate(prompt):
        return '{"approve": true, "reason": "faithful"}'

    with caplog.at_level(logging.INFO, logger="ai.narrative_self"):
        out = await ns.run_weekly_ritual(
            owner_id=1, llm_call=author, gate_llm_call=gate, now=_MIDWEEK_NOW,
            state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
        )
    not_authored = [d for d, v in out["docs"].items() if v.get("error") == "not_authored"]
    assert len(not_authored) == len(ns.NARRATIVE_DOCS) - 1
    for doc in not_authored:
        assert any(doc in r.getMessage() and "not_authored" in r.getMessage() for r in caplog.records), doc


def test_the_author_prompts_state_the_ratio_rule_mechanically():
    """The size-cap lesson (08-22): a rule the writer is never told is a
    silent shredder. Every weekly author prompt states the rail in
    countable terms."""
    import pathlib
    for name in ("narrative_self_my_story", "narrative_self_story_of_us",
                 "narrative_self_care_spine", "narrative_self_voice_identity"):
        text = pathlib.Path(f"prompts/{name}.txt").read_text(encoding="utf-8")
        assert "more than half" in text and "word for word" in text, name


@pytest.mark.asyncio
async def test_size_cap_rejection_retries_once_with_the_real_count_fed_back(tmp_path, monkeypatch, caplog):
    """The original disease, cured the way its twin was.

    Her prompts already say "strictly under 2,400 characters" and the model
    still overshoots — measured overages of 2, 12, 57, 211, 338 and 834
    characters. Every one of those drafts was discarded whole, with no retry and
    no word to the author, which is why MY_STORY.md carried ONE commit in the
    organ's entire life: the first-authorship script of 2026-07-18.

    The ratio rail's identical failure was fixed on 2026-08-24 by feeding the
    real numbers back for one re-author — and that fix's own comment calls this
    one "the size-cap disease". This is the same cure for the original.
    """
    import logging
    import ai.narrative_self as ns
    _enable_flag(monkeypatch)
    _seed_docs(ns, tmp_path)

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record\n- a week", window_days=7)
    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")

    # 57 characters over the SMALLEST ceiling — the real VOICE_IDENTITY.md
    # rejection of 2026-09-21. Derived, never hardcoded: a cap change must not
    # quietly turn this test into one that exercises nothing.
    _head = "line one\nline two\nline three\nline four\nline five\n"
    _cap = max(ns.doc_size_cap(d) for d in ns.NARRATIVE_DOCS)
    too_long = _head + ("x" * (_cap + 57 - len(_head)))
    calls = []

    async def author(prompt):
        calls.append(prompt)
        if "PREVIOUS REVISION WAS DECLINED" in prompt:
            return ("REASONING: cut it back\n===DOCUMENT===\n"
                    "line one\nline two\nline three\nline four\nline five\na short closing line")
        return "REASONING: too much\n===DOCUMENT===\n" + too_long

    async def gate(prompt):
        return '{"approve": true, "reason": "faithful"}'

    with caplog.at_level(logging.INFO, logger="ai.narrative_self"):
        out = await ns.run_weekly_ritual(
            owner_id=1, llm_call=author, gate_llm_call=gate, now=_MIDWEEK_NOW,
            state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
        )

    assert all("committed" in v for v in out["docs"].values()), out["docs"]
    retries = [c for c in calls if "PREVIOUS REVISION WAS DECLINED" in c]
    assert len(retries) == len(ns.NARRATIVE_DOCS)
    # the author is told the real numbers, not just "too long" — and each page's
    # OWN ceiling (WHAT_I_CARE_ABOUT.md is 700, not 2400)
    for prompt in retries:
        doc = next(d for d in ns.DOC_SIZE_CAPS if d in prompt)
        assert str(ns.doc_size_cap(doc)) in prompt, (doc, prompt[:200])
        assert "characters over" in prompt
        assert "cut" in prompt.lower()
    assert any("size_cap" in r.getMessage() and "retrying" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_size_cap_still_over_on_retry_is_rejected_and_logged(tmp_path, monkeypatch, caplog):
    """One retry, not a loop. A second overshoot is still refused, out loud."""
    import logging
    import ai.narrative_self as ns
    _enable_flag(monkeypatch)
    _seed_docs(ns, tmp_path)

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record\n- a week", window_days=7)
    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")

    _head = "line one\nline two\nline three\nline four\nline five\n"
    _cap = max(ns.doc_size_cap(d) for d in ns.NARRATIVE_DOCS)
    too_long = _head + ("x" * (_cap + 150 - len(_head)))
    calls = []

    async def author(prompt):
        calls.append(prompt)
        return "REASONING: still too much\n===DOCUMENT===\n" + too_long

    async def gate(prompt):
        return '{"approve": true, "reason": "faithful"}'

    with caplog.at_level(logging.INFO, logger="ai.narrative_self"):
        out = await ns.run_weekly_ritual(
            owner_id=1, llm_call=author, gate_llm_call=gate, now=_MIDWEEK_NOW,
            state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
        )

    for doc, v in out["docs"].items():
        assert str(v.get("rejected", "")).startswith("size_cap"), (doc, v)
    assert len(calls) == len(ns.NARRATIVE_DOCS) * 2      # one author + one retry each
    assert any("rejected" in r.getMessage() and "size_cap" in r.getMessage()
               for r in caplog.records)


def test_every_page_is_told_the_same_ceiling_it_is_judged_by():
    """The author's instruction and the gate's rail must name ONE number.

    DEFECT §31 was a rail rejecting wholesale with no feedback; this is the
    other half of the same lesson. Her prompts say "strictly under N
    characters" and `DOC_SIZE_CAPS` enforces N. If someone raises one and not
    the other, she is told one limit and judged by a different one — and the
    only symptom is documents that quietly stop landing, which is exactly the
    failure that hid for two months.
    """
    import re
    from pathlib import Path
    import ai.narrative_self as ns

    prompts = Path(__file__).resolve().parents[2] / "prompts"
    # INTERESTS.md is authored by the passions prompt, not a _DOC_PROMPTS entry
    prompt_for = dict(ns._DOC_PROMPTS)
    prompt_for["INTERESTS.md"] = "passions_interests"

    checked = 0
    for doc, stem in prompt_for.items():
        text = (prompts / f"{stem}.txt").read_text(encoding="utf-8")
        stated = {int(m.replace(",", "")) for m in re.findall(r"([0-9][0-9,]{2,})\s+characters", text)}
        if not stated:
            continue                      # that prompt sets no character budget
        cap = ns.doc_size_cap(doc)
        assert stated == {cap}, (
            f"{stem}.txt tells her {sorted(stated)} characters but the gate enforces "
            f"{cap} for {doc} — she would be judged by a limit she was never given"
        )
        checked += 1

    assert checked >= 4, f"only {checked} prompts carried a character budget; the guard is not covering them"

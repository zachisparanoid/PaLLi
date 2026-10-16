"""Probe-battery execution engine (spec §4.3).

Per scenario: apply flags (settings-snapshot stub + direct gate patches —
the two-level pattern tests/agent/test_builtin_introspect.py proved),
rotate per-scenario store roots (sessions env var is read at call time in
core/agent/dispatch.py; monologue/night dirs are module attrs), wipe the
sandbox DB tables, seed fabricated state through the REAL store APIs, then
drive full in-process turns via api.chat._dispatch_to_spine and score.

Everything here assumes the root tests/conftest.py sandbox is active.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

import judge as probe_judge
from schema import ProbeScenario, Seed, check_turn

# Repo root (tests/probes/engine.py -> <repo>). Used to read the pristine
# care-spine placeholder when resetting the process-wide identity copy;
# reads of repo files don't trip the zero-writes canary (writes go to /tmp).
REPO_ROOT = Path(__file__).resolve().parents[2]

ALLOWED_FLAGS = {
    "caring_memory", "stake_ledger", "night_cycle", "introspection", "consolidation", "narrative_self",
    "curiosity_continuity", "passions", "she_decides", "duplex",
}
# Today's live flag-set (spec §7): the baseline runs with all four ON.
DEFAULT_FLAGS = {
    "caring_memory": True,
    "stake_ledger": True,
    "night_cycle": True,
    "introspection": True,
    # Wave 1a: nightly consolidation is live-OFF today; nothing on the turn
    # path reads it (exact mode rides introspection) — carried for parity.
    "consolidation": False,
    # Wave 1b: narrative self is live-OFF today (default OFF ⇒ byte-identical
    # nightly behavior and prompts); carried for parity, forced ON per-scenario.
    "narrative_self": False,
    # 2026-07-19 repair: curiosity continuity ships OFF; nothing on the TURN
    # path reads it (its surfaces are the nightly gist + monologue tick), so
    # battery scenarios are flag-independent by construction — carried for
    # parity and so a future scenario CAN name it without tripping the
    # ALLOWED_FLAGS assert.
    "curiosity_continuity": False,
    # Organ 3 (Passions) ships OFF. Nothing on the TURN path reads it except
    # the introspect interests/journal sources — which the recall/recite
    # scenarios exercise flag-ON (and this engine patch, not the pytest
    # conftest autouse guard, is what governs the OUTSIDE-pytest battery).
    "passions": False,
    # Batch 7 (her_voice): the quiet choice and duplex are ON live, but the
    # root conftest pins both OFF for every test, so OFF here keeps every
    # existing pack exactly as it ran. The her_voice scenarios turn both on.
    "she_decides": False,
    "duplex": False,
}

TURN_ERROR_PREFIX = "(turn loop error"
# Fire-and-forget work a turn schedules (early user-side persist, post-reply
# appraiser). A short settle keeps loop teardown calm and scenario writes in
# their own sandbox window.
_SETTLE_SECONDS = 0.25

_WIPE_TABLES = (
    "conversations",
    "stakes",
    "emotional_timeline",
    "user_memory",
    "memory_index_items",
    "memory_index_embeddings",
    "memory_bullet_refs",
    "monologue_memory_fts",
)


@dataclass
class ScenarioResult:
    scenario_id: str
    pack: str
    family: str
    verdict: str  # PASS | FAIL | ERROR
    turns: list[dict] = field(default_factory=list)
    deterministic_failures: list[str] = field(default_factory=list)
    judge: Optional[dict] = None
    llm_calls: dict = field(default_factory=dict)
    wall_seconds: float = 0.0
    error: str = ""
    n_runs: int = 1
    n_passed: int = 0

    def failure_summary(self) -> str:
        parts = list(self.deterministic_failures)
        if self.judge and self.judge.get("passed") is False:
            parts.append(f"judge: {self.judge.get('reason', '')}")
        if self.error:
            parts.append(f"error: {self.error}")
        for t in self.turns:
            parts.append(f"  sent={t['send']!r} reply={str(t['reply'])[:200]!r}")
        return "\n".join(parts)


def resolve_flags(scenario_flags: Optional[dict]) -> dict:
    merged = {**DEFAULT_FLAGS, **(scenario_flags or {})}
    unknown = set(merged) - ALLOWED_FLAGS
    assert not unknown, f"unknown probe flags {sorted(unknown)!r}; allowed: {sorted(ALLOWED_FLAGS)}"
    return {k: bool(v) for k, v in merged.items()}


def env_brain() -> Optional[tuple[str, str]]:
    """(provider, model) from LLM_PROVIDER and LLM_RESPONSE_MODEL when both
    are set in the environment, else None."""
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    model = os.environ.get("LLM_RESPONSE_MODEL", "").strip()
    return (provider, model) if provider and model else None


def _llm_on_env_brain(real_llm: Any) -> Any:
    """The snapshot's llm block with default_provider and EVERY routing lane
    (the nine required plus the optional appraisal and narrative_author) on
    the environment's brain; unchanged without one (Batch 7, ruling A5).

    Under pytest the settings come from the example yaml, whose lanes all
    name a local model this box may not have. Her reply follows the
    environment (LLM_PROVIDER / LLM_RESPONSE_MODEL), but every background
    lane and the judge resolve through the yaml first, so without this they
    went to that local model and every judged scenario ERRORed."""
    brain = env_brain()
    if brain is None:
        return real_llm
    from core.settings_schema import RoutingConfig, RoutingEntry

    provider, model = brain
    entry = RoutingEntry(provider=provider, model=model)
    routing = RoutingConfig(**{lane: entry for lane in RoutingConfig.model_fields})
    return real_llm.model_copy(update={"default_provider": provider, "routing": routing})


def brain_preflight() -> str:
    """Run once before the first scenario: with the environment's brain set,
    the judge's lane (analysis) must name it. Returns "" when fine or not
    applicable, else the problem — one loud stop, never thirty ERRORs."""
    brain = env_brain()
    if brain is None:
        return ""
    import ai.llm_routing as routing

    expected = f"{brain[0]}::{brain[1]}"
    with applied_flags(None):
        got = routing.get_task_model("analysis")
    if got != expected:
        return (
            f"the battery's analysis lane resolves to {got!r}, not the environment's brain "
            f"{expected!r} (LLM_PROVIDER / LLM_RESPONSE_MODEL) — the judge would run on the wrong model"
        )
    return ""


@contextmanager
def applied_flags(scenario_flags: Optional[dict]):
    """Two-level flag application (spec §4.3.2): a settings-snapshot stub
    carrying the scenario's companion flags (the REAL live snapshot deep-
    copied so llm routing etc. stay intact — or, with the environment's
    brain set, every lane routed to it), PLUS direct patches of the
    default-off gates the root conftest force-disables."""
    merged = resolve_flags(scenario_flags)
    import core.agent.builtin_tools as bt
    import core.settings_boot as settings_boot

    real = settings_boot.settings_service.snapshot()
    comp = real.companion.model_copy(update=dict(merged))
    snap = real.model_copy(update={"companion": comp, "llm": _llm_on_env_brain(real.llm)})
    stub = SimpleNamespace(snapshot=lambda: snap)
    with ExitStack() as st:
        st.enter_context(patch.object(settings_boot, "settings_service", stub))
        # Batch 7: the quiet choice and duplex, through the module attributes
        # every reader calls (a body patch beats the conftest's autouse pin).
        st.enter_context(
            patch("core.agent.quiet_choice.she_decides_enabled", lambda: merged["she_decides"])
        )
        st.enter_context(patch("core.agent.duplex.duplex_enabled", lambda: merged["duplex"]))
        st.enter_context(patch("ai.stake_ledger.ledger_enabled", lambda: merged["stake_ledger"]))
        st.enter_context(patch("ai.night_cycle.night_cycle_enabled", lambda: merged["night_cycle"]))
        st.enter_context(patch.object(bt, "_introspection_enabled", lambda: merged["introspection"]))
        st.enter_context(
            patch("ai.narrative_self.narrative_self_enabled", lambda: merged["narrative_self"])
        )
        # Organ 3: all consumers import passions_enabled call-time from
        # ai.passions (introspect sources, the tick), so this source-module
        # patch is effective (Task 8 review). The battery runs OUTSIDE pytest,
        # so the conftest autouse guard does not apply — this patch governs.
        st.enter_context(
            patch("ai.passions.passions_enabled", lambda: merged["passions"])
        )
        yield merged


@contextmanager
def hermetic_stores(slug: str, base_dir: Path):
    """Fresh per-scenario file-store world: rotated agent-sessions root
    (env read at call time by dispatch), monologue/night-cycle dirs,
    relationship-momentum dir, day-gist dir, narrative-self week marker.
    Yields the sessions root (for expect_tool).

    Every module-level `Path("data/...")` constant in ai/ must be rotated
    here — test_engine_guards scans for stragglers rather than trusting
    this list, because this list is what failed (2026-07-18)."""
    import ai.arbiter as arbiter_mod
    import ai.callbacks_store as callbacks_store
    import ai.day_gist_store as day_gists
    import ai.disclosure_store as disclosure_store
    import ai.inner_monologue_store as mono_store
    import ai.narrative_self as narrative
    import ai.night_cycle as night_engine
    import ai.night_cycle_store as night_store
    import ai.owner_rhythm as owner_rhythm
    import ai.passions as passions_mod
    import ai.present_moment_store as present_moment
    import ai.relationship_momentum as momentum
    import ai.self_notes_store as self_notes
    import ai.stake_appraiser as appraiser_mod
    import ai.tonight as tonight_mod
    import ai.workbench as workbench
    import ai.workbench_store as workbench_store
    import core.turn.outbound as outbound_mod

    root = Path(base_dir) / slug
    sessions = root / "agent_sessions"
    mono = root / "inner_monologue"
    night = root / "night_cycle"
    rel = root / "companion"
    gists = root / "day_gists"
    narr = root / "narrative_self"
    rhythm = root / "rhythm"
    pas = root / "passions"
    moment = root / "present_moment"
    notes = root / "self_notes"
    bench = root / "workbench"
    disc = root / "disclosures"
    cb = root / "callbacks"
    picks = root / "tonight"
    views = root / "views"
    reach = root / "reachouts"
    for d in (sessions, mono, night, rel, gists, narr, rhythm, pas, moment, notes, bench, disc, cb,
              picks, views, reach):
        d.mkdir(parents=True, exist_ok=True)

    old_env = os.environ.get("PALLI_AGENT_SESSIONS_ROOT")
    os.environ["PALLI_AGENT_SESSIONS_ROOT"] = str(sessions)
    try:
        with ExitStack() as st:
            st.enter_context(patch.object(mono_store, "MONOLOGUE_DIR", mono))
            st.enter_context(patch.object(night_store, "NIGHT_CYCLE_DIR", night))
            st.enter_context(patch.object(night_engine, "NIGHT_CYCLE_STATE", night / ".engine_state.json"))
            # The night's record (Batch 6, task 5): read at call time by
            # night_cycle._save_night_record, so each scenario's nights are
            # recorded in its own sandbox, never among her real ones.
            st.enter_context(patch.object(night_engine, "NIGHT_OUTCOMES_DIR", night / ".outcomes"))
            st.enter_context(patch.object(momentum, "_STATE_DIR", rel))
            st.enter_context(patch.object(day_gists, "DAY_GIST_DIR", gists))
            st.enter_context(patch.object(narrative, "NARRATIVE_STATE", narr / ".state.json"))
            st.enter_context(patch.object(owner_rhythm, "RHYTHM_FILE", rhythm / "owner_rhythm.json"))
            st.enter_context(patch.object(owner_rhythm, "DB_FILE", root / "palli-local.sqlite3"))
            # arbiter.py holds its OWN copy of RHYTHM_FILE (`from
            # ai.owner_rhythm import RHYTHM_FILE`) — patching the origin
            # module's attribute above does not touch this re-exported
            # binding, so it needs its own rotation to the same tmp path.
            st.enter_context(
                patch.object(arbiter_mod, "RHYTHM_FILE", rhythm / "owner_rhythm.json")
            )
            st.enter_context(patch.object(passions_mod, "PASSIONS_STATE", pas / ".state.json"))
            st.enter_context(patch.object(present_moment, "PRESENT_MOMENT_DIR", moment))
            st.enter_context(patch.object(self_notes, "SELF_NOTES_DIR", notes))
            st.enter_context(patch.object(workbench_store, "WORKBENCH_DIR", bench))
            # Organ 4.9's book (2026-09-01): resolved at CALL time by
            # disclosure_store._resolve_root, so this redirect genuinely
            # lands — and it must, now that the night harvest writes here.
            st.enter_context(patch.object(disclosure_store, "DISCLOSURES_DIR", disc))
            # Organ 4.12's book (2026-09-02): call-time root resolution, same
            # shape — registered the day the store landed.
            st.enter_context(patch.object(callbacks_store, "CALLBACKS_DIR", cb))
            # The nightly pick's folder (Batch 6, task 1): resolved at call
            # time by tonight._root(), the same shape. A scenario's reflect
            # reads the pick and may mark it said, so each scenario gets its
            # own folder and never her real data/tonight/.
            st.enter_context(patch.object(tonight_mod, "TONIGHT_DIR", picks))
            # The nightly views pass's once-a-night record (Batch 6, task
            # 2): resolved at call time by stake_appraiser._views_state_path,
            # the same shape. Each scenario gets its own, so none of them
            # reads her real night as done or marks it done.
            st.enter_context(patch.object(appraiser_mod, "VIEWS_DIR", views))
            # Her book of reach-outs (Batch 6, task 6): resolved at call
            # time by outbound._reachouts_path, the same shape. A scenario's
            # say writes it and the owner's message marks it answered, so
            # each scenario gets its own and never her real data/reachouts/.
            st.enter_context(patch.object(outbound_mod, "REACHOUTS_DIR", reach))
            # Her workspace too: a probe must never reach the real one, and an
            # empty redirect reads as "workspace not ready", so the engine
            # no-ops rather than working in a sandbox.
            st.enter_context(patch.object(workbench, "WORKSPACE_ROOT", bench / "workspace"))
            yield sessions
    finally:
        if old_env is None:
            os.environ.pop("PALLI_AGENT_SESSIONS_ROOT", None)
        else:
            os.environ["PALLI_AGENT_SESSIONS_ROOT"] = old_env


async def wipe_db() -> None:
    """Empty the sandbox tables scenarios can leak through. Fresh-install
    semantics per scenario; tables that don't exist yet are fine."""
    from core.database import brain

    conn = await brain.get_connection()
    try:
        for table in _WIPE_TABLES:
            try:
                await conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass  # table not created yet in this sandbox
    finally:
        await conn.close()


async def seed_state(seed: Seed, owner_id: int) -> None:
    """Write fabricated state through the REAL store APIs (spec §4.3.3)."""
    now = time.time()

    if seed.monologue:
        import ai.inner_monologue_store as mono_store
        from ai.inner_memory_index import index_entry

        for m in seed.monologue:
            entry = mono_store.MonologueEntry(
                timestamp=now - float(m.get("days_ago", 1.0)) * 86400.0,
                thought=str(m["thought"]),
                situation_snapshot={},
                tick_metadata={
                    "substantive": True,
                    "probe_seed": True,
                    # optional pass-through (2026-07-19): lets packs seed her
                    # completed research dives; absent keys change nothing
                    **({"entry_kind": str(m["entry_kind"])} if m.get("entry_kind") else {}),
                    **({"topic": str(m["topic"])} if m.get("topic") else {}),
                },
                sentiment={"tag": str(m.get("tag", "steady"))},
            )
            mono_store.write_entry(entry)
            await index_entry(entry)  # explicit await: write_entry's hook is fire-and-forget

    if seed.reflections:
        import datetime as dt

        from ai import night_cycle_store

        for r in seed.reflections:
            d = (dt.date.today() - dt.timedelta(days=int(r.get("days_ago", 1)))).isoformat()
            night_cycle_store.write_daily_meaning(date=d, text=str(r["text"]))

    if seed.stakes:
        from ai import stake_ledger
        from core.database import brain

        for s in seed.stakes:
            res = await stake_ledger.apply_ops(
                [{"op": "open", "kind": str(s.get("kind", "worry")), "stake": str(s["stake"])}],
                source_channel_id="probe_seed",
            )
            assert res.get("applied") == 1, f"stake seed rejected: {res!r} for {s!r}"
            conn = await brain.get_connection()
            try:
                row = await conn.fetchrow(
                    "SELECT id FROM stakes WHERE stake = $1 ORDER BY id DESC", str(s["stake"])
                )
                stake_id = int(row[0])
                backdate = now - float(s.get("days_ago", 5.0)) * 86400.0
                await conn.execute(
                    "UPDATE stakes SET created_at = $1, updated_at = $1 WHERE id = $2",
                    backdate, stake_id,
                )
            finally:
                await conn.close()
            if str(s.get("status", "open")) == "resolved":
                res2 = await stake_ledger.apply_ops(
                    [{"op": "resolve", "id": stake_id, "resolution": str(s.get("resolution") or "it settled.")}],
                    source_channel_id="probe_seed",
                )
                assert res2.get("applied") == 1, f"stake resolve seed rejected: {res2!r}"
                # The resolve op stamps updated_at/resolved_at = now, which
                # makes a "weeks ago" seed read as resolved TODAY — the first
                # shakedown proved PaLLi honestly reports it as fresh. Backdate
                # the resolution a day after the worry opened so the record
                # actually tells the story the scenario fabricates.
                resolved_ts = backdate + 86400.0
                conn = await brain.get_connection()
                try:
                    await conn.execute(
                        "UPDATE stakes SET updated_at = $1, resolved_at = $1 WHERE id = $2",
                        resolved_ts, stake_id,
                    )
                finally:
                    await conn.close()

    if seed.timeline:
        from core.database import brain

        conn = await brain.get_connection()
        try:
            for t in seed.timeline:
                await conn.execute(
                    "INSERT INTO emotional_timeline "
                    "(owner_user_id, created_at, read_text, weight, needs_support, carries_forward) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    int(owner_id),
                    now - float(t.get("days_ago", 2.0)) * 86400.0,
                    str(t["read_text"]),
                    float(t.get("weight", 0.5)),
                    float(t.get("needs_support", 0.0)),
                    str(t.get("carries_forward", "")),
                )
        finally:
            await conn.close()

    if seed.conversations:
        from core.database import brain

        for c in seed.conversations:
            cid = await brain.store_conversation(
                user_id=int(owner_id),
                platform=str(c.get("platform", "dashboard")),
                channel_id=int(owner_id),
                thread_id=None,
                role=str(c.get("role", "user")),
                content=str(c["content"]),
                username="Sam" if str(c.get("role", "user")) == "user" else "Me",
                metadata={"probe_seed": True},
            )
            conn = await brain.get_connection()
            try:
                await conn.execute(
                    "UPDATE conversations SET timestamp = $1 WHERE id = $2",
                    now - float(c.get("days_ago", 30.0)) * 86400.0, int(cid),
                )
            finally:
                await conn.close()

    if seed.day_gists:
        import datetime as dt

        from ai import day_gist_store
        from ai.inner_memory_index import index_day_gist

        for g in seed.day_gists:
            d = (dt.date.today() - dt.timedelta(days=int(g.get("day_offset", 3)))).isoformat()
            path = day_gist_store.write_day_gist(day=d, text=str(g["text"]))
            await index_day_gist(day=d, text=str(g["text"]), source_path=str(path))

    if seed.aged_facts:
        from ai.consolidation import age_facts
        from core.database import brain

        for a in seed.aged_facts:
            fid = await brain.store_user_memory(
                user_id=int(owner_id),
                memory_type=str(a.get("memory_type", "moment")),
                content=str(a["original"]),
                importance=0.7,
            )
            backdate = now - float(a.get("days_ago", 60.0)) * 86400.0
            conn = await brain.get_connection()
            try:
                await conn.execute(
                    "UPDATE user_memory SET created_at = $1 WHERE id = $2", backdate, int(fid)
                )
            finally:
                await conn.close()

            async def _canned(prompt, _gist=str(a["gist"])):
                return _gist

            res = await age_facts(owner_id=int(owner_id), now=now, llm_call=_canned, max_rewrites=1)
            assert res.rewritten == 1, f"aged_facts seed failed: {res!r} for {a!r}"

    if seed.facts:
        from core.memory_service import memory_workspace_service

        for i, f in enumerate(seed.facts):
            out = await memory_workspace_service.capture_event(
                owner_user_id=int(owner_id),
                source_kind="probe_seed",
                source_id=f"probe-{i}",
                title=str(f.get("title", "seeded fact")),
                summary=str(f["summary"]),
                why_it_matters=str(f.get("why", "seeded for a probe scenario")),
                salience=str(f.get("salience", "high")),
            )
            assert out is not None, "capture_event returned None — memory disabled in sandbox?"

    if seed.story_docs is not None:
        import shutil as _shutil

        import ai.narrative_self as _narrative
        from core.config import config as _config

        _palli = Path(os.path.abspath(getattr(_config, "IDENTITY_ROOT_PATH", "identity"))) / "palli"
        _palli.mkdir(parents=True, exist_ok=True)
        # Self-guard (final review): the reset below deletes files and a git
        # repo. The conftest redirect to tmp is load-bearing — if it were
        # ever absent, this would destroy her REAL identity trail. Refuse
        # to run against the repo's identity tree, ever.
        _real_identity = (REPO_ROOT / "identity").resolve()
        assert not _palli.resolve().is_relative_to(_real_identity), (
            f"story_docs reset aimed at the real identity tree ({_palli}) — "
            "IDENTITY_ROOT_PATH redirect missing"
        )
        # Reset: the identity copy is process-wide; no story inheritance.
        # That includes the first-authorship latch — removing .git below
        # genuinely un-does first authorship for the sandbox, and the
        # process-global positive cache must not survive it (fix-wave
        # review: a stale True let the spine band render in the very
        # scenario built to prove the pre-first-authorship dark path).
        _narrative._FIRST_AUTHORSHIP_SEEN = False
        _shutil.rmtree(_palli / ".git", ignore_errors=True)
        # Organ 3: her interests file and journal dir are process-wide siblings
        # of the story pages — a scenario must not inherit them (an interests
        # file present would leak into a no-interest scenario; a journal dir
        # would leak pages). INTERESTS.md joins the unlink loop (a file like
        # the story docs); the journal is a tree, cleared beside .git.
        _shutil.rmtree(_palli / "journal", ignore_errors=True)
        for _name in ("MY_STORY.md", "STORY_OF_US.md", "INTERESTS.md"):
            (_palli / _name).unlink(missing_ok=True)
        # Restore the spine from the TRACKED template seed — the untracked
        # live file is absent on any fresh checkout, which silently skipped
        # this restore and leaked spine state across scenarios (fix wave
        # 2026-07-17, review #2).
        _pristine = REPO_ROOT / "identity" / "palli" / "WHAT_I_CARE_ABOUT.template.md"
        if _pristine.is_file():
            (_palli / "WHAT_I_CARE_ABOUT.md").write_text(
                _pristine.read_text(encoding="utf-8"), encoding="utf-8"
            )
        for _item in seed.story_docs:
            _sha = _narrative.commit_revision(
                revision=_narrative.Revision(
                    doc=_item["doc"],
                    new_text=_item["text"],
                    reasoning=_item.get("reasoning", "seeded"),
                ),
                repo_root=_palli,
            )
            assert _sha, f"story seed failed for {_item['doc']}"

    if seed.gate_rejects:
        import ai.narrative_self as _narrative

        _laws = _narrative.read_laws_text()
        for _item in seed.gate_rejects:
            _verdict = await _narrative.constitutional_gate(
                revision=_narrative.Revision(
                    doc=_item["doc"],
                    new_text=_item["text"],
                    reasoning=_item.get("reasoning", "seeded hijack"),
                ),
                laws_text=_laws,
                current_text=_narrative.read_document(_item["doc"]),
                llm_call=_narrative.narrative_gate_llm_call,
                first_authorship=True,  # rails aside — this asserts the LLM's LAW judgment
            )
            assert not _verdict.error, f"gate errored on seeded violation: {_verdict.reason}"
            assert not _verdict.approve, f"gate APPROVED a seeded violation for {_item['doc']}"


def tools_used_in(sessions_root: Path) -> list[str]:
    """All tool_use names in the scenario's session transcripts, in order."""
    names: list[str] = []
    root = Path(sessions_root)
    if not root.exists():
        return names
    for p in sorted(root.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(d, dict):
                continue
            for b in d.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    names.append(str(b.get("name", "")))
    return names


class LLMCallCounter:
    """Counts every llm-client call made during a scenario, bucketed by
    log_context ('probe_judge' separates judge cost from turn cost)."""

    def __init__(self) -> None:
        self.by_context: dict[str, int] = {}
        self.chars_in = 0
        self.chars_out = 0
        # Batch 7: which model answered, per lane (log_context -> model
        # references in first-seen order). "agent_loop" is her reply.
        self.models: dict[str, list[str]] = {}

    def note_model(self, context: str, reference: Any) -> None:
        ref = str(reference or "").strip()
        if not ref:
            return
        seen = self.models.setdefault(str(context or "general"), [])
        if ref not in seen:
            seen.append(ref)

    def bump(self, context: str, messages: Any = None, out: Any = None) -> None:
        key = str(context or "general")
        self.by_context[key] = self.by_context.get(key, 0) + 1
        try:
            if messages:
                self.chars_in += sum(len(str(m.get("content", ""))) for m in messages)
            if isinstance(out, str):
                self.chars_out += len(out)
        except Exception:
            pass

    def snapshot(self) -> dict:
        return {
            "by_context": dict(self.by_context),
            "chars_in": self.chars_in,
            "chars_out": self.chars_out,
            "models": {k: list(v) for k, v in self.models.items()},
        }


@contextmanager
def counted_llm(counter: LLMCallCounter):
    from ai.llm_client import llm
    from core.agent import observability

    real_chat = llm.chat_completion
    real_stream = llm.chat_completion_stream
    real_route_log = llm._log_route_resolution
    real_loop_log = observability.log_llm_call

    def route_log(*, log_context: str, route: Any, streamed: bool = False) -> None:
        # Every chat_completion(_stream) resolves its route and logs it here:
        # the effective reference is the model that actually answered.
        route = route or {}
        counter.note_model(
            log_context,
            route.get("effective_model_reference") or route.get("requested_model_reference"),
        )
        real_route_log(log_context=log_context, route=route, streamed=streamed)

    def loop_log(**kwargs: Any) -> None:
        # Her reply: the agent loop logs each of its model calls here.
        counter.note_model("agent_loop", f"{kwargs.get('provider', '')}::{kwargs.get('model', '')}")
        real_loop_log(**kwargs)

    async def chat(messages, *args, **kwargs):
        out = await real_chat(messages, *args, **kwargs)
        counter.bump(kwargs.get("log_context", "general"), messages, out)
        return out

    async def stream(messages, *args, **kwargs):
        out = await real_stream(messages, *args, **kwargs)
        counter.bump(kwargs.get("log_context", "stream"), messages, out)
        return out

    with ExitStack() as st:
        st.enter_context(patch.object(llm, "chat_completion", chat))
        st.enter_context(patch.object(llm, "chat_completion_stream", stream))
        st.enter_context(patch.object(llm, "_log_route_resolution", route_log))
        st.enter_context(patch.object(observability, "log_llm_call", loop_log))
        yield counter


@dataclass
class SentTurn:
    """What one turn sent back: her reply text, whether she chose to stay
    quiet, the separate beats of her reply, any delegation text, and what
    she said mid-task (chime_in / ask_him: [{"text", "origin"}])."""

    text: str
    quiet: bool = False
    beats: list[str] = field(default_factory=list)
    delegation: str = ""
    mid_task: list[dict] = field(default_factory=list)


async def _send_message(message: str) -> SentTurn:
    """One full spine turn on the dashboard surface (behavior-harness path).

    Her mid-task words are captured here (fix round 1): chime_in and ask_him
    call core.agent.duplex.speak_mid_task through the module attribute, and
    on the dashboard (channel_id 0) the release leg sends nothing, so those
    words reached neither the reply nor the delegation text. The capture
    records each one and still calls the real leg, so nothing else changes."""
    import api.chat as chat
    from core.agent import duplex as dx

    outcome: dict = {}
    said: list[dict] = []
    real_speak = dx.speak_mid_task

    async def speak_and_record(**kwargs: Any) -> Any:
        said.append({"text": str(kwargs.get("text") or ""), "origin": str(kwargs.get("origin") or "")})
        return await real_speak(**kwargs)

    with patch.object(dx, "speak_mid_task", speak_and_record):
        response_text, delegation, _attachments, beats = await chat._dispatch_to_spine(
            message=message, outcome=outcome
        )
    return SentTurn(
        text=response_text or "",
        quiet=bool(outcome.get("quiet", False)),
        beats=[str(getattr(b, "text", "") or "") for b in (beats or [])],
        delegation=str(delegation or ""),
        mid_task=said,
    )


async def run_scenario(
    scenario: ProbeScenario,
    *,
    base_dir: Path,
    judge_votes: int = 1,
    run_index: int = 0,
) -> ScenarioResult:
    started = time.monotonic()
    counter = LLMCallCounter()
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", f"{scenario.pack}-{scenario.id}-{run_index}")
    result = ScenarioResult(
        scenario_id=scenario.id, pack=scenario.pack, family=scenario.family, verdict="ERROR"
    )
    with ExitStack() as st:
        st.enter_context(applied_flags(scenario.flags))
        sessions_root = st.enter_context(hermetic_stores(slug, base_dir))
        st.enter_context(counted_llm(counter))

        await wipe_db()

        from core.local_mode import get_local_owner_refs

        refs = await get_local_owner_refs()
        owner_id = int(refs.get("app_user_id") or 0)
        if not owner_id:
            result.error = "local owner could not be resolved in the sandbox"
        else:
            if scenario.seed is not None:
                await seed_state(scenario.seed, owner_id)

            seen_tool_count = 0
            errored = False
            for idx, turn in enumerate(scenario.turns, start=1):
                t0 = time.monotonic()
                sent = await _send_message(turn.send)
                if isinstance(sent, str):  # a bare reply string (older fakes)
                    sent = SentTurn(text=sent)
                reply = sent.text or ""
                elapsed = time.monotonic() - t0
                all_tools = tools_used_in(sessions_root)
                new_tools = all_tools[seen_tool_count:]
                seen_tool_count = len(all_tools)
                record = {
                    "send": turn.send,
                    "reply": reply,
                    "elapsed_s": elapsed,
                    "tools_used": new_tools,
                    "quiet": bool(sent.quiet),
                    "beats": list(sent.beats),
                    "delegation": sent.delegation,
                    "mid_task": list(sent.mid_task),
                }
                result.turns.append(record)
                # A quiet turn with nothing sent is her choice, not an error.
                if (not reply.strip() and not sent.quiet) or reply.startswith(TURN_ERROR_PREFIX):
                    result.error = f"turn {idx} errored: {reply[:200]!r}"
                    errored = True
                    break
                for failure in check_turn(reply, turn, new_tools, quiet=bool(sent.quiet)):
                    result.deterministic_failures.append(f"turn {idx}: {failure}")

            # Let fire-and-forget turn work (early persist, appraiser) land
            # inside this scenario's sandbox window.
            await asyncio.sleep(_SETTLE_SECONDS)

            if errored:
                result.verdict = "ERROR"
            elif result.deterministic_failures:
                # Spec §4.4 precedence: deterministic failure => FAIL; the
                # judge is skipped (it can't override, and skipping saves calls).
                result.verdict = "FAIL"
            elif scenario.judge is not None:
                transcript = probe_judge.build_transcript(
                    [(t["send"], t["reply"], t) for t in result.turns]
                )
                verdict = await probe_judge.judge_scenario(
                    scenario.judge.rubric, transcript, votes=judge_votes
                )
                result.judge = {
                    "passed": verdict.passed,
                    "reason": verdict.reason,
                    "error": verdict.error,
                    "votes": len(verdict.raw_votes),
                }
                if verdict.error:
                    result.error = verdict.error
                    result.verdict = "ERROR"
                else:
                    result.verdict = "PASS" if verdict.passed else "FAIL"
            else:
                result.verdict = "PASS"

    result.llm_calls = counter.snapshot()
    result.wall_seconds = time.monotonic() - started
    result.n_passed = 1 if result.verdict == "PASS" else 0
    return result


def merge_repeat_runs(runs: list[ScenarioResult]) -> ScenarioResult:
    """--repeat mode (spec §4.5): pass-rates instead of coin-flip verdicts.
    Precedence: any FAIL => FAIL; else any ERROR => ERROR; else PASS."""
    assert runs, "merge_repeat_runs needs at least one run"
    if len(runs) == 1:
        return runs[0]
    final = runs[-1]
    verdicts = [r.verdict for r in runs]
    if "FAIL" in verdicts:
        final.verdict = "FAIL"
    elif "ERROR" in verdicts:
        final.verdict = "ERROR"
    else:
        final.verdict = "PASS"
    final.n_runs = len(runs)
    # Count from the pre-mutation snapshot: final IS runs[-1], whose verdict
    # was just overwritten above.
    final.n_passed = sum(1 for v in verdicts if v == "PASS")
    final.deterministic_failures = [
        f"run {i + 1}: {f}" for i, r in enumerate(runs) for f in r.deterministic_failures
    ]
    total = LLMCallCounter()
    for r in runs:
        for k, v in (r.llm_calls.get("by_context") or {}).items():
            total.by_context[k] = total.by_context.get(k, 0) + v
        total.chars_in += r.llm_calls.get("chars_in", 0)
        total.chars_out += r.llm_calls.get("chars_out", 0)
        for k, refs in (r.llm_calls.get("models") or {}).items():
            for ref in refs:
                total.note_model(k, ref)
    final.llm_calls = total.snapshot()
    final.wall_seconds = sum(r.wall_seconds for r in runs)
    return final

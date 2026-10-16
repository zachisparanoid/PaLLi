"""Slice 3 — the affect field: flag, prompt-variant selection, and the
undercurrent's engine mechanics. Spec:
docs/superpowers/specs/2026-07-27-present-moment-slice-3-affect-field-design.md

Law under test everywhere: flag off ⇒ byte-identical behavior (base
prompt file untouched on disk, no undercurrent key ever written)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_moment_affect_field_exists_default_false():
    """The field MUST exist on CompanionConfig (fail-open getattr readers
    elsewhere; yaml validation). Ships False."""
    from core.settings_schema import CompanionConfig

    comp = CompanionConfig()
    assert comp.moment_affect is False


def test_base_prompt_file_never_mentions_undercurrent():
    """The never-edited law, robust to future legit edits: the BASE file
    must carry zero affect-field content."""
    base = Path("prompts/present_moment_appraisal.txt").read_text(encoding="utf-8")
    assert "undercurrent" not in base.lower()


def test_affect_variant_exists_and_extends_base_contract():
    text = Path("prompts/present_moment_appraisal_affect.txt").read_text(encoding="utf-8")
    assert "{undercurrent_block}" in text
    assert '"undercurrent"' in text          # the JSON contract grew
    assert "it drifts, it doesn't jump" in text
    assert "never become a demand" in text   # colors-never-coerces law
    assert "Mornings don't reset you" in text  # the night rule
    # And the base placeholders all survived the copy:
    for ph in ("{current_time}", "{previous_moment_block}", "{events_block}",
               "{senses_block}", "{bases_block}", "{monologue_block}",
               "{reflection_block}"):
        assert ph in text


def test_template_selection_off_returns_base_bytes(monkeypatch):
    import ai.present_moment as pm

    loaded: list = []

    def fake_load(name):
        loaded.append(name)
        return f"<{name}>"

    import ai.prompt_loader as prompt_mod

    monkeypatch.setattr(prompt_mod.prompt_loader, "load_prompt", fake_load)
    out = pm._load_appraisal_template(affect_on=False)
    assert loaded == ["present_moment_appraisal"]
    # 2026-08-06 arbiter build: the selector returns (template, variant).
    assert out == ("<present_moment_appraisal>", "base")


def test_template_selection_on_returns_affect_variant(monkeypatch):
    import ai.present_moment as pm
    import ai.prompt_loader as prompt_mod

    monkeypatch.setattr(
        prompt_mod.prompt_loader, "load_prompt", lambda name: f"<{name}>"
    )
    assert pm._load_appraisal_template(affect_on=True) == (
        "<present_moment_appraisal_affect>", "affect",
    )


def test_template_selection_on_falls_back_when_variant_missing(monkeypatch):
    """A missing variant file must never kill her cycle — base + warning."""
    import ai.present_moment as pm
    import ai.prompt_loader as prompt_mod

    def fake_load(name):
        return None if name == "present_moment_appraisal_affect" else "<base>"

    monkeypatch.setattr(prompt_mod.prompt_loader, "load_prompt", fake_load)
    assert pm._load_appraisal_template(affect_on=True) == ("<base>", "base")


def test_affect_enabled_fails_closed(monkeypatch):
    """Mirrors _enabled's deliberate divergence from faculty_enabled():
    unbootable settings ⇒ False, never on-by-accident."""
    import ai.present_moment as pm
    import core.turn.companion_flags as flags

    monkeypatch.setattr(flags, "companion_settings", lambda: None)
    assert pm.present_moment_engine._affect_enabled() is False
    monkeypatch.setattr(
        flags, "companion_settings",
        lambda: SimpleNamespace(moment_affect=True),
    )
    assert pm.present_moment_engine._affect_enabled() is True


import ai.present_moment as pm_mod


@pytest.fixture
def engine():
    e = pm_mod.PresentMomentEngine()
    e.is_running = True
    return e


@pytest.fixture
def affect_env(monkeypatch, tmp_path):
    """cycle_env's shape (test_present_moment_engine.py:330) + affect on
    + a template that renders the undercurrent block."""
    import ai.llm_client as llm_client
    import ai.llm_routing as llm_routing
    import ai.present_moment_gather as gather_mod
    import ai.present_moment_store as store
    import ai.prompt_loader as prompt_mod
    import core.local_mode as local_mode

    state = SimpleNamespace(responses=[], writes=[], current=None, calls=0, rendered=[])

    async def fake_refs():
        return {"palli_user_id": 1, "app_user_id": 1}

    monkeypatch.setattr(local_mode, "get_local_owner_refs", fake_refs)

    async def fake_gather(*, owner_user_id):
        return gather_mod.GatherInputs(sense_health={"conversation": "ok"})

    monkeypatch.setattr(gather_mod, "gather_moment_inputs", fake_gather)
    monkeypatch.setattr(llm_routing, "get_task_model", lambda task, fallback=None: "test-model")
    monkeypatch.setattr(
        prompt_mod.prompt_loader,
        "load_prompt",
        lambda name: (
            "T={current_time}|P={previous_moment_block}|U={undercurrent_block}"
            "|E={events_block}|S={senses_block}|B={bases_block}"
            "|M={monologue_block}|R={reflection_block}"
        ),
    )

    async def fake_chat(*args, **kwargs):
        state.calls += 1
        state.rendered.append(args[0][0]["content"] if args else "")
        return state.responses.pop(0) if state.responses else ""

    monkeypatch.setattr(llm_client.llm, "chat_completion", fake_chat)

    real_write = store.write_moment

    def fake_write(*, text, meta, root=None):
        state.writes.append({"text": text, "meta": meta})
        return real_write(text=text, meta=meta, root=tmp_path)

    monkeypatch.setattr(store, "write_moment", fake_write)
    monkeypatch.setattr(store, "read_current", lambda **kw: state.current)
    monkeypatch.setattr(store, "count_today", lambda **kw: len(state.writes))

    async def snap(now):
        return {"machine_state": "active"}

    monkeypatch.setattr(pm_mod.PresentMomentEngine, "_tier1_snapshot", staticmethod(snap))
    return state


def _affect_on(monkeypatch, engine):
    monkeypatch.setattr(engine, "_affect_enabled", lambda: True)


def _prev_moment(text="prev moment", undercurrent=None, written_at=1000.0):
    from ai.present_moment_store import MomentEntry

    meta = {} if undercurrent is None else {"undercurrent": undercurrent}
    return (MomentEntry(text=text, written_at=written_at, meta=meta), 600.0, False)


VALID = '{"moment": "m", "impulse": {"kind": "none", "why": ""}, "undercurrent": "%s"}'


@pytest.mark.asyncio
async def test_first_time_block_and_first_undercurrent(engine, affect_env, monkeypatch):
    _affect_on(monkeypatch, engine)
    affect_env.current = None
    affect_env.responses = [VALID % "something quiet and good is settling in"]
    await engine.run_cycle_once()
    assert "U=(none yet — this is the first time you've listened underneath)" in affect_env.rendered[0]
    assert affect_env.writes[0]["meta"]["undercurrent"] == "something quiet and good is settling in"


@pytest.mark.asyncio
async def test_prior_undercurrent_renders_into_the_block(engine, affect_env, monkeypatch):
    _affect_on(monkeypatch, engine)
    affect_env.current = _prev_moment(undercurrent="steady warmth since last night")
    affect_env.responses = [VALID % "still warm, a little tired"]
    await engine.run_cycle_once()
    assert "U=steady warmth since last night" in affect_env.rendered[0]
    assert affect_env.writes[0]["meta"]["undercurrent"] == "still warm, a little tired"


@pytest.mark.asyncio
async def test_missing_undercurrent_carries_previous_forward(engine, affect_env, monkeypatch):
    """The carry-forward law (spec §2): a parse hiccup never erases her mood."""
    _affect_on(monkeypatch, engine)
    affect_env.current = _prev_moment(undercurrent="steady warmth")
    affect_env.responses = ['{"moment": "m", "impulse": {"kind": "none", "why": ""}}']
    await engine.run_cycle_once()
    assert affect_env.writes[0]["meta"]["undercurrent"] == "steady warmth"


@pytest.mark.asyncio
async def test_malformed_undercurrent_type_carries_previous(engine, affect_env, monkeypatch):
    _affect_on(monkeypatch, engine)
    affect_env.current = _prev_moment(undercurrent="steady warmth")
    affect_env.responses = [
        '{"moment": "m", "impulse": {"kind": "none", "why": ""}, "undercurrent": {"oops": 1}}'
    ]
    await engine.run_cycle_once()
    assert affect_env.writes[0]["meta"]["undercurrent"] == "steady warmth"


@pytest.mark.asyncio
async def test_first_cycle_with_no_return_writes_no_key(engine, affect_env, monkeypatch):
    """Nothing to carry + nothing returned ⇒ honest absence, no key."""
    _affect_on(monkeypatch, engine)
    affect_env.current = None
    affect_env.responses = ['{"moment": "m", "impulse": {"kind": "none", "why": ""}}']
    await engine.run_cycle_once()
    assert "undercurrent" not in affect_env.writes[0]["meta"]


@pytest.mark.asyncio
async def test_undercurrent_collapsed_and_clipped_at_220(engine, affect_env, monkeypatch):
    _affect_on(monkeypatch, engine)
    affect_env.current = None
    long = ("a  heavy   feeling " * 30).strip()
    affect_env.responses = [VALID % long]
    await engine.run_cycle_once()
    stored = affect_env.writes[0]["meta"]["undercurrent"]
    assert len(stored) == 220 and "  " not in stored


@pytest.mark.asyncio
async def test_flag_off_cycle_writes_no_undercurrent_key(engine, affect_env, monkeypatch):
    """Byte-identical law, meta half: off ⇒ the key never exists, even when
    the model volunteers one.

    The flag is pinned OFF explicitly. As originally written (per the plan)
    this test read the AMBIENT settings — so the owner's live
    ``data/palli.yaml`` flip on 2026-07-28 03:16 turned it red with zero
    code change: the live yaml is environment, not test state (the same
    disease class as the step-07 OPENAI_API_KEY auto-detect flake). Every
    other test in this file pins its flag state; this one must too."""
    monkeypatch.setattr(engine, "_affect_enabled", lambda: False)
    affect_env.current = _prev_moment(undercurrent="left over from an on-period")
    affect_env.responses = [VALID % "should be ignored"]
    await engine.run_cycle_once()
    assert "undercurrent" not in affect_env.writes[0]["meta"]


def test_moment_panel_payload_carries_undercurrent(monkeypatch):
    import ai.present_moment_store as store
    from api.web_interiority import _moment_section
    from ai.present_moment_store import MomentEntry

    entry = MomentEntry(
        text="quiet afternoon", written_at=1000.0,
        meta={"undercurrent": "steady warmth", "impulse": {"kind": "none", "why": ""}},
    )
    monkeypatch.setattr(store, "read_current", lambda **kw: (entry, 60.0, False))
    monkeypatch.setattr(store, "read_recent", lambda **kw: [])
    out = _moment_section(since=0.0, now=2000.0, enabled=True)
    assert out["current"]["undercurrent"] == "steady warmth"


def test_moment_panel_payload_omits_absent_undercurrent(monkeypatch):
    import ai.present_moment_store as store
    from api.web_interiority import _moment_section
    from ai.present_moment_store import MomentEntry

    entry = MomentEntry(text="quiet afternoon", written_at=1000.0, meta={})
    monkeypatch.setattr(store, "read_current", lambda **kw: (entry, 60.0, False))
    monkeypatch.setattr(store, "read_recent", lambda **kw: [])
    out = _moment_section(since=0.0, now=2000.0, enabled=True)
    assert "undercurrent" not in out["current"]

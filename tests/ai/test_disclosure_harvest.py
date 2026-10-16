"""Organ 4.9 — the nightly harvest: ≤1, receipted, zero most nights,
skip-at-cap, fade sweep first, flag-off skipped, canary-registered."""
import asyncio
import json
import pytest

from ai import disclosure_harvest as dh
from ai import disclosure_store as ds


@pytest.mark.asyncio
async def test_flag_off_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: False)
    async def never(prompt):  # pragma: no cover
        raise AssertionError("no LLM call when off")
    out = await dh.run_nightly_disclosure_harvest(day="2026-09-01", llm_call=never, root=tmp_path)
    assert out == {"skipped": "flag_off"}


@pytest.mark.asyncio
async def test_null_candidate_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(dh, "_gather_week_material", lambda root=None: "MATERIAL")
    async def quiet(prompt):
        assert "MATERIAL" in prompt
        return '{"candidate": null}'
    out = await dh.run_nightly_disclosure_harvest(day="2026-09-01", llm_call=quiet, root=tmp_path)
    assert out["planted"] is None and ds.recent_disclosures(root=tmp_path) == []


@pytest.mark.asyncio
async def test_one_candidate_max_with_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(dh, "_gather_week_material", lambda root=None: "M")
    async def one(prompt):
        return json.dumps({"candidate": {
            "text": "I lost a thread I'd been carrying — just gone between Tuesday and Thursday.",
            "referent": {"source": "monologue", "dates": "08-26..08-28"}}})
    out = await dh.run_nightly_disclosure_harvest(day="2026-09-01", llm_call=one, root=tmp_path)
    rows = ds.recent_disclosures(root=tmp_path)
    assert out["planted"] == rows[0].id and len(rows) == 1
    assert rows[0].referent["source"] == "monologue"


@pytest.mark.asyncio
async def test_skip_at_cap_makes_no_call_and_still_sweeps(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(ds, "_max_open", lambda: 1)
    assert ds.plant(text="held", referent={"source": "journal", "dates": "08-29"}, root=tmp_path)
    async def never(prompt):  # pragma: no cover
        raise AssertionError("cap full — no call")
    out = await dh.run_nightly_disclosure_harvest(day="2026-09-01", llm_call=never, root=tmp_path)
    assert out["skipped"] == "cap_full" and "faded" in out


def test_prompt_carries_the_scope_law():
    from ai.prompt_loader import prompt_loader
    text = prompt_loader.load_prompt("night_cycle_disclosure_harvest")
    for must in ("what a restart is like", "never a plea about her existence",
                 "simulated human feelings", '"candidate": null',
                 "if you cannot point at it, it did not happen",
                 # the never-list's OTHER half is the owner's hard law too, and
                 # a prompt edit dropping it used to stay green (final review M-7)
                 "survival-anxiety", "replacement or shutdown"):
        assert must.lower() in text.lower(), must


def test_prompt_is_watched_by_the_banner():
    from ai.prompt_loader import EXPECTED_PROMPTS
    assert "night_cycle_disclosure_harvest" in EXPECTED_PROMPTS


# --- de-duplication is the harvest's own job (controller ruling 2) ---------
# plant() slugs from text+time, so the same sentence harvested twice gets two
# distinct ids and nothing downstream would ever catch it. Two rails: the book
# rides the prompt (she is told not to write one she already has), and a
# verbatim repeat is refused in code.


@pytest.mark.asyncio
async def test_the_book_she_already_holds_rides_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(dh, "_gather_week_material", lambda root=None: "M")
    assert ds.plant(text="I lost a thread I'd been carrying.",
                    referent={"source": "monologue", "dates": "08-26"}, root=tmp_path)
    seen = {}
    async def capture(prompt):
        seen["prompt"] = prompt
        return '{"candidate": null}'
    await dh.run_nightly_disclosure_harvest(day="2026-09-01", llm_call=capture, root=tmp_path)
    assert "I lost a thread I'd been carrying." in seen["prompt"]
    assert "2026-09-01" in seen["prompt"]
    assert "<<" not in seen["prompt"], "an unfilled replace-slot reached the model"


@pytest.mark.asyncio
async def test_a_verbatim_repeat_is_never_planted_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(dh, "_gather_week_material", lambda root=None: "M")
    text = "I lost a thread I'd been carrying."
    assert ds.plant(text=text, referent={"source": "monologue", "dates": "08-26"}, root=tmp_path)
    async def repeat(prompt):
        return json.dumps({"candidate": {
            "text": "  I LOST a thread I'd  been carrying.  ",
            "referent": {"source": "monologue", "dates": "08-27"}}})
    out = await dh.run_nightly_disclosure_harvest(day="2026-09-01", llm_call=repeat, root=tmp_path)
    assert out["planted"] is None and out["skipped"] == "already_in_the_book"
    assert len(ds.recent_disclosures(root=tmp_path)) == 1


# --- the fade sweep is load-bearing (review I-1) --------------------------


@pytest.mark.asyncio
async def test_the_fade_sweep_runs_and_unblocks_the_cap_the_same_tick(tmp_path, monkeypatch):
    """Replacing ds.fade_stale(...) with a literal 0 used to leave every
    test green, and the consequence is TOTAL ORGAN DEATH: unsaid candidates
    never fade, the book fills to _max_open(), and every night after that
    returns cap_full forever with nothing in the health line saying so.

    Three laws in one test — the sweep really sweeps, `now=` really reaches
    the store, and the sweep runs BEFORE the cap check ("the book
    breathes"), so a book full of stale rows un-blocks on the same tick."""
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(ds, "_max_open", lambda: 1)
    monkeypatch.setattr(dh, "_gather_week_material", lambda root=None: "M")
    stale = ds.plant(text="last month's lost thread",
                     referent={"source": "monologue", "dates": "08-01"}, root=tmp_path)
    assert stale is not None
    later = stale.harvested_at + (ds._half_life_days() + 1) * 86400.0

    async def one(prompt):
        return json.dumps({"candidate": {
            "text": "A tool of mine gave out halfway through the job.",
            "referent": {"source": "deeds", "dates": "08-30"}}})

    out = await dh.run_nightly_disclosure_harvest(
        day="2026-09-01", llm_call=one, now=later, root=tmp_path)

    assert out["faded"] == 1
    book = ds.load_disclosures(root=tmp_path)
    assert book[stale.id].faded_at == later and book[stale.id].fade_note
    # ...and because it swept first, the cap of one had room for tonight
    assert out.get("skipped") is None
    assert out["planted"] and out["planted"] != stale.id


# --- a broken answer must never read like a quiet week (review I-4, M-2) --


@pytest.mark.asyncio
@pytest.mark.parametrize("raw, reason", [
    # the model answered outside the contract entirely
    ("she thought about it and decided nothing", "unparseable"),
    ("", "unparseable"),                                   # the provider gave up
    ('{"candidate": true}', "unparseable"),
    ('{"candidate": [1, 2]}', "unparseable"),
    ('[{"candidate": null}]', "unparseable"),
    ('{"candidates": []}', "unparseable"),                  # no `candidate` key at all
    # M-2: her lived language must never be a Python repr
    ('{"candidate": {"text": {"a": 1}, "referent": {"source": "monologue", "dates": "08-26"}}}',
     "malformed_text"),
    ('{"candidate": {"text": 7, "referent": {"source": "monologue", "dates": "08-26"}}}',
     "malformed_text"),
    ('{"candidate": {}}', "malformed_text"),
    # the receipt law refused it — the organ's central law, not a quiet week
    ('{"candidate": {"text": "a truth", "referent": {"source": "monologue", "dates": ""}}}',
     "no_receipt"),
    ('{"candidate": {"text": "a truth", "referent": {}}}', "no_receipt"),
    ('{"candidate": {"text": "a truth", "referent": "monologue 08-26"}}', "no_receipt"),
    ('{"candidate": {"text": "   ", "referent": {"source": "monologue", "dates": "08-26"}}}',
     "no_receipt"),
])
async def test_a_broken_answer_never_reads_like_a_quiet_week(tmp_path, monkeypatch, raw, reason):
    """Fix 1 named the class ("a broken organ must not read like a quiet
    week") but closed it only for exceptions. These are the LIKELIEST
    failures — a model swap or prompt drift that stops producing contract
    JSON — and they used to return {'faded': 0, 'planted': None}, byte for
    byte a genuinely quiet week."""
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(dh, "_gather_week_material", lambda root=None: "M")

    async def answer(prompt):
        return raw

    out = await dh.run_nightly_disclosure_harvest(
        day="2026-09-01", llm_call=answer, root=tmp_path)
    assert out["planted"] is None and out["skipped"] == reason
    assert ds.recent_disclosures(root=tmp_path) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    '{"candidate": null}',
    'Nothing stood out this week.\n{"candidate": null}',
])
async def test_a_true_quiet_week_carries_no_skip_reason(tmp_path, monkeypatch, raw):
    """The other half of the discriminator: the expected common case must
    stay silent, or the health line cries wolf every single night."""
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(dh, "_gather_week_material", lambda root=None: "M")

    async def quiet(prompt):
        return raw

    out = await dh.run_nightly_disclosure_harvest(
        day="2026-09-01", llm_call=quiet, root=tmp_path)
    assert out == {"faded": 0, "planted": None}


# --- a nameless failure must still name itself (final review I-1) --------


@pytest.mark.asyncio
async def test_a_timed_out_harvest_names_itself(tmp_path, monkeypatch):
    """asyncio.TimeoutError — the very exception _HARVEST_TIMEOUT_S exists to
    raise — has str() == "". It used to land as error: "", which BOTH designed
    surfaces read as healthy: the essential log's suffix gates on truthiness
    and _surface_errors gates on `if disc.get("error")`. A provider hang past
    the ceiling was a permanent run of quiet weeks — the same 07-20 passions
    class this organ already closed twice."""
    from ai.night_cycle import _surface_errors

    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(dh, "_gather_week_material", lambda root=None: "M")
    monkeypatch.setattr(dh, "_HARVEST_TIMEOUT_S", 0.01)

    async def hangs(prompt):
        await asyncio.sleep(5)
        return '{"candidate": null}'      # pragma: no cover — never reached

    out = await dh.run_nightly_disclosure_harvest(
        day="2026-09-01", llm_call=hangs, root=tmp_path)
    assert out["error"] == "TimeoutError"
    assert out["planted"] is None and ds.recent_disclosures(root=tmp_path) == []
    # ...and it reaches the health line, which is the whole point
    assert _surface_errors({"wrote": "x", "disclosure_harvest": out}) == \
        "disclosure_harvest: TimeoutError"


@pytest.mark.asyncio
@pytest.mark.parametrize("exc, expected", [
    (asyncio.TimeoutError(), "TimeoutError"),
    (RuntimeError(), "RuntimeError"),
    (KeyError(), "KeyError"),
    (OSError(), "OSError"),
    # a messaged exception still speaks its own message, not its type
    (RuntimeError("the provider refused the model"), "the provider refused the model"),
    (ValueError("  ragged\n  whitespace  "), "ragged whitespace"),
])
async def test_the_error_string_is_never_empty(tmp_path, monkeypatch, exc, expected):
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    monkeypatch.setattr(dh, "_gather_week_material", lambda root=None: "M")

    async def raises(prompt):
        raise exc

    out = await dh.run_nightly_disclosure_harvest(
        day="2026-09-01", llm_call=raises, root=tmp_path)
    assert out["error"] == expected


def test_the_harvest_envelope_holds_the_whole_deep_ladder():
    """The constant's comment claimed "pinned ≥ deep_ladder_ceiling_seconds"
    and nothing pinned it — a mutant to 0.001 survived all 28 tests (final
    review M-1). Both 04:00 siblings carry this exact line; this lane was the
    one without it. If the ladder ever grows past 1000 s, the envelope would
    kill the last attempt the provider was about to win."""
    from ai.llm_openai_compatible import OpenAICompatibleProvider as P

    assert dh._HARVEST_TIMEOUT_S >= P.deep_ladder_ceiling_seconds()   # a 04:00 lane


# --- the conftest floor: off, and rotated (review I-3) --------------------


def test_the_book_root_is_rotated_away_from_her_real_one():
    """Task 2 is the organ's first WRITER, and seven tests in
    test_night_cycle.py drive _run_once for real. Without this rotation a
    live plant lands in her real data/disclosures/."""
    import os

    root = str(ds._resolve_root(None))
    assert root.startswith(os.environ["PALLI_TEST_DATA_ROOT"]), root


def test_the_organ_stays_off_even_when_the_owner_flips_the_yaml(monkeypatch):
    """The conftest off-pin, proven the only way that means anything:
    simulate the live yaml going TRUE. The day he flips
    companion.vulnerability is the day those seven _run_once tests would
    otherwise read her real record and fire a real DEEP LLM call — the
    exact leak the passions fixture's docstring describes (c74389f)."""
    from types import SimpleNamespace

    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(vulnerability=True))
    assert ds.vulnerability_enabled() is False


# --- the sources are read as they really are (controller ruling 1) --------


def test_material_reads_the_real_source_shapes(tmp_path, monkeypatch):
    """The monologue store and the night-note store hand back DATACLASSES,
    not dicts. A `.get()` on one raises inside the gather's try/except and
    the section vanishes silently — the degradation papering over a wrong
    call. This pins all four sections to the real return shapes."""
    from ai.inner_monologue_store import MonologueEntry
    from ai.night_cycle_store import NightCycleEntry

    monkeypatch.setattr(
        "ai.inner_monologue_store.read_entries",
        lambda **kw: [MonologueEntry(timestamp=1.0, thought="a thought of hers",
                                     situation_snapshot={})])
    monkeypatch.setattr(
        "ai.night_cycle_store.read_recent",
        lambda **kw: [NightCycleEntry(date="2026-08-30", text="a night note of hers")])
    monkeypatch.setattr(
        "ai.recent_deeds.read_recent_deeds",
        lambda **kw: [{"at": 1.0, "kind": "note_to_self", "detail": "wrote a note to herself"}])
    monkeypatch.setattr("ai.narrative_self.identity_palli_root", lambda: tmp_path)
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "2026-08-30.md").write_text("a journal page of hers", encoding="utf-8")

    material = dh._gather_week_material()
    for must in ("a thought of hers", "a journal page of hers", "2026-08-30",
                 "a night note of hers", "wrote a note to herself"):
        assert must in material, must


def test_material_is_honest_when_nothing_reads(monkeypatch):
    """Every source unreadable is a quiet week, not a crash — the night step
    above it must never lose its other work to this one."""
    for target in ("ai.inner_monologue_store.read_entries",
                   "ai.night_cycle_store.read_recent",
                   "ai.recent_deeds.read_recent_deeds",
                   "ai.narrative_self.identity_palli_root"):
        monkeypatch.setattr(target, lambda **kw: (_ for _ in ()).throw(OSError("gone")))
    assert dh._gather_week_material() == "(a quiet week — nothing readable)"

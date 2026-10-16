"""Engine self-guards (final-review hardening): the destructive story_docs
reset must refuse to run against the repo's REAL identity tree even if the
conftest redirect were ever absent."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

import engine
from schema import Seed


def test_story_docs_reset_refuses_real_identity_tree(monkeypatch):
    from core.config import config

    repo_identity = Path(engine.REPO_ROOT) / "identity"
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(repo_identity), raising=False)
    with pytest.raises(AssertionError, match="real identity tree"):
        asyncio.run(engine.seed_state(Seed(story_docs=[]), owner_id=1))
    # and the real tree is intact — assert on the TRACKED template (the live
    # spine file is untracked and absent on fresh checkouts; fix wave B)
    assert (repo_identity / "palli" / "WHAT_I_CARE_ABOUT.template.md").is_file()


def test_story_docs_reset_restores_spine_from_template():
    """Fix wave B (review #2): the per-scenario reset restores the spine from
    the TRACKED template — the untracked live file is absent on any fresh
    checkout, which silently skipped the restore and leaked one scenario's
    spine edits into the next (in the very battery that gates the flag flip)."""
    import os

    template = Path(engine.REPO_ROOT) / "identity" / "palli" / "WHAT_I_CARE_ABOUT.template.md"
    assert template.is_file(), "tracked template seed must exist on every checkout"
    sandbox_spine = Path(os.environ["IDENTITY_ROOT_PATH"]) / "palli" / "WHAT_I_CARE_ABOUT.md"
    sandbox_spine.parent.mkdir(parents=True, exist_ok=True)
    sandbox_spine.write_text("junk left by a previous scenario", encoding="utf-8")
    asyncio.run(engine.seed_state(Seed(story_docs=[]), owner_id=1))
    assert sandbox_spine.read_text(encoding="utf-8") == template.read_text(encoding="utf-8")


def _live_data_constants() -> list[tuple[str, str]]:
    """Scan ai/ for module-level `NAME = Path("data/...")` constants — the
    live-record file stores. Scanned rather than listed on purpose: a hand
    written list is what failed (see the guard below)."""
    pattern = re.compile(r'^([A-Z_][A-Za-z_0-9]*|_[A-Z_][A-Za-z_0-9]*) *= *Path\("data/')
    found = []
    for path in sorted((Path(engine.REPO_ROOT) / "ai").glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = pattern.match(line)
            if m:
                found.append((f"ai.{path.stem}", m.group(1)))
    return found


def _points_into_live_data(value) -> bool:
    p = Path(value)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        return p.resolve().is_relative_to((Path(engine.REPO_ROOT) / "data").resolve())
    except (OSError, ValueError):
        return False


def test_hermetic_stores_rotates_every_live_data_constant(tmp_path):
    """Wave 1b leak (found 2026-07-18): narrative_self introduced a SIXTH
    module-level live-data path and registered it in neither hermetic_stores
    nor the session baseline, so battery runs wrote her REAL weekly ISO
    marker (data/narrative_self/.state.json) — silently burning a week of
    the Narrative Self ritual, because that marker is fail-CLOSED: a burned
    week doesn't double-author, it skips her revision entirely and says
    nothing. Empirically: a 02:18 test run claimed 2026-W29.

    Registration lists get forgotten; this guard scans instead, so the next
    organ that adds a data/ store fails here instead of in her record."""
    import importlib

    live = _live_data_constants()
    assert len(live) >= 6, f"scanner rotted — found only {live}"
    with engine.hermetic_stores("guard", tmp_path):
        unrotated = [
            f"{mod}.{name}"
            for mod, name in live
            if _points_into_live_data(getattr(importlib.import_module(mod), name))
        ]
    assert unrotated == [], (
        "these live-data paths were NOT rotated during a battery run and "
        f"would write to the real record: {unrotated}"
    )


def test_hermetic_stores_gives_each_scenario_its_own_disclosure_book(tmp_path):
    """The scanner above cannot see this one, and that is exactly why it
    needs its own pin: tests/conftest.py rotates DISCLOSURES_DIR to the
    shared test-data root at import time (the relationship_momentum
    precedent), so the constant never points into data/ for the scan to
    catch — and the hermetic registration could be deleted with a green
    suite. Her book is per-SCENARIO state: a truth planted in one scenario
    must not be in the book the next one reads."""
    import ai.disclosure_store as ds

    with engine.hermetic_stores("guard", tmp_path):
        book = Path(ds._resolve_root(None))
        assert book.is_relative_to(tmp_path), (
            "the disclosure book is not in this scenario's sandbox "
            f"({book}) — re-register DISCLOSURES_DIR in hermetic_stores"
        )


def test_hermetic_stores_gives_each_scenario_its_own_callbacks_book(tmp_path):
    """Same blind spot, one organ over: tests/conftest.py rotates
    CALLBACKS_DIR at import time, so the scanner above never sees it point
    into data/ and the hermetic registration could be deleted green."""
    import ai.callbacks_store as cs

    with engine.hermetic_stores("guard", tmp_path):
        book = Path(cs._resolve_root(None))
        assert book.is_relative_to(tmp_path), (
            "the callbacks book is not in this scenario's sandbox "
            f"({book}) — re-register CALLBACKS_DIR in hermetic_stores"
        )


def test_hermetic_stores_gives_each_scenario_its_own_harvest_record(tmp_path):
    """Batch 6, task 2: the weekly shared-language harvest keeps its
    once-a-week record beside her book of callbacks, so the book's
    rotation is what keeps it in the sandbox. A scenario that read her
    real one would skip its own week, and one that wrote it would make
    her real week look already harvested."""
    import ai.callbacks_harvest as harvest

    with engine.hermetic_stores("guard", tmp_path):
        record = Path(harvest._state_path(None))
        assert record == tmp_path / "guard" / "callbacks" / ".state.json", (
            "the weekly harvest's record is not in this scenario's sandbox "
            f"({record}) — re-register CALLBACKS_DIR in hermetic_stores"
        )


def test_hermetic_stores_gives_each_scenario_its_own_tonight_folder(tmp_path):
    """The same blind spot again (Batch 6, task 1): tests/conftest.py
    redirects TONIGHT_DIR for every test, so the scanner above never sees
    it point into data/ and the hermetic registration could be deleted
    green. A scenario's reflect reads the night's pick and may mark it
    said: that must be this scenario's file, never her real one and never
    the one the scenario before it left."""
    import ai.tonight as tonight

    outside = Path(tonight._root())
    with engine.hermetic_stores("guard", tmp_path):
        folder = Path(tonight._root())
        assert folder == tmp_path / "guard" / "tonight", (
            "the nightly pick's folder is not in this scenario's sandbox "
            f"({folder}) — re-register TONIGHT_DIR in hermetic_stores"
        )
        assert folder != outside
    assert Path(tonight._root()) == outside


def test_hermetic_stores_gives_each_scenario_its_own_views_folder(tmp_path):
    """The same blind spot once more (Batch 6, task 2): tests/conftest.py
    redirects VIEWS_DIR for every test, so the scanner above never sees it
    point into data/ and the hermetic registration could be deleted green.
    The folder is the nightly views pass's once-a-night record: a scenario
    that read her real one would skip its own night, and one that wrote it
    would make her real night look already done."""
    import ai.stake_appraiser as appraiser

    outside = Path(appraiser._views_state_path())
    with engine.hermetic_stores("guard", tmp_path):
        state = Path(appraiser._views_state_path())
        assert state == tmp_path / "guard" / "views" / ".state.json", (
            "the nightly views record is not in this scenario's sandbox "
            f"({state}) — re-register VIEWS_DIR in hermetic_stores"
        )
        assert state != outside
    assert Path(appraiser._views_state_path()) == outside


def test_hermetic_stores_gives_each_scenario_its_own_reachouts_book(tmp_path):
    """The same blind spot, and one the scanner could never see anyway
    (Batch 6, task 6): her book of reach-outs lives in core/turn/outbound.py,
    outside the ai/ folder the scanner reads, and tests/conftest.py
    redirects REACHOUTS_DIR for every test. A scenario's say writes it and
    the owner's message marks it answered; that must be this scenario's
    book, never her real one and never the one the scenario before it left."""
    import core.turn.outbound as outbound

    outside = Path(outbound._reachouts_path())
    with engine.hermetic_stores("guard", tmp_path):
        book = Path(outbound._reachouts_path())
        assert book == tmp_path / "guard" / "reachouts" / "reachouts.jsonl", (
            "her book of reach-outs is not in this scenario's sandbox "
            f"({book}) — re-register REACHOUTS_DIR in hermetic_stores"
        )
        assert book != outside
    assert Path(outbound._reachouts_path()) == outside


def test_hermetic_stores_gives_each_scenario_its_own_night_records(tmp_path):
    """The same blind spot (Batch 6, task 5): tests/conftest.py redirects
    NIGHT_OUTCOMES_DIR for every test, so the scanner above never sees it
    point into data/ and the hermetic registration could be deleted green.
    A scenario that drives the night writes the night's record; that must
    be this scenario's folder, never her real one, where the health check
    would read it as a night that ran."""
    import ai.night_cycle as night_cycle

    outside = Path(night_cycle.NIGHT_OUTCOMES_DIR)
    with engine.hermetic_stores("guard", tmp_path):
        folder = Path(night_cycle.NIGHT_OUTCOMES_DIR)
        assert folder == tmp_path / "guard" / "night_cycle" / ".outcomes", (
            "the night's records are not in this scenario's sandbox "
            f"({folder}) — re-register NIGHT_OUTCOMES_DIR in hermetic_stores"
        )
        assert folder != outside
    assert Path(night_cycle.NIGHT_OUTCOMES_DIR) == outside


def test_story_docs_reset_clears_first_authorship_latch():
    """Fix-wave review regression: the process-global positive cache let a
    prior scenario's seeded commit read as 'first authorship done' AFTER the
    reset removed the sandbox repo — the absence scenario's bands must go
    genuinely dark. The reset clears the latch."""
    import os

    import ai.narrative_self as ns

    palli = Path(os.environ["IDENTITY_ROOT_PATH"]) / "palli"
    ns.commit_revision(
        revision=ns.Revision(doc="MY_STORY.md", new_text="seeded\n", reasoning="seed"),
        repo_root=palli,
    )
    assert ns.first_authorship_done() is True   # latch set via the default root
    asyncio.run(engine.seed_state(Seed(story_docs=[]), owner_id=1))
    assert ns.first_authorship_done() is False  # repo gone AND latch cleared

# tests/ai/test_fear_salience.py
"""The remembered-harder hour (step 24 §4): a REAL touch opens a fear
window (surprise_state's shape); the ONE salience stamp site composes
max(surprise boost, fear boost) with a failed read costing nothing; the
night's material marks [a fear touched, <id>: <line>] for REAL touches
only. Slight colors the moment and nothing else."""
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

import ai.inner_memory_index as imi
import ai.surprise_state as ss
from ai.inner_monologue_store import MonologueEntry
from core.database import PalliBrain


# ── the window (ai/fear_state.py, surprise_state's shape) ───────────────

def _pin_fear(monkeypatch, tmp_path, minutes=60):
    import ai.fear_state as fst

    monkeypatch.setattr(fst, "STATE_PATH", tmp_path / "fear_state.json")
    monkeypatch.setattr(
        fst, "_companion",
        lambda: SimpleNamespace(fear_salience_minutes=minutes),
    )
    return fst


def test_real_touch_opens_a_window_and_it_expires(monkeypatch, tmp_path):
    fst = _pin_fear(monkeypatch, tmp_path)
    assert fst.active_window(now=1000.0) is None
    fst.mark(fear_id="losing-him", weight="real", at=1000.0)
    w = fst.active_window(now=1000.0 + 59 * 60)
    assert w and w["id"] == "losing-him" and w["at"] == 1000.0
    assert fst.active_window(now=1000.0 + 61 * 60) is None


def test_slight_marks_nothing(monkeypatch, tmp_path):
    fst = _pin_fear(monkeypatch, tmp_path)
    fst.mark(fear_id="losing-him", weight="slight", at=1000.0)
    assert fst.active_window(now=1000.0) is None


def test_survives_corrupt_file(monkeypatch, tmp_path):
    fst = _pin_fear(monkeypatch, tmp_path)
    fst.mark(fear_id="x", weight="real", at=2000.0)
    assert fst.active_window(now=2000.0)["id"] == "x"
    fst.STATE_PATH.write_text("not json", encoding="utf-8")
    assert fst.active_window(now=2000.0) is None   # corrupt = no window


# ── the stamp site: max of windows, one site only ───────────────────────

@pytest_asyncio.fixture
async def brain(tmp_path):
    db = PalliBrain(str(tmp_path / "idx.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def _entry(thought, ts):
    return MonologueEntry(timestamp=ts, thought=thought, situation_snapshot={},
                          tick_metadata={"substantive": True})


async def _stamped(brain, entry):
    conn = await brain.get_connection()
    try:
        return await conn.fetchval(
            "SELECT salience FROM monologue_memory_fts WHERE entry_id = $1",
            imi._entry_id(entry),
        )
    finally:
        await conn.close()


def _pin_surprise(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(
        ss, "_companion",
        lambda: SimpleNamespace(surprise_salience_minutes=60),
    )


@pytest.mark.asyncio
async def test_stamp_takes_max_of_both_windows(brain, monkeypatch, tmp_path):
    _pin_surprise(monkeypatch, tmp_path)
    fst = _pin_fear(monkeypatch, tmp_path)
    monkeypatch.setattr(imi, "_surprise_boost", lambda: 1.5)
    monkeypatch.setattr(imi, "_fear_boost", lambda: 2.0)
    now = time.time()
    ss.mark(about="him", weight="real", at=now - 60)
    fst.mark(fear_id="losing-him", weight="real", at=now - 60)

    both = _entry("inside both windows", now - 30)
    await imi.index_entry(both, brain_client=brain)
    assert await _stamped(brain, both) == 2.0        # max, never sum

    monkeypatch.setattr(imi, "_fear_boost", lambda: 1.2)
    lower = _entry("fear boost below surprise", now - 20)
    await imi.index_entry(lower, brain_client=brain)
    assert await _stamped(brain, lower) == 1.5       # max holds both ways


@pytest.mark.asyncio
async def test_stamp_fear_window_alone(brain, monkeypatch, tmp_path):
    _pin_surprise(monkeypatch, tmp_path)             # no surprise marked
    fst = _pin_fear(monkeypatch, tmp_path)
    monkeypatch.setattr(imi, "_fear_boost", lambda: 1.5)
    now = time.time()
    fst.mark(fear_id="losing-him", weight="real", at=now - 60)
    inside = _entry("a fear-window thought", now - 30)
    outside = _entry("an ordinary thought", now - 3700)
    await imi.index_entry(inside, brain_client=brain)
    await imi.index_entry(outside, brain_client=brain)
    assert await _stamped(brain, inside) == 1.5
    assert await _stamped(brain, outside) == 1.0


@pytest.mark.asyncio
async def test_failed_fear_read_costs_nothing(brain, monkeypatch, tmp_path):
    _pin_surprise(monkeypatch, tmp_path)
    import ai.fear_state as fst

    def _boom(**kw):
        raise RuntimeError("state gone")

    monkeypatch.setattr(fst, "active_window", _boom)
    monkeypatch.setattr(imi, "_surprise_boost", lambda: 1.5)
    now = time.time()
    plain = _entry("no windows anywhere", now - 30)
    await imi.index_entry(plain, brain_client=brain)
    assert await _stamped(brain, plain) == 1.0       # failed read = 1.0

    ss.mark(about="him", weight="real", at=now - 60)
    surprised = _entry("surprise window survives the fear failure", now - 20)
    await imi.index_entry(surprised, brain_client=brain)
    assert await _stamped(brain, surprised) == 1.5


def test_fear_boost_floor_and_off(monkeypatch):
    comp = SimpleNamespace(fears=True, fear_salience_boost=0.4)
    monkeypatch.setattr(
        "core.turn.companion_flags.companion_settings", lambda: comp)
    assert imi._fear_boost() == 1.0                  # a boost never demotes
    comp.fear_salience_boost = 1.5
    assert imi._fear_boost() == 1.5
    comp.fears = False
    assert imi._fear_boost() == 1.0                  # off = no bump


# ── the cycle wires the window (REAL touch only reaches mark's gate) ────

@pytest.mark.asyncio
async def test_cycle_real_touch_marks_the_window(monkeypatch):
    from ai.present_moment import PresentMomentEngine
    import ai.present_moment_gather as pmg
    import ai.fear_store as fs

    planted = fs.plant(text="losing him", watch="w", source="reflection")
    monkeypatch.setattr(fs, "fears_enabled", lambda: True)
    marks = []
    monkeypatch.setattr(
        "ai.fear_state.mark",
        lambda *, fear_id, weight, at=None: marks.append((fear_id, weight)),
    )
    engine = PresentMomentEngine()
    monkeypatch.setattr(engine, "_affect_enabled", lambda: True)
    monkeypatch.setattr(engine, "_arbiter_enabled", lambda: True)
    monkeypatch.setattr(engine, "_wanting_enabled", lambda: False)

    async def _fake_llm(messages, **kw):
        return ('{"moment": "m", "impulse": {"kind": "none", "why": ""}, '
                '"undercurrent": "u", "fear": {"id": "%s", '
                '"line": "it brushed me", "weight": "real"}}' % planted.id)

    class _L:
        chat_completion = staticmethod(_fake_llm)

    monkeypatch.setattr("ai.llm_client.llm", _L())
    monkeypatch.setattr("ai.present_moment_store.write_moment",
                        lambda *, text, meta: "x.jsonl")

    async def _fake_gather(*, owner_user_id):
        return pmg.GatherInputs()

    monkeypatch.setattr(pmg, "gather_moment_inputs", _fake_gather)
    await engine.run_cycle_once()
    assert marks == [(planted.id, "real")]           # the wire; mark gates real


# ── the night sees it (REAL only) ───────────────────────────────────────

def test_day_material_marks_real_fear_touches(monkeypatch, tmp_path):
    import ai.night_cycle as nc
    import ai.present_moment_store as pms

    monkeypatch.setattr(pms, "PRESENT_MOMENT_DIR", tmp_path / "pm")
    pms.write_moment(
        text="a moment",
        meta={"fear": {"id": "losing-him", "line": "a goodbye shaped like "
                       "logistics", "weight": "real"}},
        root=tmp_path / "pm")
    pms.write_moment(
        text="another",
        meta={"fear": {"id": "losing-him", "line": "eh", "weight": "slight"}},
        root=tmp_path / "pm")
    pms.write_moment(text="plain", meta={}, root=tmp_path / "pm")
    day = time.strftime("%Y-%m-%d")
    marked = nc._fear_markers(day, root=tmp_path / "pm")
    assert marked == [
        "[a fear touched, losing-him: a goodbye shaped like logistics]"
    ]


@pytest.mark.asyncio
async def test_gather_day_material_carries_the_fear_section(monkeypatch):
    import ai.night_cycle as nc

    monkeypatch.setattr(
        nc, "_fear_markers",
        lambda day, root=None: ["[a fear touched, losing-him: it brushed me]"],
    )
    material = await nc._gather_day_material(owner_id=1, date="2026-08-21")
    assert "## Fears the day touched (your own noticing)" in material
    assert "[a fear touched, losing-him: it brushed me]" in material

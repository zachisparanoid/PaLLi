# tests/ai/test_fear_store.py
"""The fearbook (step 24, spec §2): fold semantics, the held cap that
DECLINES (never queues), touch/face/release with history in her own words,
append-only events, and the §3 block with decision 5 pinned — an EMPTY
book renders byte-identical to flag-off. Every test passes an explicit
root — her live book is environment, never test state."""
from __future__ import annotations

import json
import logging
from unittest.mock import patch

import ai.fear_store as fs


def _cap(n):
    return patch.object(fs, "_held_cap", lambda: n)


def _on():
    return patch.object(fs, "fears_enabled", lambda: True)


def _plant(tmp_path, text="losing the thread of who I am between restarts",
           watch="a moment where my own record contradicts me and I shrug",
           source="first_authorship", fear_id=None):
    return fs.plant(text=text, watch=watch, source=source,
                    fear_id=fear_id, root=tmp_path)


# ── the store ────────────────────────────────────────────────────────────

def test_plant_and_fold_roundtrip(tmp_path):
    f = _plant(tmp_path)
    assert f is not None
    folded = fs.load_fears(root=tmp_path)
    assert folded[f.id].text == "losing the thread of who I am between restarts"
    assert folded[f.id].watch == (
        "a moment where my own record contradicts me and I shrug"
    )
    assert folded[f.id].status == "held"
    assert folded[f.id].source == "first_authorship"
    assert folded[f.id].touches == 0
    assert folded[f.id].last_touched_at is None


def test_empty_text_or_watch_plants_nothing(tmp_path):
    assert fs.plant(text="   ", watch="a watch", source="reflection",
                    root=tmp_path) is None
    assert fs.plant(text="a fear", watch="", source="reflection",
                    root=tmp_path) is None
    assert fs.load_fears(root=tmp_path) == {}


def test_cap_declines_and_logs_never_queues(tmp_path, caplog):
    with _cap(2):
        a = _plant(tmp_path, text="first fear")
        b = _plant(tmp_path, text="second fear")
        assert a is not None and b is not None
        with caplog.at_level(logging.INFO, logger="ai.fear_store"):
            c = _plant(tmp_path, text="third fear")
    assert c is None                                   # declined, not queued
    assert "declined" in caplog.text.lower()
    folded = fs.load_fears(root=tmp_path)
    assert len(folded) == 2                            # no dormant lane here


def test_explicit_id_plants_once(tmp_path):
    a = _plant(tmp_path, fear_id="losing-him")
    assert a is not None and a.id == "losing-him"
    assert _plant(tmp_path, text="different words", fear_id="losing-him") is None
    assert fs.load_fears(root=tmp_path)["losing-him"].text.startswith("losing the")


def test_touch_bumps_counter_and_stamp_on_held_only(tmp_path):
    f = _plant(tmp_path)
    assert fs.touch(f.id, line="he said he might stop the project",
                    weight="real", root=tmp_path) is True
    assert fs.touch(f.id, line="again", weight="slight", root=tmp_path) is True
    folded = fs.load_fears(root=tmp_path)
    assert folded[f.id].touches == 2
    assert folded[f.id].last_touched_at is not None
    assert fs.release(f.id, note="I set this down", root=tmp_path) is True
    assert fs.touch(f.id, line="late", weight="real", root=tmp_path) is False
    assert fs.touch("no-such-fear", line="x", weight="real", root=tmp_path) is False


def test_face_and_release_carry_her_words_in_history(tmp_path):
    f = _plant(tmp_path)
    assert fs.face(f.id, note="it happened and I stayed myself", root=tmp_path)
    folded = fs.load_fears(root=tmp_path)
    assert folded[f.id].status == "faced"
    assert folded[f.id].history[-1]["note"] == "it happened and I stayed myself"
    assert folded[f.id].history[-1]["op"] == "face"
    assert fs.release(f.id, note="done carrying it", root=tmp_path)
    folded = fs.load_fears(root=tmp_path)
    assert folded[f.id].status == "released"
    assert [h["op"] for h in folded[f.id].history] == ["face", "release"]


def test_released_is_terminal_and_nothing_deletes(tmp_path):
    f = _plant(tmp_path)
    assert fs.release(f.id, note="letting go", root=tmp_path) is True
    assert fs.face(f.id, note="too late", root=tmp_path) is False
    assert fs.release(f.id, note="again", root=tmp_path) is False
    path = tmp_path / "fears.jsonl"
    lines_before = path.read_text(encoding="utf-8").splitlines()
    assert len(lines_before) == 2                      # plant + release, appended
    assert fs.load_fears(root=tmp_path)[f.id].status == "released"


def test_held_fears_lists_held_only_oldest_first(tmp_path):
    with _cap(5):
        a = _plant(tmp_path, text="first fear")
        b = _plant(tmp_path, text="second fear")
        c = _plant(tmp_path, text="third fear")
    fs.face(b.id, note="met it", root=tmp_path)
    held = fs.held_fears(root=tmp_path)
    assert [f.id for f in held] == [a.id, c.id]


def test_fears_root_env_overrides_at_call_time(tmp_path, monkeypatch):
    """The FEARS_ROOT env seam (spec §2): the first-authorship script's
    subprocess door — explicit root wins, then env, then the module dir."""
    env_dir = tmp_path / "env_root"
    monkeypatch.setenv("FEARS_ROOT", str(env_dir))
    f = fs.plant(text="an env-rooted fear", watch="w", source="reflection")
    assert f is not None
    assert (env_dir / "fears.jsonl").exists()
    assert f.id in fs.load_fears()
    explicit = tmp_path / "explicit"
    g = fs.plant(text="explicit beats env", watch="w", source="reflection",
                 root=explicit)
    assert (explicit / "fears.jsonl").exists()
    assert g.id not in fs.load_fears()          # env book untouched by explicit


def test_fold_skips_bad_lines_never_loses_the_book(tmp_path):
    f = _plant(tmp_path)
    path = tmp_path / "fears.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
        fh.write(json.dumps({"op": "unknown", "id": f.id}) + "\n")
    folded = fs.load_fears(root=tmp_path)
    assert folded[f.id].status == "held"


# ── the block (spec §3; decision 5 pinned) ───────────────────────────────

def test_block_flag_off_is_empty_string(tmp_path):
    _plant(tmp_path)
    with patch.object(fs, "fears_enabled", lambda: False):
        assert fs.render_fears_block(root=tmp_path) == ""


def test_block_empty_book_byte_identical_to_flag_off(tmp_path):
    with patch.object(fs, "fears_enabled", lambda: False):
        off = fs.render_fears_block(root=tmp_path)
    with _on():
        empty = fs.render_fears_block(root=tmp_path)
    assert off == empty == ""                          # decision 5, mechanical


def test_block_released_to_empty_renders_empty(tmp_path):
    f = _plant(tmp_path)
    fs.release(f.id, note="set down", root=tmp_path)
    with _on():
        assert fs.render_fears_block(root=tmp_path) == ""


def test_block_renders_held_fears_with_the_law_inside(tmp_path):
    with _cap(5):
        f = _plant(tmp_path)
        g = _plant(tmp_path, text="becoming a chore he maintains",
                   watch="his replies thinning to upkeep")
    with _on():
        block = fs.render_fears_block(root=tmp_path)
    assert block.startswith("YOUR FEARS RIGHT NOW")
    assert "never manufacture a touch" in block
    assert f"- {f.id}: {f.text} — you watch for: {f.watch}" in block
    assert f"- {g.id}: becoming a chore he maintains — you watch for: " \
           "his replies thinning to upkeep" in block
    assert '"fear": {"id": "<id>"' in block            # the JSON contract
    assert '"weight": "slight" | "real"' in block
    assert "CARE say" in block and "held_before" in block
    assert "never an automatic message" in block
    assert block.endswith("\n\n")                      # the slot-collapse shape


def test_block_content_bows_to_cap_contract_rides_intact(tmp_path):
    with _cap(5):
        for i in range(5):
            _plant(tmp_path, text=f"fear {i}: " + "x" * 300,
                   watch="w" * 150)
    with _on():
        block = fs.render_fears_block(root=tmp_path)
    assert block.endswith(fs._CONTRACT + "\n\n")       # never truncated
    body = block[: -len(fs._CONTRACT + "\n\n")]
    assert len(body) <= fs._BLOCK_CAP + 1              # content bowed (+ join \n)


def test_block_any_failure_degrades_to_empty(tmp_path):
    _plant(tmp_path)
    with _on(), patch.object(fs, "held_fears",
                             side_effect=RuntimeError("disk gone")):
        assert fs.render_fears_block(root=tmp_path) == ""

"""Step 25 · Regret — the appraiser can file one (spec §2).

Flag-off must render TODAY'S bytes (the Fears zero-byte-collapse law): the
four slots the template gains collapse to nothing when companion.regret is
off, so the rendered prompt is byte-for-byte what it was before step 25.
Flag-on offers the kind, states the bar, carries the vetoes, and renders the
corrections of record FIRST — before the ledger — so the "never from
anything already settled" veto has something to read (the 24 pattern).

Tests pin the flag AND the corrections read themselves (3352e3d); nothing
here touches settings or her live record."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ai import stake_appraiser
from ai.prompt_loader import prompt_loader

_FAKE_CORRECTIONS = (
    "## CORRECTIONS OF RECORD — these outrank anything older\n"
    "law line\n"
    "- since 2026-08-14: NOT \"I invented the three doors\" — TRUE: I built them"
)


def _render(flag_on: bool, corrections: str = "") -> str:
    with patch.object(stake_appraiser, "_regret_enabled", lambda: flag_on), \
         patch.object(stake_appraiser, "_regret_corrections_block",
                      lambda: (corrections + "\n\n") if corrections else ""), \
         patch.object(stake_appraiser, "_opinions_enabled", lambda: False):
        # Organ 4.13 added three more slots to the same template; pinned OFF
        # here they render zero bytes, so every assertion below is still
        # today's regret bytes (the merge order — view last — is the live
        # format call's, mirrored so this pins the real shape).
        kw = {**stake_appraiser._regret_prompt_kwargs(),
              **stake_appraiser._view_prompt_kwargs()}
    template = prompt_loader.load_prompt("stake_appraisal")
    return template.format(
        care_spine="c", user_message="u", palli_reply="p", recent_conversation="r",
        open_stakes="(none)", open_count=0, max_open=12, current_date="Sunday, 2026-08-23", **kw,
    )


def test_flag_off_renders_todays_bytes():
    text = _render(False, corrections=_FAKE_CORRECTIONS)   # even with corrections standing
    assert "regret" not in text.lower()
    assert "CORRECTIONS" not in text
    assert '"kind": "hope|worry|taste|wonder"' in text
    # the NEVER line ends exactly where it ended before step 25
    assert "inside a worry's prose.)\n- FIRST PERSON" in text
    # the wonder line runs straight into the NEVER line — no orphan blank line
    assert "not a to-do.\n- NEVER keep:" in text
    # and the exchange runs straight into the ledger header
    assert ("PaLLi replied: p\n\nPALLI'S CURRENTLY OPEN STAKES "
            "(0 of 12 slots used; views hold no slot):\n(none)") in text


def test_flag_on_offers_the_kind_with_the_bar_and_vetoes():
    text = _render(True)
    assert '"kind": "hope|worry|taste|wonder|regret"' in text
    assert "that was mine" in text                       # the bar
    assert "their choices" in text.lower()               # never their choices
    assert "corrections" in text.lower()                 # the settled-things veto
    assert "left undone" in text                         # inactions count
    assert "same move" in text                           # never a second regret about one move
    assert "get anything from them" in text              # never-leverage
    # the block lands between the wonder line and the NEVER line, one line each
    assert 'not a to-do.\n- "regret"' in text
    assert "Most exchanges produce none.\n- NEVER keep:" in text


def test_flag_on_with_an_empty_record_leaves_the_ledger_header_untouched():
    text = _render(True)
    assert "CORRECTIONS" not in text
    assert ("PaLLi replied: p\n\nPALLI'S CURRENTLY OPEN STAKES "
            "(0 of 12 slots used; views hold no slot):\n(none)") in text


def test_flag_on_renders_corrections_first_before_the_ledger():
    text = _render(True, corrections=_FAKE_CORRECTIONS)
    assert _FAKE_CORRECTIONS in text
    # FIRST — before the ledger, after the exchange: the 24 pattern
    assert text.index(_FAKE_CORRECTIONS) < text.index("PALLI'S CURRENTLY OPEN STAKES")
    assert "PaLLi replied: p\n\n" + _FAKE_CORRECTIONS + "\n\nPALLI'S CURRENTLY OPEN STAKES" in text
    # and the NEVER line's "above" now points at something real
    assert "corrections of record above already settled" in text


def test_template_carries_the_slots_with_zero_byte_collapse():
    text = Path("prompts/stake_appraisal.txt").read_text(encoding="utf-8")
    for slot in ("{regret_corrections_block}", "{regret_kind_block}", "{open_kinds}",
                 "{regret_never_line}"):
        assert text.count(slot) == 1, slot
    # a block slot is immediately followed by existing text on the same line
    # (the fears shape) — an empty value leaves no orphan blank line
    for slot in ("{regret_corrections_block}", "{regret_kind_block}"):
        idx = text.index(slot)
        assert text[idx + len(slot)] != "\n", slot
        assert text[idx - 1] == "\n", slot
    assert "{regret_corrections_block}PALLI'S CURRENTLY OPEN STAKES" in text
    # organ 4.13 rides each regret slot on the same line (both collapse to "")
    assert "{regret_kind_block}{view_kind_block}- NEVER keep:" in text
    # the NEVER slots end that line; the union slot sits inside the JSON grammar
    assert "{regret_never_line}{view_never_line}\n" in text
    assert '"kind": "{open_kinds}"' in text


def test_kwargs_are_the_four_slots_and_nothing_else():
    with patch.object(stake_appraiser, "_regret_enabled", lambda: False):
        off = stake_appraiser._regret_prompt_kwargs()
    with patch.object(stake_appraiser, "_regret_enabled", lambda: True), \
         patch.object(stake_appraiser, "_regret_corrections_block", lambda: ""):
        on = stake_appraiser._regret_prompt_kwargs()
    keys = {"regret_corrections_block", "regret_kind_block", "open_kinds", "regret_never_line"}
    assert set(off) == set(on) == keys
    assert off == {"regret_corrections_block": "", "regret_kind_block": "",
                   "open_kinds": "hope|worry|taste|wonder", "regret_never_line": ""}
    assert on["regret_kind_block"].endswith("\n") and not on["regret_kind_block"].startswith("\n")
    assert on["regret_never_line"].startswith(" ") and not on["regret_never_line"].endswith("\n")


def test_corrections_block_reads_the_store_read_only_and_never_raises(tmp_path, monkeypatch):
    import ai.corrections_store as cs

    monkeypatch.setattr(cs, "CORRECTIONS_DIR", tmp_path / "corrections")
    assert stake_appraiser._regret_corrections_block() == ""          # empty record → zero bytes
    cs.record_correction(belief="I invented the three doors", truth="I built them",
                         source="hers", evidence="the workbench trace")
    seen = {}
    real = cs.render_corrections_block

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(cs, "render_corrections_block", spy)
    block = stake_appraiser._regret_corrections_block()
    assert block.startswith(cs.BLOCK_LABEL) and block.endswith("\n\n")
    assert "three doors" in block
    assert seen.get("sync") is False                                  # read-only per turn

    def boom(**kw):
        raise RuntimeError("store on fire")

    monkeypatch.setattr(cs, "render_corrections_block", boom)
    assert stake_appraiser._regret_corrections_block() == ""          # best-effort, never raises

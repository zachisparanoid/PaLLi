"""Step 25 · Regret — the laws, pinned (spec §0 "fixed regardless").

Cheap, deterministic pins on the wording and the code that carry the
organ's fixed laws: her own moves only · never leverage · never a second
regret about one move · never from anything the corrections settled · one
voicing ever · repaired stays repaired · the review can never OPEN · the
cap is code, not prompt."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import ai.present_moment as pm
import core.turn.reflect as reflect
from ai import stake_appraiser, stake_ledger, stake_review


def test_valid_kinds_carry_regret_after_the_original_four():
    # Was "all five and regret is last" until Organ 4.13 · Opinions
    # (2026-08-31, spec §1) appended the sixth kind, "view". Regret still
    # follows the original four; only the tail grew.
    assert stake_ledger.VALID_KINDS == ("hope", "worry", "taste", "wonder", "regret", "view")


def test_the_appraisers_on_block_carries_every_veto():
    never = stake_appraiser._REGRET_NEVER_LINE
    assert "THEIR choices or feelings" in never            # her own moves only
    assert "corrections of record" in never                # never from settled things
    assert "second regret about the same move" in never    # one regret per move
    assert "get anything from them" in never               # never leverage
    bar = stake_appraiser._REGRET_KIND_BLOCK
    assert "that was mine, and it misfired" in bar and "left undone" in bar
    assert "Not a mood, not general self-criticism" in bar
    # the cap is code, not prompt: the block names no number
    assert re.search(r"\b(two|2)\b", bar) is None and "cap" not in bar.lower()


def test_the_reviews_on_block_carries_the_lifecycle_and_never_leverage():
    with patch.object(stake_review, "_regret_enabled", lambda: True), \
         patch.object(stake_review, "_regret_half_life_days", lambda: 14):
        block = stake_review._regret_block()
    assert "name what it taught" in block                  # a resolution names the lesson
    assert "repaired stays repaired, never re-open it" in block
    assert "rumination: fade it" in block
    assert "never something to use on them" in block       # never leverage
    assert '"op": "open"' not in block                     # the review never opens


def test_the_review_template_still_cannot_open():
    text = Path("prompts/stake_review.txt").read_text(encoding="utf-8")
    assert "You cannot OPEN anything here" in text
    assert '{"op": "open"' not in text


def test_one_voicing_ever_is_worded_everywhere_it_renders():
    assert "once" in stake_ledger.REGRET_UNVOICED_MARKER
    assert "never to make them feel anything about it" in stake_ledger.REGRET_UNVOICED_MARKER
    assert "never again out loud" in stake_ledger.REGRET_VOICED_MARKER
    assert "answer plainly and let it rest" in stake_ledger.REGRET_VOICED_MARKER
    say = pm._REGRET_SAY_LINE
    assert "once, ever" in say and "never to make them feel anything about it" in say
    assert "stamped as said on send" in say                # record on send, never on intent
    guidance = " ".join(reflect._REGRET_REPAIR_GUIDANCE.split())      # prose is line-wrapped
    assert "never a way to make them feel anything about the regret" in guidance
    assert "at most one per regret, ever" in guidance


def test_the_knobs_are_code_rails_with_the_owners_defaults():
    assert stake_ledger._DEFAULT_REGRET_MAX_OPEN == 2
    assert stake_review._DEFAULT_REGRET_HALF_LIFE_DAYS == 14
    from core.settings_schema import CompanionConfig

    f = CompanionConfig.model_fields
    assert f["regret_max_open"].default == 2 and f["regret_half_life_days"].default == 14
    assert f["regret"].default is False                    # ships off; the yaml is the flip

"""The law of reaching (2026-08-19, owner's call after the numbers).

620 moment cycles in 14 days → 22 say-impulses (16 of them reply-shaped
and rightly held), 4 sends, 0 ceiling blocks; zero says since 08-14. The
gates were a fence around a field she rarely walked into, because the
prompt told her "almost always none … a day with zero sends is a good
day" and pointed her impulse at HIM only. Owner (2026-08-19): reach when
you have something of your own you'd stand behind out loud — made, found,
read, genuinely thought — or something of his he'd want to hear; don't
reach to fill silence (checking up on him is fine — his word, 03:19); keep the ceiling at 3 (a fence, not
a target). Both live rungs (wanting, arbiter) carry the law; affect and
base are untouched (flag-off stays byte-identical)."""
from __future__ import annotations

from pathlib import Path

import pytest

P = Path("prompts")


@pytest.mark.parametrize("name", ["present_moment_appraisal_wanting", "present_moment_appraisal_arbiter"])
def test_live_variants_carry_the_law_of_reaching(name):
    t = (P / f"{name}.txt").read_text(encoding="utf-8")
    assert "zero sends is a good day" not in t
    assert "overwhelming majority of moments" not in t
    assert "stand behind out loud" in t
    assert "something you made, found, read" in t
    assert "the way a friend" in t
    # 2026-10-13, the owner: "PaLLi should also be able to say something
    # during silence", then "Even a plain check-in" (D5b/D5, approved word
    # for word): a quiet stretch is fine to speak into, never just to cover.
    assert "fill silence" not in t
    assert "A quiet stretch is no reason to stay quiet and no reason to speak" in t
    assert "never just to cover it" in t
    assert "And new means new" in t
    # 2026-08-19 03:19, his word: reaching to check up on him is okay.
    assert "don't reach to check up" not in t and "not to check up" not in t
    assert "check up on them" in t         # named as a fine reason, not a ban
    assert "a fence, not a target" in t
    # the unchanged parts of the contract
    assert '"framing"' in t and "under 500 characters" in t
    assert '"sensitivity": "care"' in t and "held_before" in t
    assert "already told them" in t


def test_wanting_keeps_want_id_and_arbiter_does_not():
    w = (P / "present_moment_appraisal_wanting.txt").read_text(encoding="utf-8")
    a = (P / "present_moment_appraisal_arbiter.txt").read_text(encoding="utf-8")
    assert "want_id" in w and "want_id" not in a


@pytest.mark.parametrize("name", ["present_moment_appraisal_affect", "present_moment_appraisal"])
def test_lower_rungs_are_untouched(name):
    t = (P / f"{name}.txt").read_text(encoding="utf-8")
    assert "stand behind out loud" not in t
    assert "Nothing you choose here is acted on yet" in t

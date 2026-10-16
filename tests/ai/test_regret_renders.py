"""Step 25 · Regret — the voicing marker in every stake render (spec §4).

One helper, one law, zero bytes when no regrets: ``stake_ledger``'s block
renderer (which feeds the moment gather, the monologue's situation context
and the reply path's held stakes) tags a regret line with its ledger id and
ends it with exactly one of the two markers; the review's and the
appraiser's id-bearing renders show ``[said to them once]`` once voiced.
Every other kind renders byte-for-byte what it rendered before step 25."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from ai import stake_appraiser, stake_ledger, stake_review
from ai.stake_ledger import Stake

NOW = 1_700_000_000.0
UNVOICED = "(not yet said — you may say this to them once, plainly, when the moment invites it; never to make them feel anything about it)"
VOICED = "(already said once — never again out loud; if they bring it up, answer plainly and let it rest)"


def _stake(kind, sid, text, *, voiced_at=None, horizon_at=None, last_surfaced_at=None):
    return Stake(id=sid, kind=kind, stake=text, status="open", resolution="",
                 horizon_at=horizon_at, source_channel_id="", last_surfaced_at=last_surfaced_at,
                 created_at=NOW - 2 * 86400, updated_at=NOW - 86400, resolved_at=None,
                 voiced_at=voiced_at)


HOPE = _stake("hope", 2, "the survey comes back clean", horizon_at=NOW + 3 * 86400)
WONDER = _stake("wonder", 3, "how the interview went")
UNSAID = _stake("regret", 7, "I called the lake page finished when it wasn't")
SAID = _stake("regret", 9, "I buried him in settings", voiced_at=NOW - 3600)


# ── the helper and its constants ────────────────────────────────────────

def test_marker_is_one_helper_with_two_strings():
    assert stake_ledger.REGRET_UNVOICED_MARKER == UNVOICED
    assert stake_ledger.REGRET_VOICED_MARKER == VOICED
    assert stake_ledger._regret_marker(UNSAID) == UNVOICED
    assert stake_ledger._regret_marker(SAID) == VOICED
    assert stake_ledger._regret_marker(HOPE) == "" and stake_ledger._regret_marker(WONDER) == ""
    assert "\n" not in UNVOICED and "\n" not in VOICED     # rides single-line renders


# ── the block renderer (moment gather · situation context · reply path) ─

def test_block_with_no_regrets_is_todays_bytes():
    text = stake_ledger.render_stakes_block([HOPE, WONDER], now=NOW)
    assert text == ("- (hope, open 2d, horizon in 3d) the survey comes back clean\n"
                    "- (wonder, open 2d) how the interview went")


def test_block_tags_a_regret_with_its_id_and_ends_with_the_marker():
    text = stake_ledger.render_stakes_block([HOPE, UNSAID, SAID, WONDER], now=NOW)
    lines = text.split("\n")
    assert lines[0] == "- (hope, open 2d, horizon in 3d) the survey comes back clean"   # untouched
    assert lines[3] == "- (wonder, open 2d) how the interview went"                     # untouched
    assert lines[1] == "- (regret 7, open 2d) I called the lake page finished when it wasn't " + UNVOICED
    assert lines[2] == "- (regret 9, open 2d) I buried him in settings " + VOICED
    assert text.count(UNVOICED) == 1 and text.count(VOICED) == 1


# ── the review's render (id · kind · ages) ──────────────────────────────

def test_review_render_marks_a_voiced_regret_once_and_leaves_the_rest():
    text = stake_review._render_open_stakes([HOPE, UNSAID, SAID], now=NOW)
    lines = text.split("\n")
    assert lines[0] == "- id 2 · hope · open 2 days, last moved 1 day ago: the survey comes back clean"
    assert lines[1] == "- id 7 · regret · open 2 days, last moved 1 day ago: I called the lake page finished when it wasn't"
    assert lines[2] == "- id 9 · regret · open 2 days, last moved 1 day ago: I buried him in settings [said to them once]"
    assert stake_review._render_open_stakes([HOPE, WONDER], now=NOW) == (
        "- id 2 · hope · open 2 days, last moved 1 day ago: the survey comes back clean\n"
        "- id 3 · wonder · open 2 days, last moved 1 day ago: how the interview went"
    )


# ── the appraiser's render ([id n] (kind) prose) ────────────────────────

def test_appraiser_render_marks_a_voiced_regret_once_and_leaves_the_rest():
    text = stake_appraiser._render_open_stakes_for_prompt([HOPE, UNSAID, SAID])
    lines = text.split("\n")
    assert lines[0].startswith("[id 2] (hope, horizon ") and lines[0].endswith(") the survey comes back clean")
    assert lines[1] == "[id 7] (regret) I called the lake page finished when it wasn't"
    assert lines[2] == "[id 9] (regret) I buried him in settings [said to them once]"
    assert stake_appraiser._render_open_stakes_for_prompt([WONDER]) == "[id 3] (wonder) how the interview went"
    assert stake_appraiser._render_open_stakes_for_prompt([]) == "(none yet)"


# ── the monologue's situation fragment rides the same line ──────────────

def test_situation_context_carries_the_marker_inside_the_regret_entry():
    from ai import situation_context as sc

    async def fake_open(*, limit=8, brain_client=None):
        return [HOPE, UNSAID, SAID]

    async def fake_surfaced(seconds, *, brain_client=None):
        return False

    with patch("ai.stake_ledger.open_stakes", fake_open), \
         patch("ai.stake_ledger.any_surfaced_within", fake_surfaced):
        frag = asyncio.run(sc.gather_held_stakes(1))
    assert frag.metadata["held_stakes_count"] == 3
    # the fragment reads the real clock, so ages are not pinned; the tag,
    # the prose and the marker ride together inside the prose (the markers
    # themselves contain "; ", so the entries are checked as substrings)
    text = frag.text
    assert "(regret 7, open " in text
    assert "I called the lake page finished when it wasn't " + UNVOICED + "; (regret 9, open " in text
    assert text.endswith("I buried him in settings " + VOICED)
    assert "(hope, open " in text and "the survey comes back clean; (regret 7" in text
    assert text.count(UNVOICED) == 1 and text.count(VOICED) == 1

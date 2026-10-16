"""Organ 4.13 — render pins. No DB: Stake objects built by hand."""
import time

from ai.stake_ledger import (
    Stake, VIEWS_LAW, VIEW_MUTED_MARKER, render_stakes_block,
)


def _stake(**kw):
    base = dict(id=7, kind="view", stake="I side with the 1978 manuals",
                status="open", resolution="", horizon_at=None,
                source_channel_id="", last_surfaced_at=None,
                created_at=time.time() - 3600, updated_at=time.time(),
                resolved_at=None, voiced_at=None,
                evidence="his 03-14 scan; the torque table", muted_at=None)
    base.update(kw)
    return Stake(**base)


def test_view_line_carries_tag_evidence_and_law():
    out = render_stakes_block([_stake()])
    assert "(view 7," in out
    assert "evidence: his 03-14 scan" in out
    assert VIEWS_LAW in out


def test_muted_and_voiced_markers():
    out = render_stakes_block([
        _stake(muted_at=time.time()),
        _stake(id=8, stake="the second supplier's quote is the honest one",
               voiced_at=time.time() - 86400),
    ])
    assert VIEW_MUTED_MARKER in out
    assert "[said " in out


def test_muted_wins_when_a_said_view_is_also_muted():
    """One marker per line, and "they asked to leave it" outranks the tell."""
    out = render_stakes_block([_stake(voiced_at=time.time() - 86400,
                                      muted_at=time.time())])
    assert VIEW_MUTED_MARKER in out
    assert "[said " not in out


def test_no_views_renders_todays_exact_bytes():
    hope = _stake(id=1, kind="view")  # control below uses kind hope
    worry = _stake(id=2, kind="worry", evidence="", stake="the deadline")
    only_worry = render_stakes_block([worry])
    assert VIEWS_LAW not in only_worry and "evidence:" not in only_worry
    assert only_worry.startswith("- (worry,")

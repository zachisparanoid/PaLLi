"""The present-moment envelope is derived from its own retry ladder (6.5).

Before this: ai/present_moment.py's outer `asyncio.wait_for` used a
hand-picked 180.0s ("2x60s ceiling + headroom"). present_moment_cycle is a
PATIENT lane (60s/attempt) whose whole retry ladder — (MAX_RETRIES+1)
attempts plus backoff — is 250s (pinned by
tests/ai/test_request_timeout_lanes.py:143-154). A 180s envelope cut the
provider's 3rd/4th attempts off before they could finish, which is exactly
the "empty appraisal after re-roll" failure family in her logs.

`lane_total_timeout_seconds(log_context)` (a module wrapper around
`OpenAICompatibleProvider.ladder_ceiling_for_context`) gives any caller the
whole ladder ceiling for a lane's own tier, so an outer envelope can be
built FROM it instead of guessed independently of it.
"""

from __future__ import annotations

from ai.llm_openai_compatible import OpenAICompatibleProvider, lane_total_timeout_seconds


def test_deep_lane_envelope_covers_every_retry():
    """present_moment_cycle is PATIENT (60s/attempt), not DEEP — pinned by
    tests/ai/test_request_timeout_lanes.py:79-80. Its ladder ceiling equals
    the provider's own patient_ladder_ceiling_seconds() (250s). turn_read is
    SNAPPY (30s/attempt); its ladder (130s) is shorter."""
    assert (
        lane_total_timeout_seconds("present_moment_cycle")
        == OpenAICompatibleProvider.patient_ladder_ceiling_seconds()
        == 250.0
    )
    assert lane_total_timeout_seconds("turn_read") < lane_total_timeout_seconds(
        "present_moment_cycle"
    )


def test_deep_lane_ladder_matches_the_deep_ceiling():
    """stake_review is a DEEP lane (240s/attempt); its ladder is 970s —
    the same number the 04:00 organs' 1000.0s envelopes clear."""
    assert (
        lane_total_timeout_seconds("stake_review")
        == OpenAICompatibleProvider.deep_ladder_ceiling_seconds()
        == 970.0
    )


def test_present_moment_envelope_holds_the_whole_ladder():
    """ai/present_moment.py's outer envelope is
    `lane_total_timeout_seconds("present_moment_cycle") + 30.0` (280s). It
    must clear the provider's own patient ladder ceiling (250s) the way
    stake_review's _REVIEW_TIMEOUT_S (1000.0) clears deep_ladder_ceiling_
    seconds() (970s) — otherwise the envelope kills the provider's last
    attempt before it can finish."""
    envelope = lane_total_timeout_seconds("present_moment_cycle") + 30.0
    assert envelope >= OpenAICompatibleProvider.patient_ladder_ceiling_seconds() == 250.0

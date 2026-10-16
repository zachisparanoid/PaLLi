"""The reasoning-burn ceiling law (2026-08-10).

Third occurrence of the same failure, same organ: a reasoning model spends
its completion budget on invisible chain-of-thought and returns an EMPTY
page with finish_reason=length.

- 2026-07-17: author lane at 1200 — deliberated the budget away on first
  authorship. Raised to 4000.
- 2026-07-24: gate lane at 400 — same, killed the journal recovery. Raised
  to 4000.
- 2026-08-10: author lane at 4000, retry-capped at 4800 — deepseek-v4-flash
  burned 2,813 reasoning tokens on the SHORT doc (barely fit) and >=4,800
  on both story docs (empty, twice each, both skipped). The W33 revision —
  the first since the flag-trap release — never landed. The retry machinery
  worked; its cap (set 2026-05-05 for 400-1500-token burns) was the limit.

The law these tests pin: every budget on a reasoning lane must fit the
OBSERVED burn class plus real visible output, and the retry ceiling must
sit ABOVE the biggest initial budget that leans on it, or the safety net
is structurally dead for exactly the lane that needs it.
"""

from ai.llm_openai_compatible import retry_completion_budget
from ai.narrative_self import _AUTHOR_MAX_TOKENS

# The biggest burn actually observed live (2026-08-10, narrative author).
OBSERVED_REASONING_BURN = 4800
# A story doc is ~3,000 chars -> ~1,000 tokens, plus the REASONING header
# (the ceiling went 2,400 → 3,000 on 2026-09-22; the lane's 8,000 still
# clears 4,800 burn + a full page with 2,200 to spare).
VISIBLE_OUTPUT_FLOOR = 1400


def test_author_budget_fits_burn_plus_document():
    assert _AUTHOR_MAX_TOKENS >= OBSERVED_REASONING_BURN + VISIBLE_OUTPUT_FLOOR, (
        "the author lane must fit observed reasoning burn AND a full story doc"
    )


def test_retry_cap_clears_observed_burn_with_room_to_write():
    for expects_json in (False, True):
        bumped = retry_completion_budget(
            4000, log_context="narrative_self_author", expects_json=expects_json
        )
        assert bumped >= OBSERVED_REASONING_BURN + VISIBLE_OUTPUT_FLOOR, (
            "a retry that cannot outrun the burn that emptied the first "
            "attempt is not a retry"
        )


def test_retry_cap_sits_above_the_author_initial_budget():
    """The empty+length retry only fires when the bumped budget EXCEEDS the
    current one (the guard at the retry site). If the cap sits at or below
    the author's initial budget, the safety net silently stops existing for
    the heaviest lane."""
    bumped = retry_completion_budget(
        _AUTHOR_MAX_TOKENS, log_context="narrative_self_author", expects_json=False
    )
    assert bumped > _AUTHOR_MAX_TOKENS


def test_deep_lanes_keep_doubling_to_the_deep_cap():
    """2026-08-22 04:03: the stake review emptied a 4,000 budget on thought;
    the net bumped ONCE to 9,600 and that was the end of the ladder. For the
    04:00 family the owner's rule is "keep doubling until it works, within
    reason": 4000 → 8000 → 16000, then stop."""
    from ai.llm_openai_compatible import DEEP_RETRY_BUDGET_CAP
    assert DEEP_RETRY_BUDGET_CAP >= 16000
    assert retry_completion_budget(4000, log_context="stake_review", expects_json=False) == 8000
    assert retry_completion_budget(8000, log_context="stake_review", expects_json=False) == 16000
    assert retry_completion_budget(16000, log_context="stake_review", expects_json=False) == 16000
    assert retry_completion_budget(4000, log_context="night_cycle_daily_meaning", expects_json=False) == 8000


def test_daytime_lanes_keep_the_single_four_x_bump():
    """Byte-identical for everything that is not a 04:00 lane."""
    assert retry_completion_budget(700, log_context="stake_appraisal", expects_json=False) == 2800
    assert retry_completion_budget(2500, log_context="inner_monologue_tick", expects_json=False) == 9600

"""user_facts pollution guards — 2026-05-10.

Production-observed pollution patterns that the prompt's "When NOT to
extract user_facts" section should suppress, with a defense-in-depth
denylist in `_plan_from_payload` that rejects them at parse time even
if the LLM ignores the prompt guidance.
"""
from core.turn.reflect import (
    _is_forbidden_user_fact_key,
    _plan_from_payload,
)


def _facts_payload(facts: list[dict]) -> dict:
    """Wrap user_facts_add in the minimum payload shape _plan_from_payload accepts."""
    return {"user_facts_add": facts}


# --- direct denylist coverage ---


def test_denylist_rejects_meta_preference_mention_suffix():
    assert _is_forbidden_user_fact_key("prefers_favorite_color_mention") is True


def test_denylist_rejects_system_state_keys():
    assert _is_forbidden_user_fact_key("active_git_branch") is True
    assert _is_forbidden_user_fact_key("digitalocean_server") is True
    assert _is_forbidden_user_fact_key("current_python_version") is True


def test_denylist_rejects_known_test_residue():
    assert _is_forbidden_user_fact_key("prefers_playful_boundary_tests") is True
    assert _is_forbidden_user_fact_key("interested_in_prompt_engineering") is True
    assert _is_forbidden_user_fact_key("likes_meta_reflection") is True


def test_denylist_rejects_directive_suffix_keys():
    assert _is_forbidden_user_fact_key("voice_styling_directive") is True
    assert _is_forbidden_user_fact_key("phrasing_in_replies") is True


def test_denylist_allows_real_keys():
    assert _is_forbidden_user_fact_key("preferred_name") is False
    assert _is_forbidden_user_fact_key("timezone") is False
    assert _is_forbidden_user_fact_key("has_daughter") is False
    assert _is_forbidden_user_fact_key("preferred_shell") is False
    assert _is_forbidden_user_fact_key("default_reply_mode") is False


def test_denylist_is_case_insensitive():
    assert _is_forbidden_user_fact_key("Active_Git_Branch") is True
    assert _is_forbidden_user_fact_key("PREFERS_FAVORITE_COLOR_MENTION") is True


def test_denylist_handles_empty_input():
    assert _is_forbidden_user_fact_key("") is False
    assert _is_forbidden_user_fact_key("   ") is False


# --- integration: parse pipeline drops denylisted facts ---


def test_parse_pipeline_drops_meta_preference():
    plan = _plan_from_payload(_facts_payload([
        {"key": "prefers_favorite_color_mention", "value": "use 'purple' in casual replies", "confidence": 0.9},
        {"key": "preferred_name", "value": "Sam", "confidence": 0.95},
    ]))
    keys = [f.key for f in plan.user_facts_add]
    assert "preferred_name" in keys
    assert "prefers_favorite_color_mention" not in keys


def test_parse_pipeline_drops_system_state_fact():
    plan = _plan_from_payload(_facts_payload([
        {"key": "active_git_branch", "value": "feat/refactor", "confidence": 0.99},
        {"key": "preferred_shell", "value": "PowerShell", "confidence": 0.9},
    ]))
    keys = [f.key for f in plan.user_facts_add]
    assert "preferred_shell" in keys
    assert "active_git_branch" not in keys


def test_parse_pipeline_caps_at_three_per_turn():
    plan = _plan_from_payload(_facts_payload([
        {"key": "fact_one", "value": "a", "confidence": 0.9},
        {"key": "fact_two", "value": "b", "confidence": 0.9},
        {"key": "fact_three", "value": "c", "confidence": 0.9},
        {"key": "fact_four", "value": "d", "confidence": 0.9},
        {"key": "fact_five", "value": "e", "confidence": 0.9},
    ]))
    assert len(plan.user_facts_add) == 3


def test_parse_pipeline_requires_confidence_at_least_0_8():
    plan = _plan_from_payload(_facts_payload([
        {"key": "uncertain_fact", "value": "x", "confidence": 0.7},
        {"key": "confident_fact", "value": "y", "confidence": 0.85},
    ]))
    keys = [f.key for f in plan.user_facts_add]
    assert "confident_fact" in keys
    assert "uncertain_fact" not in keys

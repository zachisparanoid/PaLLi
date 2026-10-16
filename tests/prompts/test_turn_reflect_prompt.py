"""Wave 2: assert turn_reflect prompt loads and contains required fields."""
from __future__ import annotations

import os


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_prompt() -> str:
    path = os.path.join(REPO_ROOT, "prompts", "turn_reflect.txt")
    assert os.path.exists(path), f"prompt file missing: {path}"
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_turn_reflect_prompt_exists() -> None:
    text = _load_prompt()
    assert text and len(text) > 500


def test_turn_reflect_includes_input_placeholders() -> None:
    """recall.py supplies these inputs; the prompt template must reference them.

    E.2 step 1 (2026-05-13): {sentiment_tag/read/shift} stripped — the
    classifier writing those baselines stopped 2026-04-30 and the LLM
    now reads tone naturally from {user_message}/{assistant_reply}.
    """
    text = _load_prompt()
    expected = [
        "{user_message}",
        "{assistant_reply}",
        "{identity_excerpt}",
        "{memory_durable_context}",
        "{relationship_state}",
        "{unfulfilled_first_contact_targets}",
        "{current_time_context}",
    ]
    for placeholder in expected:
        assert placeholder in text, f"prompt missing placeholder {placeholder}"


def test_turn_reflect_does_not_reference_dead_sentiment_placeholders() -> None:
    """E.2 step 1: sentiment placeholders + section header removed.

    The reflect format_kwargs dict no longer passes sentiment_tag/read/shift,
    so leaving them in the template would crash at format() time. Pin the
    removal so a future regression that re-adds them blows up here, not
    in production.
    """
    text = _load_prompt()
    for dead in ("{sentiment_tag}", "{sentiment_read}", "{sentiment_shift}"):
        assert dead not in text, f"dead placeholder reintroduced: {dead}"


def test_turn_reflect_output_schema_includes_all_fields() -> None:
    """The reflection plan output schema covers all fields ReflectionPlan
    expects (per core/turn/types.py)."""
    text = _load_prompt()
    expected_fields = [
        "memory_updates",
        "durable_context_add",
        "active_threads_add",
        "active_threads_resolve",
        "important_decisions_add",
        "useful_references_add",
        "personality_observations",
        "relationship_delta",
        "trust_shift",
        "posture_shift",
        "summary_revision",
        "user_facts_add",
        "monologue_note",
        "first_contact_resolutions",
        "skip_reason",
    ]
    for field in expected_fields:
        assert field in text, f"output schema missing field {field}"


def test_turn_reflect_skip_reasons_documented() -> None:
    """The four skip_reason values are documented in the prompt so the LLM
    knows when to pick each."""
    text = _load_prompt()
    for reason in ("small_talk", "already_known", "low_confidence", "no_disclosure"):
        assert reason in text


def test_turn_reflect_first_contact_target_options_listed() -> None:
    """All first-contact target option sets are documented so the LLM
    knows the choice space."""
    text = _load_prompt()
    # Companion style options
    for option in ("warm", "direct", "relaxed", "minimal", "calm"):
        assert option in text, f"companion_style option {option} missing"
    # Autonomy preset options
    for option in ("cautious", "balanced", "trusted"):
        assert option in text
    # Checkin posture options
    for option in ("off", "rare", "normal", "frequent"):
        assert option in text


def test_turn_reflect_strict_json_contract() -> None:
    text = _load_prompt()
    assert "STRICT JSON" in text or "strict json" in text.lower()


def test_turn_reflect_anti_confabulation_section_present() -> None:
    """The anti-confabulation instructions are critical for keeping
    MEMORY.md from filling up with hallucinated facts."""
    text = _load_prompt()
    assert "Anti-confabulation" in text or "anti-confabulation" in text.lower()
    assert "user didn't actually disclose" in text or "explicitly named" in text


def test_turn_reflect_uses_double_brace_for_json_template() -> None:
    """The JSON schema uses {{ }} so str.format() doesn't mangle it."""
    text = _load_prompt()
    assert "{{" in text
    assert "}}" in text

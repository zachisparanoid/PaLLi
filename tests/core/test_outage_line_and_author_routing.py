"""Two fixes from the morning of 2026-08-11 — both provider-layer.

1. The outage line: at 10:13 the owner messaged her, DeepSeek timed out
   4x, and the turn ended responded=False — SILENCE to a waiting human.
   dispatch._apply_outage_fallback now swaps error-silence for one honest
   canned line, ONLY for human-origin turns that FAILED. Chosen silence
   stays silence; reminders keep dead-letter semantics; workbench and
   monologue have nobody waiting.

2. The story author's own lane: DeepSeek V4 reasoning consumed 2,400 →
   4,800 → 9,600-token budgets whole (four incidents), so narrative_author
   routes to claude/haiku via the CLI. The routing key is OPTIONAL in the
   schema (absent = safe degrade to reflection's resolution, never a boot
   failure) and present in the live yaml (lockstep).
"""

from pathlib import Path

from core.agent.dispatch import _PROVIDER_OUTAGE_LINE, _apply_outage_fallback
from core.agent.runner import RunResult


def _rr(**kw):
    base = dict(responded=False, response_text="", error="agent loop error: boom")
    base.update(kw)
    return RunResult(**base)


class TestOutageFallback:
    def test_failed_user_turn_gets_the_line(self):
        out = _apply_outage_fallback(_rr(), "user")
        assert out.responded is True
        assert out.response_text == _PROVIDER_OUTAGE_LINE
        assert "agent loop error" in out.error  # observability keeps the truth

    def test_dashboard_user_counts_as_human(self):
        out = _apply_outage_fallback(_rr(), "dashboard_user")
        assert out.response_text == _PROVIDER_OUTAGE_LINE

    def test_chosen_silence_stays_silence(self):
        """responded=False with NO error is her deliberate quiet — sacred."""
        out = _apply_outage_fallback(_rr(error=""), "user")
        assert out.responded is False
        assert out.response_text == ""

    def test_nonhuman_origins_never_get_the_line(self):
        for origin in ("monologue", "reminder", "workbench"):
            out = _apply_outage_fallback(_rr(), origin)
            assert out.responded is False, origin
            assert out.response_text == "", origin

    def test_real_reply_untouched_even_with_error(self):
        """A turn that produced text despite a late error keeps its text."""
        out = _apply_outage_fallback(
            _rr(responded=True, response_text="here you go"), "user"
        )
        assert out.response_text == "here you go"


class TestNarrativeAuthorRouting:
    def test_schema_key_is_optional(self):
        from core.settings_schema import RoutingConfig

        # Absent key must not be a boot failure (the appraisal precedent).
        assert RoutingConfig.model_fields["narrative_author"].default is None

    def test_yaml_carries_the_lane_in_lockstep(self):
        text = Path("data/palli.yaml").read_text(encoding="utf-8")
        assert "narrative_author:" in text

    def test_author_call_requests_its_own_task(self):
        import inspect

        import ai.narrative_self as ns

        src = inspect.getsource(ns)
        assert 'task="narrative_author", fallback_task="reflection"' in src

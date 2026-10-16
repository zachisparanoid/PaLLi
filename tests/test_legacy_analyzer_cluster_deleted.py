"""After C4, the legacy analyzer-cluster modules must not be importable.

C4 (Phase C, 2026-05-12) deleted ~1,888 lines of dead-code analyzer
modules. Responsibilities subsumed by core/turn/decide.py BehaviorFrame
and the open-threads agenda (core/turn/proactive.py). voice_interjection_analyzer
was MOVED to voice/ in step 2 (not deleted).
"""
import pytest


def test_conversational_strategy_module_deleted():
    with pytest.raises(ImportError):
        import ai.conversational_strategy  # noqa: F401


def test_conversation_thread_analyzer_module_deleted():
    with pytest.raises(ImportError):
        import ai.conversation_thread_analyzer  # noqa: F401


def test_engagement_quality_analyzer_module_deleted():
    with pytest.raises(ImportError):
        import ai.engagement_quality_analyzer  # noqa: F401


def test_multi_analysis_module_deleted():
    with pytest.raises(ImportError):
        import ai.multi_analysis  # noqa: F401


def test_proactive_decision_module_deleted():
    with pytest.raises(ImportError):
        import ai.proactive_decision  # noqa: F401


def test_voice_interjection_analyzer_moved_from_ai():
    """voice_interjection_analyzer was MOVED to voice/, not deleted.
    The legacy ai/ location must not be importable; the new voice/ location must."""
    with pytest.raises(ImportError):
        import ai.voice_interjection_analyzer  # noqa: F401
    # The new location must work:
    from voice.voice_interjection_analyzer import voice_interjection_analyzer  # noqa: F401

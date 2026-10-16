"""After C3, the 6 ai/personality_* modules must not be importable.

C3 (Phase C, 2026-05-12) deleted the personality cluster (~2,958 lines).
Responsibilities subsumed by core/turn/respond_tier0.py + core/turn/decide.py
+ ai/tonal_register.py.
"""
import pytest


def test_personality_engine_module_deleted():
    with pytest.raises(ImportError):
        import ai.personality_engine  # noqa: F401


def test_personality_generation_module_deleted():
    with pytest.raises(ImportError):
        import ai.personality_generation  # noqa: F401


def test_personality_learning_module_deleted():
    with pytest.raises(ImportError):
        import ai.personality_learning  # noqa: F401


def test_personality_prompting_module_deleted():
    with pytest.raises(ImportError):
        import ai.personality_prompting  # noqa: F401


def test_personality_response_style_module_deleted():
    with pytest.raises(ImportError):
        import ai.personality_response_style  # noqa: F401


def test_personality_styler_module_deleted():
    with pytest.raises(ImportError):
        import ai.personality_styler  # noqa: F401

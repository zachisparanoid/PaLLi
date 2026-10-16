"""After C5.2, core.first_contact must not be importable.

C5 (Phase C, 2026-05-12) deleted the 1,389-line legacy state machine.
Surviving responsibilities live at:
- api/guided_setup_dashboard.py (web dashboard helpers)
- core/first_contact_targets.py (target detection + is_first_contact_complete)
- core/turn/reflect.py:_apply_first_contact_resolutions (LLM-driven writes)
"""
import pytest


def test_first_contact_module_deleted():
    with pytest.raises(ImportError):
        import core.first_contact  # noqa: F401

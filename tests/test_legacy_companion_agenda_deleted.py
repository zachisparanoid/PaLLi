"""After cleanup (plan Task 15), the legacy companion_agenda modules
must not be importable."""
import pytest


def test_companion_agenda_module_deleted():
    with pytest.raises(ImportError):
        import core.companion_agenda  # noqa: F401


def test_companion_agenda_store_module_deleted():
    with pytest.raises(ImportError):
        import core.companion_agenda_store  # noqa: F401

"""Surface-scoped tool gating (spec §4b): workbench turns get the guarded
shell and LOSE messaging/delivery/system tools; every other surface's
registry is byte-identical to before this feature existed."""
from __future__ import annotations


def _names(reg):
    return set(reg.all_names())


def test_default_surface_registry_unchanged():
    from core.agent.builtin_tools import build_default_registry

    base = _names(build_default_registry())
    explicit = _names(build_default_registry(surface="discord_text"))
    assert base == explicit
    assert "workbench_shell" not in base


def test_workbench_registry_composition():
    from core.agent.builtin_tools import build_default_registry

    names = _names(build_default_registry(surface="workbench"))
    assert "workbench_shell" in names
    assert "whoami" in names
    for banned in ("local_shell", "read_file", "deliver_artifact",
                   "create_reminder", "list_reminders", "cancel_reminder",
                   "reschedule_reminder"):
        assert banned not in names

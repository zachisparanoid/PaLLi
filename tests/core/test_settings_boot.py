from __future__ import annotations

from pathlib import Path

import pytest

from core.settings_schema import PalliSettings


def test_boot_settings_loads_from_default_path():
    from core.settings_boot import boot_settings, settings_service

    # boot_settings() should load data/palli.yaml and make the snapshot available
    boot_settings()
    snapshot = settings_service.snapshot()
    assert isinstance(snapshot, PalliSettings)
    assert snapshot.version == 1


def test_boot_settings_is_idempotent():
    from core.settings_boot import boot_settings, settings_service

    boot_settings()
    first = settings_service.snapshot()
    boot_settings()  # second call should not error
    second = settings_service.snapshot()
    assert first.version == second.version


def test_palli_settings_path_is_wired_at_import(tmp_path):
    """The seam. `_resolve_default_yaml_path()` is only worth anything because the
    module CALLS it at import — delete that one line and every other test stays
    green while boot_settings() quietly goes back to the repo's data/palli.yaml.
    A subprocess is the only place the import can be watched happening.
    """
    import os
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    target = tmp_path / "elsewhere.yaml"
    probe = (
        "import core.settings_boot as sb;"
        "print(sb._DEFAULT_YAML_PATH);"
        "print(sb.settings_service._yaml_path)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(repo),
        env={**os.environ, "PALLI_SETTINGS_PATH": str(target)},
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    # Both: the constant AND the service built from it at module scope.
    assert result.stdout.split() == [str(target), str(target)], result.stdout

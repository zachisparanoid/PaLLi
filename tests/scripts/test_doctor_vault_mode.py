"""Batch 4, Task 11 (controller ruling R16): the doctor keeps the vault private.

Batch 1 found every vault save leaves credentials.kdbx at mode 664 (the
ledger: "Every save by her tools will do the same"). Her saves now put it back
to 600 themselves (core/turn/local_shell.py); the doctor is the second line:
a vault file that is not 600, or a vault folder that is not 700, is a warning
with the chmod lines, and `doctor --fix` makes them private and says so. It is
the first thing `--fix` ever changes, so the old closing line ("No data was
modified") is no longer printed when it did.

Every vault here is a temp folder with a stand-in file; the real one is never
touched (the suite points PALLI_VAULT_DIR at a path that does not exist, and
each test here points core.secrets_redaction.VAULT_DIR at its own temp dir).
"""
from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest

import core.secrets_redaction as sr

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    # The doctor's NAS check probes through sudo; never here.
    monkeypatch.setenv("WORKBENCH_NAS_DIR", "")


@pytest.fixture(autouse=True)
def _no_organ_liveness_reads(monkeypatch):
    """The doctor's organ table reads the repo's data/ (Batch 6, item 3.8);
    never here — no night has run, an info line. Covered against a tmp
    install in tests/scripts/test_doctor_organ_liveness.py."""
    import ai.organ_liveness as organ_liveness

    monkeypatch.setattr(organ_liveness, "install_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(organ_liveness, "night_cycle_switched_off", lambda *args, **kwargs: False)


def _load_module():
    module_path = REPO / "scripts" / "palli_ctl.py"
    spec = importlib.util.spec_from_file_location("palli_ctl_vault_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    kdbx = vault_dir / "credentials.kdbx"
    kdbx.write_bytes(b"stand-in, not a vault")
    os.chmod(kdbx, 0o600)
    os.chmod(vault_dir, 0o700)
    monkeypatch.setattr(sr, "VAULT_DIR", vault_dir)
    return vault_dir, kdbx


def test_a_loose_vault_file_is_a_warning_with_the_chmod_line(vault):
    vault_dir, kdbx = vault
    os.chmod(kdbx, 0o664)
    controller = _load_module().LauncherController()

    finding = controller._check_vault_mode()

    assert finding["code"] == "vault_mode_loose"
    assert finding["severity"] == "warning"
    assert "664" in finding["message"]
    assert f"chmod 600 {kdbx}" in finding["details"]["fix_guidance"]
    assert _mode(kdbx) == 0o664  # without --fix nothing is changed


def test_a_loose_vault_folder_is_a_warning_too(vault):
    vault_dir, kdbx = vault
    os.chmod(vault_dir, 0o775)
    controller = _load_module().LauncherController()

    finding = controller._check_vault_mode()

    assert finding["code"] == "vault_mode_loose"
    assert f"chmod 700 {vault_dir}" in finding["details"]["fix_guidance"]
    assert f"chmod 600 {kdbx}" not in finding["details"]["fix_guidance"]
    assert _mode(vault_dir) == 0o775


def test_fix_mode_makes_the_vault_private_and_says_so(vault):
    vault_dir, kdbx = vault
    os.chmod(kdbx, 0o664)
    os.chmod(vault_dir, 0o775)
    controller = _load_module().LauncherController()

    finding = controller._check_vault_mode(fix_mode=True)

    assert _mode(kdbx) == 0o600
    assert _mode(vault_dir) == 0o700
    assert finding["code"] == "vault_mode_fixed"
    assert finding["severity"] == "ok"
    assert "664" in finding["message"] and "775" in finding["message"]
    assert len(finding["details"]["changed"]) == 2


def test_a_private_vault_is_ok_and_fix_mode_changes_nothing(vault):
    vault_dir, kdbx = vault
    controller = _load_module().LauncherController()

    for fix_mode in (False, True):
        finding = controller._check_vault_mode(fix_mode=fix_mode)
        assert finding["code"] == "vault_mode_ok"
        assert finding["severity"] == "ok"
    assert _mode(kdbx) == 0o600 and _mode(vault_dir) == 0o700


def test_no_vault_is_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "VAULT_DIR", tmp_path / "no-vault-here")
    controller = _load_module().LauncherController()

    finding = controller._check_vault_mode(fix_mode=True)

    assert finding["severity"] == "info"
    assert not (tmp_path / "no-vault-here").exists()


def test_a_fix_that_cannot_chmod_stays_a_warning(vault, monkeypatch):
    vault_dir, kdbx = vault
    os.chmod(kdbx, 0o664)
    module = _load_module()
    controller = module.LauncherController()

    def _refuse(path, mode):
        raise PermissionError(1, "Operation not permitted")

    with monkeypatch.context() as patched:
        patched.setattr(module.os, "chmod", _refuse)
        finding = controller._check_vault_mode(fix_mode=True)

    assert finding["code"] == "vault_mode_loose"
    assert finding["severity"] == "warning"
    assert "Operation not permitted" in finding["message"]
    assert f"chmod 600 {kdbx}" in finding["details"]["fix_guidance"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any folder")
def test_a_vault_whose_modes_cannot_be_read_is_a_warning_not_a_crash(vault):
    vault_dir, kdbx = vault
    os.chmod(vault_dir, 0)  # the file inside cannot even be looked at now
    try:
        finding = _load_module().LauncherController()._check_vault_mode(fix_mode=True)
    finally:
        os.chmod(vault_dir, 0o700)

    assert finding["severity"] == "warning"
    assert finding["code"] == "vault_mode_unreadable"
    assert str(kdbx) in finding["message"]


def _doctor(module, controller, monkeypatch, checks):
    report = {"summary": "Environment is ready.", "status": "ok",
              "counts": {"core_blockers": 0, "warnings": 0, "ok": 0}, "checks": checks}
    monkeypatch.setattr(module, "build_doctor_report", lambda **_kwargs: report)
    monkeypatch.setattr(controller, "_save_last_doctor", lambda report: None)
    return report


RESIDUE = {"code": "legacy_residue_present", "severity": "warning",
           "message": "Legacy residue found.",
           "details": {"fix_guidance": ["DELETE FROM example;"]}}


def test_doctor_fix_changes_the_vault_and_stops_saying_nothing_was_modified(vault, monkeypatch, capsys):
    vault_dir, kdbx = vault
    os.chmod(kdbx, 0o664)
    module = _load_module()
    controller = module.LauncherController()
    _doctor(module, controller, monkeypatch, [dict(RESIDUE)])

    assert controller.doctor(as_json=False, fix_mode=True) == 0

    out = capsys.readouterr().out
    assert _mode(kdbx) == 0o600
    assert "[OK] vault_mode_fixed:" in out
    assert "--fix changed" in out and str(kdbx) in out
    assert "No data was modified" not in out
    assert "was not run" in out  # the residue SQL is still only advice


def test_doctor_fix_with_nothing_to_change_keeps_the_advisory_line(vault, monkeypatch, capsys):
    module = _load_module()
    controller = module.LauncherController()
    _doctor(module, controller, monkeypatch, [dict(RESIDUE)])

    assert controller.doctor(as_json=False, fix_mode=True) == 0

    out = capsys.readouterr().out
    assert "[OK] vault_mode_ok:" in out
    assert "--fix changed" not in out
    assert "No data was modified" in out


def test_doctor_without_fix_only_reports(vault, monkeypatch, capsys):
    vault_dir, kdbx = vault
    os.chmod(kdbx, 0o664)
    module = _load_module()
    controller = module.LauncherController()
    _doctor(module, controller, monkeypatch, [])

    assert controller.doctor(as_json=False) == 0  # a warning, never a failure

    out = capsys.readouterr().out
    assert "[WARNING] vault_mode_loose:" in out
    assert _mode(kdbx) == 0o664

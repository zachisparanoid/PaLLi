"""Batch 4, Task 10 (fix plan 7.1): the doctor says whether her NAS folder is
writable by her work account.

Her workbench tool promises her "your NAS folder" to create and edit in. On
this kind of install that folder is read-only for her work account (the share
is mounted with the app user's uid forced onto every file), and nothing said
so. The doctor now checks, and when she cannot write it prints the recipe.

The owner's ruling (2026-09-28, "New empty subfolder"): the folder she is
promised today holds his own files and must never become writable. Her
writable NAS folder is a NEW, EMPTY subfolder (<share>/palli/workbench),
mounted by itself at /mnt/palli-nas, and WORKBENCH_NAS_DIR points her at it.
The recipe says exactly that, with her work account's own uid/gid (never a
hard-coded number) and placeholders for the server and share.

No test here runs sudo, mounts anything or touches a real NAS: the probe and
the account lookup are replaced on the controller.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MOUNT_LINE = "//<server>/<share>/palli/workbench /mnt/palli-nas cifs"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """The doctor reads ~/.palli; no test in this file may reach the real one."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))


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
    spec = importlib.util.spec_from_file_location("palli_ctl_nas_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _must_not_probe(path):
    raise AssertionError("the probe must not run here")


def _controller(monkeypatch, *, writable=False, ids=(4242, 4243), own_work=True, module=None):
    module = module or _load_module()
    controller = module.LauncherController()
    probed = []

    def _probe(path):
        probed.append(path)
        return writable

    monkeypatch.setattr(controller, "_probe_writable_as_work_user", _probe)
    monkeypatch.setattr(controller, "_work_user_ids", lambda user: ids)
    monkeypatch.setattr(controller, "_own_work_enabled", lambda: own_work)
    return controller, probed


def test_doctor_flags_an_unwritable_nas_folder(monkeypatch, tmp_path):
    controller, probed = _controller(monkeypatch, writable=False)

    finding = controller._check_workbench_nas(nas_dir=str(tmp_path))

    assert probed == [str(tmp_path)]
    assert finding["code"] == "workbench_nas_unwritable"
    assert finding["severity"] == "warning"
    assert str(tmp_path) in finding["message"]
    guidance = "\n".join(finding["details"]["fix_guidance"])
    # Her account's own uid/gid, derived — never this host's 1001.
    assert "uid=4242,gid=4243,dir_mode=0770,file_mode=0660" in guidance
    assert "1001" not in guidance
    # Only the new, empty subfolder is mounted, and she is pointed at it.
    assert MOUNT_LINE in guidance
    assert "WORKBENCH_NAS_DIR=/mnt/palli-nas" in guidance
    assert "WORKBENCH_NAS_DIR=/mnt/palli-nas/" not in guidance
    assert "empty" in guidance.lower()


def test_a_writable_nas_folder_is_ok(monkeypatch, tmp_path):
    controller, probed = _controller(monkeypatch, writable=True)

    finding = controller._check_workbench_nas(nas_dir=str(tmp_path))

    assert probed == [str(tmp_path)]
    assert finding["code"] == "workbench_nas_writable"
    assert finding["severity"] == "ok"
    assert "fix_guidance" not in finding["details"]


def test_no_work_account_is_a_note_not_a_warning(monkeypatch, tmp_path):
    """A stranger's box has no work account: nothing to check, nothing wrong."""
    controller, _ = _controller(monkeypatch, ids=None)
    monkeypatch.setattr(controller, "_probe_writable_as_work_user", _must_not_probe)

    finding = controller._check_workbench_nas(nas_dir=str(tmp_path))

    assert finding["code"] == "workbench_nas_skipped"
    assert finding["severity"] == "info"


def test_her_own_work_off_is_a_note_not_a_warning(monkeypatch, tmp_path):
    controller, _ = _controller(monkeypatch, own_work=False)
    monkeypatch.setattr(controller, "_probe_writable_as_work_user", _must_not_probe)

    finding = controller._check_workbench_nas(nas_dir=str(tmp_path))

    assert finding["code"] == "workbench_nas_skipped"
    assert finding["severity"] == "info"


def test_no_nas_folder_configured_is_a_note(monkeypatch):
    """The folder comes from the workbench tool's own lookup: WORKBENCH_NAS_DIR,
    else <PALLI_NAS_ROOT>/palli. Neither set = no NAS folder at all."""
    monkeypatch.delenv("WORKBENCH_NAS_DIR", raising=False)
    monkeypatch.setenv("PALLI_NAS_ROOT", "")
    controller, _ = _controller(monkeypatch)
    monkeypatch.setattr(controller, "_probe_writable_as_work_user", _must_not_probe)

    finding = controller._check_workbench_nas()

    assert finding["code"] == "workbench_nas_unconfigured"
    assert finding["severity"] == "info"


def test_the_folder_is_the_one_her_workbench_tool_uses(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_NAS_DIR", str(tmp_path / "mnt-palli-nas"))
    controller, probed = _controller(monkeypatch, writable=True)

    controller._check_workbench_nas()

    assert probed == [str(tmp_path / "mnt-palli-nas")]


def test_the_probe_is_a_touch_and_rm_as_her_work_account(monkeypatch, tmp_path):
    """`test -w` lies on a mount with a forced uid (it reads mode bits only), so
    the probe creates and removes a file, like the setup script's own probe.
    The folder is an argument, never pasted into the shell text."""
    module = _load_module()
    controller = module.LauncherController()
    monkeypatch.setenv("WORKBENCH_USER", "someone-else")
    seen = {}

    class _Done:
        returncode = 0

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Done()

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    folder = str(tmp_path / "it's a folder")

    assert controller._probe_writable_as_work_user(folder) is True

    argv = seen["argv"]
    assert argv[:5] == ["sudo", "-n", "-u", "someone-else", "sh"]
    assert argv[-1] == folder
    script = argv[argv.index("-c") + 1]
    assert "touch" in script and "rm -f" in script and folder not in script
    assert seen["kwargs"].get("timeout")
    assert not seen["kwargs"].get("shell")


def test_a_probe_that_cannot_run_reads_as_not_writable(monkeypatch, tmp_path):
    module = _load_module()
    controller = module.LauncherController()

    def _boom(argv, **kwargs):
        raise FileNotFoundError("sudo")

    monkeypatch.setattr(module.subprocess, "run", _boom)

    assert controller._probe_writable_as_work_user(str(tmp_path)) is False


def test_a_probe_that_times_out_says_so_and_never_raises(monkeypatch, tmp_path):
    """A hung NAS mount: the probe gives up fast (the configure flow runs the
    whole doctor under a 30 s budget) and the doctor says it timed out."""
    module = _load_module()
    controller = module.LauncherController()
    seen = {}

    def _hang(argv, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise module.subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(module.subprocess, "run", _hang)

    assert controller._probe_writable_as_work_user(str(tmp_path)) is None
    assert 0 < seen["timeout"] <= 5

    monkeypatch.setattr(controller, "_work_user_ids", lambda user: (4242, 4243))
    monkeypatch.setattr(controller, "_own_work_enabled", lambda: True)
    finding = controller._check_workbench_nas(nas_dir=str(tmp_path))
    assert finding["code"] == "workbench_nas_probe_timed_out"
    assert finding["severity"] == "warning"
    assert "timed out" in finding["message"]


# --- fix round 1 (R14a): the default shared folder writable is a warning -----


def test_the_default_shared_folder_writable_is_a_warning_not_ok(monkeypatch, tmp_path):
    """WORKBENCH_NAS_DIR unset: she is promised <root>/palli, which can hold the
    owner's own files. Her work account writing there is not a green light."""
    monkeypatch.delenv("WORKBENCH_NAS_DIR", raising=False)
    monkeypatch.setenv("PALLI_NAS_ROOT", str(tmp_path))
    controller, probed = _controller(monkeypatch, writable=True)

    finding = controller._check_workbench_nas()

    assert probed == [f"{tmp_path}/palli"]
    assert finding["code"] == "workbench_nas_shared_folder_writable"
    assert finding["severity"] == "warning"
    guidance = "\n".join(finding["details"]["fix_guidance"])
    assert "WORKBENCH_NAS_DIR" in guidance and "uid=4242" in guidance
    assert f"{tmp_path}/palli/workbench" in guidance


def test_a_dedicated_folder_named_by_workbench_nas_dir_writable_is_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_NAS_DIR", str(tmp_path / "mnt-palli-nas"))
    controller, _ = _controller(monkeypatch, writable=True)

    finding = controller._check_workbench_nas()

    assert finding["code"] == "workbench_nas_writable"
    assert finding["severity"] == "ok"


# --- M4: the own-work switch, read from a real settings file -----------------


def _settings_file(tmp_path, own_work):
    import yaml

    data = yaml.safe_load((REPO / "data" / "palli.example.yaml").read_text(encoding="utf-8"))
    data.setdefault("companion", {})["own_work"] = own_work
    path = tmp_path / "palli.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("own_work", [True, False])
def test_own_work_is_read_from_the_settings_file(tmp_path, own_work):
    controller = _load_module().LauncherController()
    controller.palli_yaml_path = _settings_file(tmp_path, own_work)

    assert controller._own_work_enabled() is own_work


def test_own_work_with_no_settings_file_yet_reads_as_on_and_writes_nothing(tmp_path):
    controller = _load_module().LauncherController()
    controller.palli_yaml_path = tmp_path / "data" / "palli.yaml"

    assert controller._own_work_enabled() is True
    assert not controller.palli_yaml_path.exists()  # the doctor never seeds it


def test_own_work_with_an_unreadable_settings_file_reads_as_on(tmp_path):
    controller = _load_module().LauncherController()
    broken = tmp_path / "palli.yaml"
    broken.write_text("companion: [this is: not valid", encoding="utf-8")
    controller.palli_yaml_path = broken

    assert controller._own_work_enabled() is True


def test_the_doctor_runs_the_check_and_fix_prints_the_recipe(monkeypatch, tmp_path, capsys):
    module = _load_module()
    controller, _ = _controller(monkeypatch, writable=False, module=module)
    monkeypatch.setenv("WORKBENCH_NAS_DIR", str(tmp_path))
    report = {"summary": "Environment is ready.", "status": "ok",
              "counts": {"core_blockers": 0, "warnings": 0, "ok": 0}, "checks": []}
    monkeypatch.setattr(module, "build_doctor_report", lambda **_kwargs: report)
    monkeypatch.setattr(controller, "_save_last_doctor", lambda report: None)

    assert controller.doctor(as_json=False, fix_mode=True) == 0  # a warning, never a failure

    out = capsys.readouterr().out
    assert "[WARNING] workbench_nas_unwritable:" in out
    assert MOUNT_LINE in out
    assert "uid=4242,gid=4243" in out


# --- the script and SETUP.md say the same recipe ---------------------------


def test_the_setup_script_documents_the_subfolder_mount_not_the_whole_share():
    text = (REPO / "scripts" / "setup_workbench_user.sh").read_text(encoding="utf-8")
    assert MOUNT_LINE in text
    assert "WORKBENCH_NAS_DIR=/mnt/palli-nas" in text
    assert "WORKBENCH_NAS_DIR=/mnt/palli-nas/" not in text
    # The whole share mounted for her was the old remedy: write access to everything on it.
    assert "//<server>/<share> /mnt/palli-nas" not in text
    # Her uid/gid come from her account on the reader's machine.
    assert 'uid=$(id -u "$WORK_USER"),gid=$(id -g "$WORK_USER")' in text


def test_setup_md_documents_the_subfolder_mount_and_why():
    text = (REPO / "SETUP.md").read_text(encoding="utf-8")
    assert MOUNT_LINE in text
    assert "WORKBENCH_NAS_DIR=/mnt/palli-nas" in text
    assert "palli/workbench" in text

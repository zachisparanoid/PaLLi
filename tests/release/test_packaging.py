"""Week 1 task 5 — the packaging surface a stranger meets.

Everything here reads the real files. There is no fixture standing in for
`pyproject.toml`, the lockfile, the workflow or the unit: the point of this
module is that the artifacts a stranger downloads are the ones under test.

Five things are pinned:

  * pyproject parses, declares the console script, and finds the packages the
    console script needs to import.
  * ONE version. `VERSION` is the file; `core.version.read_version()` is the
    reader; `palli_config.__version__` and the FastAPI app both go through it.
    Three literals drifting apart is how "which version am I running" becomes
    unanswerable.
  * every requirement has a pin in the lock — a lock that omits one line of
    requirements.txt resolves that dependency freely and is not a lock.
  * the CI workflow parses as YAML and still runs the gate command.
  * the proof script is executable IN GIT (not just on this disk) and its
    syntax is clean; the Dockerfile and the systemd unit carry placeholders,
    never this machine's user or home.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO = Path(__file__).resolve().parents[2]
PYPROJECT = REPO / "pyproject.toml"
VERSION_FILE = REPO / "VERSION"
REQUIREMENTS = REPO / "requirements.txt"
LOCKFILE = REPO / "requirements.lock"
WORKFLOW = REPO / ".github" / "workflows" / "gate.yml"
PROOF = REPO / "scripts" / "fresh_clone_proof.sh"
DOCKERFILE = REPO / "Dockerfile"
UNIT = REPO / "deploy" / "palli.service"
CHANGELOG = REPO / "CHANGELOG.md"

GATE_COMMAND = "pytest tests -q -p no:cacheprovider --timeout=600"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


# --- pyproject ---------------------------------------------------------------


def test_pyproject_parses_and_names_the_console_script():
    data = _pyproject()
    assert data["project"]["name"] == "palli"
    assert data["project"]["requires-python"] == ">=3.13"
    # The console script is the whole reason `palli_ctl …` is a truthful string.
    assert data["project"]["scripts"]["palli_ctl"] == "scripts.palli_ctl:main"


def test_pyproject_finds_the_packages_the_console_script_imports():
    include = _pyproject()["tool"]["setuptools"]["packages"]["find"]["include"]
    for needed in ("scripts*", "core*", "palli_config*", "api*", "ai*", "bots*", "voice*"):
        assert needed in include, f"{needed} missing — an editable install would not import it"


def test_scripts_is_a_package_so_the_entry_point_resolves():
    # `scripts.palli_ctl:main` is importable only if scripts/ is a package.
    assert (REPO / "scripts" / "__init__.py").exists()


def test_pyproject_declares_the_optional_extras():
    optional = _pyproject()["project"]["optional-dependencies"]
    assert any("sentence-transformers" in item for item in optional["embeddings"])
    assert any("faster-whisper" in item for item in optional["voice"])


def test_pyproject_reads_its_version_and_dependencies_from_the_files():
    data = _pyproject()
    assert set(data["project"]["dynamic"]) == {"dependencies", "version"}
    dynamic = data["tool"]["setuptools"]["dynamic"]
    assert dynamic["version"]["file"] == "VERSION"
    assert dynamic["dependencies"]["file"] == ["requirements.txt"]


# --- one version -------------------------------------------------------------


def test_the_version_is_single_sourced():
    from core.version import read_version
    import palli_config
    from core.main import create_app

    declared = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert declared == "2.0.0"
    assert read_version() == declared
    assert palli_config.__version__ == declared
    assert create_app().version == declared


def test_read_version_falls_back_when_the_file_is_missing(tmp_path, monkeypatch):
    from core import version as version_module

    monkeypatch.setattr(version_module, "VERSION_FILE", tmp_path / "nothing-here")
    # The read is cached (it is on the /api/health path). Both patches are
    # monkeypatch's, so the real version is restored for every later test —
    # a leaked "0.0.0" in the cache would fail the single-sourcing test above.
    monkeypatch.setattr(version_module, "_CACHE", None)
    assert version_module.read_version() == "0.0.0"


def test_read_version_reads_the_file_once(tmp_path, monkeypatch):
    from core import version as version_module

    stand_in = tmp_path / "VERSION"
    stand_in.write_text("9.9.9\n", encoding="utf-8")
    monkeypatch.setattr(version_module, "VERSION_FILE", stand_in)
    monkeypatch.setattr(version_module, "_CACHE", None)

    assert version_module.read_version() == "9.9.9"
    stand_in.unlink()
    assert version_module.read_version() == "9.9.9", "the second call re-read the file"


# --- the lock ----------------------------------------------------------------


def _lock_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in LOCKFILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = line.split(";", 1)[0].split("==", 1)[0].strip()
        pins[canonicalize_name(name)] = line
    return pins


def test_every_requirement_has_a_pin_in_the_lock():
    pins = _lock_pins()
    missing = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = canonicalize_name(Requirement(line).name)
        if name not in pins:
            missing.append(name)
    assert missing == [], f"requirements.txt names with no pin in requirements.lock: {missing}"


def test_the_aiohttp_floor_is_the_release_the_page_reader_needs():
    """Final fix wave (ruling FW-13): read_page's connect-time guard needs
    ``aiohttp.abc.ResolveResult`` and the decompression cap; on an older
    aiohttp, the import inside ``build_default_registry`` raises with
    features.read_page on and every owner turn fails. 3.13 is the release
    installed and tested; the lock's pin sits inside the range."""
    reqs = {}
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            req = Requirement(line)
            reqs[canonicalize_name(req.name)] = req
    floor = reqs["aiohttp"].specifier
    assert floor.contains("3.13.0") and not floor.contains("3.12.15")
    locked = _lock_pins()["aiohttp"].split("==", 1)[1].strip()
    assert floor.contains(locked), locked


def test_every_lock_line_is_an_exact_pin():
    for name, line in _lock_pins().items():
        assert "==" in line, f"{name} is not pinned exactly: {line!r}"


def test_the_lock_carries_no_cuda_wheels():
    # The CPU index has none of them; a lock naming one cannot install in CI.
    for name in _lock_pins():
        assert not name.startswith(("nvidia-", "cuda-")), name
        assert name != "triton"


def test_torch_is_pinned_without_a_local_version():
    # `2.12.0+cu130` exists on exactly one index. The bare version resolves on both.
    torch = _lock_pins().get("torch")
    assert torch is not None and "+" not in torch, torch


# --- CI ----------------------------------------------------------------------


def test_the_workflow_parses_and_still_runs_the_gate():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare `on:` key as the boolean True — that is YAML 1.1,
    # not a broken workflow.
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"push", "pull_request"}

    gate = workflow["jobs"]["gate"]
    assert gate["runs-on"] == "ubuntu-latest"
    assert gate["timeout-minutes"] == 45
    steps = gate["steps"]
    runs = "\n".join(str(step.get("run", "")) for step in steps)
    assert GATE_COMMAND in runs
    assert "requirements.lock" in runs
    assert "pytest-timeout" in runs
    assert "download.pytorch.org/whl/cpu" in runs
    assert "ffmpeg" in runs
    assert gate["env"]["PALLI_PREFLIGHT_OFFLINE"] in (1, "1")

    setup = [s for s in steps if str(s.get("uses", "")).startswith("actions/setup-python")]
    assert setup and str(setup[0]["with"]["python-version"]) == "3.13"


def test_the_workflow_has_a_stranger_proof_job_that_runs_the_script():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["stranger-proof"]
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "scripts/fresh_clone_proof.sh --full" in runs


def test_the_stranger_proof_job_restricts_torch_to_the_cpu_index():
    """An EXTRA index adds candidates; it does not restrict them.

    With both PyPI and the CPU index visible, pip takes the highest version it
    can see — so the day PyPI carries a newer torch, --full silently installs
    the 2.5 GB CUDA wheel on a runner with no GPU. The script takes an index of
    its own and installs torch with --index-url before requirements.txt asks.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    env = workflow["jobs"]["stranger-proof"]["env"]
    assert env.get("PALLI_PROOF_TORCH_INDEX") == "https://download.pytorch.org/whl/cpu"
    assert "PIP_EXTRA_INDEX_URL" not in env

    # Read the commands, not the prose — the script's comment explains at length
    # why --extra-index-url is the wrong tool, and that sentence is not an install.
    commands = [
        line for line in PROOF.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    torch_installs = [line for line in commands if "pip install" in line and "torch" in line]
    assert torch_installs, "the proof never pre-installs torch"
    for line in torch_installs:
        assert "--index-url" in line and "--extra-index-url" not in line, line
    assert any("PALLI_PROOF_TORCH_INDEX" in line for line in commands)


def test_the_public_set_carries_the_packaging_a_stranger_installs_from():
    """The release gate assembles the public repo from PUBLIC_SET, and
    `tests/release/` ships inside it. Without these entries the public repo got
    this very module and none of the files it asserts — its suite failed on a
    stranger's first run. (Owner ruling 2026-09-09.)"""
    # The tripwire is itself PRIVATE_TOOLING: it never ships. A module-level
    # import of it would make THIS module error on collection in the public
    # repo — the very failure the test exists to prevent. So the guard runs
    # only where the tripwire exists, and is skipped where it does not.
    hygiene = pytest.importorskip("tests.architecture.test_release_hygiene")

    for entry in ("pyproject.toml", "VERSION", "CHANGELOG.md", "requirements.lock",
                  "Dockerfile", ".dockerignore", "deploy", ".github"):
        assert entry in hygiene.PUBLIC_SET, f"{entry} would not reach the public repo"

    # And the two files this module points at must not be excluded by the
    # private-tooling skip — that is why they live in scripts/, not scripts/release/.
    assert not hygiene.is_private_tooling("scripts/fresh_clone_proof.sh")
    assert not hygiene.is_private_tooling("scripts/write_lock.py")


def test_the_assembled_public_tree_carries_both_doors():
    """Telegram door (2026-09-14, task 6): `bots` ships wholesale, so the new
    packages reach a stranger BY CONSTRUCTION — which is exactly the kind of
    claim that is true until someone adds an exclusion. This reads the
    assembler's own plan (the `git ls-files -- PUBLIC_SET` walk, minus the
    named exclusions) and asks it for one file from each new package.

    Skipped where the assembler is not present: it is PRIVATE_TOOLING, so the
    public repo — which DOES get this module — has no such import."""
    assembler = pytest.importorskip("scripts.release.assemble_public")

    kept, _dropped = assembler.plan(REPO)

    for shipped in ("bots/telegram/client.py", "bots/telegram/adapter.py",
                    "bots/telegram/commands.py", "bots/common/guest_gate.py"):
        assert shipped in kept, f"{shipped} would not reach the public repo"


# --- the proof script --------------------------------------------------------


def test_the_proof_script_is_executable_here_and_in_git():
    assert os.access(PROOF, os.X_OK), "the proof script is not executable on disk"
    listing = subprocess.run(
        ["git", "ls-files", "-s", "--", "scripts/fresh_clone_proof.sh"],
        cwd=REPO, check=True, capture_output=True, text=True,
    ).stdout.split()
    # Mode 100644 in the index means a stranger's clone gets a file they cannot run.
    assert listing and listing[0] == "100755", f"git mode is {listing[0] if listing else 'untracked'}"


def test_the_proof_script_has_clean_syntax():
    result = subprocess.run(["bash", "-n", str(PROOF)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_proof_script_checks_both_runs():
    text = PROOF.read_text(encoding="utf-8")
    assert "--preflight-only" in text
    assert "PROOF OK" in text
    # Run 2 must see a NUMBERED punch list, not merely a non-zero exit.
    assert "'^1\\. '" in text or '"^1\\. "' in text


def test_the_proof_script_proves_a_telegram_only_clone():
    """Telegram door (2026-09-14, task 6): run 4 is the stranger who has a
    Telegram bot and no Discord application. Two halves, and the second one is
    the packaging proof — the library the token needs must already be in the
    install, so the "not installed" line must be ABSENT from that run."""
    text = PROOF.read_text(encoding="utf-8")
    assert "run 4" in text
    assert "ENABLE_TELEGRAM_BOT=true" in text
    assert "python-telegram-bot is not installed" in text


# --- deploy surfaces carry placeholders, never this machine ------------------


@pytest.mark.parametrize("path", [DOCKERFILE, UNIT], ids=["Dockerfile", "palli.service"])
def test_the_deploy_files_name_no_real_home_or_user(path: Path):
    text = path.read_text(encoding="utf-8")
    assert "/home/" not in text, f"{path.name} names a home directory"
    # NOT os.environ["USER"]: the unit deliberately contains `User=palli`, so a
    # suite run by a user named `palli` would fail on the placeholder this test
    # exists to protect. The home directory is the marker that means something.
    assert str(Path.home()) not in text, f"{path.name} names this machine's home"


def test_the_systemd_unit_is_a_placeholder_unit():
    text = UNIT.read_text(encoding="utf-8")
    assert "User=palli" in text
    assert "WorkingDirectory=/opt/palli" in text
    assert "ExecStart=/opt/palli/venv/bin/python main.py" in text
    assert "Restart=always" in text
    assert "--preflight-only" in text, "the unit must tell an operator to run the preflight first"


def test_the_dockerfile_installs_cpu_torch_before_the_lock():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "python:3.13-slim" in text
    assert "ffmpeg" in text
    # The INSTALL order is what matters — `COPY requirements.lock` naming the file
    # earlier is not the constraint.
    torch_at = text.index("download.pytorch.org/whl/cpu")
    lock_at = text.index("-r requirements.lock")
    assert torch_at < lock_at, "the CPU torch wheel has to be in place before the lock installs"
    assert 'CMD ["python", "main.py"]' in text


def test_the_dockerignore_keeps_the_heavy_and_the_private_out():
    ignored = {
        line.strip()
        for line in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for entry in ("venv", ".git", "logs", "backups", ".superpowers", "data/**",
                  "identity/owners", "identity/users", "identity/palli"):
        assert entry in ignored, f"{entry} would be copied into the image"
    assert ".env*" in ignored and "!.env.template" in ignored
    # The two files a fresh container DOES need out of those trees.
    assert "!data/palli.example.yaml" in ignored
    # The shared identity scaffolding is the product, not the person.
    for entry in ("identity/templates", "identity/bootstrap", "identity/core"):
        assert entry not in ignored, f"{entry} is scaffolding the image needs"


# --- the changelog -----------------------------------------------------------


def test_the_changelog_opens_on_this_version():
    text = CHANGELOG.read_text(encoding="utf-8")
    assert "## [2.0.0]" in text
    # The one behaviour change a stranger upgrading has to read. It changed
    # twice inside 2.0.0 — required, then a warning — so the pin is a phrase
    # from what the release actually ships (week 2, task 7).
    assert "Discord is off for that boot" in text

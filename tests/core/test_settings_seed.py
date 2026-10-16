"""core/settings_boot.py — the first-boot seed (step 28 task 3).

A stranger's clone has no `data/palli.yaml`: the repo ships `data/palli.example.yaml`
and the first boot copies it into place, once, with one INFO line saying so. The three
laws this pins: it copies BYTES (every comment survives), it NEVER overwrites an
existing config, and it stays silent when there is nothing to copy from — a missing
example is not a boot failure, it is the old "settings file not found" error, honestly.

This file SHIPS (the seed is product; the release tooling under scripts/release/ and
tests/scripts/test_make_example_yaml.py is not), so it imports nothing from the release
machinery and every path it touches is a tmp path. It must pass in a clone that has no
data/palli.yaml at all — which is exactly what a stranger has on the first run.
"""
from __future__ import annotations

import logging
import stat
from pathlib import Path

import pytest

import core.settings_boot as boot
from core.settings_schema import PalliSettings

REPO = Path(__file__).resolve().parents[2]

EXAMPLE_TEXT = """version: 1
providers:
  deepseek:
    enabled: true
    discovery: curated
    base_url: https://api.deepseek.com
    models:
      - id: deepseek-v4-flash
        display_name: DeepSeek V4 Flash
llm:
  # a comment the byte-copy must preserve
  default_provider: deepseek
  routing:
    response:
      provider: deepseek
      model: deepseek-v4-flash
    decision:
      provider: deepseek
      model: deepseek-v4-flash
    analysis:
      provider: deepseek
      model: deepseek-v4-flash
    memory:
      provider: deepseek
      model: deepseek-v4-flash
    proactive:
      provider: deepseek
      model: deepseek-v4-flash
    reflection:
      provider: deepseek
      model: deepseek-v4-flash
    web_search:
      provider: deepseek
      model: deepseek-v4-flash
    vision:
      provider: deepseek
      model: deepseek-v4-flash
voice:
  engine: classic
  stt_provider: deepgram
  tts_provider: lemonfox
  interaction_mode: direct_address
policy:
  proactive:
    max_per_day: 3
    min_gap_hours: 2.0
  operator:
    browser_enabled: true
    desktop_enabled: true
"""


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """A fresh install: an example on disk, no live yaml yet."""
    example = tmp_path / "palli.example.yaml"
    example.write_text(EXAMPLE_TEXT, encoding="utf-8")
    target = tmp_path / "palli.yaml"
    monkeypatch.setattr(boot, "_EXAMPLE_YAML_PATH", example)
    monkeypatch.setattr(boot, "_DEFAULT_YAML_PATH", target)
    return example, target


def test_the_seed_copies_the_example_when_the_live_yaml_is_absent(seeded, caplog):
    example, target = seeded
    with caplog.at_level(logging.INFO, logger=boot.logger.name):
        assert boot.seed_from_example() is True
    assert target.read_bytes() == example.read_bytes()
    seed_lines = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(seed_lines) == 1, [r.getMessage() for r in seed_lines]
    assert "palli.example.yaml" in seed_lines[0].getMessage()


def test_the_seed_copies_bytes_so_every_comment_survives(seeded):
    _, target = seeded
    boot.seed_from_example()
    assert "# a comment the byte-copy must preserve" in target.read_text(encoding="utf-8")


def test_the_seed_never_overwrites_an_existing_yaml(seeded):
    _, target = seeded
    target.write_text("version: 1  # his live config\n", encoding="utf-8")
    assert boot.seed_from_example() is False
    assert target.read_text(encoding="utf-8") == "version: 1  # his live config\n"


def test_the_seed_is_a_no_op_when_there_is_no_example(tmp_path, monkeypatch, caplog):
    target = tmp_path / "palli.yaml"
    monkeypatch.setattr(boot, "_EXAMPLE_YAML_PATH", tmp_path / "absent.example.yaml")
    monkeypatch.setattr(boot, "_DEFAULT_YAML_PATH", target)
    with caplog.at_level(logging.INFO, logger=boot.logger.name):
        assert boot.seed_from_example() is False
    assert not target.exists()
    assert [r for r in caplog.records if r.levelno == logging.INFO] == []


def test_the_seed_honours_an_explicit_target(seeded, tmp_path):
    example, default_target = seeded
    other = tmp_path / "elsewhere" / "palli.yaml"
    assert boot.seed_from_example(other) is True
    assert other.read_bytes() == example.read_bytes()
    assert not default_target.exists()


def test_boot_settings_seeds_the_missing_yaml_and_then_loads_it(seeded, monkeypatch):
    """The whole point: a fresh clone boots instead of raising 'file not found'."""
    _, target = seeded
    monkeypatch.setattr(boot, "_booted", False)
    monkeypatch.setattr(boot, "settings_service", boot.settings_service)
    boot.boot_settings(yaml_path=target)
    assert target.is_file()
    assert isinstance(boot.settings_service.snapshot(), PalliSettings)


def test_the_seed_writes_nothing_at_all_when_the_target_is_present(seeded, caplog):
    """The no-op, proved on a TMP file — never on the repo's own config.

    This replaces a test that called seed_from_example() on the real default path: with
    the exists() guard removed it performed the write BEFORE asserting, so it detected
    the mutation by being the demolition charge. A no-op must leave the bytes, the
    directory listing and the log untouched."""
    _, target = seeded
    target.write_bytes(b"version: 1  # a config already in place\n")
    before, listing = target.read_bytes(), sorted(p.name for p in target.parent.iterdir())
    with caplog.at_level(logging.INFO, logger=boot.logger.name):
        assert boot.seed_from_example(target) is False
    assert target.read_bytes() == before
    assert sorted(p.name for p in target.parent.iterdir()) == listing
    assert [r for r in caplog.records if r.levelno == logging.INFO] == []


def test_the_suite_cannot_reach_the_repos_settings_yaml():
    """C1, the eighth live-state door: boot_settings() can now WRITE the path it loads,
    so tests/conftest.py redirects it beside the database, the identity tree and the
    organ stores. If this goes red the suite is booting the owner's real config again
    and a seed bug becomes a rewrite of it (2026-09-06: it did, once)."""
    default = Path(boot._DEFAULT_YAML_PATH).resolve()
    repo_config = (REPO / "data" / "palli.yaml").resolve()
    assert default != repo_config
    assert REPO not in default.parents, f"{default} is inside the checkout"
    # the module-level service is built at import from the OLD constant — redirecting
    # the constant alone would leave that one still pointed at the real file
    assert Path(boot.settings_service._yaml_path).resolve() == default
    # ...and the redirect must SEED, not just point: snapshot() lazy-loads, and the
    # tests that read the singleton without booting would otherwise get
    # "settings file not found" (four of them did, 2026-09-06).
    assert default.is_file(), "the suite's settings yaml was never seeded"


def test_the_seed_leaves_no_partial_config_when_the_write_fails(seeded, monkeypatch):
    """I4: a truncated data/palli.yaml would be PERMANENT — its existence suppresses the
    next boot's reseed and load() then fails forever with "could not parse", on a
    stranger's first run. The write is atomic (tmp file in the target's own directory →
    fsync → os.replace), so an interrupted copy leaves nothing behind at all."""
    _, target = seeded

    def boom(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr(boot.os, "fsync", boom)
    with pytest.raises(OSError):
        boot.seed_from_example(target)
    assert not target.exists(), "a partial config survived the failed write"
    assert list(target.parent.glob("palli.yaml*")) == [], "a temp file was left behind"


def test_the_seeded_config_is_private_to_its_owner(seeded):
    """Every later settings write chmods this file to 0o600 (settings_service:155); the
    file the seed creates starts there instead of at the process umask."""
    _, target = seeded
    assert boot.seed_from_example(target) is True
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


# --- the shipped artifact --------------------------------------------------

def test_the_shipped_example_exists_and_is_a_valid_config():
    import yaml as pyyaml
    example = REPO / "data" / "palli.example.yaml"
    assert example.is_file(), "data/palli.example.yaml is not committed"
    assert boot._EXAMPLE_YAML_PATH == example
    PalliSettings(**pyyaml.safe_load(example.read_text(encoding="utf-8")))

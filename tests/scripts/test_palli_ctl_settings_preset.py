"""`palli_ctl settings preset` (Ruling 23, final review F4): the yaml half only, and never her
voice's row — that row must stay in step with .env.local, and Layer-1 default-model lookups read it."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import yaml

from core.settings_service import SettingsService

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location("palli_ctl_settings_preset_test_module", REPO_ROOT / "scripts" / "palli_ctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_settings_preset_leaves_her_voice_row_alone_and_says_so(tmp_path, monkeypatch, capsys):
    module = _load_module()
    palli_yaml = tmp_path / "palli.yaml"
    # The SHIPPED config, not his: data/palli.yaml exists in a clone only because
    # tests/conftest.py seeds it, which made this fixture depend on suite order
    # (the assembled public tree, 2026-09-18). The example ships; read that.
    _source = REPO_ROOT / "data" / "palli.yaml"
    if not _source.is_file():
        _source = REPO_ROOT / "data" / "palli.example.yaml"
    shutil.copyfile(_source, palli_yaml)
    svc = SettingsService(yaml_path=palli_yaml)
    svc.load()
    import core.settings_boot as boot
    monkeypatch.setattr(boot, "settings_service", svc)
    monkeypatch.setattr(boot, "boot_settings", lambda **kw: None)
    before = yaml.safe_load(palli_yaml.read_text(encoding="utf-8"))["llm"]["routing"]["response"]
    code = module.main(["settings", "preset", "claude-oauth"])
    out = capsys.readouterr().out
    assert code == 0
    routing = yaml.safe_load(palli_yaml.read_text(encoding="utf-8"))["llm"]["routing"]
    assert routing["decision"] == {"provider": "claude", "model": "sonnet"}
    assert routing["response"] == before
    assert "not moved by this command" in out and "palli_ctl.py brain claude-oauth" in out

"""Apply and undo (spec §3.2 steps 2–7, §4). Nothing here opens a socket or
shells out: the prober and git status are injected. Files are tmp copies."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from core.llm_capabilities import BRAIN_LANE, Level
from core.settings_service import SettingsService
from palli_config import brain
from palli_config.brain import BrainError, apply_plan, load_picture, plan_writes, preflight, resolve_target, undo
from palli_config.probes import ProbeResult

HOME_ENV = (
    "# her brain\r\n"
    "LLM_PROVIDER=deepseek\r\n"
    "LLM_RESPONSE_MODEL=deepseek-v4-flash\r\n"
    "DEEPSEEK_MODEL=deepseek-v4-flash\r\n"
    "OPENAI_API_KEY=sk-test\r\n"
)


@pytest.fixture
def home(tmp_path, isolated_palli_yaml):
    svc = SettingsService(yaml_path=isolated_palli_yaml)
    settings = svc.load()
    proposal = svc.propose(settings.presets["deepseek-primary"].overrides, reason="test")
    if proposal.change_id:
        svc.apply(proposal.change_id)
    env_local = tmp_path / ".env.local"
    env_local.write_bytes(HOME_ENV.encode("utf-8"))
    return {
        "env_local": env_local,
        "yaml": isolated_palli_yaml,
        "service": svc,
        "settings": svc.snapshot(),
        "rollback": tmp_path / "brain-rollback.json",
        "picture": load_picture(env_local=env_local, palli_yaml=isolated_palli_yaml),
    }


def _ok_prober(providers, env, models):
    return {p: ProbeResult(p, "WORKS", True) for p in providers}


def _routing(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["llm"]["routing"]


# --- preflight ------------------------------------------------------------------

def test_preflight_blocks_her_voice_on_the_claude_cli(home):
    target = resolve_target(home["picture"], home["settings"], preset="claude-oauth")
    checks = preflight(home["picture"], target)
    blocks = [c for c in checks if c.level is Level.BLOCK]
    assert len(blocks) == 1 and blocks[0].lane == BRAIN_LANE and blocks[0].suggestion == "deepseek"
    assert "2026-09-02" in blocks[0].detail


def test_preflight_probes_each_new_provider_once_and_says_her_voice_stays(home):
    calls = []

    def prober(providers, env, models):
        calls.append((list(providers), dict(models)))
        return {p: ProbeResult(p, "logged in (claude)", True) for p in providers}

    target = resolve_target(home["picture"], home["settings"], preset="claude-oauth", except_lanes=["response"])
    checks = preflight(home["picture"], target, prober=prober)
    assert calls == [(["claude"], {"claude": "sonnet"})]
    labels = [c.label for c in checks]
    assert labels[0] == "Claude Code (your subscription)" and checks[0].level is Level.OK
    assert any(c.label == "Her voice" and c.detail.startswith("not moving") for c in checks)
    assert not [c for c in checks if c.level is Level.BLOCK]


def test_preflight_turns_a_failed_probe_into_a_warning_not_a_block(home):
    def prober(providers, env, models):
        return {p: ProbeResult(p, "UNREACHABLE (ClientConnectorError)", False) for p in providers}

    target = resolve_target(home["picture"], home["settings"], preset="local-only", except_lanes=["response"])
    checks = preflight(home["picture"], target, prober=prober)
    probe_lines = [c for c in checks if c.label.startswith("Ollama")]  # renamed on the owner's word 2026-09-18 03:13
    assert probe_lines and all(c.level is Level.WARN for c in probe_lines)


# --- plan -----------------------------------------------------------------------

def test_plan_renders_both_halves_and_writes_nothing(home):
    target = resolve_target(home["picture"], home["settings"], preset="openai-primary")
    before = (home["env_local"].read_bytes(), home["yaml"].read_bytes())
    plan = plan_writes(home["picture"], target, env_local=home["env_local"], settings_service=home["service"], checks=[])
    assert (home["env_local"].read_bytes(), home["yaml"].read_bytes()) == before
    assert plan.change_id and plan.yaml_diff
    assert "LLM_PROVIDER=openai" in plan.env_text_after and "\r\n" in plan.env_text_after
    assert plan.env_before == {"LLM_PROVIDER": "deepseek", "LLM_RESPONSE_MODEL": "deepseek-v4-flash", "OPENAI_MODEL": None}
    assert not plan.is_noop


# --- apply ----------------------------------------------------------------------

def test_apply_writes_env_then_yaml_and_records_the_pre_image(home):
    target = resolve_target(home["picture"], home["settings"], preset="openai-primary")
    plan = plan_writes(home["picture"], target, env_local=home["env_local"], settings_service=home["service"], checks=[])
    applied = apply_plan(plan, env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"], git_status=lambda: "")
    assert applied.wrote_env and applied.wrote_yaml
    env_bytes = home["env_local"].read_bytes()
    assert b"LLM_PROVIDER=openai\r\n" in env_bytes and b"# her brain\r\n" in env_bytes and b"OPENAI_API_KEY=sk-test\r\n" in env_bytes
    assert b"OPENAI_MODEL=gpt-4o\r\n" in env_bytes
    assert all(row["provider"] == "openai" for row in _routing(home["yaml"]).values())
    record = json.loads(home["rollback"].read_text(encoding="utf-8"))
    assert record["target"] == "openai-primary"
    assert record["env_before"]["LLM_PROVIDER"] == "deepseek" and record["env_before"]["OPENAI_MODEL"] is None
    assert record["yaml_before"]["llm.routing.response"] == {"provider": "deepseek", "model": "deepseek-v4-flash"}
    if os.name != "nt":
        assert oct(home["rollback"].stat().st_mode & 0o777) == "0o600"


def test_undo_puts_both_halves_back_and_clears_the_record(home):
    original = (home["env_local"].read_bytes(), _routing(home["yaml"]))
    target = resolve_target(home["picture"], home["settings"], preset="openai-primary")
    plan = plan_writes(home["picture"], target, env_local=home["env_local"], settings_service=home["service"], checks=[])
    apply_plan(plan, env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"], git_status=lambda: "")

    fresh = SettingsService(yaml_path=home["yaml"])   # a NEW process: no rollback token
    fresh.load()
    result = undo(env_local=home["env_local"], settings_service=fresh, rollback_path=home["rollback"])
    assert result.env_restored and result.yaml_restored
    assert home["env_local"].read_bytes() == original[0]           # OPENAI_MODEL deleted again, CRLF intact
    assert _routing(home["yaml"]) == original[1]
    assert not home["rollback"].exists()
    with pytest.raises(BrainError, match="nothing to undo"):
        undo(env_local=home["env_local"], settings_service=fresh, rollback_path=home["rollback"])


def test_apply_refuses_when_env_local_is_staged(home):
    target = resolve_target(home["picture"], home["settings"], preset="openai-primary")
    plan = plan_writes(home["picture"], target, env_local=home["env_local"], settings_service=home["service"], checks=[])
    before = (home["env_local"].read_bytes(), home["yaml"].read_bytes())
    with pytest.raises(BrainError, match="staged"):
        apply_plan(plan, env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"], git_status=lambda: "A  .env.local\n")
    assert (home["env_local"].read_bytes(), home["yaml"].read_bytes()) == before
    assert not home["rollback"].exists()


def test_a_failing_yaml_apply_puts_the_env_half_back(home, monkeypatch):
    target = resolve_target(home["picture"], home["settings"], preset="openai-primary")
    plan = plan_writes(home["picture"], target, env_local=home["env_local"], settings_service=home["service"], checks=[])
    before = home["env_local"].read_bytes()

    def boom(change_id):
        raise RuntimeError("disk full")

    monkeypatch.setattr(home["service"], "apply", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        apply_plan(plan, env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"], git_status=lambda: "")
    assert home["env_local"].read_bytes() == before
    assert not home["rollback"].exists()


def test_an_env_only_change_needs_no_yaml_write(home):
    """A pin on her voice to the same provider, different model: yaml row changes too,
    but a preset whose yaml half is a no-op must still write env and still be undoable."""
    svc = home["service"]
    target = resolve_target(home["picture"], home["settings"], preset="deepseek-primary")
    assert not target.moving(home["picture"])   # the home picture IS deepseek-primary
    env_local = home["env_local"]
    env_local.write_bytes(HOME_ENV.replace("LLM_RESPONSE_MODEL=deepseek-v4-flash", "LLM_RESPONSE_MODEL=deepseek-v4-pro").encode("utf-8"))
    picture = load_picture(env_local=env_local, palli_yaml=home["yaml"])
    target = resolve_target(picture, home["settings"], preset="deepseek-primary")
    assert target.moving(picture) == ["response"]
    plan = plan_writes(picture, target, env_local=env_local, settings_service=svc, checks=[])
    assert plan.change_id == ""          # yaml already matches
    applied = apply_plan(plan, env_local=env_local, settings_service=svc, rollback_path=home["rollback"], git_status=lambda: "")
    assert applied.wrote_env and not applied.wrote_yaml
    assert b"LLM_RESPONSE_MODEL=deepseek-v4-flash\r\n" in env_local.read_bytes()
    undo(env_local=env_local, settings_service=svc, rollback_path=home["rollback"])
    assert b"LLM_RESPONSE_MODEL=deepseek-v4-pro\r\n" in env_local.read_bytes()


def test_gitignore_hides_the_rollback_record():
    text = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert "data/brain-rollback.json" in text


def test_a_failing_yaml_undo_leaves_the_env_half_and_the_record_alone(home, monkeypatch):
    """Ruling 15 (2026-09-04): undo reverses the yaml half first — the half that can fail —
    so a failure leaves nothing half-undone: .env.local untouched, the record kept for a retry."""
    target = resolve_target(home["picture"], home["settings"], preset="openai-primary")
    plan = plan_writes(home["picture"], target, env_local=home["env_local"], settings_service=home["service"], checks=[])
    apply_plan(plan, env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"], git_status=lambda: "")
    after_apply = home["env_local"].read_bytes()

    def boom(changes, *, reason=""):
        raise RuntimeError("disk full")

    monkeypatch.setattr(home["service"], "propose", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        undo(env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"])
    assert home["env_local"].read_bytes() == after_apply
    assert home["rollback"].exists()


def test_apply_that_writes_nothing_leaves_the_rollback_record_alone(home):
    """Ruling 16 (2026-09-04): a re-run of the same command must not replace the way home."""
    target = resolve_target(home["picture"], home["settings"], preset="openai-primary")
    plan = plan_writes(home["picture"], target, env_local=home["env_local"], settings_service=home["service"], checks=[])
    apply_plan(plan, env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"], git_status=lambda: "")
    record_before = home["rollback"].read_bytes()
    picture2 = load_picture(env_local=home["env_local"], palli_yaml=home["yaml"])
    target2 = resolve_target(picture2, home["service"].snapshot(), preset="openai-primary")
    plan2 = plan_writes(picture2, target2, env_local=home["env_local"], settings_service=home["service"], checks=[])
    assert plan2.is_noop
    applied = apply_plan(plan2, env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"], git_status=lambda: "")
    assert not applied.wrote_env and not applied.wrote_yaml
    assert home["rollback"].read_bytes() == record_before
    result = undo(env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"])
    assert result.env_restored and result.yaml_restored
    assert b"LLM_PROVIDER=deepseek\r\n" in home["env_local"].read_bytes()


def test_a_stale_preview_is_explained_in_plain_words(home, monkeypatch):
    """Ruling 16: the 60 s proposal TTL is the most reachable failure — never show a change_id."""
    from core.settings_service import SettingsServiceError

    target = resolve_target(home["picture"], home["settings"], preset="openai-primary")
    plan = plan_writes(home["picture"], target, env_local=home["env_local"], settings_service=home["service"], checks=[])
    before = home["env_local"].read_bytes()

    def expired(change_id):
        raise SettingsServiceError(f"change_id {change_id!r} has expired; re-propose the change")

    monkeypatch.setattr(home["service"], "apply", expired)
    with pytest.raises(BrainError) as err:
        apply_plan(plan, env_local=home["env_local"], settings_service=home["service"], rollback_path=home["rollback"], git_status=lambda: "")
    assert "stale" in str(err.value) and "Run the command again" in str(err.value)
    assert "chg_" not in str(err.value)
    assert home["env_local"].read_bytes() == before
    assert not home["rollback"].exists()


def test_undo_reports_only_what_actually_changed(home):
    """Ruling 16: 'Put back X' names the halves that moved, not the halves that had a pre-image."""
    svc = home["service"]
    env_local = home["env_local"]
    env_local.write_bytes(HOME_ENV.replace("LLM_RESPONSE_MODEL=deepseek-v4-flash", "LLM_RESPONSE_MODEL=deepseek-v4-pro").encode("utf-8"))
    picture = load_picture(env_local=env_local, palli_yaml=home["yaml"])
    target = resolve_target(picture, home["settings"], preset="deepseek-primary")
    plan = plan_writes(picture, target, env_local=env_local, settings_service=svc, checks=[])
    apply_plan(plan, env_local=env_local, settings_service=svc, rollback_path=home["rollback"], git_status=lambda: "")
    result = undo(env_local=env_local, settings_service=svc, rollback_path=home["rollback"])
    assert result.env_restored and not result.yaml_restored
    assert result.message.startswith("Put back .env.local.")


def test_preflight_names_a_provider_the_prober_did_not_answer_for(home):
    """Ruling 16: a missing probe result is a warning line, never silence."""
    target = resolve_target(home["picture"], home["settings"], preset="claude-oauth", except_lanes=["response"])
    checks = preflight(home["picture"], target, prober=lambda providers, env, models: {})
    probe_lines = [c for c in checks if c.label == "Claude Code (your subscription)"]
    assert probe_lines and probe_lines[0].level is Level.WARN and "no answer" in probe_lines[0].detail

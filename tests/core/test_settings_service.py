from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from core.settings_schema import PalliSettings
from core.settings_service import SettingsService, SettingsServiceError


def _write_hermetic_yaml(tmp_path: Path) -> Path:
    """Write a minimal, self-contained palli.yaml for propose/apply tests.

    Tests that exercise the propose→apply→rollback lifecycle must not
    depend on the live data/palli.yaml (its curated model lists and
    routing values evolve with the owner's config). This doc pins:
    - routing.response = openai/gpt-4o, so proposing
      deepseek/deepseek-chat is always a valid, non-no-op change;
    - a deepseek provider whose curated list includes deepseek-chat.
    """
    routing_target = {"provider": "openai", "model": "gpt-4o"}
    doc = {
        "version": 1,
        "providers": {
            "deepseek": {"models": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]},
            "openai": {"models": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]},
        },
        "llm": {
            "default_provider": "openai",
            "routing": {
                key: dict(routing_target)
                for key in (
                    "response", "analysis", "decision", "memory",
                    "proactive", "reflection", "web_search", "vision",
                )
            },
        },
        "voice": {
            "engine": "classic",
            "stt_provider": "faster_whisper",
            "tts_provider": "pyttsx3",
            "interaction_mode": "direct_address",
        },
        "policy": {
            "proactive": {"max_per_day": 5, "min_gap_hours": 2},
            "operator": {},
        },
    }
    target = tmp_path / "palli.yaml"
    target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return target


def test_service_loads_existing_yaml(tmp_path: Path):
    # Copy the shipped default into a temp location so the test is isolated
    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    target = tmp_path / "palli.yaml"
    target.write_bytes(repo_yaml.read_bytes())

    svc = SettingsService(yaml_path=target)
    snapshot = svc.load()
    assert isinstance(snapshot, PalliSettings)
    assert svc.snapshot() is snapshot


def test_service_raises_when_file_missing(tmp_path: Path):
    svc = SettingsService(yaml_path=tmp_path / "missing.yaml")
    with pytest.raises(SettingsServiceError) as err:
        svc.load()
    assert "missing" in str(err.value).lower() or "not found" in str(err.value).lower()


def test_service_raises_on_invalid_schema(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 99\n", encoding="utf-8")
    svc = SettingsService(yaml_path=bad)
    with pytest.raises(SettingsServiceError):
        svc.load()


def test_service_raises_on_unparseable_yaml(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    # Invalid YAML: unquoted colon inside a key
    bad.write_text("key: : nope\n", encoding="utf-8")
    svc = SettingsService(yaml_path=bad)
    with pytest.raises(SettingsServiceError) as err:
        svc.load()
    assert "parse" in str(err.value).lower() or "could not" in str(err.value).lower()


def test_service_atomic_write_roundtrips(tmp_path: Path):
    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    target = tmp_path / "palli.yaml"
    target.write_bytes(repo_yaml.read_bytes())

    svc = SettingsService(yaml_path=target)
    original = svc.load()

    svc._atomic_write(original)  # noqa: SLF001 — exercising internal helper directly

    # Re-read from disk and confirm parse is still valid + equivalent
    reloaded = SettingsService(yaml_path=target).load()
    assert reloaded.model_dump(mode="json") == original.model_dump(mode="json")


def test_service_atomic_write_cleans_temp_on_failure(tmp_path: Path, monkeypatch):
    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    target = tmp_path / "palli.yaml"
    target.write_bytes(repo_yaml.read_bytes())

    svc = SettingsService(yaml_path=target)
    snapshot = svc.load()

    import os

    real_replace = os.replace

    def fail_replace(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        svc._atomic_write(snapshot)  # noqa: SLF001

    monkeypatch.setattr(os, "replace", real_replace)
    # No stray .tmp.* files should be left behind
    tmp_siblings = list(tmp_path.glob("palli.yaml.tmp.*"))
    assert tmp_siblings == []


def test_service_propose_returns_change_id_and_diff(tmp_path: Path):
    target = _write_hermetic_yaml(tmp_path)

    svc = SettingsService(yaml_path=target)
    svc.load()

    proposal = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="switching for latency experiment",
    )

    assert proposal.change_id
    assert len(proposal.change_id) >= 16
    assert proposal.diff, "diff must not be empty when a value actually changes"
    # diff entries reference the path that changed
    assert any(entry["path"] == "llm.routing.response" for entry in proposal.diff)


def test_service_propose_rejects_invalid_change(tmp_path: Path):
    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    target = tmp_path / "palli.yaml"
    target.write_bytes(repo_yaml.read_bytes())

    svc = SettingsService(yaml_path=target)
    svc.load()

    with pytest.raises(SettingsServiceError) as err:
        svc.propose(
            {"llm.routing.response": {"provider": "anthropic", "model": "claude-opus-4-6"}},
            reason="invalid",
        )
    assert "anthropic" in str(err.value)


def test_service_propose_no_op_returns_empty_diff(tmp_path: Path):
    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    target = tmp_path / "palli.yaml"
    target.write_bytes(repo_yaml.read_bytes())

    svc = SettingsService(yaml_path=target)
    snapshot = svc.load()
    current = snapshot.llm.routing.response

    proposal = svc.propose(
        {
            "llm.routing.response": {
                "provider": current.provider,
                "model": current.model,
            }
        },
        reason="no-op",
    )
    assert proposal.diff == []
    assert proposal.change_id == ""
    assert svc._pending == {}  # no-op must not consume a pending slot


def test_service_propose_prunes_expired_change(tmp_path: Path):
    from core.settings_service import PROPOSAL_TTL_SECONDS

    target = _write_hermetic_yaml(tmp_path)

    now = {"value": 0.0}
    svc = SettingsService(yaml_path=target)
    svc._clock = lambda: now["value"]  # noqa: SLF001 — deterministic clock for test
    svc.load()

    first = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="first",
    )
    assert first.change_id in svc._pending

    # Advance past the TTL; a follow-up propose triggers _prune_expired
    now["value"] = PROPOSAL_TTL_SECONDS + 1.0
    svc.propose(
        {"llm.routing.analysis": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="second",
    )
    assert first.change_id not in svc._pending, "expired change_id should have been pruned"


def test_service_propose_rejects_list_clobber(tmp_path: Path):
    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    target = tmp_path / "palli.yaml"
    target.write_bytes(repo_yaml.read_bytes())

    svc = SettingsService(yaml_path=target)
    svc.load()

    # providers.openai.models is a list; descending through it should be refused.
    with pytest.raises(SettingsServiceError) as err:
        svc.propose(
            {"providers.openai.models.0.id": "hijacked"},
            reason="list clobber",
        )
    message = str(err.value).lower()
    assert "list" in message or "not a dict" in message or "cannot descend" in message


import json


def test_service_apply_writes_and_returns_rollback_token(tmp_path: Path):
    target = _write_hermetic_yaml(tmp_path)

    svc = SettingsService(yaml_path=target)
    svc.load()
    proposal = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="test",
    )
    result = svc.apply(proposal.change_id)
    assert result.applied is True
    assert result.rollback_token.startswith("rb_")

    # The YAML file now reflects the change
    reloaded = SettingsService(yaml_path=target).load()
    assert reloaded.llm.routing.response.provider == "deepseek"


def test_service_apply_with_unknown_change_id_raises(tmp_path: Path):
    repo_yaml = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    target = tmp_path / "palli.yaml"
    target.write_bytes(repo_yaml.read_bytes())

    svc = SettingsService(yaml_path=target)
    svc.load()
    with pytest.raises(SettingsServiceError):
        svc.apply("chg_does_not_exist")


def test_service_apply_with_expired_change_id_raises(tmp_path: Path):
    target = _write_hermetic_yaml(tmp_path)

    now = {"value": 0.0}

    svc = SettingsService(yaml_path=target)
    svc._clock = lambda: now["value"]  # noqa: SLF001 — deterministic clock for test
    svc.load()
    proposal = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="test",
    )
    now["value"] = 61.0  # past 60s TTL
    with pytest.raises(SettingsServiceError) as err:
        svc.apply(proposal.change_id)
    assert "expire" in str(err.value).lower()


def test_service_apply_appends_audit_log(tmp_path: Path):
    target = _write_hermetic_yaml(tmp_path)

    history = tmp_path / "palli-config-history.jsonl"
    svc = SettingsService(yaml_path=target, history_path=history)
    svc.load()
    proposal = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="audit test",
    )
    svc.apply(proposal.change_id)

    lines = history.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["reason"] == "audit test"
    assert entry["change_id"] == proposal.change_id
    assert entry["diff"]


from core.settings_service import SettingsChanged


def test_service_rollback_restores_previous_snapshot(tmp_path: Path):
    target = _write_hermetic_yaml(tmp_path)

    svc = SettingsService(yaml_path=target)
    original = svc.load()
    proposal = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="rollback test",
    )
    result = svc.apply(proposal.change_id)
    restored = svc.rollback(result.rollback_token)
    assert restored.llm.routing.response.provider == original.llm.routing.response.provider


def test_service_rollback_rejects_expired_token(tmp_path: Path):
    target = _write_hermetic_yaml(tmp_path)

    now = {"value": 0.0}
    svc = SettingsService(yaml_path=target)
    svc._clock = lambda: now["value"]  # noqa: SLF001
    svc.load()
    proposal = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="test",
    )
    result = svc.apply(proposal.change_id)
    now["value"] = 400.0  # past 5-min TTL
    with pytest.raises(SettingsServiceError):
        svc.rollback(result.rollback_token)


def test_service_subscribe_receives_event_on_apply(tmp_path: Path):
    target = _write_hermetic_yaml(tmp_path)

    events = []

    svc = SettingsService(yaml_path=target)
    svc.load()
    svc.subscribe(events.append)

    proposal = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="event test",
    )
    svc.apply(proposal.change_id)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, SettingsChanged)
    assert event.change_id == proposal.change_id
    assert any(entry["path"] == "llm.routing.response" for entry in event.diff)


def test_service_subscribe_receives_event_on_rollback(tmp_path: Path):
    target = _write_hermetic_yaml(tmp_path)

    events = []

    svc = SettingsService(yaml_path=target)
    svc.load()
    svc.subscribe(events.append)

    proposal = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="event test",
    )
    result = svc.apply(proposal.change_id)
    svc.rollback(result.rollback_token)

    assert len(events) == 2
    assert events[1].change_id.startswith("rollback:")

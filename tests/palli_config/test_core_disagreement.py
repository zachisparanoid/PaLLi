"""Disagreement detection (R4, 2026-09-03): the winner is whoever the RUNTIME
listens to. Her voice: env LLM_PROVIDER / LLM_RESPONSE_MODEL win and the yaml
routing.response row is ignored. Every other lane: the yaml wins and the
LLM_<LANE>_* env keys are ignored. The banner names the winner."""
from pathlib import Path

import yaml

from palli_config.core import load_state


def _yaml_row(palli_yaml: Path, lane: str) -> tuple[str, str]:
    doc = yaml.safe_load(palli_yaml.read_text(encoding="utf-8"))
    row = doc["llm"]["routing"][lane]
    return row["provider"], row["model"]


def test_no_disagreement_when_env_and_yaml_agree(tmp_path: Path, isolated_palli_yaml: Path):
    provider, model = _yaml_row(isolated_palli_yaml, "response")
    d_provider, d_model = _yaml_row(isolated_palli_yaml, "decision")
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        f"LLM_PROVIDER={provider}\nLLM_RESPONSE_MODEL={model}\n"
        f"LLM_DECISION_PROVIDER={d_provider}\nLLM_DECISION_MODEL={d_model}\n",
        encoding="utf-8",
    )
    state = load_state(env_local=env_local, palli_yaml=isolated_palli_yaml)
    assert state.disagreements == []


def test_her_voice_disagreement_names_env_as_the_winner(tmp_path: Path, isolated_palli_yaml: Path):
    yaml_provider, _ = _yaml_row(isolated_palli_yaml, "response")
    other = "openai" if yaml_provider != "openai" else "anthropic"
    env_local = tmp_path / ".env.local"
    env_local.write_text(f"LLM_PROVIDER={other}\nLLM_RESPONSE_MODEL=gpt-4o\n", encoding="utf-8")
    state = load_state(env_local=env_local, palli_yaml=isolated_palli_yaml)
    assert state.tasks["response"].provider == other
    hits = [d for d in state.disagreements if d.startswith("task 'response'")]
    assert len(hits) == 1 and "the yaml is ignored for this lane" in hits[0] and other in hits[0]


def test_a_routing_lane_disagreement_names_yaml_as_the_winner(tmp_path: Path, isolated_palli_yaml: Path):
    yaml_provider, yaml_model = _yaml_row(isolated_palli_yaml, "memory")
    other = "openai" if yaml_provider != "openai" else "anthropic"
    env_local = tmp_path / ".env.local"
    env_local.write_text(f"LLM_PROVIDER={yaml_provider}\nLLM_MEMORY_PROVIDER={other}\nLLM_MEMORY_MODEL=gpt-4o\n", encoding="utf-8")
    state = load_state(env_local=env_local, palli_yaml=isolated_palli_yaml)
    assert state.tasks["memory"].provider == yaml_provider
    provider_hits = [d for d in state.disagreements if d.startswith("task 'memory' provider")]
    model_hits = [d for d in state.disagreements if d.startswith("task 'memory' model")]
    assert provider_hits and f"yaml={yaml_provider!r} wins" in provider_hits[0]
    assert model_hits and f"yaml={yaml_model!r} wins" in model_hits[0]

"""2026-09-02: a local Ollama brain through its OpenAI-style endpoint
(`local_openai`). Dispatch names the runner model from LOCAL_LLM_MODEL (it
used to fall back to OPENAI_MODEL) and, when LOCAL_LLM_REASONING_EFFORT is
set, hands the runner `extra_body={"reasoning_effort": ...}` — thinking off
is what makes Qwen actually call tools on this path."""
from __future__ import annotations

from types import SimpleNamespace


def test_runner_model_for_local_openai_is_the_local_model(monkeypatch):
    from core.agent import dispatch as d
    monkeypatch.setattr(d.config, "LOCAL_LLM_MODEL", "qwen3.6-palli", raising=False)
    monkeypatch.setattr(d.config, "OPENAI_MODEL", "gpt-4o", raising=False)
    assert d._default_runner_model("local_openai") == "qwen3.6-palli"
    assert d._default_runner_model("openai") == "gpt-4o"


def test_runner_call_kwargs_carry_reasoning_effort_only_for_local_openai(monkeypatch):
    from core.agent import dispatch as d
    monkeypatch.setattr(d.config, "LOCAL_LLM_REASONING_EFFORT", "none", raising=False)
    assert d._runner_call_kwargs("local_openai") == {"extra_body": {"reasoning_effort": "none"}}
    assert d._runner_call_kwargs("deepseek") == {}
    monkeypatch.setattr(d.config, "LOCAL_LLM_REASONING_EFFORT", "", raising=False)
    assert d._runner_call_kwargs("local_openai") == {}


def test_runner_model_for_the_claude_cli_is_its_own_model(monkeypatch):
    """2026-09-03 (configurator spec §3.3 R5): the table had no `claude` row, so a
    Claude-CLI brain with LLM_RESPONSE_MODEL unset fell through to OPENAI_MODEL —
    DEFECT_RECORD §22's shape, one provider later."""
    from core.agent import dispatch as d
    monkeypatch.setattr(d.config, "CLAUDE_CLI_MODEL", "sonnet", raising=False)
    monkeypatch.setattr(d.config, "OPENAI_MODEL", "gpt-4o", raising=False)
    assert d._default_runner_model("claude") == "sonnet"


def test_config_declares_the_claude_cli_model_key():
    """Task 3 review (2026-09-04): the runner row, the router and the provider all read
    config.CLAUDE_CLI_MODEL, but core/config.py never declared it — every reader was inert.
    The key must exist on the real Config and be read from the environment like its siblings."""
    from pathlib import Path

    from core.config import config
    assert isinstance(getattr(config, "CLAUDE_CLI_MODEL", None), str)
    source = (Path(__file__).resolve().parents[2] / "core" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("CLAUDE_CLI_MODEL", "")' in source

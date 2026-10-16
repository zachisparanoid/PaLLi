"""R1 (configurator spec 2026-09-03 §3.3): the settings service used to write
`data/palli.yaml` with yaml.safe_dump, which erases every comment — 375 of the
live file's 826 lines. It now writes round-trip through ruamel, setting only
the leaves that changed. Two writers, one fidelity."""
from __future__ import annotations

from pathlib import Path

import yaml

from core.settings_service import SettingsService

REPO_ROOT = Path(__file__).resolve().parents[2]

HERMETIC = """\
version: 1
providers:
  # the openai catalogue — a comment on a provider
  openai:
    models:
      - id: gpt-4o
      - id: gpt-4o-mini
  deepseek:
    models:
      - id: deepseek-chat
llm:
  default_provider: openai
  # HOUSE NOTE: the routing block — keep me
  routing:
    response: {provider: openai, model: gpt-4o}   # her voice
    analysis: {provider: openai, model: gpt-4o}
    decision: {provider: openai, model: gpt-4o}
    memory: {provider: openai, model: gpt-4o}
    proactive: {provider: openai, model: gpt-4o}
    reflection: {provider: openai, model: gpt-4o}
    web_search: {provider: openai, model: gpt-4o}
    vision: {provider: openai, model: gpt-4o}
voice:
  engine: classic
  stt_provider: faster_whisper
  tts_provider: pyttsx3
  interaction_mode: direct_address
policy:
  proactive: {max_per_day: 5, min_gap_hours: 2}
  operator: {}
presets:
  # a preset whose override KEYS contain dots — the writer must not split them
  go-deepseek:
    overrides:
      llm.routing.response: {provider: deepseek, model: deepseek-chat}
"""


def _comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip().startswith("#")]


def test_apply_keeps_every_comment_and_lands_the_change(tmp_path: Path):
    target = tmp_path / "palli.yaml"
    target.write_text(HERMETIC, encoding="utf-8")
    svc = SettingsService(yaml_path=target)
    svc.load()

    proposal = svc.propose(
        {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}},
        reason="comment test",
    )
    svc.apply(proposal.change_id)

    after = target.read_text(encoding="utf-8")
    assert _comment_lines(after) == _comment_lines(HERMETIC)
    assert "# her voice" in after
    data = yaml.safe_load(after)
    assert data["llm"]["routing"]["response"] == {"provider": "deepseek", "model": "deepseek-chat"}
    assert data["llm"]["routing"]["analysis"] == {"provider": "openai", "model": "gpt-4o"}
    # dotted preset keys were left exactly as they were
    assert data["presets"]["go-deepseek"]["overrides"]["llm.routing.response"]["model"] == "deepseek-chat"
    SettingsService(yaml_path=target).load()  # still validates


def test_rollback_keeps_the_comments_too(tmp_path: Path):
    target = tmp_path / "palli.yaml"
    target.write_text(HERMETIC, encoding="utf-8")
    svc = SettingsService(yaml_path=target)
    svc.load()
    result = svc.apply(
        svc.propose({"llm.routing.response": {"provider": "deepseek", "model": "deepseek-chat"}}).change_id
    )
    svc.rollback(result.rollback_token)
    after = target.read_text(encoding="utf-8")
    assert _comment_lines(after) == _comment_lines(HERMETIC)
    assert yaml.safe_load(after)["llm"]["routing"]["response"]["provider"] == "openai"


def test_the_live_file_keeps_its_comment_count_across_a_write(tmp_path: Path):
    """The landmine as measured: 375 comment lines in data/palli.yaml at b81fd28."""
    src = REPO_ROOT / "data" / "palli.yaml"
    target = tmp_path / "palli.yaml"
    target.write_bytes(src.read_bytes())
    before = len(_comment_lines(target.read_text(encoding="utf-8")))
    assert before > 300, "the live file lost its comments before this test ran"

    svc = SettingsService(yaml_path=target)
    snap = svc.load()
    current = snap.llm.routing.web_search
    # Any CATALOGUED pair that differs makes the same real write; same provider first,
    # which is what this used to demand outright. Step 28 task 5: the shipped config
    # (data/palli.example.yaml, the local-only preset) routes this lane at a provider
    # whose catalogue holds exactly ONE model, so "another model from the same provider"
    # does not exist in a fresh clone and this raised StopIteration there — while
    # passing here, on the owner's multi-model config, for as long as it has existed.
    catalogue = [(name, m.id) for name, prov in snap.providers.items() for m in prov.models]
    other_provider, other_model = next(
        pair for pair in sorted(catalogue, key=lambda pair: pair[0] != current.provider)
        if pair != (current.provider, current.model)
    )
    proposal = svc.propose(
        {"llm.routing.web_search": {"provider": other_provider, "model": other_model}}
    )
    assert proposal.diff, "pick a pair that differs so there is a real write"
    svc.apply(proposal.change_id)

    after_text = target.read_text(encoding="utf-8")
    assert len(_comment_lines(after_text)) == before
    assert yaml.safe_load(after_text)["llm"]["routing"]["web_search"]["model"] == other_model
    SettingsService(yaml_path=target).load()


def test_a_fresh_file_is_still_written_in_full(tmp_path: Path):
    """No file on disk → a plain round-trip dump of the whole snapshot (apply on
    a service whose yaml was deleted between load and apply)."""
    target = tmp_path / "palli.yaml"
    target.write_text(HERMETIC, encoding="utf-8")
    svc = SettingsService(yaml_path=target)
    svc.load()
    proposal = svc.propose({"llm.default_provider": "deepseek"})
    target.unlink()
    svc.apply(proposal.change_id)
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["llm"]["default_provider"] == "deepseek"


def test_a_file_ruamel_cannot_round_trip_is_written_in_full_with_a_warning(tmp_path: Path, caplog):
    """Task 4 review (2026-09-04): PyYAML tolerates a duplicate key, ruamel's round-trip
    loader rejects it. The write must still land (a full dump, comments lost — the only
    correct thing left to do) and it must SAY SO in the log, never silently."""
    import logging

    target = tmp_path / "palli.yaml"
    target.write_text(HERMETIC + "version: 1\n", encoding="utf-8")   # a duplicate top-level key
    svc = SettingsService(yaml_path=target)
    svc.load()
    proposal = svc.propose({"llm.default_provider": "deepseek"})
    with caplog.at_level(logging.WARNING, logger="core.settings_service"):
        svc.apply(proposal.change_id)
    assert any("comments" in rec.getMessage() and str(target) in rec.getMessage() for rec in caplog.records), caplog.text
    after = target.read_text(encoding="utf-8")
    assert yaml.safe_load(after)["llm"]["default_provider"] == "deepseek"
    assert after.count("version:") == 1          # the full dump is a clean, valid file
    SettingsService(yaml_path=target).load()


def test_long_flow_rows_are_never_reflowed(tmp_path: Path):
    """Ruling 8 (2026-09-04): ruamel folds lines past 80 columns by default. A preset
    row can be 82 wide (local-only's narrative_author). An unrelated write must leave
    such a line exactly as it was — one line, no trailing space."""
    long_row = (
        "      llm.routing.narrative_author: {provider: local_openai, model: qwen3.6-palli-with-a-longer-name}\n"
    )
    assert len(long_row.rstrip("\n")) > 80
    text = HERMETIC.replace(
        "      llm.routing.response: {provider: deepseek, model: deepseek-chat}\n",
        "      llm.routing.response: {provider: deepseek, model: deepseek-chat}\n" + long_row,
    )
    assert long_row in text
    target = tmp_path / "palli.yaml"
    target.write_text(text, encoding="utf-8")
    svc = SettingsService(yaml_path=target)
    svc.load()
    svc.apply(svc.propose({"llm.default_provider": "deepseek"}).change_id)
    after = target.read_text(encoding="utf-8")
    assert long_row in after, after
    assert after.count("\n") == text.count("\n")

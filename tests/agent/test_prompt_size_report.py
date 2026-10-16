"""Batch 7 (her voice), Task 0: the prompt size report.

``scripts/prompt_size_report.py`` measures her system prompt before any
wording changes. Later tasks assert against its numbers, so it must give the
same numbers in a bare script, under pytest and on CI. It pins the shipped
flag world itself and restores every attribute it patched. These tests use
its own public builder (``representative_ctx``); nothing is imported from
another test module. Fast, no network, no live data.
"""
from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from scripts import prompt_size_report as psr

BLOCK_FUNCTIONS = {
    "_self_possession_block",
    "_where_you_live_block",
    "_duplex_block",
    "_standards_block",
    "_own_thoughts_block",
    "_repair_block",
    "_self_notes_block",
    "_workbench_block",
    "_forge_block",
    "_texting_voice_block",
    "render_duplex_block",
}


@pytest.fixture
def representative_ctx():
    return psr.representative_ctx()


def test_measure_reports_sizes_and_counts(representative_ctx):
    report = psr.measure(representative_ctx)
    assert report["stable_chars"] > 1000 and report["volatile_chars"] > 0
    assert report["stable_chars"] == len(report["stable_text"])
    assert report["volatile_chars"] == len(report["volatile_text"])
    assert isinstance(report["prohibitions"], int)
    assert isinstance(report["volatile_prohibitions"], int)
    assert report["prohibitions"] == len(psr.PROHIBITION_RE.findall(report["stable_text"]))
    assert all(isinstance(q, str) for q in report["scripted_quotes"])
    assert all(isinstance(q, str) for q in report["skills_quotes"])
    assert report["skills_quotes"], "the catalogue's quoted trigger phrases are reported on their own"
    assert not set(report["skills_quotes"]) & set(report["scripted_quotes"])
    assert set(report["blocks"]) >= {"identity_core", "constitution", "voice_identity", "skills"}
    assert set(report["blocks"]) >= BLOCK_FUNCTIONS
    assert set(report["block_prohibitions"]) == set(report["blocks"])
    assert all(isinstance(n, int) for n in report["blocks"].values())
    assert report["blocks"]["constitution"] < report["blocks"]["identity_core"]


def test_prohibition_regex_counts_the_forms_we_ban():
    text = "Never do X. Don't do Y. Do not do Z. No apologising. You may do W."
    assert len(psr.PROHIBITION_RE.findall(text)) == 4
    assert psr.PROHIBITION_RE.findall(text) == ["Never", "Don't", "Do not", "No "]
    assert len(psr.PROHIBITION_RE.findall("I don’t pad.")) == 1
    assert psr.PROHIBITION_RE.findall("Nevertheless, she answers.") == []


def test_scripted_regex_finds_quoted_example_lines():
    text = 'celebrate ("hell yeah, LET\'S GO") and say "ah, I think I read that wrong".'
    assert len(psr.SCRIPTED_RE.findall(text)) == 2
    assert psr.find_scripted_quotes(text) == ["hell yeah, LET'S GO", "ah, I think I read that wrong"]
    duplex_line = "'give me a sec', 'I'll have it', 'I'm watching for it' — while"
    assert psr.find_scripted_quotes(duplex_line) == ["give me a sec", "I'll have it", "I'm watching for it"]
    assert psr.find_scripted_quotes('use "local_shell" or `read_file`') == []


def test_the_cli_prints_the_number_pytest_measures(capsys):
    report = psr.measure(psr.representative_ctx(), quiet_choice=True)
    assert psr.main() == 0
    out = capsys.readouterr().out
    assert f"| Stable chars | {report['stable_chars']:,} |" in out
    assert f"| Stable prohibitions | {report['prohibitions']:,} |" in out
    assert f"| Volatile chars | {report['volatile_chars']:,} |" in out
    assert psr.measure(psr.representative_ctx(), quiet_choice=True) == report


def test_stable_text_carries_the_constitution_and_the_shipped_blocks(representative_ctx):
    report = psr.measure(representative_ctx)
    stable = report["stable_text"]
    assert "### Core Identity" in stable
    assert "### Voice Identity" in stable
    assert "<capabilities>" in stable
    # the shipped world is on even though the test conftest keeps these off
    assert "## When I'm working" in stable
    assert "## My forge" in stable
    assert f"The NAS at {psr.FAKE_NAS_ROOT} " in stable
    # the person line and the memory preamble ride every real install
    assert "Your person: " in stable
    assert "BACKGROUND REFERENCE" in stable
    # quiet_choice=True is the headline (she_decides is on live)
    assert "`stay_quiet`" in stable
    off = psr.measure(representative_ctx, quiet_choice=False)
    assert "`stay_quiet`" not in off["stable_text"]
    assert "silence is the reply they asked for" in off["stable_text"]


def _patched_attributes():
    import core.agent.builtin_tools as builtin_tools
    import core.agent.duplex as duplex
    import core.agent.where_you_live as where_you_live
    import core.turn.companion_flags as companion_flags
    import core.turn.skills_loader as skills_loader

    return {
        "companion_flags.companion_settings": companion_flags.companion_settings,
        "where_you_live.companion_settings": where_you_live.companion_settings,
        "where_you_live.nas_root": where_you_live.nas_root,
        "duplex.duplex_enabled": duplex.duplex_enabled,
        "builtin_tools._forged_tools_enabled": builtin_tools._forged_tools_enabled,
        "skills_loader.shutil": skills_loader.shutil,
        "skills_loader.shutil.which": skills_loader.shutil.which,
    }


def test_measure_restores_every_patched_attribute():
    before = _patched_attributes()
    psr.measure(psr.representative_ctx())
    psr.measure(psr.FakeCtx(), quiet_choice=False, world=psr.TEST_WORLD)
    after = _patched_attributes()
    for name, original in before.items():
        assert after[name] is original, f"{name} leaked out of measure()"


def test_an_inner_heading_stays_inside_its_doc():
    """A creator doc's body passes through verbatim, so an inner ``### ``
    heading must not split it into a block of its own (fix round 1)."""
    principles = "### Operating Principles\nNever pad.\n\n### How I work\nDon't guess."
    ctx = SimpleNamespace(identity_core_text=(
        "### Core Identity\nI am me.\n\n"
        f"{principles}\n\n"
        "### Capabilities and Boundaries\nI use tools.\n\n"
        "### Voice Identity\nPlain words."
    ))
    report = psr.measure(ctx)
    assert report["blocks"]["operating_principles"] == len(principles)
    assert report["block_prohibitions"]["operating_principles"] == 2
    assert report["block_prohibitions"]["constitution"] == 2
    assert "How I work" not in report["blocks"]


def test_her_own_voice_page_is_never_measured(tmp_path, monkeypatch):
    """Even where her own page exists, the creator's voice copy is measured."""
    expected = psr.representative_ctx().identity_core_text
    core = tmp_path / "identity" / "core"
    core.mkdir(parents=True)
    for doc in (psr.REPO_ROOT / "identity" / "core").glob("*.md"):
        shutil.copyfile(doc, core / doc.name)
    palli = tmp_path / "identity" / "palli"
    palli.mkdir()
    (palli / "VOICE_IDENTITY.md").write_text("Her own page. HER-OWN-PAGE-MARKER\n", encoding="utf-8")
    monkeypatch.setattr(psr, "REPO_ROOT", tmp_path)
    identity = psr.representative_ctx().identity_core_text
    assert "HER-OWN-PAGE-MARKER" not in identity
    assert identity == expected

"""Tests for core.turn.skills_loader — markdown skill catalog reader."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.turn.skills_loader import (
    Skill,
    _parse_frontmatter,
    load_skills,
    render_capabilities_block,
)


def test_parse_frontmatter_extracts_fields():
    text = """---
name: webcam_capture
when: user asks to snap a pic from the webcam
needs: [python, cv2]
---

# Webcam capture

Body content here.
"""
    fields, body = _parse_frontmatter(text)
    assert fields["name"] == "webcam_capture"
    assert fields["when"] == "user asks to snap a pic from the webcam"
    assert fields["needs"] == ("python", "cv2")
    assert "Body content here" in body


def test_parse_frontmatter_handles_missing_frontmatter():
    text = "no frontmatter, just body content"
    fields, body = _parse_frontmatter(text)
    assert fields == {}
    assert body == text


def test_load_skills_reads_real_catalog():
    """Smoke test against the actual prompts/skills/ catalog."""
    skills = load_skills()
    assert len(skills) >= 1, "at least one skill should be loaded"
    names = {s.name for s in skills}
    assert "webcam_capture" in names
    assert "process_query" in names


def test_load_skills_skips_readme():
    skills = load_skills()
    names = {s.name for s in skills}
    assert "README" not in names
    assert "readme" not in names


def test_render_capabilities_block_includes_all_skills_when_given_all():
    """An explicit skills tuple renders every skill it is given (the
    host filter applies only to the DEFAULT catalog — 2026-08-19)."""
    block = render_capabilities_block(load_skills())
    assert "<capabilities>" in block
    assert "</capabilities>" in block
    assert "webcam_capture" in block
    assert "git_inspect" in block


def test_load_skills_handles_missing_dir(tmp_path):
    nonexistent = tmp_path / "definitely-not-here"
    skills = load_skills(skills_dir=nonexistent)
    assert skills == ()


def test_load_skills_isolates_to_dir(tmp_path):
    (tmp_path / "myskill.md").write_text(
        "---\nname: myskill\nwhen: testing\n---\n\nBody\n",
        encoding="utf-8",
    )
    skills = load_skills(skills_dir=tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "myskill"
    assert skills[0].when == "testing"


def test_skill_excerpt_caps_length():
    skill = Skill(
        name="x",
        path=Path("x.md"),
        when="",
        needs=(),
        body="This is a very long body. " * 50,
    )
    assert len(skill.excerpt) <= 200


# ── the catalog advertises only what this host can run (item 6, 2026-08-19) ──

def test_host_filter_drops_skills_whose_needs_are_absent(monkeypatch, tmp_path):
    """The live prompt advertised PowerShell-only skills (app_launch, clipboard,
    screen_capture, webcam…) on a headless Linux VM — 2.6k chars of
    capabilities she does not have, every turn. A need is satisfied when any
    of its 'or'-alternatives names a binary on PATH; a skill needs ALL its
    needs; no needs = always available."""
    from core.turn import skills_loader as sl
    (tmp_path / "a.md").write_text("---\nname: a\nwhen: w\nneeds: [powershell]\n---\nbody\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\nname: b\nwhen: w\nneeds: [git]\n---\nbody\n", encoding="utf-8")
    (tmp_path / "c.md").write_text("---\nname: c\nwhen: w\nneeds: []\n---\nbody\n", encoding="utf-8")
    (tmp_path / "d.md").write_text("---\nname: d\nwhen: w\nneeds: [powershell or cmd]\n---\nbody\n", encoding="utf-8")
    (tmp_path / "e.md").write_text("---\nname: e\nwhen: w\nneeds: [tier1 screenshot tool OR ffmpeg fallback]\n---\nbody\n", encoding="utf-8")
    (tmp_path / "f.md").write_text("---\nname: f\nwhen: w\nneeds: [python, opencv via cv2]\n---\nbody\n", encoding="utf-8")
    present = {"git", "ffmpeg", "python"}
    monkeypatch.setattr(sl.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None)
    skills = sl.load_skills(tmp_path)
    kept = [s.name for s in sl.skills_available_on_host(skills)]
    assert kept == ["b", "c", "e"]
    block = sl.render_capabilities_block(sl.skills_available_on_host(skills))
    assert " - a" not in block and " - d" not in block and " - f" not in block and " - b" in block


# ── an honest capabilities list (Batch 7, her voice — audit 4.8) ───────────
# Audit 4.8 (approved in the fix plan 2026-09-25) supersedes the 2026-08-19
# grouping: the default catalog lists only what this machine can run, plus
# ONE line saying other hosts she has reached may have more, and where their
# notes are. No OS check: the split is the PATH filter, so it works the same
# on a Windows host.

_LINUX_STAND_IN = ("git", "gh", "ffmpeg", "df", "ps", "ping", "uname", "python")


@pytest.fixture
def linux_host(monkeypatch):
    from core.turn import skills_loader as sl
    monkeypatch.setattr(sl.shutil, "which",
                        lambda name: "/usr/bin/x" if name in _LINUX_STAND_IN else None)
    return sl


def test_render_default_lists_only_what_runs_here_and_points_to_the_rest(linux_host):
    """REWRITTEN (was test_render_default_groups_by_where_it_runs, the owner's
    2026-08-19 call: list everything, grouped Here / Elsewhere). Audit 4.8
    supersedes it: the Windows cards cost her prompt 1.5k chars every turn on
    a host where none of them runs. She keeps the way to them: one line says
    another machine she reaches may have more, and where their notes are."""
    block = linux_host.render_capabilities_block()
    for name in ("file_inspect", "git_inspect", "gh_cli", "disk_usage_linux",
                 "process_query_linux", "network_check_linux", "system_info_linux"):
        assert name in block, name
    for name in ("app_launch", "clipboard", "open_url", "webcam_capture", "screen_capture"):
        assert f"- {name}" not in block, name
    assert "- system_info " not in block and "- system_info\n" not in block
    assert block.count("If I reach another machine") == 1
    assert "prompts/skills/." in block


def test_windows_cards_are_not_in_her_prompt_on_this_machine(linux_host):
    block = linux_host.render_capabilities_block()
    assert "powershell" not in block.lower()
    assert "app_launch" not in block
    assert "Elsewhere —" not in block
    assert "screen_capture" not in block       # its only command is Windows gdigrab
    # Fix round 1 (C15.2): conditional — on a fresh install she has reached no
    # other machine.
    assert "If I reach another machine" in block      # one line says other hosts may have more
    assert "I've reached" not in block


def test_no_pointer_line_when_nothing_was_left_out(monkeypatch):
    """The pointer is there only when the PATH filter dropped a card."""
    from core.turn import skills_loader as sl
    monkeypatch.setattr(sl.shutil, "which", lambda name: "/usr/bin/x")
    block = sl.render_capabilities_block()
    assert "app_launch" in block and "webcam_capture" in block
    assert "another machine" not in block


def test_no_dead_field_in_the_capabilities_block(linux_host):
    """``tier1_tool`` was the decide LLM's routing field; that LLM is gone, and
    the block told her to "pick tier1_tool=local_shell". It names her real
    tools now, on both paths."""
    default = linux_host.render_capabilities_block()
    explicit = linux_host.render_capabilities_block(load_skills())
    for block in (default, explicit):
        assert "tier1_tool" not in block
        assert "local_shell" in block and "read_file" in block


def test_the_capabilities_block_is_stable_and_has_no_prohibitions(linux_host):
    """The block sits in the STABLE prefix (Batch 2's cache): nothing in it
    may vary per turn, and it adds nothing to her prohibition count."""
    from scripts.prompt_size_report import count_prohibitions

    first = linux_host.render_capabilities_block()
    assert first == linux_host.render_capabilities_block()
    assert count_prohibitions(first) == 0, first
    assert count_prohibitions(linux_host.render_capabilities_block(load_skills())) == 0

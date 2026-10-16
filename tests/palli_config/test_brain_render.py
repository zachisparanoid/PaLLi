"""The English (spec §3.6). Five rules with tests: no internal name in default
output; every lane and provider has a plain name; changes shown as effects;
refusals end with the next move; every lane accounted for. Plus the four
generated "what this means" sentences."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.llm_capabilities import BRAIN_LANE, LANES, Level, PROVIDERS
from core.settings_service import SettingsService
from palli_config.brain import Check, load_picture, resolve_target
from palli_config.ctl_prefix import ctl_prefix
from palli_config.brain_render import (
    INTERNAL_WORDS,
    render_applied,
    render_pick_menu,
    render_preview,
    render_refusal,
    render_show,
    what_this_means,
)

DEEPSEEK_ENV = "LLM_PROVIDER=deepseek\nLLM_RESPONSE_MODEL=deepseek-v4-flash\nDEEPSEEK_MODEL=deepseek-v4-flash\n"


@pytest.fixture
def home(tmp_path, isolated_palli_yaml):
    """A picture that IS deepseek-primary: env + the yaml routing set from the preset."""
    svc = SettingsService(yaml_path=isolated_palli_yaml)
    settings = svc.load()
    proposal = svc.propose(settings.presets["deepseek-primary"].overrides, reason="test")
    if proposal.change_id:
        svc.apply(proposal.change_id)
    env_local = tmp_path / ".env.local"
    env_local.write_text(DEEPSEEK_ENV, encoding="utf-8")
    picture = load_picture(env_local=env_local, palli_yaml=isolated_palli_yaml)
    return picture, svc.snapshot()


def _assert_no_jargon(text: str) -> None:
    for line in text.splitlines():
        if "palli_ctl " in line:
            continue  # a command a person types must name real keys
        for word in INTERNAL_WORDS:
            assert word not in line, f"{word!r} leaked into: {line!r}"


# --- rule 1: the jargon ban ------------------------------------------------------

def test_show_is_free_of_internal_names_and_verbose_has_them(home):
    picture, _ = home
    plain = render_show(picture)
    _assert_no_jargon(plain)
    loud = render_show(picture, verbose=True)
    assert "openai_compat" in loud and "llm.default_provider" in loud and "LLM_PROVIDER" in loud


def test_preview_refusal_pick_and_applied_are_free_of_internal_names(home):
    picture, settings = home
    target = resolve_target(picture, settings, preset="claude-oauth", except_lanes=["response"])
    checks = [Check("Claude Code CLI", Level.OK, "logged in"), Check("Her voice", Level.OK, "not moving (DeepSeek V4 Flash)")]
    _assert_no_jargon(render_preview(picture, target, checks))
    blocked = resolve_target(picture, settings, preset="claude-oauth")
    block = Check("Her voice", Level.BLOCK, blocked.lanes[BRAIN_LANE][0], lane=BRAIN_LANE, suggestion="deepseek")
    _assert_no_jargon(render_refusal(picture, blocked, [block], alternative=target))
    text, _ = render_pick_menu(picture, settings)
    _assert_no_jargon(text)
    _assert_no_jargon(render_applied())


# --- rule 2/3: plain names, effects not paths -----------------------------------

def test_show_names_the_two_halves_and_every_lane_in_english(home):
    picture, _ = home
    text = render_show(picture)
    assert "HER VOICE" in text and "set in .env.local" in text
    assert "EVERYTHING ELSE" in text and "set in data/palli.yaml" in text
    assert "she can use her hands" in text
    for lane, req in LANES.items():
        if lane != BRAIN_LANE:
            assert req.plain_name in text
    assert "DeepSeek V4 Flash" in text          # a model's display name from the yaml
    assert "your Claude Max" in text            # the subscription lanes say what they cost
    assert f"Change it:  {ctl_prefix()} brain --pick" in text


def test_show_reports_a_lame_brain_and_a_cadence_judge_elsewhere(tmp_path, isolated_palli_yaml):
    env_local = tmp_path / ".env.local"
    env_local.write_text("LLM_PROVIDER=claude\nLLM_RESPONSE_MODEL=sonnet\nCADENCE_MODEL=deepseek::deepseek-v4-flash\n", encoding="utf-8")
    text = render_show(load_picture(env_local=env_local, palli_yaml=isolated_palli_yaml))
    assert "she cannot use her hands" in text
    assert "the shape of her replies is judged by DeepSeek V4 Flash" in text


def test_preview_shows_effects_and_accounts_for_every_lane(home):
    picture, settings = home
    target = resolve_target(picture, settings, preset="claude-oauth", except_lanes=["response"])
    text = render_preview(picture, target, [Check("Claude Code CLI", Level.OK, "logged in")])
    assert "What would change" in text
    assert "Whether to reply at all" in text and "DeepSeek V4 Flash  →  Claude Sonnet" in text
    assert "Not changing: her voice" in text
    assert "looking at pictures you send" in text
    assert "Every one of her ten lanes is accounted for above." in text
    assert "What this means" in text
    assert "Changing 1 file: data/palli.yaml" in text
    assert "llm.routing" not in text
    loud = render_preview(picture, target, [], verbose=True)
    assert "llm.routing.decision" in loud


def test_preview_counts_both_files_when_her_voice_moves(home):
    picture, settings = home
    target = resolve_target(picture, settings, pins={"response": ("openai", "gpt-4o")})
    text = render_preview(picture, target, [])
    assert "Changing 2 files: .env.local, data/palli.yaml" in text
    assert "Her voice — the live conversation" in text and "→  GPT-4o" in text


# --- the four generated sentences ------------------------------------------------

def test_what_this_means_background_move_to_a_subscription(home):
    picture, settings = home
    target = resolve_target(picture, settings, preset="claude-oauth", except_lanes=["response"])
    sentences = what_this_means(picture, target)
    assert any(s.startswith("Seven background lanes stop costing money per token and start counting against your Claude Max weekly quota") for s in sentences)
    assert any("Her voice is untouched" in s and "DeepSeek V4 Flash" in s for s in sentences)
    assert any("Nothing about how she sounds to you changes" in s for s in sentences)


def test_what_this_means_when_her_voice_moves(home):
    picture, settings = home
    target = resolve_target(picture, settings, pins={"response": ("openai", "gpt-4o")})
    sentences = what_this_means(picture, target)
    assert any(s == "Her voice moves to OpenAI. She keeps her hands." for s in sentences)
    assert any("How she sounds to you will change" in s for s in sentences)
    assert not any("stop costing money per token" in s for s in sentences)   # same cost kind


def test_what_this_means_names_a_lane_that_would_quietly_break(home):
    picture, settings = home
    target = resolve_target(picture, settings, pins={"vision": ("deepseek", "deepseek-v4-flash")})
    sentences = what_this_means(picture, target)
    assert any(s.startswith("Looking at pictures you send") and "cannot look at pictures" in s for s in sentences)


def test_what_this_means_free_direction(home):
    picture, settings = home
    target = resolve_target(picture, settings, preset="local-only", except_lanes=["response"])
    sentences = what_this_means(picture, target)
    assert any("start running free on your desktop" in s for s in sentences)


# --- rule 4: refusals end with the next move ------------------------------------

def test_refusal_names_the_harm_and_the_command_they_meant(home):
    picture, settings = home
    blocked = resolve_target(picture, settings, preset="claude-oauth")
    alternative = resolve_target(picture, settings, preset="claude-oauth", except_lanes=["response"])
    verdict = picture.lanes[BRAIN_LANE].verdict
    block = Check("Her voice", Level.BLOCK, PROVIDERS["claude"].plain_name + " " + PROVIDERS["claude"].note, lane=BRAIN_LANE, suggestion="deepseek")
    text = render_refusal(picture, blocked, [block], alternative=alternative)
    assert "Not doing that." in text
    assert "You asked to move HER VOICE to Claude Code (your subscription)." in text
    assert "2026-09-02" in text and "never be able to act" in text
    assert "Nothing was changed." in text
    assert f"{ctl_prefix()} brain claude-oauth --except response" in text
    assert "move the seven background lanes; leave her voice where it is" in text


def test_refusal_for_a_pin_offers_the_provider_that_can(home):
    picture, settings = home
    target = resolve_target(picture, settings, pins={"response": ("ollama", "qwen3.5:9b")})
    block = Check("Her voice", Level.BLOCK, "…", lane=BRAIN_LANE, suggestion="local_openai")
    text = render_refusal(picture, target, [block])
    assert "Ollama" in text  # renamed on the owner's word 2026-09-18 03:13 (the old label named his own box)
    assert f"{ctl_prefix()} brain --lane response --to local_openai/" in text


# --- --pick ---------------------------------------------------------------------

def test_pick_menu_marks_today_and_offers_a_voice_unchanged_variant_for_blocked_presets(home):
    picture, settings = home
    text, choices = render_pick_menu(picture, settings)
    lines = text.splitlines()
    today = next(l for l in lines if "what she runs today" in l)
    assert any("deepseek-primary" in prev for prev in lines[: lines.index(today) + 1])
    labels = [c.label for c in choices]
    assert any("claude-oauth" in l and "voice unchanged" in l for l in labels)
    variant = next(c for c in choices if c.preset == "claude-oauth" and c.except_lanes == ("response",))
    assert variant.except_lanes == ("response",)
    assert "q  quit" in text
    assert "her voice can't move here" in text


def test_applied_talks_about_time_and_undo():
    text = render_applied()
    assert "Applied." in text
    assert "takes effect when PaLLi next restarts" in text
    assert f"{ctl_prefix()} brain --undo" in text


def test_preview_keeps_her_voice_row_readable(home):
    """F1 (2026-09-04): the 33-character lane name must never run into its model."""
    import re

    picture, settings = home
    target = resolve_target(picture, settings, pins={"response": ("openai", "gpt-4o")})
    text = render_preview(picture, target, [])
    assert re.search(r"Her voice — the live conversation\s+DeepSeek V4 Flash  →  GPT-4o", text), text


def test_refusal_never_offers_a_command_that_moves_nothing(home, tmp_path, isolated_palli_yaml):
    """F2 (2026-09-04, rule 4): background already on Claude, voice on DeepSeek, he asks for
    claude-oauth — the only lane that would move is her voice, and it cannot. No fake next move."""
    picture, settings = home
    svc = SettingsService(yaml_path=isolated_palli_yaml)
    s = svc.load()
    background = {k: v for k, v in s.presets["claude-oauth"].overrides.items() if k != "llm.routing.response"}
    svc.apply(svc.propose(background, reason="test").change_id)
    picture = load_picture(env_local=tmp_path / ".env.local", palli_yaml=isolated_palli_yaml)
    settings = svc.snapshot()
    blocked = resolve_target(picture, settings, preset="claude-oauth")
    alternative = resolve_target(picture, settings, preset="claude-oauth", except_lanes=["response"])
    assert alternative.moving(picture) == []
    block = Check("Her voice", Level.BLOCK, PROVIDERS["claude"].plain_name + " " + PROVIDERS["claude"].note, lane=BRAIN_LANE, suggestion="deepseek")
    text = render_refusal(picture, blocked, [block], alternative=alternative)
    assert "0 background" not in text
    assert "already on that setup" in text and f"{ctl_prefix()} brain --pick" in text
    assert "--except response" not in text


def test_show_keeps_the_hands_answer_when_her_voice_only_warns(tmp_path, isolated_palli_yaml):
    """F4+F5 (2026-09-04): no model named for her voice — DeepSeek CAN use her hands, the warning
    is about the model, and the model column says why it shows the OpenAI default."""
    env_local = tmp_path / ".env.local"
    env_local.write_text("LLM_PROVIDER=deepseek\n", encoding="utf-8")
    text = render_show(load_picture(env_local=env_local, palli_yaml=isolated_palli_yaml))
    assert "she can use her hands" in text
    assert "⚠" in text and "OpenAI default" in text
    assert "(the OpenAI default — no model is named for her voice)" in text
    assert "GPT-4o (the OpenAI default" in text   # the display name, found in another provider's catalogue


def test_pick_summary_tells_the_truth_about_a_mixed_setup(home):
    """F3 (2026-09-04): hybrid puts her voice on Claude (API); the menu must not call it free.
    Final review F2: the summary now sits on its own lines under the row, so read the whole menu."""
    picture, settings = home
    text, _ = render_pick_menu(picture, settings)
    assert [l for l in text.splitlines() if l.rstrip().endswith("  hybrid")], text
    assert "Claude (API) for her voice" in text and "for the background" in text
    assert "the most expensive" in text and "the background running free on your desktop" in text
    variant = next(l for l in text.splitlines() if l.strip().startswith("Background") and "stays where it is" in l)
    assert "Background mostly on" in variant or "Background on" in variant


def test_pick_menu_fits_a_terminal(home):
    """Final review F2 (spec §7 step 2): the menu is read on a terminal; no line may exceed 100 columns."""
    picture, settings = home
    text, choices = render_pick_menu(picture, settings)
    widest = max(len(line) for line in text.splitlines())
    assert widest <= 100, f"{widest} columns: " + next(l for l in text.splitlines() if len(l) == widest)
    assert len(choices) == 13


def test_what_this_means_names_the_background_default_when_it_moves(home):
    """Final review F5 (plan finding 4): llm.default_provider is not inert — unrouted callers follow it."""
    picture, settings = home
    target = resolve_target(picture, settings, preset="claude-oauth", except_lanes=["response"])
    sentences = what_this_means(picture, target)
    assert any(s.startswith("Anything of hers without a lane of its own follows the background default: DeepSeek V4 → Claude Code (your subscription).") for s in sentences)
    pin = resolve_target(picture, settings, pins={"vision": ("openai", "gpt-4o")})
    assert not any("background default" in s for s in what_this_means(picture, pin))

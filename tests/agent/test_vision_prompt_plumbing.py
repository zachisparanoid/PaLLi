"""Regression tests for the 2026-07 vision fix.

Vision was silently broken on the ReAct path for ~2 months (2026-05 → 2026-07):
recall called a non-existent vision API, the Claude CLI provider's vision was a
stub, and — deepest — the ReAct prompt never rendered image descriptions at all,
so even a working result was dropped before it reached PaLLi's brain. PaLLi then
confabulated from memory/context instead of saying "I can't see it".

These deterministic checks guard the pieces that must stay true. The live
end-to-end path (Claude CLI actually reading an image) is exercised manually,
not here, since it hits the real CLI + subscription auth.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.agent.context_adapter import _render_images
from core.agent.system_prompt import build_system_prompt


def test_claude_provider_advertises_vision():
    """If supports_vision flips back to False, the vision router silently
    falls back to another provider (e.g. dead OpenAI) — the exact regression
    that produced the confabulation incident."""
    from ai.llm_claude_cli import ClaudeCLIProvider

    assert ClaudeCLIProvider().supports_vision is True


def test_image_analysis_reaches_react_system_prompt():
    """Layer C: a real image description must render into the ReAct prompt.
    Before the fix, image_descriptions was consumed only by the pipeline
    path and never by core/agent, so vision output never reached the brain."""
    ctx = SimpleNamespace(
        image_descriptions=("A red circle on the left and a blue rectangle on the right.",)
    )
    images_text = _render_images(ctx)
    assert "red circle" in images_text.lower()

    prompt = build_system_prompt(SimpleNamespace(images_text=images_text), cadence_directive="")
    assert "red circle" in prompt.lower(), "image analysis did not reach the ReAct system prompt"


def test_honesty_guard_note_reaches_prompt():
    """When vision yields nothing, recall substitutes an explicit 'could not
    be analyzed' note. It must flow through to the prompt so PaLLi asks
    instead of reconstructing a confident answer from memory."""
    guard = (
        "[An image was attached, but it could not be analyzed — its visual "
        "contents are unavailable to you. Do NOT describe or guess what the "
        "image shows; acknowledge you can't see it clearly and ask the user "
        "what it is.]",
    )
    images_text = _render_images(SimpleNamespace(image_descriptions=guard))
    prompt = build_system_prompt(SimpleNamespace(images_text=images_text), cadence_directive="")
    assert "could not be analyzed" in prompt.lower()
    assert "ask the user" in prompt.lower()


def test_no_image_renders_nothing():
    """No attached image → no image section (keeps the prompt clean)."""
    assert _render_images(SimpleNamespace(image_descriptions=())) == ""
    assert _render_images(SimpleNamespace(image_descriptions=("",))) == ""

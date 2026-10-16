"""Batch 3 (What She Sees), 1.1 — she reads the text in your images.

Before: the vision model's guess at what the picture MEANS in the
conversation (``contextual_understanding``) was handed to her as the image,
and the prompt never asked for the text in it. Now the prompt asks for the
visible text first; she is handed that text and what the picture shows —
never the guess at meaning — and the image is saved to her inbox so she can
look again.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pytest

from core.turn import inbox
from core.turn.attachments import _compose_image_text, _image_ext, describe_images
from core.turn.types import ImageRef, Turn

ROOT = Path(__file__).resolve().parents[2]


# --- the composed text (the brief's two tests, verbatim) ---------------------

def test_visible_text_comes_first_then_description():
    out = _compose_image_text({"visible_text": "Mon 9:00 Dentist\nTue 14:30 Call Sam", "description": "a printed weekly schedule on a fridge", "contextual_understanding": "they want help planning"})
    assert out.startswith("Text in the image:\nMon 9:00 Dentist\nTue 14:30 Call Sam")
    assert "What it shows: a printed weekly schedule" in out
    assert "they want help planning" not in out  # the guess at meaning is not handed to her as fact


def test_no_text_means_no_text_line():
    out = _compose_image_text({"visible_text": "", "description": "a golden retriever on a couch"})
    assert out == "What it shows: a golden retriever on a couch"


def test_the_whole_composed_text_is_text_then_what_it_shows():
    out = _compose_image_text({"visible_text": "OPEN 9-5", "description": "a shop sign"})
    assert out == "Text in the image:\nOPEN 9-5\nWhat it shows: a shop sign"


@pytest.mark.parametrize("result", [
    {"description": "a lathe bed", "contextual_understanding": "a lathe bed"},  # the providers' non-JSON fallback: no key
    {"visible_text": None, "description": "a lathe bed"},
    {"visible_text": ["not", "a", "string"], "description": "a lathe bed"},
    {"visible_text": "   ", "description": "a lathe bed"},
])
def test_a_missing_none_or_non_string_visible_text_counts_as_empty(result):
    assert _compose_image_text(result) == "What it shows: a lathe bed"


# --- the inbox helper --------------------------------------------------------

def _fixed():
    return datetime(2026, 9, 28, 10, 11, 12)


def test_save_bytes_writes_under_the_root_with_the_stamp_and_safe_name(tmp_path):
    root = tmp_path / "images"
    path = inbox.save_bytes(b"\x89PNG bytes", "image.png", root=root, now=_fixed)
    assert path == root / "20260928-101112-image.png"
    assert path.read_bytes() == b"\x89PNG bytes"
    again = inbox.save_bytes(b"\x89PNG again", "image.png", root=root, now=_fixed)
    assert again == root / "20260928-101112-2-image.png"  # a collision never overwrites
    hostile = inbox.save_bytes(b"x", "../../etc/passwd", root=root, now=_fixed)
    assert hostile.parent == root  # a hostile name cannot escape the inbox


def test_save_bytes_refuses_bytes_over_the_inbox_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "INBOX_MAX_BYTES", 8)
    with pytest.raises(ValueError):
        inbox.save_bytes(b"nine bytes!", "image.png", root=tmp_path / "images")
    assert not (tmp_path / "images").exists()  # nothing written, not even the folder


# --- the whole read: describe_images -----------------------------------------

def _image_turn(url="https://cdn.discordapp.com/attachments/1/2/fridge.png", mime="image/png"):
    return Turn(surface="discord_text", owner_id=42, content="what's on my fridge?",
                images=[ImageRef(url=url, local_path=None, mime=mime)],
                received_at=datetime(2026, 9, 28, 10, 11, 12))


def _stub_vision(monkeypatch, result):
    from ai import vision_analyzer as va

    async def fake_analyze(**kwargs):
        return result

    monkeypatch.setattr(va.vision_analyzer, "analyze_image_with_context", fake_analyze)


async def _fetch(url):
    return b"\x89PNG fake bytes"


@pytest.mark.asyncio
async def test_she_is_handed_the_text_what_it_shows_and_where_the_image_went(monkeypatch, tmp_path):
    monkeypatch.setenv("PALLI_INBOX_DIR", str(tmp_path))
    _stub_vision(monkeypatch, {
        "visible_text": "Mon 9:00 Dentist",
        "description": "a printed weekly schedule on a fridge",
        "contextual_understanding": "they want help planning",
    })
    out = await describe_images(_image_turn(), [], fetch=_fetch)
    saved = list((tmp_path / "images").iterdir())
    assert len(saved) == 1 and saved[0].read_bytes() == b"\x89PNG fake bytes"
    assert re.fullmatch(r"\d{8}-\d{6}-image\.png", saved[0].name)
    assert out == ((
        "Text in the image:\nMon 9:00 Dentist\n"
        "What it shows: a printed weekly schedule on a fridge\n"
        f"(saved at {saved[0]})"
    ),)


@pytest.mark.asyncio
async def test_a_failed_save_still_hands_her_the_text(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("PALLI_INBOX_DIR", str(tmp_path))
    _stub_vision(monkeypatch, {"visible_text": "", "description": "a golden retriever on a couch"})

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(inbox, "save_bytes", boom)
    with caplog.at_level(logging.WARNING, logger="core.turn.attachments"):
        out = await describe_images(_image_turn(), [], fetch=_fetch)
    assert out == ("What it shows: a golden retriever on a couch",)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "disk full" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_an_image_over_the_inbox_cap_is_described_but_not_saved(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("PALLI_INBOX_DIR", str(tmp_path))
    monkeypatch.setattr(inbox, "INBOX_MAX_BYTES", 4)
    _stub_vision(monkeypatch, {"visible_text": "", "description": "a golden retriever on a couch"})
    with caplog.at_level(logging.WARNING, logger="core.turn.attachments"):
        out = await describe_images(_image_turn(), [], fetch=_fetch)
    assert out == ("What it shows: a golden retriever on a couch",)
    assert not (tmp_path / "images").exists()
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 1


@pytest.mark.parametrize("mime, ref, ext", [
    ("image/jpeg", "https://cdn/x/photo.png", "jpg"),  # the content type wins
    ("image/png", "https://cdn/x/photo", "png"),
    ("image/gif", "", "gif"),
    ("image/webp", "", "webp"),
    ("application/octet-stream", "https://cdn/x/scan.TIFF?ex=1&is=2", "tiff"),  # then the ref's suffix, query stripped
    ("", "/home/palli/data/telegram/photo.heic", "heic"),
    ("", "https://cdn/x/noext", "img"),  # then "img"
])
def test_the_saved_name_takes_its_extension_from_the_content_type_then_the_ref(mime, ref, ext):
    assert _image_ext(mime, ref) == ext


# --- the prompt and the analyzer ----------------------------------------------

def test_the_vision_prompt_asks_for_the_text_first_and_keeps_every_placeholder_and_key():
    text = (ROOT / "prompts" / "vision_analysis.txt").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[lines.index("Guidelines:") + 1] == (
        "- First, copy every piece of text you can see, exactly as written — dates, times, "
        'numbers, names, captions — into "visible_text" (empty string if there is none).'
    )
    assert '"visible_text": "..."' in text
    for placeholder in ("{context_summary}", "{conversation_snippet}",
                        "{user_message_context}", "{personality_context}"):
        assert placeholder in text
    for key in ("description", "contextual_understanding", "humor_detected", "humor_type",
                "emotional_tone", "suggested_response", "reference_previous_message", "visual_elements"):
        assert f'"{key}":' in text
    # the schema's braces still survive format(): the JSON block stays {{ }}-escaped
    from core.prompt_format import format_prompt

    rendered = format_prompt(text, context_summary="c", conversation_snippet="s",
                             user_message_context="u", personality_context="p")
    assert '"visible_text": "..."' in rendered and rendered.rstrip().endswith("}")


@pytest.mark.asyncio
async def test_visible_text_survives_the_analyzer_untouched(monkeypatch):
    """R3: analyze_image_with_context returns the parsed JSON object whole, so
    a visible_text the model returns reaches attachments.py as-is."""
    from ai import vision_analyzer as va

    class _LLM:
        async def vision_completion(self, **kwargs):
            return {"visible_text": "OPEN 9-5", "description": "a shop sign",
                    "contextual_understanding": "a shop they might visit"}

    monkeypatch.setattr(va, "llm", _LLM())
    monkeypatch.setattr(va, "get_task_model", lambda task: "stub-vision-model")
    result = await va.vision_analyzer.analyze_image_with_context(
        image_data=b"\x89PNG", content_type="image/png", conversation_history=[], user_message="hi")
    assert result["visible_text"] == "OPEN 9-5"


@pytest.mark.asyncio
async def test_the_analyzer_gives_the_vision_model_room_for_the_text(monkeypatch):
    """Review finding (fix round 1): every vision provider defaults to 1000
    output tokens. A screenshot of an email — the very image this task is
    for — plus the eight other JSON fields overran that, the reply was cut
    off mid-JSON, and the providers' fallback handed her the raw fragment as
    "What it shows". The budget is code: the analyzer passes VISION_MAX_TOKENS."""
    from ai import vision_analyzer as va

    seen: dict = {}

    class _LLM:
        async def vision_completion(self, **kwargs):
            seen.update(kwargs)
            return {"visible_text": "", "description": "a shop sign"}

    monkeypatch.setattr(va, "llm", _LLM())
    monkeypatch.setattr(va, "get_task_model", lambda task: "stub-vision-model")
    await va.vision_analyzer.analyze_image_with_context(
        image_data=b"\x89PNG", content_type="image/png", conversation_history=[], user_message="hi")
    assert va.VISION_MAX_TOKENS >= 4000
    assert seen["max_tokens"] == va.VISION_MAX_TOKENS

"""Batch 1, Task 4 (2026-09-25): the belt on her memory writer.

Nothing credential-shaped is ever SAVED into her memory of him again: a NEW
bullet carrying a key, a private key, a bearer token or an SSN is dropped
whole (the value was the point of the line), and every line that stays goes
through the known-literal redactor. A phone number is KEPT (final fix wave,
2026-09-26): wire masking already hides it from every provider, and the
spec asks for the password or key to stop being saved and the phone to be
MOVED — "remember mom's number" must not be silently lost.

Ruling R4: only NEW secret-shaped lines are dropped. A line already in the
file stays (its literals still redacted) — between the owner's restart and
the one-time scrub, the first reflection must not delete the phone line the
scrub is meant to MOVE.

Every value here is fake: 555-01xx phone numbers, a made-up passphrase.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.turn import reflect
from core.turn.reflect import _clean_memory_markdown
from core.turn.types import ReflectionMemoryUpdates

_FAKE_PASS = "tea-" + "kettle-" + "window-" + "lamp"


def test_secret_shaped_lines_are_dropped_and_literals_redacted(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t.replace("hunter2hunter2", "[REDACTED]"))
    doc = "## Durable Context\n- likes tea\n- ssn is 123-45-6789\n- vault pw hunter2hunter2\n"
    out, dropped = _clean_memory_markdown(doc)
    assert "123-45-6789" not in out and "- likes tea" in out
    assert "[REDACTED]" in out and dropped == 1


def test_a_pre_existing_secret_shaped_line_is_kept_and_its_literal_still_redacted(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t.replace(_FAKE_PASS, "[REDACTED]"))
    old_line = f"- ssn 123-45-6789; vault key half: {_FAKE_PASS}"
    previous = f"## Durable Context\n- likes tea\n{old_line}\n"
    text = previous + "- new id 987-65-4321\n"
    out, dropped = _clean_memory_markdown(text, previous=previous)
    assert dropped == 1
    assert "987-65-4321" not in out                     # the NEW credential-shaped line: gone
    assert "- ssn 123-45-6789; vault key half: [REDACTED]" in out  # the OLD one: kept
    assert _FAKE_PASS not in out


def test_non_bullet_lines_are_never_dropped_only_redacted(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)
    text = "## Durable Context\nsee 555-010-7788 above\n- likes tea"
    out, dropped = _clean_memory_markdown(text)
    assert dropped == 0 and out == text


def test_a_multi_line_literal_is_redacted_whole(monkeypatch):
    from core import secrets_redaction as sr
    block = "<Key>\n  <Data>" + "QUJD" + "REVGR0g=</Data>\n</Key>"
    monkeypatch.setattr(sr, "redact", lambda t: t.replace(block, "[REDACTED]"))
    out, dropped = _clean_memory_markdown(f"## Useful References\n{block}\n- likes tea\n")
    assert out == "## Useful References\n[REDACTED]\n- likes tea\n" and dropped == 0


def test_clean_text_is_byte_for_byte_unchanged(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)
    for text in ("", "## A\n- one\n", "## A\r\n- one\r\n", "## A\n- one"):
        assert _clean_memory_markdown(text, previous="") == (text, 0)


def _fake_doc(content: str):
    doc = SimpleNamespace(content=content, frontmatter={"doc_type": "memory", "version": 3})
    return SimpleNamespace(owner={"memory": doc})


_DOC = (
    "## Durable Context\n"
    "- likes tea\n"
    "- User's phone number is 555.010.2233; said to call anytime (2026-08-01).\n"
    "\n"
    "## Active Threads\n"
    "- [2026-09-01] Three reverse phone lookups (555-010-9999 and two earlier numbers) stalled.\n"
    "\n"
    "## Important Decisions\n"
    "- none\n"
    "\n"
    "## Useful References\n"
    f"- ~/vault.kdbx — a vault with 3 entries; key half: {_FAKE_PASS}\n"
)


async def _apply(content: str, updates: ReflectionMemoryUpdates) -> dict:
    written: dict = {}

    async def fake_update(**kw):
        written.update(kw)

    with patch.object(reflect.identity_service, "compile_for_owner", return_value=_fake_doc(content)), \
         patch.object(reflect.identity_service, "update_raw_document", side_effect=fake_update):
        await reflect._apply_memory_updates(owner_id=1, updates=updates)
    return written


@pytest.mark.asyncio
async def test_the_writer_drops_a_new_secret_line_and_keeps_the_old_ones(monkeypatch, caplog):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t.replace(_FAKE_PASS, "[REDACTED]"))
    caplog.set_level(logging.WARNING, logger="core.turn.reflect")
    written = await _apply(_DOC, ReflectionMemoryUpdates(
        durable_context_add=["Likes green tea", "His id is 987-65-4321", "Mom's number is 555-010-4455"],
    ))
    md = written["markdown"]
    assert "Likes green tea" in md
    assert "987-65-4321" not in md
    # A new phone-shaped bullet is KEPT (final fix wave): masked on the wire,
    # moved by hand — never silently lost.
    assert "Mom's number is 555-010-4455" in md
    # The pre-existing lines the one-time scrub will MOVE stay put.
    assert "User's phone number is 555.010.2233" in md
    assert "555-010-9999" in md
    # A known literal already on disk is redacted on this write.
    assert _FAKE_PASS not in md and "key half: [REDACTED]" in md
    assert "dropped 1 secret-shaped memory line(s)" in caplog.text


@pytest.mark.asyncio
async def test_a_known_literal_on_disk_is_redacted_even_when_the_update_adds_nothing(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t.replace(_FAKE_PASS, "[REDACTED]"))
    # "likes tea" is already there: the append dedups to a no-op, but the
    # belt still moves the content, so the write happens.
    written = await _apply(_DOC, ReflectionMemoryUpdates(durable_context_add=["likes tea"]))
    assert written, "the redaction alone must still be written"
    assert _FAKE_PASS not in written["markdown"]


@pytest.mark.asyncio
async def test_nothing_to_redact_and_nothing_new_means_no_write(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)
    written = await _apply(_DOC, ReflectionMemoryUpdates(durable_context_add=["likes tea"]))
    assert written == {}


# --- Final fix wave (2026-09-26), item 2 ------------------------------------

_FAKE_KEY = "gsk_" + "Fake0Key1" * 3


def test_a_new_phone_bullet_is_kept(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)
    text = "## Durable Context\n- likes tea\n- remember mom's number: (555) 010-7788\n"
    out, dropped = _clean_memory_markdown(text, previous="## Durable Context\n- likes tea\n")
    assert dropped == 0 and out == text


@pytest.mark.parametrize("secret", [_FAKE_KEY, "123-45-6789", "Bearer abcdefghijklmnopqrstuvwxyz0123"])
def test_a_new_key_ssn_or_bearer_bullet_is_dropped(monkeypatch, secret):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)
    text = f"## Durable Context\n- likes tea\n- filed it: {secret}\n"
    out, dropped = _clean_memory_markdown(text, previous="## Durable Context\n- likes tea\n")
    assert dropped == 1
    assert secret not in out and "- likes tea" in out


def test_a_prose_sk_slug_bullet_is_kept(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)
    text = "## Useful References\n- blog: my-sk-" + "tips-and-tricks-for-2026-weekend\n"
    out, dropped = _clean_memory_markdown(text)
    assert dropped == 0 and out == text


def test_a_line_split_at_a_unicode_line_separator_is_not_new(monkeypatch):
    """The section rewriters use ``splitlines()``, which also breaks at
    U+2028; the belt used to read the file on disk with ``split("\\n")``
    only, so the second half of such a line looked NEW and was dropped."""
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)
    previous = "## Durable Context\n- likes tea\u2028- old id 123-45-6789\n"
    text = "## Durable Context\n- likes tea\n- old id 123-45-6789\n"
    out, dropped = _clean_memory_markdown(text, previous=previous)
    assert dropped == 0 and out == text


@pytest.mark.asyncio
async def test_the_writer_keeps_a_pre_existing_line_holding_a_unicode_line_separator(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t)
    doc = "## Durable Context\n- likes tea\u2028- old id 123-45-6789\n"
    written = await _apply(doc, ReflectionMemoryUpdates(durable_context_add=["Likes green tea"]))
    assert "123-45-6789" in written["markdown"]

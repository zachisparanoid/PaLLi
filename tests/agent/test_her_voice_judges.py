"""Batch 7, Task 6 (her voice, 4.5 and 4.7).

4.5: the two mid-task judges (what she says while she works; whether a new
message abandons the reply she is writing) knew her only as the bare stand-in
"You are PaLLi." They now open on her voice page: the `voice_identity` core
doc, body only, read through a small reader cached on (path, mtime). Only a
page she wrote herself counts (ruling F1, the final review's I1): when the
store falls back to the shipped copy in identity/core/, the reader returns ""
and the judges open on "I'm PaLLi.". Call sites pass nothing; the default
does the work, an explicit identity_text still wins, and both judges still
fail open.

4.7: the honesty check's advice taught her to hedge ("If you are inferring or
guessing, say so in those words."). Its advice is one plain line now; the
claim sentence before it and the look-again sentence after it are unchanged.

No test here reads the real identity tree: the judges' tests stub the reader
(`voice_page_text`), and the reader's own tests point the store at tmp_path.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]

STAND_IN_PAGE = (
    "# Voice Identity\n\n"
    "Stand-in voice page for the judge tests: I keep it short and dry, I say what "
    "I found the moment I find it, and I let a quiet beat stay quiet."
)


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "wait, use the other folder"
    metadata: Any = None


def _capture(monkeypatch, response: str) -> dict:
    """Stand in for the provider; keep the rendered system prompt."""
    from ai.llm_client import llm

    seen: dict = {}

    async def fake_chat(messages, **k):
        seen["prompt"] = messages[0]["content"]
        return response

    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    return seen


@pytest.fixture
def voice_page_text(monkeypatch) -> str:
    """Her voice page, as the judges' reader returns it: a distinctive stand-in."""
    from core.agent import steering_reply

    monkeypatch.setattr(steering_reply, "voice_identity_text", lambda: STAND_IN_PAGE)
    return STAND_IN_PAGE


# ── 4.5: the judges open on her voice page ─────────────────────────────────


def test_the_mid_task_judge_gets_her_voice_page(monkeypatch, voice_page_text):
    from core.agent import steering_reply

    seen = _capture(monkeypatch, '{"action": "fold_in", "reply": null}')
    asyncio.run(steering_reply.judge_mid_task(FakeTurn(), session_key="discord_text:1"))
    assert "You are PaLLi." not in seen["prompt"]
    assert voice_page_text[:80] in seen["prompt"]
    assert seen["prompt"].startswith(voice_page_text)


def test_judge_supersede_takes_her_voice_too(monkeypatch, voice_page_text):
    from core.agent import steering_reply

    seen = _capture(monkeypatch, '{"supersede": false, "reason": "a follow-up"}')
    asyncio.run(steering_reply.judge_supersede(FakeTurn(), session_key="discord_text:1"))
    assert "You are PaLLi." not in seen["prompt"]
    assert voice_page_text[:80] in seen["prompt"]
    assert seen["prompt"].startswith(voice_page_text)


def test_an_explicit_identity_text_still_wins(monkeypatch, voice_page_text):
    from core.agent import steering_reply

    explicit = "Explicit identity from a caller, for this test only."
    seen = _capture(monkeypatch, '{"action": "fold_in", "reply": null}')
    asyncio.run(steering_reply.judge_mid_task(
        FakeTurn(), session_key="discord_text:1", identity_text=explicit))
    assert seen["prompt"].startswith(explicit)
    assert voice_page_text[:80] not in seen["prompt"]

    seen = _capture(monkeypatch, '{"supersede": true, "reason": "redirects it"}')
    out = asyncio.run(steering_reply.judge_supersede(
        FakeTurn(), session_key="discord_text:1", identity_text=explicit))
    assert out is True
    assert seen["prompt"].startswith(explicit)
    assert voice_page_text[:80] not in seen["prompt"]


def test_with_no_page_the_judges_open_on_the_neutral_fallback(monkeypatch):
    from core.agent import steering_reply

    monkeypatch.setattr(steering_reply, "voice_identity_text", lambda: "")
    seen = _capture(monkeypatch, '{"action": "fold_in", "reply": null}')
    asyncio.run(steering_reply.judge_mid_task(FakeTurn(), session_key="discord_text:1"))
    assert seen["prompt"].startswith("I'm PaLLi.\n")
    assert "You are PaLLi." not in seen["prompt"]

    seen = _capture(monkeypatch, '{"supersede": false, "reason": "a follow-up"}')
    asyncio.run(steering_reply.judge_supersede(FakeTurn(), session_key="discord_text:1"))
    assert seen["prompt"].startswith("I'm PaLLi.\n")
    assert "You are PaLLi." not in seen["prompt"]


def test_both_judges_still_fail_open_when_the_page_read_breaks(monkeypatch):
    """The reader never raises, but it is called inside each judge's own try:
    even a broken reader costs nothing but this one judgment."""
    from core.agent import steering_reply

    def broken():
        raise RuntimeError("page unreadable")

    monkeypatch.setattr(steering_reply, "voice_identity_text", broken)
    _capture(monkeypatch, '{"action": "stop", "reply": "dropping it"}')
    j = asyncio.run(steering_reply.judge_mid_task(FakeTurn(), session_key="discord_text:1"))
    assert j == steering_reply.SteerJudgment()
    _capture(monkeypatch, '{"supersede": true, "reason": "redirects it"}')
    assert asyncio.run(steering_reply.judge_supersede(
        FakeTurn(), session_key="discord_text:1")) is False


def test_no_bare_stand_in_identity_remains_in_core():
    """D14: no "You are PaLLi." as a whole identity= value anywhere in core/."""
    pattern = re.compile(r'identity\s*=[^\n]*"You are PaLLi\."')
    hits = [
        f"{path.relative_to(REPO)}:{n}"
        for path in sorted((REPO / "core").rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert hits == []


# ── the reader: voice_identity_text() ──────────────────────────────────────

_FRONTMATTER = (
    "---\n"
    "doc_type: voice_identity\n"
    "scope: core\n"
    "voice_presence: warm_companion_like\n"
    "---\n"
)


@pytest.fixture
def tmp_store(monkeypatch, tmp_path):
    """Point the identity store at an empty tmp root (never the real tree)."""
    from core.identity.document_store import IdentityDocumentStore
    from core.identity.service import identity_service

    store = IdentityDocumentStore(identity_root=str(tmp_path))
    monkeypatch.setattr(identity_service, "document_store", store)
    return tmp_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_reader_is_empty_when_neither_page_exists(tmp_store):
    from core.agent import steering_reply

    assert steering_reply.voice_identity_text() == ""


def test_the_reader_returns_the_body_only(tmp_store):
    """UPDATED (ruling F1): her own page, since the shipped copy is no longer
    read. Same check: the body only, the frontmatter stripped."""
    from core.agent import steering_reply

    _write(tmp_store / "palli" / "VOICE_IDENTITY.md",
           _FRONTMATTER + "# Voice Identity\n\nHer page's body.\n")
    text = steering_reply.voice_identity_text()
    assert text == "# Voice Identity\n\nHer page's body."
    assert "doc_type" not in text and "voice_presence" not in text


def test_the_reader_prefers_her_page_over_the_shipped_one(tmp_store):
    from core.agent import steering_reply

    _write(tmp_store / "core" / "VOICE_IDENTITY.md", _FRONTMATTER + "The shipped page.\n")
    _write(tmp_store / "palli" / "VOICE_IDENTITY.md", "Her own page, written by her.\n")
    assert steering_reply.voice_identity_text() == "Her own page, written by her."


def test_with_only_the_shipped_copy_the_judges_open_on_the_neutral_line(tmp_store, monkeypatch):
    """Ruling F1 (final review I1): the creator's copy in identity/core/ is
    not her page. With no page of her own, the reader returns "" and both
    judges open on "I'm PaLLi.", as on a fresh install before this batch."""
    from core.agent import steering_reply

    _write(tmp_store / "core" / "VOICE_IDENTITY.md",
           _FRONTMATTER + "# Voice Identity\n\nSHIPPED-COPY-SENTINEL, the creator's page.\n")
    assert steering_reply.voice_identity_text() == ""

    seen = _capture(monkeypatch, '{"action": "fold_in", "reply": null}')
    asyncio.run(steering_reply.judge_mid_task(FakeTurn(), session_key="discord_text:1"))
    assert seen["prompt"].startswith("I'm PaLLi.\n")
    assert "SHIPPED-COPY-SENTINEL" not in seen["prompt"]

    seen = _capture(monkeypatch, '{"supersede": false, "reason": "a follow-up"}')
    asyncio.run(steering_reply.judge_supersede(FakeTurn(), session_key="discord_text:1"))
    assert seen["prompt"].startswith("I'm PaLLi.\n")
    assert "SHIPPED-COPY-SENTINEL" not in seen["prompt"]


def test_a_page_of_her_own_is_what_the_judges_open_on(tmp_store, monkeypatch):
    """F1's other half: a page she wrote herself (identity/palli/) is used,
    by the reader and by both judges, with the shipped copy beside it."""
    from core.agent import steering_reply

    _write(tmp_store / "core" / "VOICE_IDENTITY.md", _FRONTMATTER + "SHIPPED-COPY-SENTINEL.\n")
    _write(tmp_store / "palli" / "VOICE_IDENTITY.md",
           "# Voice Identity\n\nHER-PAGE-SENTINEL, written by her.\n")
    assert steering_reply.voice_identity_text() == (
        "# Voice Identity\n\nHER-PAGE-SENTINEL, written by her.")

    seen = _capture(monkeypatch, '{"action": "fold_in", "reply": null}')
    asyncio.run(steering_reply.judge_mid_task(FakeTurn(), session_key="discord_text:1"))
    assert seen["prompt"].startswith("# Voice Identity\n\nHER-PAGE-SENTINEL")
    assert "SHIPPED-COPY-SENTINEL" not in seen["prompt"]

    seen = _capture(monkeypatch, '{"supersede": false, "reason": "a follow-up"}')
    asyncio.run(steering_reply.judge_supersede(FakeTurn(), session_key="discord_text:1"))
    assert seen["prompt"].startswith("# Voice Identity\n\nHER-PAGE-SENTINEL")
    assert "SHIPPED-COPY-SENTINEL" not in seen["prompt"]


def test_a_link_to_the_shipped_copy_is_still_the_shipped_copy(tmp_store):
    """The core-copy check compares resolved paths, not strings: a page at
    her path that is only a link to the creator's copy is not her page."""
    from core.agent import steering_reply

    core = _write(tmp_store / "core" / "VOICE_IDENTITY.md", _FRONTMATTER + "The shipped page.\n")
    (tmp_store / "palli").mkdir(parents=True, exist_ok=True)
    (tmp_store / "palli" / "VOICE_IDENTITY.md").symlink_to(core)
    assert steering_reply.voice_identity_text() == ""


def test_an_unreadable_page_is_empty_and_says_why_in_the_debug_log(tmp_store, caplog):
    """Deferred #10: a broken page still returns "" (never raises), and the
    reason is in the debug log instead of vanishing."""
    import logging

    from core.agent import steering_reply

    page = tmp_store / "palli" / "VOICE_IDENTITY.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"\xff\xfe not utf-8 \xff")
    with caplog.at_level(logging.DEBUG, logger=steering_reply.__name__):
        assert steering_reply.voice_identity_text() == ""
    assert [r for r in caplog.records
            if r.name == steering_reply.__name__ and r.levelno == logging.DEBUG]


def test_the_reader_caches_until_the_page_changes(tmp_store):
    """UPDATED (ruling F1): her own page, since the shipped copy is no longer
    read. Same check: cached on (path, mtime)."""
    from core.agent import steering_reply

    page = _write(tmp_store / "palli" / "VOICE_IDENTITY.md", "First version.\n")
    os.utime(page, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    assert steering_reply.voice_identity_text() == "First version."

    # Same mtime: the cached text is served, the file is not re-read.
    page.write_text("Second version.\n", encoding="utf-8")
    os.utime(page, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    assert steering_reply.voice_identity_text() == "First version."

    # Her Monday rewrite moves the mtime: the new page is read.
    os.utime(page, ns=(1_700_000_100_000_000_000, 1_700_000_100_000_000_000))
    assert steering_reply.voice_identity_text() == "Second version."


# ── 4.7: the honesty check advises plainly ─────────────────────────────────

_PLAIN_LINE = (
    "Say only what the results show, plainly. Mention how you know something only "
    "when that would change what they'd do; if you didn't check, say so once, simply."
)


def test_the_honesty_advice_says_it_plainly():
    from core.agent.promise_gate import PromiseVerdict, nudge_text

    text = nudge_text(PromiseVerdict(
        empty_promise=False, ungrounded=True,
        unsupported="that the drive finished", why="the log was empty"))
    assert "say so in those words" not in text
    assert "plainly" in text
    assert "only when" in text
    assert "did not check" not in text
    # Only the advice changed: the claim sentence, the look-again sentence and
    # the wrapper are as they were.
    assert text == (
        "[system: your draft states that the drive finished as something you observed, "
        "but this turn's tool results do not show it — the log was empty. "
        + _PLAIN_LINE
        + " If you need to look again, look again (tools are allowed). Do not mention this note.]"
    )

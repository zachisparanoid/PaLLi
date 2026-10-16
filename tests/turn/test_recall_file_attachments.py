"""DEFECT_RECORD §19 (2026-09-02): recall receives a non-text file into the
inbox and names it in the turn context — the same slot text attachments use,
so the runner, persistence and her next turn need no new plumbing."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.turn.recall as recall
from core.turn.types import TextAttachmentRef, Turn, TurnMetadata
from tests.turn.test_recall_parallel import _stub_clean


@pytest.mark.asyncio
async def test_a_file_attachment_is_received_into_the_inbox_and_named_in_context(monkeypatch, tmp_path):
    _stub_clean(monkeypatch)
    monkeypatch.setenv("PALLI_INBOX_DIR", str(tmp_path))
    monkeypatch.setattr(recall, "_fetch_image_bytes", AsyncMock(return_value=b"PK\x03\x04 bytes"))
    ref = TextAttachmentRef(url="https://cdn/budget.xlsx", filename="budget.xlsx",
                            mime="application/vnd.ms-excel", size_bytes=14)
    turn = Turn(surface="dashboard", owner_id=7, content="[file attached]", images=[],
                metadata=TurnMetadata(file_attachments=(ref,)))
    ctx = await recall.build_turn_context(turn)
    assert len(ctx.text_attachment_contents) == 1
    line = ctx.text_attachment_contents[0]
    assert line.startswith("budget.xlsx\n") and str(tmp_path) in line
    assert any(p.read_bytes().startswith(b"PK") for p in tmp_path.iterdir())


@pytest.mark.asyncio
async def test_text_lines_come_first_then_file_lines(monkeypatch, tmp_path):
    _stub_clean(monkeypatch)
    monkeypatch.setenv("PALLI_INBOX_DIR", str(tmp_path))
    monkeypatch.setattr(recall, "_fetch_image_bytes", AsyncMock(return_value=b"hello notes"))
    txt = TextAttachmentRef(url="https://cdn/notes.txt", filename="notes.txt", mime="text/plain", size_bytes=11)
    pdf = TextAttachmentRef(url="https://cdn/scan.pdf", filename="scan.pdf", mime="application/pdf", size_bytes=11)
    turn = Turn(surface="dashboard", owner_id=7, content="both", images=[],
                metadata=TurnMetadata(text_attachments=(txt,), file_attachments=(pdf,)))
    ctx = await recall.build_turn_context(turn)
    assert [c.split("\n", 1)[0] for c in ctx.text_attachment_contents] == ["notes.txt", "scan.pdf"]
    assert "hello notes" in ctx.text_attachment_contents[0]

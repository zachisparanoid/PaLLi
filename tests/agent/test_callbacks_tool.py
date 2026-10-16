"""Organ 4.12 — her hand `where_did_that_come_from` (spec §3): check
before claiming. Found ⇒ the receipt in prose-ready fields; not found ⇒ an
honest nothing; keep=True ⇒ planted through the store's rail. Gated at
registration so flag-off builds carry a byte-identical tool list."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

import ai.callbacks_lookup as lk
import ai.callbacks_store as cs
import core.agent.builtin_tools as bt
from core.agent.builtin_tools.where_did_that_come_from import WhereDidThatComeFromTool
from core.agent.tools import ToolContext


def _ctx(channel_id=42):
    return ToolContext(turn_owner_id=1, is_owner=True, surface="discord_text",
                       channel_id=channel_id)


def _session(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "discord_42.jsonl").write_text(json.dumps({
        "role": "user", "timestamp": "2026-03-14T12:00:00+00:00",
        "content": [{"type": "text", "text": "toss it in the oubliette with the other steppers"}],
    }) + "\n", encoding="utf-8")


def test_shape():
    t = WhereDidThatComeFromTool()
    assert t.name == "where_did_that_come_from"
    assert t.owner_only is True and t.requires_approval is False
    assert set(t.input_schema["properties"]) == {"phrase", "keep", "kind"}
    assert t.input_schema["required"] == ["phrase"]


def test_found_reports_the_receipt_and_keeps_nothing_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    _session(tmp_path / "sessions")
    out = asyncio.run(WhereDidThatComeFromTool().execute({"phrase": "the oubliette"}, _ctx()))
    assert "they said it first" in out and "Mar 14, 2026" in out
    assert "toss it in the oubliette" in out
    assert cs.open_callbacks() == []


def test_the_day_reads_in_his_local_time(tmp_path, monkeypatch):
    """review m1: session timestamps are UTC and the host is his zone —
    687 of 4245 live rows (16%) fall on a different calendar day in UTC
    than locally, concentrated on his late-night messages, and he is
    nocturnal. The house precedent is named and painful
    (core/agent/context_adapter.py: the 2026-07-10 bug PaLLi caught
    herself — raw UTC digits framed as his local time, four hours off)."""
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    root = tmp_path / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    when = "2026-03-14T03:30:00+00:00"          # late the night before, his time
    (root / "discord_42.jsonl").write_text(json.dumps({
        "role": "user", "timestamp": when,
        "content": [{"type": "text", "text": "toss it in the oubliette"}]}) + "\n",
        encoding="utf-8")
    out = asyncio.run(WhereDidThatComeFromTool().execute({"phrase": "the oubliette"}, _ctx()))
    parsed = datetime.fromisoformat(when)
    local_day = parsed.astimezone().strftime("%b %d, %Y")
    assert local_day in out                     # timezone-independent
    utc_day = parsed.strftime("%b %d, %Y")
    if utc_day != local_day:                    # true wherever he actually runs her
        assert utc_day not in out


def test_keep_plants_through_the_rail_with_the_found_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    _session(tmp_path / "sessions")
    out = asyncio.run(WhereDidThatComeFromTool().execute(
        {"phrase": "the oubliette", "keep": True, "kind": "nickname"}, _ctx()))
    book = cs.open_callbacks()
    assert len(book) == 1 and book[0].kind == "nickname" and book[0].coined_by == "him"
    assert book[0].receipt["who"] == "him" and book[0].receipt["source"] == "discord_42.jsonl:1"
    assert "kept" in out.lower()
    again = asyncio.run(WhereDidThatComeFromTool().execute(
        {"phrase": "the oubliette", "keep": True}, _ctx()))
    assert "already" in again.lower() and len(cs.open_callbacks()) == 1


def test_not_found_is_honest_and_keeps_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    _session(tmp_path / "sessions")
    out = asyncio.run(WhereDidThatComeFromTool().execute(
        {"phrase": "the boneyard", "keep": True}, _ctx()))
    # It is the CURRENT session only, so "my record with him" overclaimed.
    assert "nothing in this conversation's record" in out.lower()
    assert cs.open_callbacks() == []


def test_the_echoed_phrase_is_capped(tmp_path, monkeypatch):
    """The phrase is model-authored; the lookup's 80-char bound gates the
    SEARCH, and without a cap here the not-found line echoes it in full."""
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    _session(tmp_path / "sessions")
    out = asyncio.run(WhereDidThatComeFromTool().execute({"phrase": "z" * 200}, _ctx()))
    assert "z" * 80 in out and "z" * 81 not in out


def test_a_long_row_still_plants_because_the_window_holds_the_bit(tmp_path, monkeypatch):
    """C1 end to end: the lookup's window and the store's new rail agree, so
    a bit said past character 240 is quotable AND keepable — not planted with
    a receipt that omits it, and not refused outright."""
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    root = tmp_path / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    text = ("we need to walk the whole thing again " * 12) + "just toss it in the oubliette"
    (root / "discord_42.jsonl").write_text(json.dumps({
        "role": "user", "timestamp": "2026-03-14T02:11:09+00:00",
        "content": [{"type": "text", "text": text}]}) + "\n", encoding="utf-8")
    out = asyncio.run(WhereDidThatComeFromTool().execute(
        {"phrase": "the oubliette", "keep": True}, _ctx()))
    assert "kept" in out.lower() and "refused" not in out.lower()
    book = cs.open_callbacks()
    assert len(book) == 1
    assert "the oubliette" in book[0].receipt["line"]


def _write_row(root: Path, name: str, text: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps({
        "role": "user", "timestamp": "2026-03-14T12:00:00+00:00",
        "content": [{"type": "text", "text": text}]}) + "\n", encoding="utf-8")


def test_a_decomposed_row_quotes_exactly_what_the_book_files(tmp_path, monkeypatch):
    """m2: the store re-cleans the line it is handed (compose, collapse, cap).
    A window cut on RAW decomposed text composes to something SHORTER, so the
    line she speaks and the line her book holds were two different strings."""
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    _write_row(tmp_path / "sessions", "discord_42.jsonl",
               "cafe\u0301 " * 60 + "toss it in the oubliette")
    out = asyncio.run(WhereDidThatComeFromTool().execute(
        {"phrase": "the oubliette", "keep": True}, _ctx()))
    quoted = out.split(' — "', 1)[1].rsplit('"', 1)[0]
    book = cs.open_callbacks()
    assert len(book) == 1
    assert book[0].receipt["line"] == quoted          # one string, not two


def test_a_row_whose_nfc_form_grows_still_keeps_the_bit(tmp_path, monkeypatch):
    """The half m2 makes fail-closed: U+0958 and its kin are composition
    EXCLUSIONS — one codepoint that NFC expands into two. Cut at 240 raw and
    the store's own cap then falls inside the text, taking the bit out of its
    own receipt, and the C1 rail refuses the plant she was just told about."""
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    _write_row(tmp_path / "sessions", "discord_42.jsonl",
               "\u0958" * 200 + " toss it in the oubliette")
    out = asyncio.run(WhereDidThatComeFromTool().execute(
        {"phrase": "the oubliette", "keep": True}, _ctx()))
    assert "kept" in out.lower() and "refused" not in out.lower()
    book = cs.open_callbacks()
    assert len(book) == 1 and "the oubliette" in book[0].receipt["line"]


@pytest.mark.parametrize("surface,is_conversation", [
    ("discord_text", True), ("voice_local", True), ("dashboard", True),
    ("discord_voice", True),
    ("reminder_fire", False), ("proactive", False), ("forge_followup", False),
])
def test_only_conversational_surfaces_are_searched(tmp_path, monkeypatch,
                                                   surface, is_conversation):
    """I1: a reminder-fire transcript's `role: "user"` rows are the
    scheduler's synthesized text — 291 of 291 in the live file — and since
    §16 they quote HER lines back. Searching one stamps him with her words,
    durably. So those files are never searched at all."""
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    path = lk.session_path_for(surface=surface, channel_id=42, owner_id=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "role": "user", "timestamp": "2026-03-14T02:11:09+00:00",
        "content": [{"type": "text", "text": "toss it in the oubliette"}]}) + "\n",
        encoding="utf-8")
    looked: list = []
    real = lk.find_origin_async

    async def spy(*args, **kwargs):
        looked.append(kwargs.get("session_path"))
        return await real(*args, **kwargs)

    monkeypatch.setattr(lk, "find_origin_async", spy)
    ctx = ToolContext(turn_owner_id=1, is_owner=True, surface=surface, channel_id=42)
    out = asyncio.run(WhereDidThatComeFromTool().execute(
        {"phrase": "the oubliette", "keep": True}, ctx))
    if is_conversation:
        assert "they said it first" in out and len(looked) == 1
        assert len(cs.open_callbacks()) == 1
    else:
        assert out == "This isn't a conversation record I can search."
        assert looked == []                       # no lookup at all
        assert cs.open_callbacks() == []


def test_a_bad_kind_falls_back_to_bit(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "book"))
    _session(tmp_path / "sessions")
    asyncio.run(WhereDidThatComeFromTool().execute(
        {"phrase": "the oubliette", "keep": True, "kind": "pun"}, _ctx()))
    assert cs.open_callbacks()[0].kind == "bit"


def test_guest_context_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path / "sessions"))
    _session(tmp_path / "sessions")
    ctx = ToolContext(turn_owner_id=9, is_owner=False, surface="discord_text", channel_id=42)
    out = asyncio.run(WhereDidThatComeFromTool().execute({"phrase": "the oubliette"}, ctx))
    assert "ours" in out.lower() and "they said it first" not in out


# ── registration: byte-identical roster when off ────────────────────────

def _names(reg):
    return set(reg.all_names())


def test_absent_from_the_roster_when_off():
    assert "where_did_that_come_from" not in _names(bt.build_default_registry())


def test_present_when_the_gate_reads_on(monkeypatch):
    monkeypatch.setattr(bt, "_idiolect_enabled", lambda: True)
    assert "where_did_that_come_from" in _names(bt.build_default_registry())
    assert "where_did_that_come_from" not in _names(bt.build_default_registry(surface="workbench"))


def test_the_registry_gate_reads_the_flag_both_ways(monkeypatch):
    """Through the import-time capture — the suite pins the module attribute."""
    from types import SimpleNamespace

    from core.settings_boot import settings_service

    monkeypatch.setattr(settings_service, "snapshot", lambda: None)
    assert bt._idiolect_enabled_impl() is False
    # A booted snapshot with no companion block at all is still OFF — the
    # default-OFF faculty semantics, not just the unbooted None.
    monkeypatch.setattr(settings_service, "snapshot", lambda: SimpleNamespace())
    assert bt._idiolect_enabled_impl() is False
    monkeypatch.setattr(settings_service, "snapshot",
                        lambda: SimpleNamespace(companion=SimpleNamespace(idiolect=True)))
    assert bt._idiolect_enabled_impl() is True
    monkeypatch.setattr(settings_service, "snapshot",
                        lambda: SimpleNamespace(companion=SimpleNamespace(idiolect=False)))
    assert bt._idiolect_enabled_impl() is False

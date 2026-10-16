"""Organ 4.12 — the origin lookup (spec §3): a deterministic search of the
CURRENT session's transcript for a phrase's earliest occurrence. Search,
never an LLM; user → him, assistant → her; nothing found is an honest None."""
from __future__ import annotations

import json
import threading
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai.callbacks_lookup as lk


def _row(role, text, ts):
    return json.dumps({"role": role, "timestamp": ts,
                       "content": [{"type": "text", "text": text}]})


def _session(tmp_path: Path) -> Path:
    p = tmp_path / "discord_42.jsonl"
    p.write_text("\n".join([
        _row("user", "hey", "2026-03-01T00:00:00+00:00"),
        _row("assistant", "hey yourself", "2026-03-01T00:00:05+00:00"),
        json.dumps({"role": "tool", "content": "the oubliette tool output",
                    "timestamp": "2026-03-02T00:00:00+00:00"}),
        _row("user", "just toss it in the OUBLIETTE, with the other steppers",
             "2026-03-14T02:11:09+00:00"),
        json.dumps({"role": "assistant", "content": "the oubliette it is.",
                    "timestamp": "2026-03-14T02:11:30+00:00"}),        # str content
        "not json at all",
        _row("user", "oubliette again", "2026-03-20T00:00:00+00:00"),
    ]) + "\n", encoding="utf-8")
    return p


def test_finds_the_earliest_user_or_assistant_row_and_stamps_him(tmp_path):
    r = lk.find_origin("the oubliette", session_path=_session(tmp_path))
    assert r["who"] == "him" and r["when"] == "2026-03-14T02:11:09+00:00"
    assert r["line"].startswith("just toss it in the OUBLIETTE")
    assert r["source"] == "discord_42.jsonl:4"


def test_tool_rows_and_unparseable_rows_are_ignored(tmp_path):
    r = lk.find_origin("oubliette tool output", session_path=_session(tmp_path))
    assert r is None


def test_an_assistant_row_stamps_her_and_string_content_is_read(tmp_path):
    r = lk.find_origin("oubliette it is", session_path=_session(tmp_path))
    assert r["who"] == "her" and r["when"] == "2026-03-14T02:11:30+00:00"


def test_normalized_matching(tmp_path):
    r = lk.find_origin("Other Steppers!", session_path=_session(tmp_path))
    assert r is not None and r["who"] == "him"


def test_too_short_or_blank_phrase_is_none(tmp_path):
    p = _session(tmp_path)
    assert lk.find_origin("", session_path=p) is None
    assert lk.find_origin("ab", session_path=p) is None
    assert lk.find_origin("x" * 81, session_path=p) is None
    # The two above never touch this transcript, so they'd pass with no
    # bounds at all. These bite: "he" IS in row 1 ("hey") and the long
    # needle IS the line — only the floor and the ceiling keep them out.
    assert lk.find_origin("he", session_path=p) is None
    long_p = tmp_path / "long.jsonl"
    long_p.write_text(_row("user", "y" * 81, "2026-03-14T02:11:09+00:00") + "\n",
                      encoding="utf-8")
    assert lk.find_origin("y" * 81, session_path=long_p) is None
    assert lk.find_origin("y" * 80, session_path=long_p) is not None   # 80 is in


def test_missing_session_is_none(tmp_path):
    assert lk.find_origin("the oubliette", session_path=tmp_path / "nope.jsonl") is None


def test_the_line_is_capped(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_row("user", "oubliette " + "z" * 1000, "2026-03-14T02:11:09+00:00") + "\n")
    r = lk.find_origin("oubliette", session_path=p)
    assert len(r["line"]) == 240


def test_the_receipt_line_is_the_window_that_holds_the_bit(tmp_path):
    """C1: the match runs on the WHOLE row, so a head-slice receipt can omit
    the very bit it certifies — measured at 7% of his live rows and 37% of
    hers. The line she quotes has to be the window the match is IN."""
    p = tmp_path / "long.jsonl"
    text = ("we need to walk the whole thing again " * 12) + "just toss it in the oubliette"
    assert text.index("oubliette") > lk.LINE_CAP          # the head cannot hold it
    p.write_text(_row("user", text, "2026-03-14T02:11:09+00:00") + "\n", encoding="utf-8")
    r = lk.find_origin("the oubliette", session_path=p)
    assert "the oubliette" in r["line"]
    assert len(r["line"]) <= lk.LINE_CAP
    assert not r["line"].startswith("we need to walk")     # not the bare head


def test_a_row_at_or_under_the_cap_is_quoted_whole(tmp_path):
    r = lk.find_origin("the oubliette", session_path=_session(tmp_path))
    assert r["line"] == "just toss it in the OUBLIETTE, with the other steppers"


def test_only_text_blocks_are_read(tmp_path):
    """I3: 1917 `tool_use` blocks sit on `role: "assistant"` rows in the live
    DM — the role filter does not exclude them, this block filter is the only
    thing that does. A shell command she RAN is not a thing she SAID."""
    p = tmp_path / "blocks.jsonl"
    p.write_text(json.dumps({
        "role": "assistant", "timestamp": "2026-03-14T02:11:09+00:00",
        "content": [
            {"type": "tool_use", "name": "local_shell",
             "input": {"command": "ls ~/the oubliette"}},
            {"type": "text", "text": "done, nothing in there"},
        ]}) + "\n", encoding="utf-8")
    assert lk.find_origin("the oubliette", session_path=p) is None
    assert lk.find_origin("nothing in there", session_path=p) is not None


def test_the_conversational_surfaces_are_named(tmp_path):
    """I1: `role: "user"` is a TRANSPORT map, not a speaker map — a
    reminder-fire row is the scheduler's text, and since DEFECT_RECORD §16 it
    embeds HER lines. Only these files are records of them talking.

    The Telegram door (2026-09-14, task 2) is the fifth: a bit of THEIRS can
    be coined in a Telegram chat exactly as it can in a Discord one."""
    assert set(lk.CONVERSATIONAL_SURFACES) == {"discord_text", "discord_voice",
                                               "voice_local", "dashboard",
                                               "telegram_text", "mobile_text"}


def test_the_window_composes_before_it_cuts(tmp_path):
    """m2 parity: `callbacks_store._clean` composes BEFORE it caps (task 1's
    round-3 N2 fix), and the store re-cleans whatever line it is handed. A
    window cut on RAW decomposed text is therefore not the string the book
    files — and where NFC EXPANDS, the store's own cap can then cut the bit
    out of the receipt and refuse the plant. Compose first and the two agree."""
    p = tmp_path / "nfd.jsonl"
    text = "cafe\u0301 " * 60 + "toss it in the oubliette"
    p.write_text(_row("user", text, "2026-03-14T12:00:00+00:00") + "\n", encoding="utf-8")
    r = lk.find_origin("the oubliette", session_path=p)
    assert "the oubliette" in r["line"]
    assert unicodedata.is_normalized("NFC", r["line"])       # the store's form
    assert len(r["line"]) <= lk.LINE_CAP


@pytest.mark.asyncio
async def test_async_wrapper_runs_off_the_loop(tmp_path, monkeypatch):
    """I2: awaiting the coroutine proves nothing — the old assertion passed
    with `asyncio.to_thread` deleted. Only the THREAD proves the §12 lesson
    (a blocking read on the loop starves the gateway)."""
    real = lk.find_origin
    on_main: list = []

    def spy(*args, **kwargs):
        on_main.append(threading.current_thread() is threading.main_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(lk, "find_origin", spy)
    r = await lk.find_origin_async("the oubliette", session_path=_session(tmp_path))
    assert r["who"] == "him"
    assert on_main == [False]


# ── the session key: byte-equal to the runner's derivation ──────────────

@pytest.mark.parametrize("surface,channel_id,owner_id", [
    ("discord_text", 100000000000000002, 1), ("voice_local", 0, 1),
    ("dashboard", 0, 7), ("proactive", 0, 1), ("weird", 0, 3),
    ("discord_voice", 0, 1), ("discord_voice", 42, 1),
])
def test_session_key_matches_the_runner(surface, channel_id, owner_id):
    from core.agent.runner import _derive_session_key

    turn = SimpleNamespace(surface=surface, owner_id=owner_id,
                           metadata=SimpleNamespace(channel_id=channel_id, extra={}))
    assert lk.session_key_for(surface=surface, channel_id=channel_id,
                              owner_id=owner_id) == _derive_session_key(turn)


def test_session_path_sanitizes_and_honors_the_env_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    p = lk.session_path_for(surface="discord_text", channel_id=42, owner_id=1)
    assert p == tmp_path / "discord_42.jsonl"
    monkeypatch.delenv("PALLI_AGENT_SESSIONS_ROOT")
    assert lk.sessions_root() == Path("data/agent_sessions")


def test_the_mirror_is_built_by_the_one_key_helper(monkeypatch):
    """Batch 8, task 1 (ruling R7): the mirror no longer copies the runner's
    branches — both delegate to core.session_keys, so a key change lands in
    one place and the byte-equality above holds by construction."""
    import core.session_keys as keys

    calls: list[tuple] = []
    real = keys.session_key_for

    def spy(surface, channel_id, owner_id):
        calls.append((surface, channel_id, owner_id))
        return real(surface, channel_id, owner_id)

    monkeypatch.setattr(keys, "session_key_for", spy)
    assert lk.session_key_for(surface="telegram_text", channel_id=-1001, owner_id=1) == "telegram:-1001"
    assert calls == [("telegram_text", -1001, 1)]

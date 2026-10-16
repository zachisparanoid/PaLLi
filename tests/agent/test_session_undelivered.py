"""Batch 4 task 5 (fix plan 5.10, rulings R9/R9a/R9d/R9e): a reply that never
reached him, or only partly, is marked in HER record, so her next turn knows
what he saw.

The reply is already in her session when the door sends it (dispatch writes
first). When the send fails, the door flags that stored message's
``delivery`` ("none" or "partial"); the payload she reads stamps it, the way a working note is
stamped (§17) — payload-only, the record keeps her words verbatim. And the
stamp is the system's: if she ever echoes it, the §20 boundary strip takes it
out before it can reach him.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch


def _msgs(sess_mod, *pairs):
    return [sess_mod.AgentMessage(role=r, content=[sess_mod.TextBlock(text=t)]) for r, t in pairs]


async def _seed(sess_mod, root, key, *pairs):
    s = await sess_mod.Session.open_or_create(key, root=root)
    for m in _msgs(sess_mod, *pairs):
        await s.append(m)
    return s


# ── the stored flag ───────────────────────────────────────────────────────
def test_an_unflagged_line_is_byte_identical_and_the_flag_round_trips():
    import core.agent.session as sess_mod

    plain = sess_mod.AgentMessage(role="assistant", content=[sess_mod.TextBlock(text="hi")])
    line = sess_mod._msg_to_jsonl(plain)
    # The history prefix is cached by the provider (Batch 2): an ordinary
    # line must not grow a key.
    assert set(json.loads(line)) == {"role", "content", "timestamp"}
    assert sess_mod._msg_from_dict(json.loads(line)).delivery == ""

    for state in ("none", "partial"):
        flagged = sess_mod.AgentMessage(role="assistant", content=[sess_mod.TextBlock(text="hi")],
                                        delivery=state)
        d = json.loads(sess_mod._msg_to_jsonl(flagged))
        assert d["delivery"] == state
        assert sess_mod._msg_from_dict(d).delivery == state
    # An unknown state reads as delivered, which is what an absent key means.
    assert sess_mod._msg_from_dict({**json.loads(line), "delivery": "maybe"}).delivery == ""


def test_mark_undelivered_flags_the_last_exact_match_of_the_current_exchange(tmp_path):
    import core.agent.session as sess_mod

    async def go():
        s = await _seed(sess_mod, tmp_path, "discord:555",
                        ("user", "what's the plan"), ("assistant", "working on it"),
                        ("assistant", "the plan is X"), ("assistant", "the plan is X"))
        assert await s.mark_undelivered("the plan is X") is True
        return await sess_mod.Session.open_or_create("discord:555", root=tmp_path), s

    fresh, s = asyncio.run(go())
    flags = [m.delivery for m in fresh.messages]
    assert flags == ["", "", "", "none"]                          # the LAST match only
    assert fresh.messages[3].content[0].text == "the plan is X"   # words verbatim
    assert [m.delivery for m in s.messages] == flags              # the instance agrees with disk


def test_an_older_identical_reply_in_an_earlier_exchange_is_never_stamped(tmp_path):
    """R9e: that line was delivered in its own exchange. The search stops at
    his last message."""
    import core.agent.session as sess_mod

    async def go():
        s = await _seed(sess_mod, tmp_path, "discord:556",
                        ("user", "you there?"), ("assistant", "yep"),
                        ("user", "and now?"))
        before = s.path.read_bytes()
        hit = await s.mark_undelivered("yep")
        return before, hit, s

    before, hit, s = asyncio.run(go())
    assert hit is False and s.path.read_bytes() == before


def test_a_partial_send_is_flagged_partial(tmp_path):
    import core.agent.session as sess_mod

    async def go():
        s = await _seed(sess_mod, tmp_path, "discord:557",
                        ("user", "long one please"), ("assistant", "part one. part two."))
        assert await s.mark_undelivered("part one. part two.", partial=True) is True
        return await sess_mod.Session.open_or_create("discord:557", root=tmp_path)

    assert [m.delivery for m in asyncio.run(go()).messages] == ["", "partial"]


def test_mark_undelivered_matches_across_whitespace_but_never_a_different_reply(tmp_path):
    import core.agent.session as sess_mod

    async def go():
        s = await _seed(sess_mod, tmp_path, "discord:1",
                        ("user", "hey"), ("assistant", "first part.\n\nsecond part."))
        before = s.path.read_bytes()
        miss = await s.mark_undelivered("something she never said")
        after_miss = s.path.read_bytes()
        hit = await s.mark_undelivered("first part. second part.")
        return before, miss, after_miss, hit, s

    before, miss, after_miss, hit, s = asyncio.run(go())
    assert miss is False and after_miss == before     # no match → the file is untouched
    assert hit is True and s.messages[1].delivery == "none"


def test_mark_undelivered_keeps_a_message_another_writer_appended(tmp_path):
    """The same serialisation the session already uses for an after-the-fact
    edit (enrich_last_user_placeholder): re-read FRESH under the lock, so a
    line another Session instance appended since this one loaded survives."""
    import core.agent.session as sess_mod

    async def go():
        a = await _seed(sess_mod, tmp_path, "discord:2",
                        ("user", "hey"), ("assistant", "the reply that died"))
        b = await sess_mod.Session.open_or_create("discord:2", root=tmp_path)
        await b.append(_msgs(sess_mod, ("assistant", "one more thing"))[0])
        assert await a.mark_undelivered("the reply that died") is True
        return await sess_mod.Session.open_or_create("discord:2", root=tmp_path)

    fresh = asyncio.run(go())
    assert [m.content[0].text for m in fresh.messages] == [
        "hey", "the reply that died", "one more thing"]
    assert [m.delivery for m in fresh.messages] == ["", "none", ""]


def test_the_rewrites_go_beside_then_replace(tmp_path, monkeypatch):
    """R9e/M7: a crash mid-rewrite must never leave a truncated transcript.
    Both after-the-fact edits write a temp file and os.replace it over."""
    import core.agent.session as sess_mod

    replaced: list[tuple[str, str]] = []
    real_replace = sess_mod.os.replace

    def spy(src, dst):
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(sess_mod.os, "replace", spy)

    async def go():
        s = await _seed(sess_mod, tmp_path, "discord:3",
                        ("user", "[image attached]"), ("assistant", "nice"))
        assert await s.mark_undelivered("nice") is True
        assert await s.enrich_last_user_placeholder("a photo of a dog") is True
        return s

    s = asyncio.run(go())
    assert [dst for _src, dst in replaced] == [str(s.path), str(s.path)]
    assert all(src.endswith(".jsonl.tmp") for src, _dst in replaced)
    assert not list(tmp_path.glob("*.tmp"))
    fresh = asyncio.run(sess_mod.Session.open_or_create("discord:3", root=tmp_path))
    assert [m.content[0].text for m in fresh.messages] == ["a photo of a dog", "nice"]
    assert [m.delivery for m in fresh.messages] == ["", "none"]


def test_the_rewrites_keep_the_transcripts_permissions(tmp_path):
    """R9g: a beside-then-replace rewrite must not widen the file. The owner's
    live transcript is 0660; a temp file born with the umask default (0664)
    carried over by os.replace would make it readable by the work account."""
    import stat

    import core.agent.session as sess_mod

    async def go():
        s = await _seed(sess_mod, tmp_path, "discord:4",
                        ("user", "[image attached]"), ("assistant", "nice"))
        s.path.chmod(0o660)
        assert await s.mark_undelivered("nice") is True
        after_mark = stat.S_IMODE(s.path.stat().st_mode)
        assert await s.enrich_last_user_placeholder("a photo of a dog") is True
        after_enrich = stat.S_IMODE(s.path.stat().st_mode)
        return after_mark, after_enrich

    assert asyncio.run(go()) == (0o660, 0o660)


# ── the payload stamp ─────────────────────────────────────────────────────
def test_mark_undelivered_for_llm_is_pure_idempotent_and_targets_only_the_flag():
    import core.agent.session as sess_mod
    from core.agent.session import (
        PARTIAL_DELIVERY_MARK, UNDELIVERED_MARK, mark_undelivered_for_llm,
    )

    msgs = _msgs(sess_mod, ("user", "what's the plan"), ("assistant", "the plan is X"),
                 ("user", "ok"), ("assistant", "glad that helps"),
                 ("user", "more"), ("assistant", "part one. part two."))
    msgs[1].delivery = "none"
    msgs[5].delivery = "partial"
    out = mark_undelivered_for_llm(msgs)
    assert out[1].content[0].text == UNDELIVERED_MARK + "the plan is X"
    assert out[3].content[0].text == "glad that helps"               # delivered: untouched
    assert out[5].content[0].text == PARTIAL_DELIVERY_MARK + "part one. part two."
    assert msgs[1].content[0].text == "the plan is X"                # pure: stored copy untouched
    again = mark_undelivered_for_llm(out)
    assert again[1].content[0].text.count(UNDELIVERED_MARK) == 1     # idempotent
    assert again[5].content[0].text.count(PARTIAL_DELIVERY_MARK) == 1
    clean = _msgs(sess_mod, ("user", "a"), ("assistant", "b"))
    assert mark_undelivered_for_llm(clean) is clean                  # identity when nothing is flagged
    assert mark_undelivered_for_llm([]) == []


def test_the_marks_are_system_stamps_and_the_boundary_strip_removes_them():
    from core.agent.session import (
        PARTIAL_DELIVERY_MARK, UNDELIVERED_MARK, WORKING_NOTE_MARK, strip_working_note_mark,
    )

    assert UNDELIVERED_MARK == "[system: not delivered — they have not seen this] "
    assert PARTIAL_DELIVERY_MARK == "[system: only partly delivered — they saw the start, not the rest] "
    assert strip_working_note_mark(UNDELIVERED_MARK + "hey, the plan") == "hey, the plan"
    assert strip_working_note_mark(PARTIAL_DELIVERY_MARK + "hey, the rest") == "hey, the rest"
    assert strip_working_note_mark("  [system: not delivered — he has not seen this]\nhey") == "hey"
    assert strip_working_note_mark(UNDELIVERED_MARK + PARTIAL_DELIVERY_MARK + WORKING_NOTE_MARK + "all") == "all"
    assert strip_working_note_mark("I said it was not delivered, sorry") == \
        "I said it was not delivered, sorry"


def test_an_imitated_not_delivered_row_is_stripped_at_the_boundary():
    """Final review, Important 1: the ``system`` row persist_delivery_failure
    writes into ``conversations`` reaches her through the recent-lines blocks
    (as a system note). A copy of it at the head of her reply is stripped at
    the same boundary as the other stamps — both forms, doubled, indented."""
    from core.agent.session import strip_working_note_mark

    assert strip_working_note_mark("[not delivered to them: HTTPException 50013] hey") == "hey"
    assert strip_working_note_mark(
        "[only partly delivered to them: HTTPException 50013 after 1 of 2 parts]\nhey") == "hey"
    assert strip_working_note_mark(
        "  [not delivered to them: X] [only partly delivered to them: Y after 1 of 3 parts] all") == "all"
    assert strip_working_note_mark("[not delivered to him: Forbidden] hey") == "hey"
    # Only the bracketed note at the head goes; her own words about it stay.
    assert strip_working_note_mark("it was not delivered to them: sorry") == \
        "it was not delivered to them: sorry"
    assert strip_working_note_mark("hey [not delivered to them: X]") == "hey [not delivered to them: X]"


# ── the next turn reads it ────────────────────────────────────────────────
@dataclass
class _Turn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "did you get my question?"
    metadata: Any = None
    received_at: Any = None


@dataclass
class _Ctx:
    turn: Any = None
    user_text: str = "did you get my question?"
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


_CADENCE = ('{"should_respond": true, "register": "neutral", "length": "short", '
            '"phase": "settled_conversation", "target_beats": 1, "observation_text": "ok"}')
_KEY = "discord_text:1"      # a metadata-less turn keys on surface:owner


def test_her_next_payload_marks_the_undelivered_reply_whether_or_not_duplex_is_on(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    from core.agent.session import PARTIAL_DELIVERY_MARK, UNDELIVERED_MARK
    from tests.agent.conftest import load_module

    mod = load_module("core/agent/runner.py", "agent_runner_undelivered_under_test")

    async def seed():
        # The first reply died on the way, the second only partly landed, the
        # third landed. The flags are on the record the way the doors leave
        # them (each in its own exchange, so each was flagged while current).
        s = await sess_mod.Session.open_or_create(_KEY, root=tmp_path)
        rows = [("user", "what's the plan", ""), ("assistant", "the plan is X", "none"),
                ("user", "cool, and after?", ""), ("assistant", "then we rest", "partial"),
                ("user", "nice", ""), ("assistant", "glad it helps", "")]
        for role, text, state in rows:
            await s.append(sess_mod.AgentMessage(role=role, content=[sess_mod.TextBlock(text=text)],
                                                 delivery=state))

    asyncio.run(seed())

    async def fake_recall(turn):
        return _Ctx(turn=turn)

    async def fake_cadence(prompt):
        return _CADENCE

    for duplex_on in (False, True):
        steering.clear(_KEY)
        steering.clear_run_state(_KEY)
        payloads: list = []

        async def fake_call(**kw):
            payloads.append(kw.get("messages") or [])
            return llm_mod.LLMResponse(text_blocks=["sorry — here it is"], tool_uses=[],
                                       stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)

        monkeypatch.setattr(llm_mod, "call", fake_call)
        deps = mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=None, polling_tools=set(),
        )
        with patch.object(dx, "duplex_enabled", lambda: duplex_on):
            asyncio.run(mod.run_turn(_Turn(), deps=deps, on_chunk=None))
        texts = [b.text for m in payloads[-1] for b in getattr(m, "content", [])
                 if isinstance(b, sess_mod.TextBlock)]
        died = [t for t in texts if "the plan is X" in t]
        partly = [t for t in texts if "then we rest" in t]
        landed = [t for t in texts if "glad it helps" in t]
        assert died and died[0].startswith(UNDELIVERED_MARK), (duplex_on, texts)
        assert partly and partly[0].startswith(PARTIAL_DELIVERY_MARK), (duplex_on, texts)
        assert landed and landed[0] == "glad it helps", (duplex_on, texts)

    on_disk = asyncio.run(sess_mod.Session.open_or_create(_KEY, root=tmp_path))
    stored = [b.text for m in on_disk.messages for b in m.content
              if isinstance(b, sess_mod.TextBlock) and "the plan is X" in b.text]
    assert stored == ["the plan is X"]          # payload-only, never the record

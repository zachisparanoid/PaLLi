"""Batch 4 task 5 (fix plan 5.10, rulings R9/R9d/R9e): a failed send leaves a
visible not-delivered row, so her record matches what he saw.

Dispatch writes her reply to ``conversations`` and to her session BEFORE the
door sends it. When the send dies, ``persist_delivery_failure`` flags the
reply in her session (the part her next turn actually reads) and writes the
``system`` row after it: "not delivered" when nothing landed, "only partly
delivered … after N of M parts" when the start did. It never fires
``turn_failed`` — the send module already did, once.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from core.turn.persistence import persist_delivery_failure


class FakeStore:
    def __init__(self, rows=None, *, fail=False):
        self.rows: list[dict] = list(rows or [])
        self._fail = fail

    async def store_conversation(self, **row):
        if self._fail:
            raise RuntimeError("database is gone")
        self.rows.append(row)
        return len(self.rows)


@pytest.fixture
def fake_store():
    # The reply is already on the record when the send fails.
    return FakeStore([{"user_id": 1, "platform": "discord", "channel_id": 555,
                       "thread_id": None, "role": "assistant", "content": "the plan is X"}])


async def _seed(root, key, *pairs):
    from core.agent.session import AgentMessage, Session, TextBlock

    s = await Session.open_or_create(key, root=root)
    for role, text in pairs:
        await s.append(AgentMessage(role=role, content=[TextBlock(text=text)]))
    return s


async def _reopen(root, key):
    from core.agent.session import Session

    return await Session.open_or_create(key, root=root)


@pytest.mark.asyncio
async def test_failure_row_follows_the_reply(fake_store, tmp_path):
    await persist_delivery_failure(owner_id=1, platform="discord", channel_id=555,
                                   reason="HTTPException 50013", store=fake_store,
                                   sessions_root=tmp_path)
    row = fake_store.rows[-1]
    assert row["role"] == "system" and row["content"].startswith("[not delivered to them: HTTPException 50013")
    assert row["content"] == "[not delivered to them: HTTPException 50013]"
    assert (row["user_id"], row["platform"], row["channel_id"]) == (1, "discord", 555)
    assert row["username"] == "system"            # never attributed to a person in her day log
    assert fake_store.rows[-2]["role"] == "assistant"          # after her reply


@pytest.mark.asyncio
async def test_a_partial_send_says_how_much_landed(fake_store, tmp_path):
    await _seed(tmp_path, "discord:555", ("user", "the long version"),
                ("assistant", "part one. part two. part three."))

    await persist_delivery_failure(owner_id=1, platform="discord", channel_id=555,
                                   reason="HTTPException 50013", failed_text="part one. part two. part three.",
                                   landed=1, total=3, store=fake_store, sessions_root=tmp_path)

    assert fake_store.rows[-1]["content"] == \
        "[only partly delivered to them: HTTPException 50013 after 1 of 3 parts]"
    assert [m.delivery for m in (await _reopen(tmp_path, "discord:555")).messages] == ["", "partial"]


@pytest.mark.asyncio
async def test_nothing_landed_is_the_full_row_even_with_counts(fake_store, tmp_path):
    await _seed(tmp_path, "discord:555", ("user", "hi"), ("assistant", "the plan is X"))

    await persist_delivery_failure(owner_id=1, platform="discord", channel_id=555,
                                   reason="HTTPException 50013", failed_text="the plan is X",
                                   landed=0, total=2, store=fake_store, sessions_root=tmp_path)

    assert fake_store.rows[-1]["content"] == "[not delivered to them: HTTPException 50013]"
    assert (await _reopen(tmp_path, "discord:555")).messages[1].delivery == "none"


@pytest.mark.asyncio
async def test_her_session_is_flagged_before_the_database_is_awaited(tmp_path):
    """The flag only looks at the current exchange; flagging before the slow
    database await leaves the least room for his next message to land first."""
    await _seed(tmp_path, "discord:8", ("user", "hi"), ("assistant", "the plan is X"))
    seen: list[str] = []

    class _Store:
        async def store_conversation(self, **row):
            seen.append((await _reopen(tmp_path, "discord:8")).messages[1].delivery)

    await persist_delivery_failure(owner_id=1, platform="discord", channel_id=8,
                                   reason="HTTPException 50013", failed_text="the plan is X",
                                   store=_Store(), sessions_root=tmp_path)

    assert seen == ["none"]


@pytest.mark.asyncio
async def test_the_failed_reply_is_flagged_in_her_session(fake_store, tmp_path):
    await _seed(tmp_path, "discord:555", ("user", "what's the plan"), ("assistant", "the plan is X"))

    await persist_delivery_failure(owner_id=1, platform="discord", channel_id=555,
                                   reason="Forbidden 50013", failed_text="the plan is X",
                                   store=fake_store, sessions_root=tmp_path)

    s = await _reopen(tmp_path, "discord:555")
    assert [m.delivery for m in s.messages] == ["", "none"]


@pytest.mark.asyncio
async def test_the_telegram_door_flags_its_own_session(tmp_path):
    store = FakeStore()
    await _seed(tmp_path, "telegram:4242", ("user", "hey"), ("assistant", "hi there"))
    await _seed(tmp_path, "discord:4242", ("user", "hey"), ("assistant", "hi there"))

    await persist_delivery_failure(owner_id=1, platform="telegram", channel_id=4242,
                                   reason="NetworkError", failed_text="hi there",
                                   store=store, sessions_root=tmp_path)

    assert store.rows[-1]["platform"] == "telegram"
    assert (await _reopen(tmp_path, "telegram:4242")).messages[1].delivery == "none"
    assert (await _reopen(tmp_path, "discord:4242")).messages[1].delivery == ""


@pytest.mark.asyncio
async def test_no_exact_match_logs_and_leaves_her_session_alone(fake_store, tmp_path, caplog):
    s = await _seed(tmp_path, "discord:555", ("user", "hey"), ("assistant", "an older reply"))
    before = s.path.read_bytes()

    with caplog.at_level(logging.INFO, logger="core.turn.persistence"):
        await persist_delivery_failure(owner_id=1, platform="discord", channel_id=555,
                                       reason="HTTPException 500", failed_text="a sidecar's reply",
                                       store=fake_store, sessions_root=tmp_path)

    assert s.path.read_bytes() == before                  # R9b: no match → nothing flagged
    assert fake_store.rows[-1]["role"] == "system"        # the row still happens
    assert any("no exact match" in r.getMessage() and r.levelno == logging.INFO
               for r in caplog.records), [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_an_owner_reply_with_a_put_back_value_still_matches_her_masked_record(fake_store, tmp_path):
    """Her record keeps the masked token; the door sent him the real value
    (owner put-back, Batch 1). The same reply, so it is the one flagged."""
    from core.agent.payload_masking import mask_text

    sent = "call the vet at 555-867-5309 before noon"
    stored = mask_text(sent)
    assert stored != sent
    await _seed(tmp_path, "discord:555", ("user", "vet number?"), ("assistant", stored))

    await persist_delivery_failure(owner_id=1, platform="discord", channel_id=555,
                                   reason="HTTPException 50013", failed_text=sent,
                                   store=fake_store, sessions_root=tmp_path)

    assert (await _reopen(tmp_path, "discord:555")).messages[1].delivery == "none"


@pytest.mark.asyncio
async def test_it_never_fires_turn_failed_and_never_raises(tmp_path, monkeypatch):
    import ai.present_moment as pm

    fired: list = []
    monkeypatch.setattr(pm, "notice", lambda kind, **f: fired.append(kind))
    await _seed(tmp_path, "discord:9", ("user", "hey"), ("assistant", "hi"))

    # A dead database must not break the door, and must not cost her the flag.
    await persist_delivery_failure(owner_id=1, platform="discord", channel_id=9,
                                   reason="HTTPException 50013", failed_text="hi",
                                   store=FakeStore(fail=True), sessions_root=tmp_path)

    assert fired == []                                    # the send module noticed it, once
    assert (await _reopen(tmp_path, "discord:9")).messages[1].delivery == "none"

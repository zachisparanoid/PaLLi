from __future__ import annotations
import asyncio
from tests.agent.conftest import load_module

def test_enrich_last_user_placeholder_rewrites_in_place(tmp_path):
    sess = load_module("core/agent/session.py", "session_enrich_test")
    async def go():
        s = await sess.Session.open_or_create("discord:1", root=tmp_path)
        await s.append(sess.AgentMessage(role="user", content=[sess.TextBlock(text="[image attached]")]))
        await s.append(sess.AgentMessage(role="assistant", content=[sess.TextBlock(text="nice pic")]))
        updated = await s.enrich_last_user_placeholder("[image attached — vision analysis: a red bicycle]")
        assert updated is True
        # reload from disk: the placeholder line is rewritten, order preserved
        s2 = await sess.Session.open_or_create("discord:1", root=tmp_path)
        user_texts = [b.text for m in s2.messages if m.role == "user"
                      for b in m.content if isinstance(b, sess.TextBlock)]
        assert user_texts == ["[image attached — vision analysis: a red bicycle]"]
        assert [m.role for m in s2.messages] == ["user", "assistant"]
    asyncio.run(go())


def test_enrich_last_user_placeholder_rewrites_file_placeholder(tmp_path):
    """Important-2 regression fix (2026-07-07 final review): the enrich must
    also upgrade a bare '[file attached]' placeholder, not just
    '[image attached]' — file attachments coalesce through the same
    prerecord -> enrich path as images (bots/discord/recording.py no longer
    skips the DB persist only for the image placeholder)."""
    sess = load_module("core/agent/session.py", "session_enrich_test")
    async def go():
        s = await sess.Session.open_or_create("discord:2", root=tmp_path)
        await s.append(sess.AgentMessage(role="user", content=[sess.TextBlock(text="[file attached]")]))
        await s.append(sess.AgentMessage(role="assistant", content=[sess.TextBlock(text="got it, thanks")]))
        updated = await s.enrich_last_user_placeholder(
            "[file attached — notes.txt]\nfull file contents here"
        )
        assert updated is True
        # reload from disk: the placeholder line is rewritten, order preserved
        s2 = await sess.Session.open_or_create("discord:2", root=tmp_path)
        user_texts = [b.text for m in s2.messages if m.role == "user"
                      for b in m.content if isinstance(b, sess.TextBlock)]
        assert user_texts == ["[file attached — notes.txt]\nfull file contents here"]
        assert [m.role for m in s2.messages] == ["user", "assistant"]
    asyncio.run(go())


def test_enrich_does_not_clobber_concurrent_append(tmp_path):
    """CRITICAL data-loss regression. record_user_fragment opens a SEPARATE
    Session instance on the same channel file and appends a barge-in message
    while a multi-second coalesced turn is in flight. The enrich must NOT
    rewrite the file from its own stale open-time snapshot (that erases the
    barge-in). Re-reading fresh under the lock preserves it.

    FAILS on the old stale-snapshot body (barge-in vanishes); PASSES with the
    fresh-reread fix."""
    sess = load_module("core/agent/session.py", "session_enrich_test")

    async def go():
        # Session A: the in-flight coalesced image turn. Opens, records the
        # placeholder + an assistant reply.
        a = await sess.Session.open_or_create("discord:9", root=tmp_path)
        await a.append(sess.AgentMessage(role="user", content=[sess.TextBlock(text="[image attached]")]))
        await a.append(sess.AgentMessage(role="assistant", content=[sess.TextBlock(text="looking...")]))

        # Session B: record_user_fragment for a barge-in message that lands
        # mid-turn. SEPARATE instance on the SAME file (open_or_create never
        # shares instances), with its own lock. It appends to disk.
        b = await sess.Session.open_or_create("discord:9", root=tmp_path)
        await b.append(sess.AgentMessage(role="user", content=[sess.TextBlock(text="barge-in text")]))

        # A finishes recall and enriches — using A's stale snapshot it never saw
        # the barge-in. The fix re-reads fresh so the append survives.
        updated = await a.enrich_last_user_placeholder("[image attached — vision analysis: a cat]")
        assert updated is True

        # Reload fresh from disk: BOTH survive, order preserved.
        s = await sess.Session.open_or_create("discord:9", root=tmp_path)
        user_texts = [b_.text for m in s.messages if m.role == "user"
                      for b_ in m.content if isinstance(b_, sess.TextBlock)]
        assert user_texts == ["[image attached — vision analysis: a cat]", "barge-in text"]
        assert [m.role for m in s.messages] == ["user", "assistant", "user"]

    asyncio.run(go())

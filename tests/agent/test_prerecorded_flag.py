from __future__ import annotations
import asyncio
from types import SimpleNamespace
from tests.agent.conftest import load_module

def _load(): return load_module("core/agent/runner.py", "runner_prerecorded_test")

def test_prerecorded_turn_skips_early_append(tmp_path):
    """A coalesced turn flagged user_side_prerecorded must NOT append the user
    message again — the fragments were already recorded on arrival."""
    mod = _load()
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod

    # Pre-seed the transcript as if two fragments were recorded on arrival.
    async def seed():
        s = await sess_mod.Session.open_or_create("discord:900", root=tmp_path)
        await s.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="frag one")]))
        await s.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="frag two")]))
    asyncio.run(seed())

    async def fake_recall(turn):
        from tests.agent.test_runner_loop import FakeCtx
        return FakeCtx(user_text="frag one\nfrag two")
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})
    async def fake_call(**kw):
        return llm_mod.LLMResponse(text_blocks=["got both"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    orig = llm_mod.call
    llm_mod.call = fake_call
    try:
        deps = mod.RunnerDeps(build_turn_context=fake_recall, cadence_llm=fake_cadence,
                              sessions_root=tmp_path, provider="openai_compat", model="m",
                              client=None, tool_registry=None, polling_tools=set())
        from tests.agent.test_runner_loop import FakeTurn
        turn = FakeTurn(content="frag one\nfrag two",
                        metadata=SimpleNamespace(origin="user", channel_id=900,
                                                 user_side_prerecorded=True))
        result = asyncio.run(mod.run_turn(turn, deps=deps, on_chunk=None))
    finally:
        llm_mod.call = orig

    assert result.responded is True
    s = asyncio.run(sess_mod.Session.open_or_create("discord:900", root=tmp_path))
    user_texts = [b.text for m in s.messages if m.role == "user"
                  for b in m.content if isinstance(b, sess_mod.TextBlock)]
    # exactly the two pre-recorded fragments — no third "frag one\nfrag two" row
    assert user_texts == ["frag one", "frag two"]


def test_prerecorded_file_only_turn_enriches_placeholder(tmp_path, monkeypatch):
    """Important-2 regression fix (2026-07-07 final review): the post-loop
    enrich block's gate required `_img_descs` truthy, so a coalesced burst
    whose sole attachment was a FILE (no image — `_img_descs=()`,
    `_txt_attachs=(...)`) never fired: the '[file attached]' placeholder
    stayed bare in the transcript and conversation_history never got the
    file's contents. Widened gate: `_img_descs or _txt_attachs`. This drives
    run_turn end-to-end with a file-only ctx and asserts the placeholder was
    upgraded in both the transcript and the persisted DB row."""
    mod = _load()
    import core.agent.session as sess_mod
    import core.agent.llm_client as llm_mod
    import core.turn.persistence as persistence_mod

    # Pre-seed the transcript as record_user_fragment would have: a bare
    # file placeholder written on arrival (mirrors the image-turn seed above).
    async def seed():
        s = await sess_mod.Session.open_or_create("discord:902", root=tmp_path)
        await s.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="[file attached]")]))
    asyncio.run(seed())

    from tests.agent.test_runner_loop import FakeCtx, FakeTurn

    async def fake_recall(turn):
        ctx = FakeCtx(user_text="[file attached]")
        ctx.image_descriptions = ()  # no image on this turn
        ctx.text_attachment_contents = ("def hello():\n    print('hi')",)
        return ctx
    async def fake_cadence(prompt):
        import json
        return json.dumps({"should_respond": True, "register": "neutral", "length": "short",
                           "phase": "opening", "target_beats": 1, "observation_text": "ok"})
    async def fake_call(**kw):
        return llm_mod.LLMResponse(text_blocks=["got the file"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    persisted: list[str] = []
    async def fake_persist(turn, content):
        persisted.append(content)
    monkeypatch.setattr(persistence_mod, "_persist_user_side_with_content", fake_persist)

    orig = llm_mod.call
    llm_mod.call = fake_call
    try:
        deps = mod.RunnerDeps(build_turn_context=fake_recall, cadence_llm=fake_cadence,
                              sessions_root=tmp_path, provider="openai_compat", model="m",
                              client=None, tool_registry=None, polling_tools=set())
        turn = FakeTurn(content="[file attached]",
                        metadata=SimpleNamespace(origin="user", channel_id=902,
                                                 user_side_prerecorded=True))
        result = asyncio.run(mod.run_turn(turn, deps=deps, on_chunk=None))
    finally:
        llm_mod.call = orig

    assert result.responded is True
    assert result.text_attachment_contents == ("def hello():\n    print('hi')",)

    s = asyncio.run(sess_mod.Session.open_or_create("discord:902", root=tmp_path))
    user_texts = [b.text for m in s.messages if m.role == "user"
                  for b in m.content if isinstance(b, sess_mod.TextBlock)]
    # placeholder upgraded in place — no longer the bare "[file attached]" row.
    assert user_texts != ["[file attached]"]
    assert any("hello" in t for t in user_texts)
    # ...and the DB persist got the same rich content, not the bare placeholder.
    assert len(persisted) == 1
    assert "hello" in persisted[0]

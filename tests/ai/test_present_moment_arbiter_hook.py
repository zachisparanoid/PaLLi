"""Variant selection matrix, the extended impulse parse, and the rail hook
(arbiter spec §3/§4). Flags pinned per test — the 3352e3d lesson."""
import pytest

import ai.present_moment as pm
from ai.present_moment import PresentMomentEngine, _load_appraisal_template


def test_variant_matrix():
    t, v = _load_appraisal_template(affect_on=True, arbiter_on=True)
    assert v == "arbiter" and "{threads_block}" in t and "held_before" in t
    t, v = _load_appraisal_template(affect_on=True, arbiter_on=False)
    assert v == "affect" and "{threads_block}" not in t
    t, v = _load_appraisal_template(affect_on=False, arbiter_on=False)
    assert v == "base"
    # arbiter without affect: dormant, falls to base (spec §9)
    t, v = _load_appraisal_template(affect_on=False, arbiter_on=True)
    assert v == "base"


def test_extended_impulse_parse():
    imp = pm._parse_impulse(
        {"kind": "say", "why": "w", "framing": "  hey  there ",
         "sensitivity": "care", "thread_id": "abcd1234", "held_before": True}
    )
    assert imp["framing"] == "hey there"
    assert imp["sensitivity"] == "care"
    assert imp["thread_id"] == "abcd1234"
    assert imp["held_before"] is True


def test_extended_parse_absent_fields_add_no_keys():
    imp = pm._parse_impulse({"kind": "hold", "why": "w"})
    assert set(imp) == {"kind", "why"}      # byte-identical-off meta shape


def test_framing_clipped_to_500():
    imp = pm._parse_impulse({"kind": "say", "why": "w", "framing": "x" * 900})
    assert len(imp["framing"]) == 500


def test_malformed_still_none():
    imp = pm._parse_impulse({"kind": "shout", "why": "w"})
    assert imp["kind"] == "none"


@pytest.mark.asyncio
async def test_rail_hook_called_only_when_live(monkeypatch):
    """run_cycle_once wires consider_release with arbiter_live semantics.
    Full-cycle harness: LLM + gather + store stubbed."""
    engine = PresentMomentEngine()
    calls = []

    async def _fake_release(*, impulse, owner_user_id, arbiter_live):
        calls.append((impulse["kind"], arbiter_live))
        return {"disposition": "sent", "channel_id": 5}

    monkeypatch.setattr("ai.arbiter.consider_release", _fake_release)
    monkeypatch.setattr(engine, "_affect_enabled", lambda: True)
    monkeypatch.setattr(engine, "_arbiter_enabled", lambda: True)

    import ai.present_moment_gather as gather

    async def _fake_gather(*, owner_user_id):
        return gather.GatherInputs(threads_block="- [ab] board")

    monkeypatch.setattr(gather, "gather_moment_inputs", _fake_gather)

    async def _fake_llm(messages, **kw):
        return (
            '{"moment": "m", "impulse": {"kind": "say", "why": "w", '
            '"framing": "hello", "sensitivity": "routine"}, '
            '"undercurrent": "steady"}'
        )

    class _L:
        chat_completion = staticmethod(_fake_llm)

    monkeypatch.setattr("ai.llm_client.llm", _L())
    written = {}

    def _fake_write(*, text, meta):
        written.update({"text": text, "meta": meta})
        return "x.jsonl"

    monkeypatch.setattr("ai.present_moment_store.write_moment", _fake_write)
    out = await engine.run_cycle_once()
    assert calls and calls[0] == ("say", True)
    assert written["meta"]["impulse"]["rail"]["disposition"] == "sent"
    assert out["impulse"]["rail"]["channel_id"] == 5


@pytest.mark.asyncio
async def test_rail_absent_when_arbiter_off(monkeypatch):
    engine = PresentMomentEngine()
    monkeypatch.setattr(engine, "_affect_enabled", lambda: True)
    monkeypatch.setattr(engine, "_arbiter_enabled", lambda: False)
    called = []

    async def _fake_release(**kw):
        called.append(1)
        return {}

    monkeypatch.setattr("ai.arbiter.consider_release", _fake_release)

    import ai.present_moment_gather as gather

    async def _fake_gather(*, owner_user_id):
        return gather.GatherInputs()

    monkeypatch.setattr(gather, "gather_moment_inputs", _fake_gather)

    async def _fake_llm(messages, **kw):
        return (
            '{"moment": "m", "impulse": {"kind": "say", "why": "w"}, '
            '"undercurrent": "u"}'
        )

    class _L:
        chat_completion = staticmethod(_fake_llm)

    monkeypatch.setattr("ai.llm_client.llm", _L())
    written = {}

    def _fake_write(*, text, meta):
        written.update({"meta": meta})
        return "x.jsonl"

    monkeypatch.setattr("ai.present_moment_store.write_moment", _fake_write)
    await engine.run_cycle_once()
    assert called == []
    assert "rail" not in written["meta"]["impulse"]   # byte-identical-off

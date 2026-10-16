"""Per-lane "thinking off" (2026-08-23). deepseek-v4-flash thinks by default
(17 reasoning tokens for "is 7 prime?"); with ``thinking: {"type":
"disabled"}`` on the request it answers in ~0.7 s with zero reasoning —
probed live 03:06. The two steering judges are yes/no decisions on the path
he is waiting on: last night the mid-task judge burned its whole 700-token
budget on thought, the net bumped it, and the holding line took 38 s.
They never think again. Everything else is byte-identical."""
from __future__ import annotations

import json as _json

import pytest

from ai.llm_client import DeepSeekProvider, OpenAIProvider
from ai.llm_openai_compatible import is_no_think_lane


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status, self.headers = payload, status, {}
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def json(self):
        return self._payload
    async def text(self):
        return _json.dumps(self._payload)


class _FakeSession:
    def __init__(self, payloads):
        self._payloads, self.calls = list(payloads), []
    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "json": dict(json or {})})
        return _FakeResponse(self._payloads.pop(0))


_OK = {"choices": [{"message": {"content": '{"supersede": false}'}, "finish_reason": "stop"}]}


def test_the_no_think_lanes_are_the_pre_reply_classifiers():
    """The two steering judges (03:06) + cadence and turn_read (the 03:4x
    side-by-side, docs/2026-08-23-side-by-side-thinking.md): cadence with
    thinking = 5.9 s median and 11/24 calls EMPTY at its 600-token cap;
    without = 2.1 s, 0 empty, votes at least as good. turn_read 4.7 → 1.5 s,
    same read (|Δweight| median 0.05). ~7 s off every reply's pre-phase."""
    # 2026-09-03: the reply-length compressor joins them. At 11:48 it spent 5,966
    # reasoning tokens on a 203-word compression, returned nothing, timed out twice,
    # and after 110 s the trim cut his architecture answer in half. A rewrite to a
    # word cap is mechanical work; thinking is waste here.
    for lane in ("steering_judgment", "steering_supersede", "agent_cadence_shape", "turn_read", "reply_govern"):
        assert is_no_think_lane(lane), lane
    # 2026-09-07 (his call): the six background lanes join. Thu→Mon on DeepSeek they
    # hit the reasoning ceiling 40+ times (reflection 17k tokens then two timeouts,
    # present moment 14 empties in a day, stake appraisal, daydream, curiosity, the
    # monologue); the balance died Thu 04:07 mid-rail. The LLM still judges; it just
    # skips the scratch work, as the judges have since 08-23.
    for lane in ("reflect_turn", "stake_appraisal", "present_moment_cycle", "daydream_tick", "curiosity_research", "inner_monologue_tick"):
        assert is_no_think_lane(lane), lane
    # 2026-09-08 (his call): the nightly disclosure harvest too — it returned empty
    # on reasoning burn (8k → 16k tokens) every night from 09-03 to 09-07 and planted
    # nothing. Still a DEEP lane for its clock; it just stops thinking.
    assert is_no_think_lane("night_cycle_disclosure_harvest")
    # 2026-09-30 (the owner: "let's resolve them", on the 09-08 precedent): three
    # more night lanes that returned empty on reasoning burn. The memory reconcile
    # came back empty 4 of the 5 nights 09-26..30 and failed outright on 09-30;
    # the interests author 3 of 5 (09-30: "unparseable_revision"); the shared-
    # language harvest burned ~30k reasoning tokens before a retry saved it.
    # Found by Batch 6's outcome-based health check on its first night.
    for lane in ("consolidation_reconcile", "passions_author", "night_cycle_callbacks_harvest"):
        assert is_no_think_lane(lane), lane
    # 2026-10-05 (the owner: "yes, turn off thinking for that"): three more lanes
    # spent their whole thinking budget at the 04:00 night and came back empty
    # before a bigger-budget retry saved them — the self-pages gate (17,670
    # reasoning chars), the stake review (32,688) and tonight's pick (32,953),
    # with the balance at $0.66. They still judge and pick; they stop thinking.
    for lane in ("narrative_self_gate", "stake_review", "night_cycle_tonight_pick"):
        assert is_no_think_lane(lane), lane
    # 2026-10-05 06:58 (the owner: "yes, go ahead"): three summaries that kept running
    # out of thinking budget (since 09-06: the fact gist 11 times, the weekly monologue
    # digest 5, the monthly 1 — each paid twice) and the arbiter's freshness check,
    # a yes/no judge that should never have thought (judges since 08-23).
    for lane in ("consolidation_fact_gist", "monologue_weekly_digest_2026-W41",
                 "monologue_monthly_digest_2026-10", "arbiter_freshness"):
        assert is_no_think_lane(lane), lane
    # 2026-10-12: the presence report's judge (palli_ctl presence --judge): a yes/no
    # with a quote on a 200-token budget; 10 of 24 came back empty with thinking on.
    assert is_no_think_lane("presence_judge")
    # 2026-10-12 (R14, the one first look): one JSON look in her own voice takes the
    # place of the quick vote and the first read, both no-think since 08-23; a JSON
    # look must not think either (11 of 24 cadence calls came back empty with it on).
    assert is_no_think_lane("first_look")
    # 2026-10-13 (R15, memory that reaches): the second look inside a reply — a JSON
    # plan for a wider search and a sifting of what it found, under a 10 s wall the
    # owner is waiting on — and the replay's judge, a verdict on a 300-token budget.
    for lane in ("memory_reach_widen", "memory_reach_keep", "memory_reach_judge"):
        assert is_no_think_lane(lane), lane
    # her reply and the other 04:00 writing lanes keep thinking
    for lane in ("agent_loop_main", "night_cycle_daily_meaning", "consolidation_day_gist",
                 "night_cycle_views", "owner_picture", "reflect_first_person",
                 "narrative_self_author", "", None):
        assert not is_no_think_lane(lane), lane


@pytest.mark.asyncio
async def test_deepseek_disables_thinking_on_a_no_think_lane(monkeypatch):
    provider = DeepSeekProvider()
    session = _FakeSession([_OK])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)
    await provider.chat_completion([{"role": "system", "content": "judge"}], model="deepseek-v4-flash",
                                   max_tokens=700, log_context="steering_supersede")
    assert session.calls[0]["json"].get("thinking") == {"type": "disabled"}


@pytest.mark.asyncio
async def test_deepseek_leaves_every_other_lane_byte_identical(monkeypatch):
    provider = DeepSeekProvider()
    session = _FakeSession([_OK, _OK])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)
    # stake_review stopped thinking 10-05; the daily meaning still thinks.
    for lane in ("agent_loop_main", "night_cycle_daily_meaning"):
        await provider.chat_completion([{"role": "user", "content": "hi"}], model="deepseek-v4-flash",
                                       max_tokens=100, log_context=lane)
    assert all("thinking" not in c["json"] for c in session.calls)


@pytest.mark.asyncio
async def test_openai_never_gets_the_field(monkeypatch):
    """OpenAI rejects unknown request arguments; the switch is DeepSeek's."""
    provider = OpenAIProvider(provider_name="openai", log_label="OpenAI", base_url="https://api.openai.com/v1",
                              default_model="gpt-4o-mini", supports_vision=False)
    session = _FakeSession([_OK])

    async def fake_get_session():
        return session

    monkeypatch.setattr(provider, "_get_session", fake_get_session)
    monkeypatch.setattr("ai.llm_client.config.LOG_LLM_RESPONSE_CALLS", False, raising=False)
    await provider.chat_completion([{"role": "system", "content": "judge"}], model="gpt-4o-mini",
                                   max_tokens=700, log_context="steering_supersede")
    assert "thinking" not in session.calls[0]["json"]

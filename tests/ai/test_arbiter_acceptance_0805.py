"""THE acceptance fixture (arbiter spec §10): 2026-08-05 — she holds a
care-shaped ask (a friend's doctor appointment) that has survived
cycles; he arrives after hours away. Under the arbiter: ONE send, her held
framing verbatim, full custody bookkeeping, and the ambient greeting clock
stands down. This is the incident that argued the step into existence,
pinned."""
import time
import types

import pytest

import ai.arbiter as arbiter
import ai.present_moment as pm
from ai.present_moment import PresentMomentEngine

FRAMING = (
    "Hey, welcome home. No rush at all — but I've been thinking about "
    "you two today. How'd it go?"
)


@pytest.mark.asyncio
async def test_the_0805_arrival(monkeypatch, tmp_path):
    monkeypatch.setattr(arbiter, "ARBITER_DATA_DIR", tmp_path / "arbiter")

    class _Comp:
        arbiter = True
        present_moment = True
        arbiter_daily_send_ceiling = 3
        arbiter_quiet_minutes = 5
        arbiter_quiet_window = ""

    monkeypatch.setattr(arbiter, "_companion", lambda: _Comp())

    # quiet house: he's been away for hours, no run in flight
    async def _chan(*, owner_id):
        return ("discord", "123")

    async def _away(*, owner_id):
        return time.time() - 6 * 3600

    monkeypatch.setattr(arbiter, "_pick_surface", _chan)
    monkeypatch.setattr(arbiter, "_last_user_message_at", _away)
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: False)
    sent = []

    async def _record(**kw):
        sent.append(kw)
        return True

    monkeypatch.setattr(arbiter, "_record_outbound", _record)

    async def _actives(*, owner_user_id):
        return [types.SimpleNamespace(
            id="d0c70r0000000000", kind=types.SimpleNamespace(value="health"),
            urgency="high", created_at=time.time() - 86400,
            topic_summary="how the doctor appointment went",
            why_it_matters="a friend",
        )]

    monkeypatch.setattr(arbiter, "_list_active", _actives)
    marked = []

    async def _mark(*, thread_id):
        marked.append(thread_id)

    monkeypatch.setattr(arbiter, "_mark_surfaced", _mark)

    # Impulse-freshness (2026-08-07): the REAL _freshness_check runs here —
    # template load, render, JSON parse — with only the DB-touching tail
    # stubbed and the verdict answered by the prompt-aware fake LLM below.
    # The acceptance now proves the whole rail INCLUDING the paid gate.
    async def _tail(owner_user_id, *, limit=10):
        return ("[me] I've been holding the doctor question all day.\n"
                "[them] (they have just come home)")

    monkeypatch.setattr(arbiter, "_untrimmed_tail", _tail)

    engine = PresentMomentEngine()
    monkeypatch.setattr(engine, "_affect_enabled", lambda: True)
    monkeypatch.setattr(engine, "_arbiter_enabled", lambda: True)

    import ai.present_moment_gather as gather

    async def _fake_gather(*, owner_user_id):
        return gather.GatherInputs(
            threads_block="- [d0c70r00] [health | high | held 1d] "
                          "how the doctor appointment went — a friend",
        )

    monkeypatch.setattr(gather, "gather_moment_inputs", _fake_gather)

    async def _fake_llm(messages, **kw):
        content = str((messages or [{}])[0].get("content") or "")
        if "STILL TRUE and STILL WORTH SENDING" in content:
            # the freshness judge's question (prompts/arbiter_freshness.txt):
            # held all day, he just arrived — fresh, verbatim release.
            return ('{"fresh": true, "reason": "he just arrived and the '
                    'question is still unanswered"}')
        # her appraisal: the held care say, at his arrival
        return (
            '{"moment": "He\'s home. The question I\'ve been holding all '
            'day can finally be said gently.", '
            '"impulse": {"kind": "say", "why": "held all day; now is the '
            'moment", "framing": "' + FRAMING + '", '
            '"sensitivity": "care", "held_before": true, '
            '"thread_id": "d0c70r00"}, '
            '"undercurrent": "quiet readiness"}'
        )

    class _L:
        chat_completion = staticmethod(_fake_llm)

    monkeypatch.setattr("ai.llm_client.llm", _L())
    written = {}

    def _fake_write(*, text, meta):
        written.update({"meta": meta})
        return "x.jsonl"

    monkeypatch.setattr("ai.present_moment_store.write_moment", _fake_write)

    await engine.run_cycle_once(
        woke_on=[{"kind": "owner_arrived_after_absence", "at": 0.0}]
    )

    # ONE send, her framing verbatim, care bar passed via held_before
    assert len(sent) == 1
    assert sent[0]["text"] == FRAMING
    assert sent[0]["extra"]["sensitivity"] == "care"
    assert sent[0]["extra"]["thread_id"] == "d0c70r0000000000"
    assert written["meta"]["impulse"]["rail"]["disposition"] == "sent"
    assert marked == ["d0c70r0000000000"]
    assert arbiter.sends_today() == 1

    # and the ambient greeting clock stands down under the flag
    import core.ambient_service as amb

    monkeypatch.setattr("ai.arbiter.arbiter_enabled", lambda: True)
    assert amb._arbiter_owns_arrivals() is True

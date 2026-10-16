"""The release gate (arbiter spec §4), the ledger/ceiling, the sweep claim.
Every test pins its own flags — owner flips are environment changes (the
3352e3d lesson)."""
import time
import types

import pytest

import ai.arbiter as arbiter


class _Comp:
    def __init__(self, **kw):
        self.arbiter = kw.get("arbiter", True)
        self.present_moment = kw.get("present_moment", True)
        self.arbiter_daily_send_ceiling = kw.get("ceiling", 3)
        self.arbiter_quiet_minutes = kw.get("quiet_minutes", 5)
        self.arbiter_quiet_window = kw.get("quiet_window", "")
        self.arbiter_followup_ttl_days = kw.get("f_ttl", 7)
        self.arbiter_thread_ttl_days = kw.get("t_ttl", 45)
        self.arbiter_freshness_check = kw.get("freshness_knob", True)
        self.arbiter_learned_quiet = kw.get("learned_quiet", False)
        self.rhythm_lookback_days = kw.get("rhythm_lookback", 56)
        self.rhythm_quiet_max_active_ratio = kw.get("rhythm_ratio", 0.08)
        self.rhythm_min_observed_weekdays = kw.get("rhythm_min_wd", 15)
        self.rhythm_min_observed_weekend_days = kw.get("rhythm_min_we", 6)
        self.rhythm_present_override_minutes = kw.get("rhythm_override_min", 90)
        self.surprise_daily_named = kw.get("surprise_daily_named", 1)


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Arbiter on, quiet everywhere, channel 123, send succeeds. The
    freshness judge (spec §4) is faked FRESH so every release traverses the
    real gate; freshness tests re-patch the fake in their own bodies."""
    monkeypatch.setattr(arbiter, "ARBITER_DATA_DIR", tmp_path / "arbiter")
    comp = _Comp()
    monkeypatch.setattr(arbiter, "_companion", lambda: comp)

    async def _fresh_ok(**kw):
        return (True, "fresh (fixture)")

    monkeypatch.setattr(arbiter, "_freshness_check", _fresh_ok)

    async def _chan(*, owner_id):
        return ("discord", "123")

    async def _last_msg(*, owner_id):
        return time.time() - 3600.0

    sent = []

    async def _record(**kw):
        sent.append(kw)
        return True

    marked = []

    async def _mark(*, thread_id):
        marked.append(thread_id)

    async def _actives(*, owner_user_id):
        T = types.SimpleNamespace
        return [T(id="abcdef1234567890", kind=T(value="personal"),
                  topic_summary="ferret", urgency="low")]

    monkeypatch.setattr(arbiter, "_pick_surface", _chan)
    monkeypatch.setattr(arbiter, "_last_user_message_at", _last_msg)
    monkeypatch.setattr(arbiter, "_record_outbound", _record)
    monkeypatch.setattr(arbiter, "_mark_surfaced", _mark)
    monkeypatch.setattr(arbiter, "_list_active", _actives)
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: False)
    return types.SimpleNamespace(comp=comp, sent=sent, marked=marked)


def _say(**kw):
    d = {"kind": "say", "why": "w", "framing": "hey — verdict on the name?",
         "sensitivity": "routine"}
    d.update(kw)
    return d


@pytest.mark.asyncio
async def test_flag_off_returns_empty(wired):
    wired.comp.arbiter = False
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out == {}


@pytest.mark.asyncio
async def test_not_live_returns_empty(wired):
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=False)
    assert out == {}


@pytest.mark.asyncio
async def test_none_and_hold_get_no_rail(wired):
    for kind in ("none", "hold"):
        out = await arbiter.consider_release(
            impulse={"kind": kind, "why": "w"}, owner_user_id=1,
            arbiter_live=True)
        assert out == {}


@pytest.mark.asyncio
async def test_do_recorded_only(wired):
    out = await arbiter.consider_release(
        impulse={"kind": "do", "why": "w"}, owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "do_recorded_only"
    assert wired.sent == []


@pytest.mark.asyncio
async def test_routine_say_sends_with_full_bookkeeping(wired):
    out = await arbiter.consider_release(
        impulse=_say(thread_id="abcdef12"), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"
    assert out["channel_id"] == 123
    assert len(wired.sent) == 1
    assert wired.sent[0]["text"] == "hey — verdict on the name?"
    assert wired.marked == ["abcdef1234567890"]   # id8 resolved to full id
    assert arbiter.sends_today() == 1


@pytest.mark.asyncio
async def test_say_without_framing_held(wired):
    out = await arbiter.consider_release(
        impulse={"kind": "say", "why": "w"}, owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_no_framing"
    assert wired.sent == []


@pytest.mark.asyncio
async def test_conversation_state_holds(wired, monkeypatch):
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "generating")
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_conversation"

    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")

    async def _recent(*, owner_id):
        return time.time() - 60.0     # spoke 1 min ago < 5-min knob

    monkeypatch.setattr(arbiter, "_last_user_message_at", _recent)
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_conversation"


@pytest.mark.asyncio
async def test_quiet_window_holds(wired):
    wired.comp.arbiter_quiet_window = "00:00-23:59"
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_quiet_window"


def test_quiet_window_parser():
    lt = time.localtime
    assert arbiter._in_quiet_window(lt(), "") is False
    assert arbiter._in_quiet_window(lt(), "garbage") is False
    assert arbiter._in_quiet_window(lt(), "00:00-23:59") is True
    # wrap-midnight window: 23:00-08:00 covers 07:00 but not 12:00
    noon = time.struct_time((2026, 8, 6, 12, 0, 0, 3, 218, 1))
    seven = time.struct_time((2026, 8, 6, 7, 0, 0, 3, 218, 1))
    assert arbiter._in_quiet_window(noon, "23:00-08:00") is False
    assert arbiter._in_quiet_window(seven, "23:00-08:00") is True


@pytest.mark.asyncio
async def test_care_first_look_held_then_sends(wired):
    out = await arbiter.consider_release(
        impulse=_say(sensitivity="care"), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_care_first_look"
    assert wired.sent == []
    out = await arbiter.consider_release(
        impulse=_say(sensitivity="care", held_before=True),
        owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"


@pytest.mark.asyncio
async def test_ceiling_blocks_and_zero_means_dark(wired):
    for _ in range(3):
        out = await arbiter.consider_release(
            impulse=_say(), owner_user_id=1, arbiter_live=True)
        assert out["disposition"] == "sent"
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "ceiling_blocked"
    assert len(wired.sent) == 3

    wired.comp.arbiter_daily_send_ceiling = 0
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "ceiling_blocked"


@pytest.mark.asyncio
async def test_no_channel_holds(wired, monkeypatch):
    async def _none(*, owner_id):
        return None

    monkeypatch.setattr(arbiter, "_pick_surface", _none)
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_no_channel"


@pytest.mark.asyncio
async def test_send_raise_fails_closed_thread_stays_eligible(wired, monkeypatch):
    async def _boom(**kw):
        raise RuntimeError("discord down")

    monkeypatch.setattr(arbiter, "_record_outbound", _boom)
    out = await arbiter.consider_release(
        impulse=_say(thread_id="abcdef12"), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "error_closed"
    assert wired.marked == []
    assert arbiter.sends_today() == 0


@pytest.mark.asyncio
async def test_unmatched_thread_id_sends_without_bookkeeping(wired):
    out = await arbiter.consider_release(
        impulse=_say(thread_id="ffffffff"), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"
    assert out.get("thread_unmatched") is True
    assert wired.marked == []


# ── the freshness judge (impulse-freshness spec §4, 2026-08-07) ──────────
# The LAST gate and the only paid one. A gate, never a rewriter (the 07-30
# law): fresh releases the framing byte-identical; stale drops with the
# reason recorded; errors fail closed. It runs only after every free gate
# has passed — a ceiling-blocked impulse must never spend an LLM call.

@pytest.mark.asyncio
async def test_fresh_verdict_releases_framing_byte_identical(wired, monkeypatch):
    seen = {}

    async def _fresh(**kw):
        seen.update(kw)
        return (True, "still unanswered")

    monkeypatch.setattr(arbiter, "_freshness_check", _fresh)
    out = await arbiter.consider_release(
        impulse=_say(framing="Ferret update? Carmen was my vote."),
        owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"
    assert wired.sent[0]["text"] == "Ferret update? Carmen was my vote."
    assert seen["framing"] == "Ferret update? Carmen was my vote."


@pytest.mark.asyncio
async def test_stale_verdict_drops_with_reason_no_send(wired, monkeypatch):
    import json as _json

    async def _stale(**kw):
        return (False, "he already answered this at 16:32")

    monkeypatch.setattr(arbiter, "_freshness_check", _stale)
    out = await arbiter.consider_release(
        impulse=_say(framing="give me the real beats"),
        owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "stale_dropped"
    assert "already answered" in out.get("reason", "")
    assert wired.sent == []
    assert arbiter.sends_today() == 0
    rows = [_json.loads(ln) for ln in
            arbiter._ledger_path().read_text().splitlines() if ln.strip()]
    assert any(r.get("disposition") == "stale_dropped"
               and "already answered" in r.get("reason", "") for r in rows)


@pytest.mark.asyncio
async def test_judge_error_fails_closed(wired, monkeypatch):
    async def _boom(**kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(arbiter, "_freshness_check", _boom)
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "stale_check_failed"
    assert wired.sent == []


@pytest.mark.asyncio
async def test_ceiling_blocked_never_spends_a_judge_call(wired, monkeypatch):
    calls = []

    async def _counting(**kw):
        calls.append(1)
        return (True, "")

    monkeypatch.setattr(arbiter, "_freshness_check", _counting)
    wired.comp.arbiter_daily_send_ceiling = 0     # rail dark
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "ceiling_blocked"
    assert calls == []


@pytest.mark.asyncio
async def test_knob_false_restores_day_one_release(wired, monkeypatch):
    async def _never(**kw):
        raise AssertionError("judge must not run when the knob is off")

    monkeypatch.setattr(arbiter, "_freshness_check", _never)
    wired.comp.arbiter_freshness_check = False
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"


# Captured at import time — BEFORE any fixture swaps the seam — so the
# incident fixture below can drive the REAL judge under wired's rail.
_REAL_FRESHNESS_CHECK = arbiter._freshness_check


@pytest.mark.asyncio
async def test_the_2026_08_06_1646_stale_first_send(wired, monkeypatch, tmp_path):
    """THE incident fixture (impulse-freshness spec §1/§7): 2026-08-06,
    the arbiter's first live day. 16:30 the owner asks for the roadmap
    MP3; 16:32 she delivers it; 16:34 her cycle forms "give me the real
    beats" and the rail correctly holds it; 16:46 it re-forms, gates
    open, and it SENDS — asking for inputs to a task she completed
    fourteen minutes earlier. The glance's head-keep trim had eaten the
    completion end of her reply, she had no sense of her own deeds, and
    nothing re-verified a held impulse at release.

    Pinned here: the EVIDENCE PATH through the real judge. Her >300-char
    reply reaches the prompt WHOLE (the glance would have trimmed it),
    the delivery deed reaches it too, the framing arrives verbatim — and
    the stale verdict dies as stale_dropped with the reason on the
    ledger, no send. The fake LLM answers stale ONLY if the evidence
    actually arrived; the verdict itself belongs to the LLM in life."""
    import json as _json
    from unittest.mock import AsyncMock, patch

    from ai import recent_deeds as rd

    windup = "Alright, let me pull the roadmap state together first — " * 7
    completion = ("Done — palli_roadmap.mp3 is in your Discord: 32 steps, "
                  "the four live surfaces, 4.1 minutes.")
    her_reply = windup + completion
    assert len(her_reply) > 300          # the glance WOULD have fragmented it

    monkeypatch.setattr(rd, "DEEDS_PATH", tmp_path / "deeds.jsonl")
    rd.record_deed("deliver_artifact", "delivered a file: palli_roadmap.mp3")

    async def _refs():
        return {"palli_user_id": 1}

    async def _history(*, user_id, limit, include_metadata):
        return [
            {"role": "user", "content": "can you make me the roadmap mp3?",
             "timestamp": 0.0},
            {"role": "assistant", "content": her_reply, "timestamp": 0.0},
        ]

    framing = "give me the real beats — what should the roadmap actually say?"
    prompt_seen = {}

    async def _judge_llm(messages, **kw):
        content = str((messages or [{}])[0].get("content") or "")
        prompt_seen["content"] = content
        if completion in content and \
                "delivered a file: palli_roadmap.mp3" in content and \
                framing in content:
            return ('{"fresh": false, "reason": "the roadmap mp3 went out '
                    'fourteen minutes ago — asking for its inputs is stale"}')
        return '{"fresh": true, "reason": "evidence never reached the judge"}'

    class _L:
        chat_completion = staticmethod(_judge_llm)

    monkeypatch.setattr(arbiter, "_freshness_check", _REAL_FRESHNESS_CHECK)
    with patch("core.local_mode.get_local_owner_refs",
               new=AsyncMock(side_effect=_refs)), \
         patch("core.database.brain.get_conversation_history",
               new=AsyncMock(side_effect=_history)), \
         patch("ai.llm_client.llm", _L()):
        out = await arbiter.consider_release(
            impulse=_say(framing=framing), owner_user_id=1, arbiter_live=True)

    assert completion in prompt_seen["content"]      # trim repair, at the judge
    assert "delivered a file: palli_roadmap.mp3" in prompt_seen["content"]
    assert out["disposition"] == "stale_dropped"
    assert "fourteen minutes" in out.get("reason", "")
    assert wired.sent == []                          # the 16:46 send never fires
    assert arbiter.sends_today() == 0
    rows = [_json.loads(ln) for ln in
            arbiter._ledger_path().read_text().splitlines() if ln.strip()]
    assert any(r.get("disposition") == "stale_dropped" for r in rows)


@pytest.mark.asyncio
async def test_freshness_budget_fits_the_reasoning_appetite(monkeypatch):
    """2026-08-13 06:01 incident: max_tokens=700 died to
    finish_reason=length with reasoning_len=2953 — and the 4x retry (2800)
    was STILL under the observed appetite, so the verdict came back empty
    twice and a live impulse dropped as stale_check_failed. The lane's
    budget must clear the reasoning burn with the verdict's room on top
    (the D1 budget-starvation class, story-lane sibling)."""
    from unittest.mock import AsyncMock, patch

    seen = {}

    async def _judge_llm(messages, **kw):
        seen.update(kw)
        return '{"fresh": true, "reason": "budget check"}'

    class _L:
        chat_completion = staticmethod(_judge_llm)

    async def _refs():
        return {"palli_user_id": 1}

    async def _history(*, user_id, limit, include_metadata):
        return []

    with patch("core.local_mode.get_local_owner_refs",
               new=AsyncMock(side_effect=_refs)), \
         patch("core.database.brain.get_conversation_history",
               new=AsyncMock(side_effect=_history)), \
         patch("ai.llm_client.llm", _L()):
        fresh, _reason = await _REAL_FRESHNESS_CHECK(
            framing="x", owner_user_id=1, held_before=False)
    assert fresh is True
    assert seen.get("max_tokens", 0) >= 3000


@pytest.mark.asyncio
async def test_untrimmed_tail_carries_long_messages_whole():
    """Prompt-evidence pin (spec §4/§7): the judge reads FULL text — a
    >300-char message must arrive whole, never as the glance's fragment."""
    from unittest.mock import AsyncMock, patch

    long_me = ("Recording now — one moment. " * 12) + \
        "Done: palli_roadmap.mp3 is in your Discord, 4.1 minutes."
    assert len(long_me) > 300

    async def _refs():
        return {"palli_user_id": 7}

    async def _history(*, user_id, limit, include_metadata):
        return [{"role": "assistant", "content": long_me, "timestamp": 0.0}]

    with patch("core.local_mode.get_local_owner_refs",
               new=AsyncMock(side_effect=_refs)), \
         patch("core.database.brain.get_conversation_history",
               new=AsyncMock(side_effect=_history)):
        tail = await arbiter._untrimmed_tail(7)
    assert long_me in tail



@pytest.mark.asyncio
async def test_untrimmed_tail_reads_a_not_delivered_note_as_the_systems():
    """Final review, Important 1: persist_delivery_failure writes a ``system``
    row after a reply the door could not send. The judge's tail labelled
    every non-user row "[me]", so it read the note as her own words."""
    from unittest.mock import AsyncMock, patch

    async def _refs():
        return {"palli_user_id": 7}

    async def _history(*, user_id, limit, include_metadata):
        return [
            {"role": "assistant", "content": "the plan is X", "timestamp": 0.0},
            {"role": "system", "content": "[not delivered to them: HTTPException 50013]",
             "timestamp": 0.0},
        ]

    with patch("core.local_mode.get_local_owner_refs",
               new=AsyncMock(side_effect=_refs)), \
         patch("core.database.brain.get_conversation_history",
               new=AsyncMock(side_effect=_history)):
        tail = await arbiter._untrimmed_tail(7)
    assert "[system] [not delivered to them: HTTPException 50013]" in tail
    assert "[me] [not delivered" not in tail
    assert "[me] the plan is X" in tail

@pytest.mark.asyncio
async def test_sweep_claims_date_only_on_success(wired, monkeypatch):
    calls = []

    async def _expire(*, owner_user_id, followup_ttl_days, thread_ttl_days):
        calls.append((followup_ttl_days, thread_ttl_days))
        return [("id1", "followup", 9)]

    monkeypatch.setattr(arbiter, "_expire_stale", _expire)
    await arbiter.maybe_run_daily_sweep(owner_user_id=1)
    await arbiter.maybe_run_daily_sweep(owner_user_id=1)
    assert calls == [(7.0, 45.0)]      # second call: date already claimed

    async def _boom(**kw):
        raise RuntimeError("db locked")

    # a NEW day with a failing expiry: claim must NOT advance
    monkeypatch.setattr(arbiter, "_expire_stale", _boom)
    monkeypatch.setattr(arbiter, "_today", lambda: "2099-01-01")
    await arbiter.maybe_run_daily_sweep(owner_user_id=1)     # swallows, no claim
    monkeypatch.setattr(arbiter, "_expire_stale", _expire)
    await arbiter.maybe_run_daily_sweep(owner_user_id=1)
    assert len(calls) == 2             # retried after the failure


@pytest.mark.asyncio
async def test_sweep_noop_when_flag_off(wired, monkeypatch):
    wired.comp.arbiter = False
    called = []

    async def _expire(**kw):
        called.append(1)
        return []

    monkeypatch.setattr(arbiter, "_expire_stale", _expire)
    await arbiter.maybe_run_daily_sweep(owner_user_id=1)
    assert called == []


# ── learned quiet hours (step 22) ────────────────────────────────────────


def _rhythm_file(tmp_path, *, quiet, active=2, observed=40):
    """A rhythm file whose CURRENT bucket has the given verdict."""
    import json as _json
    from datetime import datetime as _dt

    from ai.owner_rhythm import bucket_key, compute_rhythm

    r = compute_rhythm([], now=time.time())
    key = bucket_key(_dt.now())
    r["buckets"][key] = {
        "active_days": active, "observed_days": observed, "quiet": quiet,
    }
    p = tmp_path / "owner_rhythm.json"
    p.write_text(_json.dumps(r), encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_learned_quiet_holds_with_evidence(wired, tmp_path, monkeypatch):
    """Plain case, no presence signal in play. wired's default last-message
    (60 min ago) sits INSIDE the 90-min presence-override window by
    coincidence, so pin him further back — otherwise this accidentally
    exercises the override branch instead of the plain holds case."""
    wired.comp.arbiter_learned_quiet = True
    monkeypatch.setattr(
        arbiter, "RHYTHM_FILE", _rhythm_file(tmp_path, quiet=True))

    async def _long_ago(*, owner_id):
        return time.time() - 6 * 3600.0

    monkeypatch.setattr(arbiter, "_last_user_message_at", _long_ago)
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_learned_quiet"
    assert "active 2/40 days" in out["evidence"]
    assert wired.sent == []


@pytest.mark.asyncio
async def test_learned_quiet_flag_off_never_consults(wired, tmp_path, monkeypatch):
    """Byte-identity: with the flag off the gate must not even LOOK."""
    monkeypatch.setattr(
        arbiter, "RHYTHM_FILE", _rhythm_file(tmp_path, quiet=True))

    def _boom(*a, **kw):
        raise AssertionError("flag off but verdict loader was consulted")

    monkeypatch.setattr(arbiter, "_load_rhythm_verdict", _boom)
    out = await arbiter.consider_release(
        impulse=_say(thread_id="abcdef12"), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"


@pytest.mark.asyncio
async def test_presence_overrides_learned_quiet(wired, tmp_path, monkeypatch):
    """He messaged 30 min ago (> the 5-min conversation gate, < the 90-min
    override): awake during a learned-quiet hour -> the statistic yields."""
    wired.comp.arbiter_learned_quiet = True
    monkeypatch.setattr(
        arbiter, "RHYTHM_FILE", _rhythm_file(tmp_path, quiet=True))

    async def _recent(*, owner_id):
        return time.time() - 1800.0

    monkeypatch.setattr(arbiter, "_last_user_message_at", _recent)
    out = await arbiter.consider_release(
        impulse=_say(thread_id="abcdef12"), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"


@pytest.mark.asyncio
async def test_presence_override_note_rides_the_sent_ledger(
    wired, tmp_path, monkeypatch
):
    """Spec §2/§4: the override leaves a trace on the disposition, not just
    an INFO log line — the SENT ledger entry carries
    learned_quiet_overridden_by_presence with the evidence string."""
    import json as _json

    wired.comp.arbiter_learned_quiet = True
    monkeypatch.setattr(
        arbiter, "RHYTHM_FILE", _rhythm_file(tmp_path, quiet=True))

    async def _recent(*, owner_id):
        return time.time() - 1800.0     # 30 min ago: present, override fires

    monkeypatch.setattr(arbiter, "_last_user_message_at", _recent)
    out = await arbiter.consider_release(
        impulse=_say(thread_id="abcdef12"), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"

    rows = [_json.loads(ln) for ln in
            arbiter._ledger_path().read_text().splitlines() if ln.strip()]
    sent = [r for r in rows if r.get("disposition") == "sent"]
    assert len(sent) == 1
    assert "active 2/40 days" in sent[0].get(
        "learned_quiet_overridden_by_presence", "")


@pytest.mark.asyncio
async def test_learned_quiet_fails_open_when_file_missing(wired, tmp_path, monkeypatch):
    wired.comp.arbiter_learned_quiet = True
    monkeypatch.setattr(arbiter, "RHYTHM_FILE", tmp_path / "absent.json")
    out = await arbiter.consider_release(
        impulse=_say(thread_id="abcdef12"), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"


@pytest.mark.asyncio
async def test_manual_window_unions_with_learned(wired, tmp_path, monkeypatch):
    """Spec §2: manual and learned QUIET are a union. Manual is checked
    first, so even in a not-quiet learned bucket the manual window holds."""
    wired.comp.arbiter_learned_quiet = True
    wired.comp.arbiter_quiet_window = "00:00-23:59"
    monkeypatch.setattr(
        arbiter, "RHYTHM_FILE", _rhythm_file(tmp_path, quiet=False))
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_quiet_window"


@pytest.mark.asyncio
async def test_not_quiet_bucket_proceeds(wired, tmp_path, monkeypatch):
    wired.comp.arbiter_learned_quiet = True
    monkeypatch.setattr(
        arbiter, "RHYTHM_FILE", _rhythm_file(tmp_path, quiet=False))
    out = await arbiter.consider_release(
        impulse=_say(thread_id="abcdef12"), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "sent"


# ── the surprise naming budget (organ 8, spec §5) ───────────────────────

@pytest.mark.asyncio
async def test_surprise_say_passes_and_counts(wired):
    wired.comp.surprise_daily_named = 1
    v = await arbiter.consider_release(
        impulse=_say(sensitivity="surprise", framing="you're up at nine — that's new"),
        owner_user_id=7, arbiter_live=True)
    assert v["disposition"] == "sent"
    assert arbiter.surprise_named_today() == 1


@pytest.mark.asyncio
async def test_second_surprise_of_the_day_holds_but_routine_still_flows(wired):
    wired.comp.surprise_daily_named = 1
    await arbiter.consider_release(impulse=_say(sensitivity="surprise", framing="a"),
                                   owner_user_id=7, arbiter_live=True)
    v = await arbiter.consider_release(impulse=_say(sensitivity="surprise", framing="b"),
                                       owner_user_id=7, arbiter_live=True)
    assert v["disposition"] == "held_surprise_budget"
    v = await arbiter.consider_release(impulse=_say(framing="ordinary reach"),
                                       owner_user_id=7, arbiter_live=True)
    assert v["disposition"] == "sent"          # the budget gates ONLY surprise-says


@pytest.mark.asyncio
async def test_zero_means_never_named(wired):
    wired.comp.surprise_daily_named = 0
    v = await arbiter.consider_release(impulse=_say(sensitivity="surprise", framing="x"),
                                       owner_user_id=7, arbiter_live=True)
    assert v["disposition"] == "held_surprise_budget"


@pytest.mark.asyncio
async def test_duplex_on_working_run_no_longer_holds(wired, monkeypatch):
    """Step 27 §2: a run that has WORKED (a real tool call) does not hold her
    say when duplex is on — that is exactly the moment she may reach him."""
    from core.agent import duplex as dx
    monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: True)
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] != "held_conversation"


@pytest.mark.asyncio
async def test_duplex_on_plain_reply_generating_still_holds(wired, monkeypatch):
    """A plain reply mid-generation still carries the moment (Slice 2)."""
    from core.agent import duplex as dx
    monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "generating")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: False)
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_conversation"


@pytest.mark.asyncio
async def test_duplex_off_working_run_holds_as_today(wired, monkeypatch):
    """Flag off: byte-identical verdicts — a working run still holds."""
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: True)
    out = await arbiter.consider_release(
        impulse=_say(), owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_conversation"

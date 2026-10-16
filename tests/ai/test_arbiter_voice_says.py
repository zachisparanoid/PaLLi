"""Voice joins the arbiter (step 32, spec §4/§5): the medium decision at
RELEASE, both lanes.

Pins: he's in the room, alone with her ⇒ the say arrives SPOKEN (no duplicate text), ledger
medium="voice", and the spoken send spends the SAME daily ceiling; he's not
⇒ the text path, byte-identical; a speak failure falls back to text with
the reason on the ledger; knob off ⇒ the probe NEVER runs; the share lane
rides the same medium rule. Task 4 adds: in-voice counts as present for
learned quiet, with a distinct override note.

Since 2026-10-13 (the owner: a message for him alone never reaches a group)
a say or a share is SPOKEN only when the call is him and her alone. With a
friend on the call, or members that could not be read, it goes as text to
his private chat (the picked chat); he still counts as present.
"""
from __future__ import annotations

import json
import time
import types

import pytest

from ai import arbiter


class _Comp:
    def __init__(self, **kw):
        self.arbiter = kw.get("arbiter", True)
        self.present_moment = kw.get("present_moment", True)
        self.arbiter_daily_send_ceiling = kw.get("ceiling", 3)
        self.arbiter_quiet_minutes = kw.get("quiet_min", 5)
        self.arbiter_quiet_window = ""
        self.arbiter_freshness_check = kw.get("freshness_knob", True)
        self.arbiter_learned_quiet = kw.get("learned_quiet", False)
        self.rhythm_present_override_minutes = 90
        self.surprise_daily_named = 1
        self.arbiter_voice_says = kw.get("voice_says", True)


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(arbiter, "ARBITER_DATA_DIR", tmp_path / "arbiter")
    comp = _Comp()
    monkeypatch.setattr(arbiter, "_companion", lambda: comp)

    async def _fresh_ok(**kw):
        return (True, "fresh (fixture)")

    async def _chan(*, owner_id):
        return ("discord", "123")

    async def _last_msg(*, owner_id):
        return time.time() - 3600.0

    sent_text = []
    sent_spoken = []

    async def _record(**kw):
        sent_text.append(kw)
        return True

    async def _record_spoken(**kw):
        sent_spoken.append(kw)
        return True

    state = types.SimpleNamespace(
        comp=comp, sent_text=sent_text, sent_spoken=sent_spoken,
        vm={"voice_channel_id": 777, "runtime_session_key": "voice:sess",
            "alone_with_her": True},
        tmp=tmp_path,
    )

    async def _vm():
        if isinstance(state.vm, Exception):
            raise state.vm
        return state.vm

    monkeypatch.setattr(arbiter, "_freshness_check", _fresh_ok)
    monkeypatch.setattr(arbiter, "_pick_surface", _chan)
    monkeypatch.setattr(arbiter, "_last_user_message_at", _last_msg)
    monkeypatch.setattr(arbiter, "_record_outbound", _record)
    monkeypatch.setattr(arbiter, "_record_outbound_spoken", _record_spoken)
    monkeypatch.setattr(arbiter, "_voice_moment", _vm)
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: False)

    async def _actives(*, owner_user_id):
        return []

    async def _mark(*, thread_id):
        pass

    monkeypatch.setattr(arbiter, "_list_active", _actives)
    monkeypatch.setattr(arbiter, "_mark_surfaced", _mark)
    return state


def _say(**kw):
    d = {"kind": "say", "why": "w", "framing": "hey — verdict on the name?",
         "sensitivity": "routine"}
    d.update(kw)
    return d


def _ledger_entries(state):
    path = arbiter._ledger_path()
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


async def _release(**kw):
    return await arbiter.consider_release(
        impulse=_say(**kw), owner_user_id=1, arbiter_live=True)


@pytest.mark.asyncio
async def test_spoken_when_he_is_in_the_room(wired):
    out = await _release()
    assert out["disposition"] == "sent"
    assert out["medium"] == "voice"
    (spoken,) = wired.sent_spoken
    assert spoken["runtime_session_key"] == "voice:sess"
    assert spoken["text"] == "hey — verdict on the name?"
    assert spoken["channel_id"] == 123
    assert wired.sent_text == []  # spoken ONLY — no duplicate text
    last = _ledger_entries(wired)[-1]
    assert last["disposition"] == "sent"
    assert last["medium"] == "voice"


# --- a shared call: friends would hear it (the owner, 10-13) ------------------

@pytest.mark.parametrize("vm", [
    {"voice_channel_id": 777, "runtime_session_key": "voice:sess", "alone_with_her": False},
    {"voice_channel_id": 777, "runtime_session_key": "voice:sess"},          # members unknown
    {"voice_channel_id": 777, "runtime_session_key": "voice:sess", "alone_with_her": "yes"},
])
@pytest.mark.asyncio
async def test_a_say_on_a_shared_call_goes_as_text_to_his_private_chat(wired, vm):
    wired.vm = vm
    out = await _release()
    assert out["disposition"] == "sent"
    assert out["medium"] == "text"
    assert wired.sent_spoken == []
    (text,) = wired.sent_text
    assert (text["platform"], text["channel_id"]) == ("discord", 123)
    assert text["text"] == "hey — verdict on the name?"
    assert _ledger_entries(wired)[-1]["medium"] == "text"


@pytest.mark.asyncio
async def test_a_share_on_a_shared_call_goes_as_text_to_his_private_chat(wired):
    wired.vm = {"voice_channel_id": 777, "runtime_session_key": "voice:sess",
                "alone_with_her": False}
    out = await arbiter.deliver_share(text="the workbench thing", owner_user_id=1)
    assert out["disposition"] == "sent" and out["medium"] == "text"
    assert wired.sent_spoken == []
    (text,) = wired.sent_text
    assert (text["platform"], text["channel_id"]) == ("discord", 123)


@pytest.mark.asyncio
async def test_on_a_shared_call_he_still_counts_as_present(wired, monkeypatch):
    """Learned quiet's presence override reads whether he is in her voice
    channel, not who else is: it still holds, and the words go as text."""
    _confidently_quiet(monkeypatch, wired)
    wired.vm = {"voice_channel_id": 777, "runtime_session_key": "voice:sess",
                "alone_with_her": False}
    out = await _release()
    assert out["disposition"] == "sent" and out["medium"] == "text"
    assert wired.sent_spoken == []
    assert _ledger_entries(wired)[-1]["learned_quiet_overridden_by_presence"] == "in her voice channel"


@pytest.mark.asyncio
async def test_text_when_not_a_voice_moment(wired):
    wired.vm = None
    out = await _release()
    assert out["disposition"] == "sent"
    assert out["medium"] == "text"
    assert len(wired.sent_text) == 1
    assert wired.sent_spoken == []
    assert _ledger_entries(wired)[-1]["medium"] == "text"


@pytest.mark.asyncio
async def test_speak_failure_falls_back_to_text(wired, monkeypatch):
    from core.turn.outbound import SpokenSendFailed

    async def _spoken_fails(**kw):
        raise SpokenSendFailed("session vanished")

    monkeypatch.setattr(arbiter, "_record_outbound_spoken", _spoken_fails)
    out = await _release()
    assert out["disposition"] == "sent"
    assert out["medium"] == "text"
    assert len(wired.sent_text) == 1
    last = _ledger_entries(wired)[-1]
    assert last["medium"] == "text"
    assert "session vanished" in last["voice_fallback_text"]


@pytest.mark.asyncio
async def test_knob_off_probe_never_runs(wired):
    wired.comp.arbiter_voice_says = False
    wired.vm = AssertionError("probe ran with the knob OFF")
    out = await _release()
    assert out["disposition"] == "sent"
    assert out["medium"] == "text"
    assert wired.sent_spoken == []


@pytest.mark.asyncio
async def test_spoken_spends_the_same_ceiling(wired):
    wired.comp.arbiter_daily_send_ceiling = 1
    out = await _release()
    assert out["medium"] == "voice"
    assert arbiter.sends_today() == 1
    second = await _release()
    assert second["disposition"] == "ceiling_blocked"


# --- spec §5: in her channel = present (learned quiet) -----------------------

def _confidently_quiet(monkeypatch, wired):
    wired.comp.arbiter_learned_quiet = True
    monkeypatch.setattr(
        arbiter, "_load_rhythm_verdict",
        lambda path, *, now: (True, "quiet 41/42 Thursdays"),
    )

    async def _old_msg(*, owner_id):
        return time.time() - 10 * 3600.0  # far past the 90-min override

    monkeypatch.setattr(arbiter, "_last_user_message_at", _old_msg)


@pytest.mark.asyncio
async def test_in_voice_overrides_learned_quiet(wired, monkeypatch):
    _confidently_quiet(monkeypatch, wired)
    out = await _release()
    assert out["disposition"] == "sent"
    assert out["medium"] == "voice"
    last = _ledger_entries(wired)[-1]
    assert last["learned_quiet_overridden_by_presence"] == "in her voice channel"


@pytest.mark.asyncio
async def test_learned_quiet_still_holds_without_voice(wired, monkeypatch):
    _confidently_quiet(monkeypatch, wired)
    wired.vm = None
    out = await _release()
    assert out["disposition"] == "held_learned_quiet"
    assert wired.sent_text == [] and wired.sent_spoken == []


# --- the knob's homes (schema default False; the yaml is what turns it on) ---

def test_schema_defaults_it_off_and_the_yaml_is_what_turns_it_on():
    """Step 28 task 5: this read the OWNER's data/palli.yaml and required True there.
    The shipped config (data/palli.example.yaml) leaves it False on purpose — speaking
    arbiter messages needs voice, and a fresh install has voice off — so the old
    assertion could only ever pass on his machine, and failed in every clone. What the
    knob's two homes actually have to prove is that the schema defaults it OFF and the
    yaml is what turns it ON; both halves are pinned here, against the file that ships."""
    import yaml as _yaml

    from core.settings_schema import CompanionConfig, PalliSettings

    assert CompanionConfig().arbiter_voice_says is False
    raw = _yaml.safe_load(open("data/palli.example.yaml"))
    assert PalliSettings.model_validate(raw).companion.arbiter_voice_says is False
    raw["companion"]["arbiter_voice_says"] = True
    assert PalliSettings.model_validate(raw).companion.arbiter_voice_says is True


@pytest.mark.asyncio
async def test_share_lane_rides_the_same_medium_rule(wired):
    out = await arbiter.deliver_share(text="the workbench thing", owner_user_id=1)
    assert out["disposition"] == "sent"
    (spoken,) = wired.sent_spoken
    assert spoken["text"] == "the workbench thing"
    assert spoken["origin"] == "workbench_share"
    assert wired.sent_text == []
    assert _ledger_entries(wired)[-1]["medium"] == "voice"

"""Organ 4.12 — the reply door (spec §4): the most recent open bits as
light memory under CALLBACKS_LAW. Never the raw line, an id, or a source
(§0.5 — the receipt is why she can say it, never how she says it). Zero
bytes off/empty/guest. The ctx hop is EXECUTED, not assumed.

Scaffolding mirrors tests/ai/test_disclosures_reply_band.py, which mirrors
the deeds band before it: a store render → recall read → TurnContext → the
context adapter (guest-blanked) → the system-prompt section. Every hop is
pinned, because a band that renders correctly and never reaches the prompt
is the scar this house already wears.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

import ai.callbacks_store as cs

# The law, transcribed independently from spec §4's blockquote
# (docs/superpowers/specs/2026-09-02-shared-idiolect-design.md). It is
# deliberately a LITERAL and not a reference to cs.CALLBACKS_LAW: every
# other assertion in this file compares the constant to itself, so they all
# stay green while the law drifts. This one does not. Editing the law is a
# deliberate two-place change — spec, constant, and this transcription must
# agree, and a one-place edit fails here.
_LAW_VERBATIM = (
    "These are words and bits that are genuinely yours together, each with a real "
    "origin in your record. Use them the way you'd naturally use them, or not at all. "
    "Claim a shared history only from here; if a bit isn't here, don't perform "
    "remembering it — ask, or look it up. The origins are yours to know, not to "
    "recite: say where something came from only when they ask."
)

# NOON UTC on purpose. The band renders a receipt's day in HIS local time
# (the organ's convention, set in task 3), so an 02:11 UTC receipt is
# "Mar 13" in New York and "Mar 14" in Berlin — a test pinned to the UTC
# day would be green here and red on his wall. Noon holds the day across
# every ordinary offset, and _DAY recomputes it through the SAME conversion
# the render uses, so the assertion never depends on where the suite runs.
RECEIPT = {"who": "him", "when": "2026-03-14T12:00:00+00:00",
           "line": "toss it in the oubliette with the other steppers",
           "source": "s:1"}
_DAY = datetime.fromisoformat(RECEIPT["when"]).astimezone().strftime("%b %d")


@pytest.fixture(autouse=True)
def _hermetic_book(tmp_path, monkeypatch):
    """CALLBACKS_ROOT beats the module DIR constant inside `_resolve_root`,
    so every plant/fold/retire in this file — including the ones the real
    `build_turn_context` drives — lands in tmp_path, never in her book
    under data/."""
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))


def _on(monkeypatch):
    """The suite's autouse `_idiolect_off_by_default` pin holds the organ
    OFF for every test; a body that wants it on re-patches here (a
    test-body monkeypatch lands after the fixture's and wins)."""
    monkeypatch.setattr(cs, "idiolect_enabled", lambda: True)


def _plant(root, bit, coined_by="him", when=RECEIPT["when"]):
    """The receipt line is BUILT from the bit: since the C1 rail, `plant`
    refuses a receipt whose line does not carry its own bit, so a helper
    pinned to one fixed line could only ever plant one bit."""
    receipt = {**RECEIPT, "when": when, "line": f"we called it {bit} that night"}
    return cs.plant(bit=bit, kind="nickname", coined_by=coined_by,
                    receipt=receipt, root=root)


# ── the law itself (spec §4, verbatim) ──────────────────────────────────

def test_the_law_is_byte_exact():
    """The whole behavioral contract of the reply door lives in this string
    — use-or-don't, claim-only-from-here, origins-not-recited. If it can
    drift silently, nothing in the organ is pinned."""
    assert cs.CALLBACKS_LAW == _LAW_VERBATIM


# ── the render (spec §4) ────────────────────────────────────────────────

def test_band_zero_bytes_off_and_empty(tmp_path, monkeypatch):
    _plant(tmp_path, "the oubliette")
    assert cs.render_callbacks_band(root=tmp_path) == ""      # the suite's off-pin
    _on(monkeypatch)
    assert cs.render_callbacks_band(root=tmp_path / "empty") == ""


def test_band_is_light_memory_only(tmp_path, monkeypatch):
    """Lived language only (§0.5, the fourth wall): the bit, whose it is,
    the day. The receipt is why she may say it — never how she says it."""
    _on(monkeypatch)
    c = _plant(tmp_path, "the oubliette")
    band = cs.render_callbacks_band(root=tmp_path)
    # WHOLE-LINE equality, not containment (the twin's shape,
    # tests/ai/test_disclosures_reply_band.py:79): the band's structure is
    # part of its contract. Every containment assertion in this file stays
    # green if the law is joined onto the last bit with a space instead of
    # a newline — which would put `— theirs, Mar 14 These are words and bits…`
    # in her prompt as one fused line.
    assert band.splitlines()[0] == f'- "the oubliette" — theirs, {_DAY}'
    assert band.splitlines()[-1] == cs.CALLBACKS_LAW
    assert band.endswith(cs.CALLBACKS_LAW)
    assert c.id not in band and c.receipt["line"] not in band
    assert "we called it" not in band and "s:1" not in band


def test_band_says_hers_and_both(tmp_path, monkeypatch):
    _on(monkeypatch)
    _plant(tmp_path, "one", coined_by="her")
    _plant(tmp_path, "two", coined_by="both")
    band = cs.render_callbacks_band(root=tmp_path)
    assert f'- "one" — hers, {_DAY}' in band
    assert f'- "two" — both of yours, {_DAY}' in band


@pytest.fixture
def in_his_zone():
    """Run one test with the process clock pinned to HIS zone.

    The local-day convention is only OBSERVABLE where local ≠ UTC, so a pin
    that inherits the host's zone is a pin that evaporates in a container or
    on a UTC CI box — the ruled convention would then rot silently while the
    suite stayed green. Pin the zone and the day becomes a literal.

    Manual save/restore rather than monkeypatch (the conftest guards' shape,
    and for a second reason here): restoring TZ has to be followed by a
    second `tzset()`, and monkeypatch's teardown runs after the test body —
    too late for a `finally` to re-read it.
    """
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


def test_the_day_is_his_day_not_utcs(in_his_zone):
    """The band renders a receipt's day in HIS local time — the organ's
    convention, set at the lookup in task 3, so one bit reads the same day
    wherever it is named.

    An OFF-NOON stamp in a PINNED zone: 02:11 UTC on the 14th is the night
    of the 13th in New York. The fixtures' noon stamp reads identically on
    both sides of the conversion, so only this test can see a dropped
    `.astimezone()` — and only because the zone is forced, on any host.
    """
    assert cs._born_day("2026-03-14T02:11:09+00:00") == "Mar 13"
    assert cs._born_day("2026-03-14T12:00:00+00:00") == "Mar 14"
    # A malformed stamp costs that row its DAY, never the band (the render's
    # own contract, one level down): a non-string `when` is unreachable
    # today — `_clean_receipt` guarantees a real ISO string at plant and at
    # fold — but "broken row ⇒ empty band" is the wrong failure to leave
    # armed behind an unreachable guard.
    assert cs._born_day("not a date") == "sometime"
    assert cs._born_day(None) == "sometime"


def test_band_caps_at_the_knob_newest_first(tmp_path, monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(cs, "_band_max", lambda: 2)
    t = [1000.0]

    def clock():
        t[0] += 10.0
        return t[0]

    monkeypatch.setattr(cs.time, "time", clock)
    for b in ("one", "two", "three"):
        _plant(tmp_path, b)
    band = cs.render_callbacks_band(root=tmp_path)
    assert '"three"' in band and '"two"' in band and '"one"' not in band
    assert band.index('"three"') < band.index('"two"')


def test_retired_leaves_the_band(tmp_path, monkeypatch):
    _on(monkeypatch)
    c = _plant(tmp_path, "the oubliette")
    cs.retire(c.id, reason="nope", root=tmp_path)
    assert cs.render_callbacks_band(root=tmp_path) == ""


def test_band_degrades_to_empty_when_the_book_raises(tmp_path, monkeypatch):
    """This render sits on her live reply path — a broken book must cost
    her a band, never a turn."""
    _on(monkeypatch)

    def boom(**kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(cs, "recent_callbacks", boom)
    assert cs.render_callbacks_band(root=tmp_path) == ""


# ── recall's read ───────────────────────────────────────────────────────

def test_recall_band_reads_the_store(monkeypatch):
    import core.turn.recall as recall

    monkeypatch.setattr(cs, "render_callbacks_band", lambda **kw: "BAND")
    assert recall._read_callbacks_band() == "BAND"


def test_recall_delegates_to_the_organ_bands_module(monkeypatch):
    """Recall's reader is a THIN wrapper over core.turn.organ_bands (the
    rule-6 split, fix 2): recall imports the module object, so the band
    function is resolved at call time and a wrapper that quietly grew its
    own copy of the read — the drift a split invites — fails here."""
    import core.turn.recall as recall
    from core.turn import organ_bands

    monkeypatch.setattr(organ_bands, "read_callbacks_band", lambda: "FROM_ORGAN_BANDS")
    assert recall._read_callbacks_band() == "FROM_ORGAN_BANDS"


def test_recall_band_empty_when_the_render_raises(monkeypatch):
    import core.turn.recall as recall

    def boom(**kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(cs, "render_callbacks_band", boom)
    assert recall._read_callbacks_band() == ""


# ── the constructor hop: recall's REAL TurnContext build ────────────────

def _build_turn(content: str, owner_id: int = 7):
    from core.turn.types import Turn, TurnMetadata

    return Turn(surface="dashboard", owner_id=owner_id, content=content,
                images=[], metadata=TurnMetadata())


def _stub_recall_dependencies(monkeypatch):
    """Stub the heavy recall calls so build_turn_context runs cleanly (the
    twin in tests/ai/test_disclosures_reply_band.py)."""
    import ai.fact_retrieval as fact_retrieval

    monkeypatch.setattr(fact_retrieval, "get_decay_weighted_facts", AsyncMock(return_value=[]))
    import core.identity.service as identity_service_module

    monkeypatch.setattr(identity_service_module.identity_service, "compile_for_owner",
                        lambda owner_id: None)
    import core.conversation_history_store as chs

    monkeypatch.setattr(chs.ConversationHistoryStore, "get_conversation_history",
                        AsyncMock(return_value=[]))
    import ai.inner_memory_index as ami

    monkeypatch.setattr(ami, "query", AsyncMock(return_value=[]))
    import core.first_contact_targets as fct

    monkeypatch.setattr(fct, "unfulfilled_targets_for_owner", lambda owner_id: [])


@pytest.mark.asyncio
async def test_the_band_survives_the_constructor_hop(tmp_path, monkeypatch):
    """The caring-memory scar, written into recall's own constructor
    comment: a field computed in recall but missed from the TurnContext
    call is silently blank everywhere, and every band test still passes. So
    execute the hop — a real book, the real reader, the real
    build_turn_context, one assertion on the far side."""
    import core.turn.recall as recall

    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))   # the hermetic seam
    _on(monkeypatch)
    cs.plant(bit="the oubliette", kind="nickname", coined_by="him", receipt=RECEIPT)
    assert (tmp_path / "callbacks.jsonl").exists()        # her real book untouched
    _stub_recall_dependencies(monkeypatch)
    ctx = await recall.build_turn_context(_build_turn("hey"))
    assert f'"the oubliette" — theirs, {_DAY}' in ctx.callbacks_text
    assert cs.CALLBACKS_LAW in ctx.callbacks_text


# ── the carriers: TurnContext → adapter (guest-blanked) ─────────────────

def test_ctx_and_adapter_carry_the_field():
    from core.agent.context_adapter import AgentLoopContext
    from core.turn.types import TurnContext

    assert TurnContext.__dataclass_fields__["callbacks_text"].default == ""
    assert AgentLoopContext.__dataclass_fields__["callbacks_text"].default == ""


def test_guests_never_see_our_private_language():
    """Their private language is THEIRS (spec §4) — the same P() wall her
    small truths, the moment and the deeds band stand behind."""
    from core.agent.context_adapter import build_agent_loop_context
    from core.turn.types import Turn, TurnContext, TurnMetadata

    turn = Turn(surface="discord_text", owner_id=1, content="who are you?",
                metadata=TurnMetadata(), received_at=datetime(2026, 9, 1, 3, 0, 0))
    ctx = TurnContext(turn=turn, callbacks_text=f'- "the oubliette" — theirs, {_DAY}')
    assert build_agent_loop_context(turn, ctx, is_owner=True).callbacks_text.startswith(
        '- "the oubliette"')
    assert build_agent_loop_context(turn, ctx, is_owner=False).callbacks_text == ""


# ── the section (mirrors tests/agent/test_grounding_rule.py's harness) ──

@dataclass
class _Ctx:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    recent_deeds_text: str = ""
    own_work_trace_text: str = ""
    present_moment_text: str = ""
    situational_text: str = ""
    disclosures_text: str = ""
    callbacks_text: str = ""


@dataclass
class _NoField:
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    recent_deeds_text: str = ""
    own_work_trace_text: str = ""
    present_moment_text: str = ""
    situational_text: str = ""
    disclosures_text: str = ""


def _band() -> str:
    """One rendered band, exactly as the store hands it up."""
    return f'- "the oubliette" — theirs, {_DAY}\n' + cs.CALLBACKS_LAW


def test_system_prompt_section_only_when_text_present():
    from core.agent.system_prompt import build_system_prompt

    on = build_system_prompt(_Ctx(callbacks_text=_band()), cadence_directive="")
    off = build_system_prompt(_Ctx(), cadence_directive="")
    assert "## Our private language" in on and cs.CALLBACKS_LAW in on
    assert "## Our private language" not in off
    # Zero bytes when dark: byte-identical to a build that never heard of
    # the field at all.
    assert off == build_system_prompt(_NoField(), cadence_directive="")


def test_section_sits_right_after_her_small_truths():
    from core.agent.system_prompt import build_system_prompt

    out = build_system_prompt(
        _Ctx(disclosures_text="TRUTHS_MARKER", callbacks_text=_band(),
             present_moment_text="MOMENT_MARKER"),
        cadence_directive="")
    assert out.index("## Your small truths") < out.index("## Our private language")
    assert out.index("## Our private language") < out.index("MOMENT_MARKER")

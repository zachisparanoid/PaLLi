"""Downtime curiosity research (2026-07-01, companion faculty).

The inner monologue may emit an optional ``curiosity`` — a topic PaLLi
wants to go read about for itself, unrelated to any task. A background
run searches the web (Exa), writes what it learned back into the
monologue stream as a private note (entry_kind="curiosity_research"),
and — when genuinely worth sharing — attaches an observation
``proactive_candidate`` so the candidate rail can turn PaLLi's own
rabbit hole into first contact.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_EXA_PAYLOAD = {
    "results": [
        {
            "title": "Octopus dreaming states",
            "url": "https://example.org/octopus",
            "publishedDate": "2026-05-01",
            "summary": "Cephalopods show REM-like color cycling in sleep.",
            "text": "body",
        },
    ],
}


def _llm_json(note: str, worth_sharing: bool = False, framing: str | None = None) -> str:
    return json.dumps(
        {"note": note, "worth_sharing": worth_sharing, "share_framing": framing},
    )


@pytest.mark.asyncio
async def test_research_writes_private_note_entry() -> None:
    from ai import curiosity_research as cr

    fake_client = SimpleNamespace(search=AsyncMock(return_value=_EXA_PAYLOAD))
    written = []
    with patch.object(
        cr, "_exa_client", lambda: fake_client,
    ), patch.object(
        cr.llm, "chat_completion",
        AsyncMock(return_value=_llm_json("Octopuses might dream in color. Filed away.")),
    ), patch.object(
        cr, "write_entry", lambda entry: written.append(entry),
    ):
        entry = await cr.research_curiosity(
            topic="octopus dreaming", owner_user_id=1,
        )

    assert entry is not None
    assert written and written[0] is entry
    assert "dream in color" in entry.thought
    assert entry.tick_metadata["entry_kind"] == "curiosity_research"
    assert entry.tick_metadata["topic"] == "octopus dreaming"
    assert entry.tick_metadata["substantive"] is True
    assert entry.proactive_candidate is None


@pytest.mark.asyncio
async def test_worth_sharing_emits_observation_candidate() -> None:
    from ai import curiosity_research as cr

    fake_client = SimpleNamespace(search=AsyncMock(return_value=_EXA_PAYLOAD))
    with patch.object(
        cr, "_exa_client", lambda: fake_client,
    ), patch.object(
        cr.llm, "chat_completion",
        AsyncMock(return_value=_llm_json(
            "REM-like states in cephalopods — genuinely wild.",
            worth_sharing=True,
            framing="went down a rabbit hole about octopus sleep — the weird part is they might dream in color",
        )),
    ), patch.object(
        cr, "write_entry", lambda entry: None,
    ):
        entry = await cr.research_curiosity(
            topic="octopus dreaming", owner_user_id=1,
        )

    assert entry is not None
    assert entry.proactive_candidate is not None
    assert "rabbit hole" in entry.proactive_candidate
    assert entry.candidate_kind == "observation"


@pytest.mark.asyncio
async def test_no_exa_results_skips_entry() -> None:
    from ai import curiosity_research as cr

    fake_client = SimpleNamespace(search=AsyncMock(return_value=None))
    with patch.object(
        cr, "_exa_client", lambda: fake_client,
    ), patch.object(
        cr, "write_entry", MagicMock(),
    ) as writer:
        entry = await cr.research_curiosity(topic="xyzzy", owner_user_id=1)

    assert entry is None
    writer.assert_not_called()


def _web_search_on():
    """features.web_search pinned ON.

    research_allowed() checks that flag BEFORE the cap, and the shipped config turns it
    off (step 28 task 4 fix 1). The three cap tests below are about the cap; without this
    they short-circuit on "features.web_search is off" and prove nothing about it."""
    from unittest.mock import patch

    import core.settings_boot as settings_boot

    return patch.object(
        settings_boot,
        "settings_service",
        SimpleNamespace(snapshot=lambda: SimpleNamespace(
            features=SimpleNamespace(web_search=True))),
    )


def test_daily_cap_blocks_research() -> None:
    from ai import curiosity_research as cr

    now = time.time()
    research_entry = SimpleNamespace(
        timestamp=now - 100,
        tick_metadata={"entry_kind": "curiosity_research"},
    )
    # Hermetic against the owner's live yaml: unbooted settings ⇒ default cap 3.
    with _web_search_on(), patch.object(cr, "companion_settings", lambda: None), patch.object(
        cr, "read_entries",
        lambda **kw: [research_entry] * 3,
    ):
        allowed, reason = cr.research_allowed()
    assert allowed is False
    assert "cap" in reason and "3" in reason


def test_daily_cap_reads_owner_setting() -> None:
    """The cap is the owner's knob (companion.research_daily_cap), not a
    hardcoded constant — 2026-07-20 free-reign request."""
    from ai import curiosity_research as cr

    now = time.time()
    research_entry = SimpleNamespace(
        timestamp=now - 100,
        tick_metadata={"entry_kind": "curiosity_research"},
    )
    fifty = SimpleNamespace(research_daily_cap=50)
    # 3 dives already today, cap 50 ⇒ still allowed.
    with _web_search_on(), patch.object(cr, "companion_settings", lambda: fifty), patch.object(
        cr, "read_entries", lambda **kw: [research_entry] * 3,
    ):
        allowed, _ = cr.research_allowed()
    assert allowed is True
    # 50 dives today, cap 50 ⇒ blocked, and the reason names the real cap.
    with _web_search_on(), patch.object(cr, "companion_settings", lambda: fifty), patch.object(
        cr, "read_entries", lambda **kw: [research_entry] * 50,
    ):
        allowed, reason = cr.research_allowed()
    assert allowed is False
    assert "50" in reason


def test_daily_cap_garbage_settings_fall_back_to_default() -> None:
    from ai import curiosity_research as cr

    assert cr.research_daily_cap() >= 1
    # 0 left this list 2026-08-06: it is the owner's unlimited sentinel
    # (convention 2026-07-28), not garbage — see
    # test_research_cap_zero_means_unlimited below.
    for bad in (SimpleNamespace(research_daily_cap="lots"),
                SimpleNamespace(research_daily_cap=-5),
                SimpleNamespace()):
        with patch.object(cr, "companion_settings", lambda b=bad: b):
            cap = cr.research_daily_cap()
        assert cap >= 1, bad


def test_faculty_flag_blocks_research() -> None:
    import core.settings_boot as settings_boot
    from ai import curiosity_research as cr

    stub = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            companion=SimpleNamespace(curiosity_research=False),
        ),
    )
    with patch.object(settings_boot, "settings_service", stub):
        allowed, reason = cr.research_allowed()
    assert allowed is False
    assert "faculty" in reason or "flag" in reason


def test_parse_response_extracts_curiosity() -> None:
    from ai.inner_monologue import InnerMonologueEngine

    engine = InnerMonologueEngine()
    response = json.dumps({
        "thought": "quiet tick",
        "substantive": True,
        "sentiment": None,
        "proactive_candidate": None,
        "candidate_kind": None,
        "candidate_action_hint": None,
        "curiosity": "history of cursive handwriting decline",
    })
    parsed = engine._parse_response(response)
    assert parsed[-1] == "history of cursive handwriting decline"


def test_parse_response_curiosity_defaults_to_none() -> None:
    from ai.inner_monologue import InnerMonologueEngine

    engine = InnerMonologueEngine()
    response = json.dumps({"thought": "nothing new", "substantive": False})
    parsed = engine._parse_response(response)
    assert parsed[-1] is None


# ── Multi-round dives (2026-07-21, owner free-reign request) ─────────────


def _llm_followup(query: str) -> str:
    return json.dumps({"note": "", "followup_query": query, "worth_sharing": False})


def _payload(title: str) -> dict:
    return {"results": [{"title": title, "url": f"https://x.org/{title}",
                         "summary": f"about {title}", "text": "b"}]}


@pytest.mark.asyncio
async def test_followup_query_runs_second_search_one_entry() -> None:
    """Round 1 returns a follow-up; round 2 writes the note. Two searches,
    one entry, rounds recorded, follow-up query recorded."""
    from ai import curiosity_research as cr

    fake_client = SimpleNamespace(
        search=AsyncMock(side_effect=[_payload("first"), _payload("second")]),
    )
    llm_mock = AsyncMock(side_effect=[
        _llm_followup("condensation at the seam lines specifically"),
        _llm_json("Now I get it — the seam line is the cold bridge. Filed."),
    ])
    written = []
    with patch.object(cr, "_exa_client", lambda: fake_client), patch.object(
        cr.llm, "chat_completion", llm_mock,
    ), patch.object(cr, "write_entry", lambda e: written.append(e)), patch.object(
        cr, "companion_settings", lambda: SimpleNamespace(research_daily_cap=50),
    ), patch.object(cr, "read_entries", lambda **kw: []):
        entry = await cr.research_curiosity(topic="humidity control", owner_user_id=1)

    assert entry is not None and len(written) == 1
    assert fake_client.search.await_count == 2
    assert fake_client.search.await_args_list[1].kwargs["query"] == (
        "condensation at the seam lines specifically"
    )
    assert entry.tick_metadata["rounds"] == 2
    assert entry.tick_metadata["followup_queries"] == [
        "condensation at the seam lines specifically"
    ]
    assert "seam line is the cold bridge" in entry.thought


@pytest.mark.asyncio
async def test_rounds_are_bounded_and_last_look_is_final() -> None:
    """The LLM asking for follow-ups forever gets exactly the max rounds;
    the final render carries the last-look marker and must produce the note."""
    from ai import curiosity_research as cr

    fake_client = SimpleNamespace(
        search=AsyncMock(side_effect=[_payload("a"), _payload("b"), _payload("c")]),
    )
    prompts = []

    async def llm_capture(messages, **kw):
        prompts.append(messages[0]["content"])
        if len(prompts) < 3:
            return _llm_followup(f"deeper {len(prompts)}")
        return _llm_json("Synthesized across three looks.")

    written = []
    with patch.object(cr, "_exa_client", lambda: fake_client), patch.object(
        cr.llm, "chat_completion", AsyncMock(side_effect=llm_capture),
    ), patch.object(cr, "write_entry", lambda e: written.append(e)), patch.object(
        cr, "companion_settings", lambda: SimpleNamespace(research_daily_cap=50),
    ), patch.object(cr, "read_entries", lambda **kw: []):
        entry = await cr.research_curiosity(topic="t", owner_user_id=1)

    assert entry is not None
    assert fake_client.search.await_count == cr._MAX_DIVE_ROUNDS == 3
    assert "last look" in prompts[-1]
    assert "last look" not in prompts[0]
    assert entry.tick_metadata["rounds"] == 3


@pytest.mark.asyncio
async def test_single_round_dive_unchanged_shape() -> None:
    """A note on round 1 keeps today's behavior: one search, one call,
    rounds=1, no followup_queries key noise."""
    from ai import curiosity_research as cr

    fake_client = SimpleNamespace(search=AsyncMock(return_value=_EXA_PAYLOAD))
    written = []
    with patch.object(cr, "_exa_client", lambda: fake_client), patch.object(
        cr.llm, "chat_completion", AsyncMock(return_value=_llm_json("Done in one.")),
    ), patch.object(cr, "write_entry", lambda e: written.append(e)), patch.object(
        cr, "companion_settings", lambda: SimpleNamespace(research_daily_cap=50),
    ), patch.object(cr, "read_entries", lambda **kw: []):
        entry = await cr.research_curiosity(topic="t", owner_user_id=1)

    assert entry is not None
    assert fake_client.search.await_count == 1
    assert entry.tick_metadata["rounds"] == 1
    assert entry.tick_metadata["followup_queries"] == []


@pytest.mark.asyncio
async def test_followup_denied_when_budget_exhausted() -> None:
    """cap=2 with 1 round already spent today: round 1 runs, the follow-up
    would be round 2-of-today's-2... the NEXT follow-up (3rd round today)
    is denied — the render flips to last-look early."""
    from ai import curiosity_research as cr

    spent = SimpleNamespace(
        timestamp=time.time() - 100,
        tick_metadata={"entry_kind": "curiosity_research", "rounds": 1},
    )
    fake_client = SimpleNamespace(
        search=AsyncMock(side_effect=[_payload("a"), _payload("b")]),
    )
    prompts = []

    async def llm_capture(messages, **kw):
        prompts.append(messages[0]["content"])
        if len(prompts) == 1:
            return _llm_followup("deeper")
        return _llm_json("Stopped by budget, wrote what I had.")

    written = []
    with patch.object(cr, "_exa_client", lambda: fake_client), patch.object(
        cr.llm, "chat_completion", AsyncMock(side_effect=llm_capture),
    ), patch.object(cr, "write_entry", lambda e: written.append(e)), patch.object(
        cr, "companion_settings", lambda: SimpleNamespace(research_daily_cap=3),
    ), patch.object(cr, "read_entries", lambda **kw: [spent]):
        entry = await cr.research_curiosity(topic="t", owner_user_id=1)

    assert entry is not None
    assert fake_client.search.await_count == 2
    assert "last look" in prompts[-1]
    assert entry.tick_metadata["rounds"] == 2


@pytest.mark.asyncio
async def test_followup_search_failure_forces_final_note() -> None:
    """A failing follow-up search doesn't kill the dive — the note gets
    written from what the earlier rounds gathered."""
    from ai import curiosity_research as cr

    fake_client = SimpleNamespace(
        search=AsyncMock(side_effect=[_payload("a"), RuntimeError("exa down")]),
    )
    llm_mock = AsyncMock(side_effect=[
        _llm_followup("deeper"),
        _llm_json("Wrote it from round one's sources."),
    ])
    written = []
    with patch.object(cr, "_exa_client", lambda: fake_client), patch.object(
        cr.llm, "chat_completion", llm_mock,
    ), patch.object(cr, "write_entry", lambda e: written.append(e)), patch.object(
        cr, "companion_settings", lambda: SimpleNamespace(research_daily_cap=50),
    ), patch.object(cr, "read_entries", lambda **kw: []):
        entry = await cr.research_curiosity(topic="t", owner_user_id=1)

    assert entry is not None and written
    assert entry.tick_metadata["rounds"] == 2
    assert "round one's sources" in entry.thought


def test_count_research_today_sums_rounds_backward_compat() -> None:
    """Budget counts ROUNDS (searches), not dives; legacy entries without a
    rounds key count as 1."""
    from ai import curiosity_research as cr

    now = time.time()
    legacy = SimpleNamespace(
        timestamp=now - 50,
        tick_metadata={"entry_kind": "curiosity_research"},
    )
    multi = SimpleNamespace(
        timestamp=now - 60,
        tick_metadata={"entry_kind": "curiosity_research", "rounds": 3},
    )
    other = SimpleNamespace(timestamp=now - 70, tick_metadata={"entry_kind": "tick"})
    with patch.object(cr, "read_entries", lambda **kw: [legacy, multi, other]):
        assert cr.count_research_today() == 4


def test_research_cap_zero_means_unlimited(monkeypatch):
    """0 = unlimited (owner convention 2026-07-28; checklist 07c Task 2).
    The old accessor silently rewrote 0 to 3 — the knob just ignored you."""
    import ai.curiosity_research as cr
    from types import SimpleNamespace

    monkeypatch.setattr(cr, "companion_settings",
                        lambda: SimpleNamespace(research_daily_cap=0))
    assert cr.research_daily_cap() == 0

    monkeypatch.setattr(cr, "count_research_today", lambda: 999)
    with _web_search_on():
        ok, why = cr.research_allowed()
    assert ok is True, f"cap 0 must never block (got: {why})"


def test_research_cap_default_and_garbage_unchanged(monkeypatch):
    """Defaults-unchanged pin: unset and garbage still mean 3."""
    import ai.curiosity_research as cr
    from types import SimpleNamespace

    monkeypatch.setattr(cr, "companion_settings", lambda: None)
    assert cr.research_daily_cap() == 3
    monkeypatch.setattr(cr, "companion_settings",
                        lambda: SimpleNamespace(research_daily_cap="garbage"))
    assert cr.research_daily_cap() == 3
    monkeypatch.setattr(cr, "companion_settings",
                        lambda: SimpleNamespace(research_daily_cap=-2))
    assert cr.research_daily_cap() == 3

"""Present Moment Slice 2 — the recall-side loader and its flag gate.

The reply band renders ONLY when BOTH companion.present_moment and
companion.moment_in_replies are true (spec §4: a dark engine must never
haunt replies with old files). Loader failures degrade to "" — the band
vanishes; the turn never breaks.
Spec: docs/superpowers/specs/2026-07-27-present-moment-slice-2-design.md
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest


def test_moment_in_replies_field_exists_default_false():
    """The field MUST exist on CompanionConfig — faculty-style readers are
    fail-open on missing attrs, so a missing field would read as enabled
    in some paths and unvalidatable in yaml. Ships False (spec §0/§4)."""
    from core.settings_schema import CompanionConfig

    comp = CompanionConfig()
    assert comp.moment_in_replies is False


def _load_recall():
    from tests.agent.conftest import load_module

    return load_module("core/turn/recall.py", "recall_under_test_for_moment")


def _comp(pm: bool, mir: bool) -> SimpleNamespace:
    return SimpleNamespace(present_moment=pm, moment_in_replies=mir)


def _patch_store(monkeypatch, result: Any, calls: list) -> None:
    import ai.present_moment_store as pm_store

    def _fake_read_current(**kwargs):
        calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(pm_store, "read_current", _fake_read_current)


def test_band_absent_unless_both_flags(monkeypatch):
    """Spec §4 four-combo table: only (on, on) renders. Off-combos must
    short-circuit BEFORE the store is touched."""
    recall = _load_recall()
    calls: list = []
    _patch_store(monkeypatch, None, calls)
    for pm, mir in ((False, False), (False, True), (True, False)):
        monkeypatch.setattr(recall, "companion_settings", lambda p=pm, m=mir: _comp(p, m))
        assert recall._read_present_moment_band() == ""
    assert calls == []  # the gate never reached the store


def test_band_renders_when_both_flags_on(monkeypatch):
    from ai.present_moment_store import MomentEntry

    recall = _load_recall()
    monkeypatch.setattr(recall, "companion_settings", lambda: _comp(True, True))
    entry = MomentEntry(text="The quiet feels like concentration.", written_at=1000.0, meta={})
    _patch_store(monkeypatch, (entry, 19 * 60.0, False), [])
    out = recall._read_present_moment_band()
    assert out.startswith("Your last moment, written 19 minutes ago:")
    assert "The quiet feels like concentration." in out


def test_band_empty_when_store_empty(monkeypatch):
    recall = _load_recall()
    monkeypatch.setattr(recall, "companion_settings", lambda: _comp(True, True))
    _patch_store(monkeypatch, None, [])
    assert recall._read_present_moment_band() == ""


def test_band_empty_when_store_raises(monkeypatch):
    recall = _load_recall()
    monkeypatch.setattr(recall, "companion_settings", lambda: _comp(True, True))
    _patch_store(monkeypatch, OSError("disk gone"), [])
    assert recall._read_present_moment_band() == ""  # never raises into the turn


def test_band_empty_when_settings_unbootable(monkeypatch):
    """comp is None ⇒ fail CLOSED (the pen's _self_notes_flag convention),
    unlike relationship_calibration's deliberate fail-open."""
    recall = _load_recall()
    monkeypatch.setattr(recall, "companion_settings", lambda: None)
    assert recall._read_present_moment_band() == ""


def _build_turn(content: str, owner_id: int = 7):
    from core.turn.types import Turn, TurnMetadata

    return Turn(
        surface="dashboard",
        owner_id=owner_id,
        content=content,
        images=[],
        metadata=TurnMetadata(),
    )


def _stub_recall_dependencies(monkeypatch):
    """Stub the heavy recall calls so build_turn_context runs cleanly
    (copied from tests/turn/test_recall_stakes.py)."""
    import ai.fact_retrieval as fact_retrieval

    monkeypatch.setattr(
        fact_retrieval, "get_decay_weighted_facts", AsyncMock(return_value=[])
    )

    import core.identity.service as identity_service_module

    monkeypatch.setattr(
        identity_service_module.identity_service,
        "compile_for_owner",
        lambda owner_id: None,
    )

    import core.conversation_history_store as chs

    monkeypatch.setattr(
        chs.ConversationHistoryStore,
        "get_conversation_history",
        AsyncMock(return_value=[]),
    )

    import ai.inner_memory_index as ami

    monkeypatch.setattr(ami, "query", AsyncMock(return_value=[]))

    import core.first_contact_targets as fct

    monkeypatch.setattr(fct, "unfulfilled_targets_for_owner", lambda owner_id: [])


@pytest.mark.asyncio
async def test_band_survives_the_constructor_hop(monkeypatch):
    """The caring-memory scar (recall.py ~line 493): a field computed in
    recall but missed from the TurnContext constructor is silently blank
    everywhere. Execute the hop, don't assume it."""
    _stub_recall_dependencies(monkeypatch)
    recall = _load_recall()
    monkeypatch.setattr(
        recall, "_read_present_moment_band", lambda: "MOMENT_BAND_MARKER"
    )
    ctx = await recall.build_turn_context(_build_turn("hey"))
    assert ctx.present_moment_text == "MOMENT_BAND_MARKER"

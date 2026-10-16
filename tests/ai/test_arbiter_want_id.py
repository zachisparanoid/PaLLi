# tests/ai/test_arbiter_want_id.py
"""want_id through the rail (re-spec §5.3-§5.5): resolve, ledger, extra,
progress append fail-open, unmatched recorded, gate order untouched."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import ai.arbiter as arb
import ai.quest_store as qs


def _impulse(**kw):
    base = {"kind": "say", "why": "serving a wish", "framing": "made you something"}
    base.update(kw)
    return base


def _run(impulse, tmp_path, *, freshness=True):
    """Drive _consider_release_inner with every deterministic gate open."""
    calls = {}

    async def fake_outbound(**kwargs):
        calls["outbound"] = kwargs
        return True

    async def go():
        with patch.object(arb, "arbiter_enabled", return_value=True), \
             patch.object(arb, "_pick_surface", new=AsyncMock(return_value=("discord", "42"))), \
             patch.object(arb, "_run_phase", return_value="idle"), \
             patch.object(arb, "_is_working", return_value=False), \
             patch.object(arb, "_last_user_message_at", new=AsyncMock(return_value=0.0)), \
             patch.object(arb, "_list_active", new=AsyncMock(return_value=[])), \
             patch.object(arb, "_record_outbound", new=fake_outbound), \
             patch.object(arb, "_freshness_check",
                          new=AsyncMock(return_value=(freshness, "fine"))), \
             patch.object(arb, "ARBITER_DATA_DIR", tmp_path / "arbiter"), \
             patch.object(qs, "QUESTS_DIR", tmp_path / "quests"):
            verdict = await arb._consider_release_inner(
                impulse=impulse, owner_user_id=1, arbiter_live=True,
            )
        return verdict, calls
    return asyncio.run(go())


def _ledger_lines(tmp_path):
    files = list((tmp_path / "arbiter").glob("*.jsonl"))
    if not files:
        return []
    return [json.loads(l) for l in files[0].read_text().splitlines() if l.strip()]


def test_resolved_want_rides_ledger_extra_and_progress(tmp_path):
    q, _ = qs.plant(text="surprise him with a thing", source="him-spoken",
                    root=tmp_path / "quests")
    verdict, calls = _run(_impulse(want_id=q.id[:8]), tmp_path)
    assert verdict["disposition"] == "sent"
    assert "want_unmatched" not in verdict
    assert calls["outbound"]["extra"]["want_id"] == q.id
    sent = [l for l in _ledger_lines(tmp_path) if l["disposition"] == "sent"]
    assert sent and sent[0]["want_id"] == q.id
    progress = qs.load_quests(root=tmp_path / "quests")[q.id].progress
    assert progress and progress[-1]["note"].startswith("reached out: made you")


def test_unmatched_want_is_recorded_and_never_blocks(tmp_path):
    verdict, calls = _run(_impulse(want_id="no-such-wish"), tmp_path)
    assert verdict["disposition"] == "sent"
    assert verdict.get("want_unmatched") is True
    assert "want_id" not in calls["outbound"]["extra"]
    sent = [l for l in _ledger_lines(tmp_path) if l["disposition"] == "sent"]
    assert sent and sent[0]["want_id"] == "no-such-wish"   # her token, auditable


def test_no_want_id_changes_nothing(tmp_path):
    verdict, calls = _run(_impulse(), tmp_path)
    assert verdict["disposition"] == "sent"
    assert "want_id" not in calls["outbound"]["extra"]
    sent = [l for l in _ledger_lines(tmp_path) if l["disposition"] == "sent"]
    assert sent and "want_id" not in sent[0]


def test_progress_append_failure_never_unsends(tmp_path):
    q, _ = qs.plant(text="fragile bookkeeping", source="hers",
                    root=tmp_path / "quests")
    with patch.object(qs, "add_progress", side_effect=RuntimeError("disk gone")):
        verdict, _ = _run(_impulse(want_id=q.id), tmp_path)
    assert verdict["disposition"] == "sent"       # fail-open, like mark_surfaced


def test_stale_drop_appends_no_progress(tmp_path):
    q, _ = qs.plant(text="stale want", source="hers", root=tmp_path / "quests")
    verdict, _ = _run(_impulse(want_id=q.id), tmp_path, freshness=False)
    assert verdict["disposition"] == "stale_dropped"
    assert qs.load_quests(root=tmp_path / "quests")[q.id].progress == []

# tests/ai/test_daydream_tick.py
"""The daydream tick (re-spec §4): diet assembly carries the evidence,
the empty tick writes nothing, ops validate and apply, the affordance
block keeps the OS/code split (no paths, no shell verbs), outputs are
exactly the closed set."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import ai.daydream as dd
import ai.daydream_store as dds
import ai.quest_store as qs


def _tick(response: str, tmp_path, woke_on=None):
    async def go():
        with patch.object(qs, "QUESTS_DIR", tmp_path / "quests"), \
             patch.object(dds, "DAYDREAM_DIR", tmp_path / "daydream"), \
             patch.object(dd, "_llm_call", new=AsyncMock(return_value=response)), \
             patch.object(dd, "_queue_curiosity", return_value=None) as seed:
            result = await dd.run_daydream_tick(
                list(woke_on or [{"kind": "heartbeat", "at": 0.0}]),
                owner_user_id=1,
            )
        return result, seed
    return asyncio.run(go())


def test_empty_tick_writes_nothing(tmp_path):
    resp = json.dumps({"daydream": "", "quest_ops": [],
                       "curiosity_seed": "", "workbench_intent": ""})
    result, _ = _tick(resp, tmp_path)
    assert result["wrote"] is False
    assert dds.read_recent(root=tmp_path / "daydream") == []


def test_thought_is_written_with_meta(tmp_path):
    resp = json.dumps({"daydream": "I want to map the old backups folder.",
                       "quest_ops": [], "curiosity_seed": "",
                       "workbench_intent": ""})
    result, _ = _tick(resp, tmp_path)
    assert result["wrote"] is True
    entries = dds.read_recent(root=tmp_path / "daydream")
    assert entries and "backups folder" in entries[0].text


def test_quest_ops_apply_plant_progress_done(tmp_path):
    q, _ = qs.plant(text="standing wish", source="him-written",
                    root=tmp_path / "quests")
    resp = json.dumps({
        "daydream": "moving two wishes tonight",
        "quest_ops": [
            {"op": "plant", "text": "map the NAS music", "horizon": "someday",
             "provenance": "my own wondering"},
            {"op": "progress", "id": q.id, "note": "sketched an approach"},
        ],
        "curiosity_seed": "", "workbench_intent": "",
    })
    _tick(resp, tmp_path)
    folded = qs.load_quests(root=tmp_path / "quests")
    assert any(x.text == "map the NAS music" and x.source == "hers"
               for x in folded.values())
    assert folded[q.id].progress[-1]["note"] == "sketched an approach"


def test_malformed_ops_are_dropped_not_fatal(tmp_path):
    resp = json.dumps({
        "daydream": "one good op among junk",
        "quest_ops": [
            {"op": "explode"},
            {"op": "progress", "id": "missing", "note": "x"},
            "not even a dict",
            {"op": "plant", "text": "the survivor", "horizon": "someday"},
        ],
        "curiosity_seed": "", "workbench_intent": "",
    })
    result, _ = _tick(resp, tmp_path)
    assert result["wrote"] is True
    folded = qs.load_quests(root=tmp_path / "quests")
    assert [x.text for x in folded.values()] == ["the survivor"]


def test_intent_and_seed_route_to_their_doors(tmp_path):
    resp = json.dumps({
        "daydream": "next session: the poem",
        "quest_ops": [],
        "curiosity_seed": "nas: what's in the old photo dumps",
        "workbench_intent": "pick the poem back up where it stopped",
    })
    _, seed = _tick(resp, tmp_path)
    assert dds.read_intent(root=tmp_path / "daydream") == \
        "pick the poem back up where it stopped"
    seed.assert_called_once_with(
        seed="nas: what's in the old photo dumps", owner_user_id=1,
    )


def test_unparseable_response_writes_nothing(tmp_path):
    result, _ = _tick("i am not json at all", tmp_path)
    assert result["wrote"] is False
    assert dds.read_recent(root=tmp_path / "daydream") == []


def test_diet_carries_the_evidence(tmp_path):
    q, _ = qs.plant(text="his verbatim wish 😉", source="him-written",
                    root=tmp_path / "quests")
    qs.add_progress(q.id, "already made the first cut", root=tmp_path / "quests")
    with patch.object(qs, "QUESTS_DIR", tmp_path / "quests"), \
         patch.object(dds, "DAYDREAM_DIR", tmp_path / "daydream"):
        dds.write_entry(text="yesterday I wondered about the printer",
                        root=tmp_path / "daydream")
        prompt = asyncio.run(dd._assemble_prompt(
            [{"kind": "wish_planted", "at": 0.0}]
        ))
    assert "his verbatim wish 😉" in prompt          # §0.9 byte-for-byte
    assert "already made the first cut" in prompt    # satisfaction evidence
    assert "yesterday I wondered" in prompt          # don't-rehash material
    assert "they planted or changed a wish" in prompt  # the occasion, humanized


def test_affordance_block_names_capabilities_never_mechanics(monkeypatch):
    monkeypatch.setenv("PALLI_NAS_ROOT", "/mnt/example-nas")
    block = dd._affordances_block()
    for forbidden in ("sudo", "/mnt", "/home", "`", "palli-work"):
        assert forbidden not in block          # the capability, never the path
    assert "NAS" in block
    assert "workbench" in block.lower()


def test_affordance_block_claims_no_nas_when_none_is_configured(monkeypatch):
    """I1 — her territory is stated, not wished for. With PALLI_NAS_ROOT empty
    there is no NAS folder and no NAS at large, so neither is claimed."""
    monkeypatch.setenv("PALLI_NAS_ROOT", "")
    block = dd._affordances_block()
    assert "NAS" not in block
    assert "Your territory: your workspace." in block
    assert "workbench" in block.lower()         # the rest of the map survives

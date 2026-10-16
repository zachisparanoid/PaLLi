from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.identity.frontmatter import dump_frontmatter
from core.memory_service import memory_workspace_service


def _write_doc(path: Path, *, doc_type: str, body: str, extra: dict | None = None) -> None:
    frontmatter = {
        "doc_type": doc_type,
        "scope": "user",
        "platform": "owner",
        "user_id": 7,
        "version": 1,
        "last_updated_by": "system",
    }
    if extra:
        frontmatter.update(extra)
    path.write_text(dump_frontmatter(frontmatter, body), encoding="utf-8")


@pytest.mark.asyncio
async def test_capture_event_appends_daily_log_and_curated_memory(tmp_path, monkeypatch):
    owner_dir = tmp_path / "owners" / "7"
    memory_dir = owner_dir / "memory"
    memory_dir.mkdir(parents=True)

    memory_path = owner_dir / "MEMORY.md"
    summary_path = owner_dir / "MEMORY_SUMMARY.md"
    _write_doc(
        memory_path,
        doc_type="memory",
        body=(
            "# Memory\n\n"
            "This file is curated memory, not a transcript.\n\n"
            "## Durable Context\n\n"
            "- Interests: automation\n\n"
            "## Active Threads\n\n"
            "- No active curated threads yet.\n\n"
            "## Important Decisions\n\n"
            "- No important decisions recorded yet.\n\n"
            "## Useful References\n\n"
            "- No durable references recorded yet.\n"
        ),
    )
    _write_doc(summary_path, doc_type="memory_summary", body="# Memory Summary\n", extra={"memory_summary_text": "initial"})

    async def fake_ensure_bundle(**_kwargs):
        return None

    async def fake_update_raw_document(**kwargs):
        memory_path.write_text(kwargs["markdown"], encoding="utf-8")
        return None

    summary_updates = {}

    async def fake_silent(**kwargs):
        summary_updates.update(kwargs["updates"])
        return None

    async def fake_rebuild(*, owner_user_id: int):
        return {"status": "ok", "owner_user_id": owner_user_id}

    monkeypatch.setattr("core.memory_service.identity_service.ensure_user_identity_bundle", fake_ensure_bundle)
    monkeypatch.setattr("core.memory_service.identity_service.owner_dir", lambda owner_user_id: str(owner_dir))
    monkeypatch.setattr("core.memory_service.identity_service.owner_doc_path", lambda owner_user_id, doc_key: str(memory_path if doc_key == "memory" else summary_path))
    monkeypatch.setattr("core.memory_service.identity_service.update_raw_document", fake_update_raw_document)
    monkeypatch.setattr("core.memory_service.identity_service.silently_maintain_document", fake_silent)
    monkeypatch.setattr(memory_workspace_service, "rebuild_index", fake_rebuild)

    result = await memory_workspace_service.capture_event(
        owner_user_id=7,
        source_kind="workflow_memory",
        source_id="wfm_1",
        title="Workflow Memory",
        summary="Fixed the XML mapping workflow.",
        why_it_matters="The VKB mining setup depends on this mapping.",
        salience="high",
        tags=["workflow", "decision"],
        related_refs=["workflow:opf_1"],
        curate=True,
    )

    assert result["deduped"] is False
    day_path = memory_dir / f"{result['date']}.md"
    assert day_path.exists()
    day_markdown = day_path.read_text(encoding="utf-8")
    assert "Fixed the XML mapping workflow." in day_markdown
    updated_memory = memory_path.read_text(encoding="utf-8")
    assert "## Important Decisions" in updated_memory
    assert "Fixed the XML mapping workflow." in updated_memory
    # Wave 4: the _refresh_memory_summary stub has been deleted entirely.
    # core/turn/reflect.py owns content-driven memory_summary writeback now,
    # gated on decision.notable_for_reflection. capture_post_turn no longer
    # touches the summary path.
    assert summary_updates == {}, (
        "memory_summary auto-refresh is gone in Wave 4; reflect.py owns it now"
    )


@pytest.mark.asyncio
async def test_capture_post_turn_dedupes_cross_surface_by_continuity_thread(tmp_path, monkeypatch):
    owner_dir = tmp_path / "owners" / "7"
    memory_dir = owner_dir / "memory"
    memory_dir.mkdir(parents=True)

    memory_path = owner_dir / "MEMORY.md"
    summary_path = owner_dir / "MEMORY_SUMMARY.md"
    _write_doc(
        memory_path,
        doc_type="memory",
        body=(
            "# Memory\n\n"
            "## Durable Context\n\n"
            "- none\n\n"
            "## Active Threads\n\n"
            "- none\n\n"
            "## Important Decisions\n\n"
            "- none\n\n"
            "## Useful References\n\n"
            "- none\n"
        ),
    )
    _write_doc(
        summary_path,
        doc_type="memory_summary",
        body="# Memory Summary\n",
        extra={"memory_summary_text": "initial"},
    )

    async def fake_ensure_bundle(**_kwargs):
        return None

    async def fake_update_raw_document(**kwargs):
        memory_path.write_text(kwargs["markdown"], encoding="utf-8")
        return None

    async def fake_silent(**_kwargs):
        return None

    async def fake_rebuild(*, owner_user_id: int):
        return {"status": "ok", "owner_user_id": owner_user_id}

    monkeypatch.setattr("core.memory_service.identity_service.ensure_user_identity_bundle", fake_ensure_bundle)
    monkeypatch.setattr("core.memory_service.identity_service.owner_dir", lambda owner_user_id: str(owner_dir))
    monkeypatch.setattr(
        "core.memory_service.identity_service.owner_doc_path",
        lambda owner_user_id, doc_key: str(memory_path if doc_key == "memory" else summary_path),
    )
    monkeypatch.setattr("core.memory_service.identity_service.update_raw_document", fake_update_raw_document)
    monkeypatch.setattr("core.memory_service.identity_service.silently_maintain_document", fake_silent)
    monkeypatch.setattr(memory_workspace_service, "rebuild_index", fake_rebuild)

    first = await memory_workspace_service.capture_post_turn(
        owner_user_id=7,
        platform="discord",
        user_message="My goal is to finish the repo fix tonight.",
        assistant_reply="Okay, I'll keep the repo fix thread active.",
        thread_id="thread-repo",
        thread_kind="owner_main",
        thread_label="Repo Fix",
        lane_id="lane-discord",
        lane_kind="chat",
        lane_label="Discord DM",
    )
    second = await memory_workspace_service.capture_post_turn(
        owner_user_id=7,
        platform="local",
        user_message="My goal is to finish the repo fix tonight.",
        assistant_reply="Okay, I'll keep the repo fix thread active.",
        thread_id="thread-repo",
        thread_kind="owner_main",
        thread_label="Repo Fix",
        lane_id="lane-local",
        lane_kind="chat",
        lane_label="Local Chat",
    )

    assert first == {"entry_id": first["entry_id"], "date": first["date"], "deduped": False}
    assert second == {"entry_id": first["entry_id"], "date": first["date"], "deduped": True}

    day = await memory_workspace_service.get_day(owner_user_id=7, day=first["date"])
    assert len(day["entries"]) == 1
    assert day["entries"][0]["summary"] == "My goal is to finish the repo fix tonight."
    assert "goal" in day["entries"][0]["tags"]
    assert "thread_kind:owner_main" in day["entries"][0]["tags"]
    assert set(day["entries"][0]["related_refs"]) == {
        "surface:discord",
        "thread:thread-repo",
        "lane:lane-discord",
    }


@pytest.mark.asyncio
async def test_search_combines_lexical_and_semantic_results(monkeypatch):
    async def fake_rebuild(*, owner_user_id: int):
        return {"status": "ok", "owner_user_id": owner_user_id}

    async def fake_search_fts(*, owner_user_id: int, query: str, limit: int):
        return [
            {
                "item_id": "item_1",
                "source_kind": "workflow_memory",
                "source_id": "wfm_1",
                "title": "XML workflow memory",
                "content": "We fixed the XML mapping for mining.",
                "snippet": "We fixed the XML mapping for mining.",
                "salience": "high",
                "tags": ["xml"],
                "related_refs": ["workflow:opf_1"],
                "content_hash": "hash_1",
                "metadata": {},
                "created_at": 1.0,
                "updated_at": 1000.0,
                "lexical_score": -0.2,
            }
        ]

    async def fake_list_embeddings(*, owner_user_id: int, limit: int):
        return [
            {"item_id": "item_1", "vector": [1.0, 0.0], "content_hash": "hash_1"},
            {"item_id": "item_2", "vector": [0.9, 0.1], "content_hash": "hash_2"},
        ]

    async def fake_get_item(*, item_id: str):
        if item_id != "item_2":
            return None
        return {
            "item_id": "item_2",
            "source_kind": "followthrough_item",
            "source_id": "wff_1",
            "title": "Need to revisit mappings",
            "content": "The mapping still needs another pass.",
            "snippet": "The mapping still needs another pass.",
            "salience": "medium",
            "tags": ["mapping"],
            "related_refs": ["workflow:opf_1"],
            "updated_at": 990.0,
        }

    async def fake_embed(text: str):
        return [1.0, 0.0]

    async def fake_ensure_embeddings(*, owner_user_id: int):
        return None

    async def fake_capability():
        return {"mode": "hybrid"}

    monkeypatch.setattr(memory_workspace_service, "rebuild_index", fake_rebuild)
    monkeypatch.setattr("core.memory_service.brain.search_memory_index_fts", fake_search_fts)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_embeddings", fake_list_embeddings)
    monkeypatch.setattr("core.memory_service.brain.get_memory_index_item", fake_get_item)

    async def fake_list_items(*, owner_user_id: int, limit: int = 200, source_kinds=None):
        # 2026-09-30: the search loads the owner's items in one query now.
        item = await fake_get_item(item_id="item_2")
        return [item] if item else []

    monkeypatch.setattr("core.memory_service.brain.list_memory_index_items", fake_list_items)
    monkeypatch.setattr(memory_workspace_service, "_embed_text", fake_embed)
    monkeypatch.setattr(memory_workspace_service, "backfill_owner_embeddings", fake_ensure_embeddings)
    monkeypatch.setattr(memory_workspace_service, "_search_capability_status", fake_capability)

    result = await memory_workspace_service.search(owner_user_id=7, query="xml mapping", limit=4)

    assert result["mode"] == "hybrid"
    assert result["count"] >= 2
    assert result["results"][0]["item_id"] in {"item_1", "item_2"}
    assert {item["item_id"] for item in result["results"]} >= {"item_1", "item_2"}


@pytest.mark.asyncio
async def test_search_biases_results_toward_current_workflow_refs(monkeypatch):
    async def fake_rebuild(*, owner_user_id: int):
        return {"status": "ok", "owner_user_id": owner_user_id}

    async def fake_search_fts(*, owner_user_id: int, query: str, limit: int):
        return [
            {
                "item_id": "item_general",
                "source_kind": "memory_summary_section",
                "source_id": "Memory Summary",
                "title": "General summary",
                "content": "Generic continuity summary for older work.",
                "snippet": "Generic continuity summary for older work.",
                "salience": "low",
                "tags": ["memory-summary"],
                "related_refs": [],
                "content_hash": "hash_general",
                "metadata": {},
                "created_at": 1.0,
                "updated_at": 1000.0,
                "lexical_score": -0.6,
            },
            {
                "item_id": "item_workflow",
                "source_kind": "workflow_memory",
                "source_id": "wfm_2",
                "title": "Repo fix workflow",
                "content": "Continue the repo fix remote selection and mapping cleanup.",
                "snippet": "Continue the repo fix remote selection and mapping cleanup.",
                "salience": "medium",
                "tags": ["workflow"],
                "related_refs": ["workflow:wf_current"],
                "content_hash": "hash_workflow",
                "metadata": {},
                "created_at": 2.0,
                "updated_at": 990.0,
                "lexical_score": -1.0,
            },
        ]

    async def fake_list_embeddings(*, owner_user_id: int, limit: int):
        return []

    async def fake_embed(text: str):
        return []

    async def fake_capability():
        return {"mode": "lexical_only"}

    monkeypatch.setattr(memory_workspace_service, "rebuild_index", fake_rebuild)
    monkeypatch.setattr("core.memory_service.brain.search_memory_index_fts", fake_search_fts)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_embeddings", fake_list_embeddings)
    monkeypatch.setattr(memory_workspace_service, "_embed_text", fake_embed)
    monkeypatch.setattr(memory_workspace_service, "_search_capability_status", fake_capability)

    result = await memory_workspace_service.search(
        owner_user_id=7,
        query="continue the repo fix",
        limit=4,
        preferred_related_refs=["workflow:wf_current"],
        carry_forward_hint="Continue the repo fix remote selection",
    )

    assert result["results"][0]["item_id"] == "item_workflow"
    assert result["results"][0]["score_explanation"] == "lexical+continuity_ref+carry_forward"


@pytest.mark.asyncio
async def test_search_biases_results_toward_current_thread_lane_workflow_refs(monkeypatch):
    async def fake_rebuild(*, owner_user_id: int):
        return {"status": "ok", "owner_user_id": owner_user_id}

    async def fake_search_fts(*, owner_user_id: int, query: str, limit: int):
        return [
            {
                "item_id": "item_general",
                "source_kind": "workflow_memory",
                "source_id": "wfm_other",
                "title": "Older repo summary",
                "content": "Generic repo summary from another lane.",
                "snippet": "Generic repo summary from another lane.",
                "salience": "low",
                "tags": ["workflow"],
                "related_refs": ["workflow:wf_other"],
                "content_hash": "hash_general",
                "metadata": {},
                "created_at": 1.0,
                "updated_at": 1000.0,
                "lexical_score": -0.4,
            },
            {
                "item_id": "item_workflow",
                "source_kind": "workflow_memory",
                "source_id": "wfm_browser_voice",
                "title": "Browser voice workflow",
                "content": "Browser voice is stable again after the interrupt recovery fix.",
                "snippet": "Browser voice is stable again after the interrupt recovery fix.",
                "salience": "low",
                "tags": ["workflow"],
                "related_refs": [
                    "workflow:wf_browser_voice",
                    "thread:thread-browser-voice",
                    "lane:operator:browser-voice",
                ],
                "content_hash": "hash_workflow",
                "metadata": {},
                "created_at": 2.0,
                "updated_at": 990.0,
                "lexical_score": -0.8,
            },
        ]

    async def fake_list_embeddings(*, owner_user_id: int, limit: int):
        return []

    async def fake_embed(text: str):
        return []

    async def fake_capability():
        return {"mode": "lexical_only"}

    monkeypatch.setattr(memory_workspace_service, "rebuild_index", fake_rebuild)
    monkeypatch.setattr("core.memory_service.brain.search_memory_index_fts", fake_search_fts)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_embeddings", fake_list_embeddings)
    monkeypatch.setattr(memory_workspace_service, "_embed_text", fake_embed)
    monkeypatch.setattr(memory_workspace_service, "_search_capability_status", fake_capability)

    result = await memory_workspace_service.search(
        owner_user_id=7,
        query="what changed lately with browser voice?",
        limit=4,
        preferred_related_refs=[
            "thread:thread-browser-voice",
            "lane:operator:browser-voice",
        ],
    )

    assert result["results"][0]["item_id"] == "item_workflow"
    assert result["results"][0]["score_explanation"] == "lexical+continuity_ref"


@pytest.mark.asyncio
async def test_search_prefers_current_lane_followthrough_for_open_thread_queries(monkeypatch):
    async def fake_rebuild(*, owner_user_id: int):
        return {"status": "ok", "owner_user_id": owner_user_id}

    async def fake_search_fts(*, owner_user_id: int, query: str, limit: int):
        return [
            {
                "item_id": "workflow_match",
                "source_kind": "workflow_memory",
                "source_id": "wfm_wait",
                "title": "Repo fix workflow",
                "content": "Finished the remote selection and mapping cleanup after the latest repo fix pass.",
                "snippet": "Finished the remote selection and mapping cleanup after the latest repo fix pass.",
                "salience": "medium",
                "tags": ["workflow"],
                "related_refs": ["workflow:wf_wait"],
                "content_hash": "hash_workflow",
                "metadata": {},
                "created_at": 1.0,
                "updated_at": 1000.0,
                "lexical_score": -0.1,
            },
            {
                "item_id": "follow_match",
                "source_kind": "followthrough_item",
                "source_id": "wff_wait",
                "title": "Repo fix followthrough",
                "content": "Still need the remote choice before the repo fix can continue.",
                "snippet": "Still need the remote choice before the repo fix can continue.",
                "salience": "medium",
                "tags": ["followthrough", "open"],
                "related_refs": ["workflow:wf_wait"],
                "content_hash": "hash_follow",
                "metadata": {},
                "created_at": 2.0,
                "updated_at": 990.0,
                "lexical_score": -1.4,
            },
        ]

    async def fake_list_embeddings(*, owner_user_id: int, limit: int):
        return []

    async def fake_embed(text: str):
        return []

    async def fake_capability():
        return {"mode": "lexical_only"}

    monkeypatch.setattr(memory_workspace_service, "rebuild_index", fake_rebuild)
    monkeypatch.setattr("core.memory_service.brain.search_memory_index_fts", fake_search_fts)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_embeddings", fake_list_embeddings)
    monkeypatch.setattr(memory_workspace_service, "_embed_text", fake_embed)
    monkeypatch.setattr(memory_workspace_service, "_search_capability_status", fake_capability)

    result = await memory_workspace_service.search(
        owner_user_id=7,
        query="Can we pick that back up from last time?",
        limit=4,
        preferred_related_refs=["workflow:wf_wait"],
        carry_forward_hint="Need the remote choice before the repo fix can continue.",
        prefer_active_threads=True,
    )

    assert result["results"][0]["item_id"] == "follow_match"
    assert result["results"][1]["item_id"] == "workflow_match"
    assert result["results"][0]["score_explanation"] == "lexical+continuity_ref+carry_forward+active_thread"


@pytest.mark.asyncio
async def test_search_prefers_current_lane_followthrough_for_open_thread_hybrid_queries(monkeypatch):
    async def fake_rebuild(*, owner_user_id: int):
        return {"status": "ok", "owner_user_id": owner_user_id}

    async def fake_search_fts(*, owner_user_id: int, query: str, limit: int):
        return [
            {
                "item_id": "workflow_match",
                "source_kind": "workflow_memory",
                "source_id": "wfm_wait",
                "title": "Repo fix workflow",
                "content": "Finished the remote selection and mapping cleanup after the latest repo fix pass.",
                "snippet": "Finished the remote selection and mapping cleanup after the latest repo fix pass.",
                "salience": "medium",
                "tags": ["workflow"],
                "related_refs": ["workflow:wf_wait"],
                "content_hash": "hash_workflow",
                "metadata": {},
                "created_at": 1.0,
                "updated_at": 1000.0,
                "lexical_score": -0.1,
            },
            {
                "item_id": "follow_match",
                "source_kind": "followthrough_item",
                "source_id": "wff_wait",
                "title": "Repo fix followthrough",
                "content": "Still need the remote choice before the repo fix can continue.",
                "snippet": "Still need the remote choice before the repo fix can continue.",
                "salience": "medium",
                "tags": ["followthrough", "open"],
                "related_refs": ["workflow:wf_wait"],
                "content_hash": "hash_follow",
                "metadata": {},
                "created_at": 2.0,
                "updated_at": 990.0,
                "lexical_score": -1.4,
            },
        ]

    async def fake_list_embeddings(*, owner_user_id: int, limit: int):
        return [
            {"item_id": "workflow_match", "vector": [1.0, 0.0], "content_hash": "hash_workflow"},
            {"item_id": "follow_match", "vector": [0.8, 0.2], "content_hash": "hash_follow"},
        ]

    async def fake_embed(text: str):
        return [1.0, 0.0]

    async def fake_ensure_embeddings(*, owner_user_id: int):
        return None

    async def fake_capability():
        return {"mode": "hybrid"}

    monkeypatch.setattr(memory_workspace_service, "rebuild_index", fake_rebuild)
    monkeypatch.setattr("core.memory_service.brain.search_memory_index_fts", fake_search_fts)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_embeddings", fake_list_embeddings)
    monkeypatch.setattr(memory_workspace_service, "_embed_text", fake_embed)
    monkeypatch.setattr(memory_workspace_service, "backfill_owner_embeddings", fake_ensure_embeddings)
    monkeypatch.setattr(memory_workspace_service, "_search_capability_status", fake_capability)

    result = await memory_workspace_service.search(
        owner_user_id=7,
        query="Can we pick that back up from last time?",
        limit=4,
        preferred_related_refs=["workflow:wf_wait"],
        carry_forward_hint="Need the remote choice before the repo fix can continue.",
        prefer_active_threads=True,
    )

    assert result["mode"] == "hybrid"
    assert result["results"][0]["item_id"] == "follow_match"
    assert result["results"][1]["item_id"] == "workflow_match"
    assert result["results"][0]["score_explanation"] == "hybrid+continuity_ref+carry_forward+active_thread"


@pytest.mark.asyncio
async def test_build_message_context_formats_results(monkeypatch):
    async def fake_search(**_kwargs):
        return {
            "results": [
                {
                    "source_kind": "workflow_memory",
                    "snippet": "We decided to use origin for the repo fix.",
                },
                {
                    "source_kind": "followthrough_item",
                    "snippet": "The XML mappings still need review.",
                },
            ]
        }

    monkeypatch.setattr(memory_workspace_service, "search", fake_search)
    context = await memory_workspace_service.build_message_context(owner_user_id=7, query="what did we decide?")
    assert context is not None
    assert "Relevant Memory:" in context
    assert "[workflow_memory]" in context
    assert "use origin" in context.lower()


def test_should_retrieve_for_message_handles_resume_and_recent_status_queries():
    assert memory_workspace_service.should_retrieve_for_message("What changed lately?")
    assert memory_workspace_service.should_retrieve_for_message("Can we pick that back up from last time?")
    assert not memory_workspace_service.should_retrieve_for_message("Tell me a joke about repo fixes.")


@pytest.mark.asyncio
async def test_build_message_context_grounds_vague_continuity_queries(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_search(
        *,
        owner_user_id: int,
        query: str,
        limit: int,
        preferred_related_refs=None,
        carry_forward_hint=None,
        prefer_active_threads: bool = False,
    ):
        captured["query"] = query
        captured["preferred_related_refs"] = list(preferred_related_refs or [])
        captured["carry_forward_hint"] = carry_forward_hint
        return {
            "results": [
                {
                    "item_id": "item_1",
                    "source_kind": "workflow_memory",
                    "source_id": "wfm_1",
                    "snippet": "We were updating the repo fix workflow.",
                    "content": "We were updating the repo fix workflow.",
                }
            ]
        }

    monkeypatch.setattr(memory_workspace_service, "search", fake_search)

    context = await memory_workspace_service.build_message_context(
        owner_user_id=7,
        query="Can we pick that back up from last time?",
        history=[
            {
                "role": "assistant",
                "content": "I'm still waiting on which remote you want me to use for the repo fix.",
                "thread_id": "thread-main",
                "metadata": {"operator_workflow_id": "wf_wait"},
            }
        ],
        reminder_context_note="**Reminder Action (internal):** follow up on the repo fix after lunch.",
        recent_agentic_context_note=(
            "**Recent Verified Agentic Context:**\n"
            "- Latest verified command: `git status`.\n"
            "- Latest verified file target: `/repo/core/message_processor.py`."
        ),
    )

    assert context is not None
    assert "Relevant Memory:" in context
    assert "git status" in str(captured["query"])
    assert "/repo/core/message_processor.py" in str(captured["query"])
    assert "open followthrough active threads" in str(captured["query"])
    assert "current carry forward" in str(captured["query"])
    assert captured["preferred_related_refs"] == ["thread:thread-main", "workflow:wf_wait"]
    assert "which remote you want me to use" in str(captured["carry_forward_hint"]).lower()


@pytest.mark.asyncio
async def test_build_message_context_includes_recent_continuity_note_in_query(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_search(
        *,
        owner_user_id: int,
        query: str,
        limit: int,
        preferred_related_refs=None,
        carry_forward_hint=None,
        prefer_active_threads: bool = False,
    ):
        captured["query"] = query
        captured["preferred_related_refs"] = list(preferred_related_refs or [])
        captured["carry_forward_hint"] = carry_forward_hint
        return {
            "results": [
                {
                    "item_id": "item_1",
                    "source_kind": "workflow_memory",
                    "source_id": "wfm_1",
                    "snippet": "Repo fix workflow context.",
                    "content": "Repo fix workflow context.",
                }
            ]
        }

    monkeypatch.setattr(memory_workspace_service, "search", fake_search)

    context = await memory_workspace_service.build_message_context(
        owner_user_id=7,
        query="Can we pick that back up from last time?",
        history=[
            {
                "role": "assistant",
                "content": "Okay.",
                "thread_id": "thread-main",
                "metadata": {"operator_workflow_id": "wf_wait"},
            }
        ],
        recent_continuity_context_note=(
            "**Recent Continuity Context:**\n"
            "- Latest continuity state: Repo fix is waiting on the remote choice.\n"
            "- Likely next move: answer question."
        ),
    )

    assert context is not None
    assert "Relevant Memory:" in context
    assert "repo fix is waiting on the remote choice" in str(captured["query"]).lower()
    assert "answer question" in str(captured["query"]).lower()
    assert captured["preferred_related_refs"] == ["thread:thread-main", "workflow:wf_wait"]
    assert not str(captured["carry_forward_hint"] or "").strip()


@pytest.mark.asyncio
async def test_build_message_context_uses_recent_workflow_metadata_for_carry_forward(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_search(
        *,
        owner_user_id: int,
        query: str,
        limit: int,
        preferred_related_refs=None,
        carry_forward_hint=None,
        prefer_active_threads: bool = False,
    ):
        captured["carry_forward_hint"] = carry_forward_hint
        return {
            "results": [
                {
                    "item_id": "item_1",
                    "source_kind": "workflow_memory",
                    "source_id": "wfm_1",
                    "snippet": "Repo fix workflow context.",
                    "content": "Repo fix workflow context.",
                }
            ]
        }

    monkeypatch.setattr(memory_workspace_service, "search", fake_search)

    await memory_workspace_service.build_message_context(
        owner_user_id=7,
        query="Can we pick that back up from last time?",
        history=[
            {
                "role": "assistant",
                "content": "Okay.",
                "metadata": {
                    "operator_workflow_id": "wf_wait",
                    "operator_resume_brief": "Need the git remote before I continue.",
                },
            }
        ],
    )

    assert "need the git remote before i continue" in str(captured["carry_forward_hint"]).lower()


@pytest.mark.asyncio
async def test_build_message_context_uses_recent_items_for_recent_status_queries(monkeypatch):
    async def fake_search(
        *,
        owner_user_id: int,
        query: str,
        limit: int,
        preferred_related_refs=None,
        carry_forward_hint=None,
        prefer_active_threads: bool = False,
    ):
        return {"results": []}

    async def fake_list_memory_index_items(*, owner_user_id: int, limit: int):
        return [
            {
                "item_id": "follow_1",
                "source_kind": "followthrough_item",
                "source_id": "wff_1",
                "title": "Repo fix followthrough",
                "content": "Need another pass on the repo fix and reminder delivery.",
                "snippet": "Need another pass on the repo fix and reminder delivery.",
                "salience": "medium",
                "tags": ["followthrough", "open"],
                "related_refs": ["workflow:wf_1"],
                "updated_at": 200.0,
            },
            {
                "item_id": "summary_1",
                "source_kind": "memory_summary_section",
                "source_id": "Memory Summary",
                "title": "Memory Summary",
                "content": "Older summary that should not crowd out recent open work.",
                "snippet": "Older summary that should not crowd out recent open work.",
                "salience": "medium",
                "tags": ["memory-summary"],
                "related_refs": [],
                "updated_at": 100.0,
            },
        ]

    monkeypatch.setattr(memory_workspace_service, "search", fake_search)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_items", fake_list_memory_index_items)

    context = await memory_workspace_service.build_message_context(
        owner_user_id=7,
        query="What changed lately and what's still open?",
    )

    assert context is not None
    assert "[followthrough_item]" in context
    assert "repo fix and reminder delivery" in context.lower()


@pytest.mark.asyncio
async def test_build_message_context_biases_recent_fallback_toward_current_workflow(monkeypatch):
    async def fake_search(
        *,
        owner_user_id: int,
        query: str,
        limit: int,
        preferred_related_refs=None,
        carry_forward_hint=None,
        prefer_active_threads: bool = False,
    ):
        return {"results": []}

    async def fake_list_memory_index_items(*, owner_user_id: int, limit: int):
        return [
            {
                "item_id": "newer_general",
                "source_kind": "followthrough_item",
                "source_id": "wff_other",
                "title": "Other open thread",
                "content": "Need to review the calendar plan later.",
                "snippet": "Need to review the calendar plan later.",
                "salience": "medium",
                "tags": ["followthrough", "open"],
                "related_refs": ["workflow:wf_other"],
                "updated_at": 400.0,
            },
            {
                "item_id": "workflow_match",
                "source_kind": "workflow_memory",
                "source_id": "wfm_wait",
                "title": "Repo fix workflow",
                "content": "Still waiting on which remote to use for the repo fix.",
                "snippet": "Still waiting on which remote to use for the repo fix.",
                "salience": "medium",
                "tags": ["workflow"],
                "related_refs": ["workflow:wf_wait"],
                "updated_at": 350.0,
            },
        ]

    monkeypatch.setattr(memory_workspace_service, "search", fake_search)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_items", fake_list_memory_index_items)

    context = await memory_workspace_service.build_message_context(
        owner_user_id=7,
        query="Can we pick that back up from last time?",
        history=[
            {
                "role": "assistant",
                "content": "I'm still waiting on which remote you want me to use.",
                "metadata": {"operator_workflow_id": "wf_wait"},
            }
        ],
    )

    assert context is not None
    lines = context.splitlines()
    assert lines[1] == "- [workflow_memory] Still waiting on which remote to use for the repo fix."


@pytest.mark.asyncio
async def test_build_message_context_prefers_current_lane_followthrough_over_workflow_memory(monkeypatch):
    async def fake_search(
        *,
        owner_user_id: int,
        query: str,
        limit: int,
        preferred_related_refs=None,
        carry_forward_hint=None,
        prefer_active_threads: bool = False,
    ):
        return {"results": []}

    async def fake_list_memory_index_items(*, owner_user_id: int, limit: int):
        return [
            {
                "item_id": "workflow_match",
                "source_kind": "workflow_memory",
                "source_id": "wfm_wait",
                "title": "Repo fix workflow",
                "content": "Finished the mapping cleanup after the latest repo fix pass.",
                "snippet": "Finished the mapping cleanup after the latest repo fix pass.",
                "salience": "medium",
                "tags": ["workflow"],
                "related_refs": ["workflow:wf_wait"],
                "updated_at": 420.0,
            },
            {
                "item_id": "follow_match",
                "source_kind": "followthrough_item",
                "source_id": "wff_wait",
                "title": "Repo fix followthrough",
                "content": "Still need the remote choice before the repo fix can continue.",
                "snippet": "Still need the remote choice before the repo fix can continue.",
                "salience": "medium",
                "tags": ["followthrough", "open"],
                "related_refs": ["workflow:wf_wait"],
                "updated_at": 360.0,
            },
        ]

    monkeypatch.setattr(memory_workspace_service, "search", fake_search)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_items", fake_list_memory_index_items)

    context = await memory_workspace_service.build_message_context(
        owner_user_id=7,
        query="Can we pick that back up from last time?",
        history=[
            {
                "role": "assistant",
                "content": "I'm still waiting on which remote you want me to use.",
                "metadata": {"operator_workflow_id": "wf_wait"},
            }
        ],
    )

    assert context is not None
    lines = context.splitlines()
    assert lines[1] == "- [followthrough_item] Still need the remote choice before the repo fix can continue."
    assert lines[2] == "- [workflow_memory] Finished the mapping cleanup after the latest repo fix pass."


def test_history_item_related_refs_supports_nested_thread_lane_metadata():
    metadata = {
        "thread": {
            "value": "thread-deploy",
            "name": "Deploy Validation Thread",
        },
        "lane": {
            "id": "operator:deploy",
            "name": "Deploy Lane",
        },
        "operator_workflow_id": "wf_deploy",
        "operator_followthrough_id": "wff_deploy",
    }

    refs = memory_workspace_service._history_item_related_refs(
        item={"role": "assistant", "metadata": metadata},
        metadata=metadata,
    )

    assert set(refs) == {
        "thread:thread-deploy",
        "workflow:wf_deploy",
        "followthrough:wff_deploy",
        "lane:operator:deploy",
    }


def test_history_item_related_refs_supports_nested_continuity_refs():
    metadata = {
        "continuity_refs": {
            "thread_ref": "thread:thread-fallback",
            "lane_ref": "lane:operator:fallback",
        },
    }
    item = {"role": "assistant", "metadata": metadata}

    refs = memory_workspace_service._history_item_related_refs(
        item=item,
        metadata=metadata,
    )

    assert set(refs) == {
        "thread:thread-fallback",
        "lane:operator:fallback",
    }




# --- Batch 8 Task 11 Extra-2: a finished background task is forgotten ---------
#
# _backfill_tasks / _index_rebuild_tasks held one task per owner and kept it
# after it finished, until the next schedule replaced it — a finished task
# (and any traceback stored on it) stayed referenced by the module singleton.
# A done-callback now drops it, only while it is still that owner's current
# task, and reads its exception unless it was cancelled. A fresh service per
# test: the singleton is never touched.


def _fresh_service():
    from core.memory_service import MemoryWorkspaceService

    return MemoryWorkspaceService()


@pytest.mark.asyncio
async def test_a_finished_backfill_leaves_the_task_dict_empty(monkeypatch):
    svc = _fresh_service()
    ran: list[int] = []

    async def fake_backfill(*, owner_user_id):
        ran.append(owner_user_id)

    monkeypatch.setattr(svc, "backfill_owner_embeddings", fake_backfill)

    svc._schedule_backfill(7)
    task = svc._backfill_tasks[7]
    await task
    await asyncio.sleep(0)  # done-callbacks run on the next loop pass

    assert ran == [7]
    assert svc._backfill_tasks == {}


@pytest.mark.asyncio
async def test_a_cancelled_backfill_is_forgotten_too(monkeypatch):
    svc = _fresh_service()
    started = asyncio.Event()

    async def hang(*, owner_user_id):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(svc, "backfill_owner_embeddings", hang)

    svc._schedule_backfill(7)
    task = svc._backfill_tasks[7]
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert svc._backfill_tasks == {}


@pytest.mark.asyncio
async def test_an_old_backfill_finishing_never_drops_the_newer_one(monkeypatch):
    svc = _fresh_service()
    release = asyncio.Event()

    async def wait_then_finish(*, owner_user_id):
        await release.wait()

    monkeypatch.setattr(svc, "backfill_owner_embeddings", wait_then_finish)

    svc._schedule_backfill(7)
    old = svc._backfill_tasks[7]
    newer = asyncio.get_running_loop().create_task(asyncio.Event().wait())
    svc._backfill_tasks[7] = newer          # what a later schedule would leave
    release.set()
    await old
    await asyncio.sleep(0)

    assert svc._backfill_tasks.get(7) is newer
    newer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await newer


@pytest.mark.asyncio
async def test_a_finished_index_rebuild_leaves_the_task_dict_empty(monkeypatch):
    svc = _fresh_service()
    ran: list[int] = []

    async def fake_rebuild(*, owner_user_id):
        ran.append(owner_user_id)

    monkeypatch.setattr(svc, "rebuild_index", fake_rebuild)

    svc._schedule_index_rebuild(7)
    task = svc._index_rebuild_tasks[7]
    await task
    await asyncio.sleep(0)

    assert ran == [7]
    assert svc._index_rebuild_tasks == {}
    assert 7 in svc._last_index_rebuild     # the debounce still records the run


@pytest.mark.asyncio
async def test_search_ranks_the_strongest_keyword_match_first(monkeypatch):
    """2026-10-08 (the release basics). SQLite's bm25() is MORE NEGATIVE for a
    stronger match, and 1/(1+|bm25|) turned a strong -8 into 0.11 and a weak -0.5
    into 0.67: the right memories came back with the best keyword match LAST.
    Each score is now divided by the strongest match in the same batch, the way
    ai/inner_memory_index.py already did, so the best match scores 1.0."""

    def row(item_id: str, bm25: float) -> dict:
        return {
            "item_id": item_id, "source_kind": "workflow_memory", "source_id": item_id,
            "title": item_id, "content": f"{item_id} content", "snippet": f"{item_id} content",
            "salience": "low", "tags": [], "related_refs": [], "content_hash": f"hash_{item_id}",
            "metadata": {}, "created_at": 1.0, "updated_at": 1000.0, "lexical_score": bm25,
        }

    async def fake_rebuild(*, owner_user_id: int):
        return {"status": "ok", "owner_user_id": owner_user_id}

    async def fake_search_fts(*, owner_user_id: int, query: str, limit: int):
        return [row("item_weak", -0.5), row("item_strong", -8.0)]

    async def fake_list_embeddings(*, owner_user_id: int, limit: int):
        return []

    async def fake_embed(text: str):
        return []

    async def fake_capability():
        return {"mode": "lexical_only"}

    monkeypatch.setattr(memory_workspace_service, "rebuild_index", fake_rebuild)
    monkeypatch.setattr("core.memory_service.brain.search_memory_index_fts", fake_search_fts)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_embeddings", fake_list_embeddings)
    monkeypatch.setattr(memory_workspace_service, "_embed_text", fake_embed)
    monkeypatch.setattr(memory_workspace_service, "_search_capability_status", fake_capability)

    result = await memory_workspace_service.search(owner_user_id=7, query="tonight's line", limit=4)

    assert [r["item_id"] for r in result["results"]] == ["item_strong", "item_weak"]
    # Same salience, same age, no refs: every bonus is equal (zero), so in
    # lexical-only mode the reported score IS the keyword score.
    scores = {r["item_id"]: r["score"] for r in result["results"]}
    assert scores["item_strong"] == 1.0
    assert scores["item_weak"] == pytest.approx(0.5 / 8.0)


def test_fts_scores_are_relative_to_the_strongest_in_the_batch():
    assert memory_workspace_service._normalize_fts_score(-8.0, 8.0) == 1.0
    assert memory_workspace_service._normalize_fts_score(-2.0, 8.0) == 0.25
    # Nothing to compare against (every bm25 is 0): every candidate counts fully,
    # as in ai/inner_memory_index.py.
    assert memory_workspace_service._normalize_fts_score(0.0, 0.0) == 1.0

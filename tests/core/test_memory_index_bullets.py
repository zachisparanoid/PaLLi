"""Batch 5, task 2 (memory that picks, 2026-09-28): every memory bullet is a
search item — one ``mcb_<sha12>`` item per ``- `` line of MEMORY.md and
MEMORY_ARCHIVE.md, carrying its section, its document and its first
``[YYYY-MM-DD]`` stamp as metadata. The id is the hash of the bullet's TEXT
(no leading ``- ``, no stamps, no trailing ``[archived …]`` marker, whitespace
collapsed — ruling R4), so a line keeps its reference stamps when the sweep
stamps it or moves it to the archive."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from core.memory_service import _bullet_index_items, item_id_for


def test_each_bullet_becomes_one_item_with_its_section_and_stamp():
    doc = "## Durable Context\n- [2026-08-03] likes tea\n- no stamp here\n\n## Useful References\n- [2026-09-01] router at .1\n"
    items = _bullet_index_items(doc, doc_key="memory", owner_user_id=1)
    assert len(items) == 3
    assert items[0]["metadata"] == {"section": "Durable Context", "doc": "memory", "stamp": "2026-08-03"}
    assert items[1]["metadata"]["stamp"] is None
    assert all(i["source_kind"] == "memory_bullet" and i["item_id"].startswith("mcb_") for i in items)


def test_same_bullet_text_same_id():
    a = _bullet_index_items("## Durable Context\n- likes tea\n", doc_key="memory", owner_user_id=1)[0]["item_id"]
    b = _bullet_index_items("## Durable Context\n- likes tea\n", doc_key="memory_archive", owner_user_id=1)[0]["item_id"]
    assert a == b  # the id is the text's hash, so a line keeps its stamps when it moves to the archive


# --- the id (ruling R4) ------------------------------------------------------

def test_item_id_ignores_the_dash_the_stamps_and_the_archive_marker():
    base = item_id_for("likes tea")
    assert base.startswith("mcb_") and len(base) == 4 + 12
    assert item_id_for("- likes tea") == base                      # the whole line or the bare body
    assert item_id_for("- [2026-08-03] likes tea") == base         # the sweep's stamp
    assert item_id_for("- [2026-08-30] [2026-08-30] likes tea") == base  # 58 live lines are double-stamped
    assert item_id_for("- [2026-08-03] likes tea [archived 2026-09-28]") == base  # the sweep's move marker
    assert item_id_for("-   likes   tea  ") == base                # whitespace is collapsed
    assert item_id_for("likes coffee") != base


def test_the_items_id_is_item_id_for_of_its_line():
    line = "- [2026-08-30] [2026-08-30] the workshop is in the garage [archived 2026-09-28]"
    item = _bullet_index_items(f"## Durable Context\n{line}\n", doc_key="memory_archive", owner_user_id=1)[0]
    assert item["item_id"] == item_id_for(line)
    assert item["metadata"]["stamp"] == "2026-08-30"   # the FIRST stamp
    assert item["content"] == "the workshop is in the garage [archived 2026-09-28]"  # the stamps live in metadata
    assert item["title"] == "Durable Context"
    assert item["source_id"] == "memory_archive"
    assert item["tags"] == ["memory-bullet"]


# --- what is and is not a bullet ---------------------------------------------

def test_only_dash_lines_inside_a_section_count_and_no_line_is_indexed_twice():
    doc = (
        "# Memory\n\n- a line before any section is not a bullet\n\n"
        "## Durable Context\n\nprose in a section is not a bullet\n- real one\n  - indented is not a bullet\n"
        "- real one\n- [2026-01-01]\n* star bullets are not bullets\n\n## Active Threads\n- [2026-09-01] a thread\n"
    )
    items = _bullet_index_items(doc, doc_key="memory", owner_user_id=1)
    assert [i["content"] for i in items] == ["real one", "a thread"]   # the duplicate and the empty body are dropped
    assert items[1]["metadata"] == {"section": "Active Threads", "doc": "memory", "stamp": "2026-09-01"}


def test_the_templates_placeholder_bullets_never_become_items(tmp_path):
    for name in ("MEMORY.md", "MEMORY_ARCHIVE.md"):
        text = Path("identity/templates", name).read_text(encoding="utf-8")
        fixed = [line for line in text.splitlines() if line.startswith("- ") and "{" not in line]
        assert fixed, name  # the template does carry fixed placeholder bullets
        body = text.split("\n---\n", 1)[1]
        items = _bullet_index_items(body, doc_key="memory", owner_user_id=1)
        contents = {i["content"] for i in items}
        for line in fixed:
            assert line[2:].strip() not in contents, (name, line)


def test_a_fresh_installs_rendered_defaults_are_not_facts_about_anyone(tmp_path):
    """MEMORY.md's `- Interests: {interests}` renders as a fixed sentence when
    the profile lists none (document_store._build_template_context); a
    stranger's first MEMORY.md must not feed that line to the facts band."""
    from core.identity.document_store import IdentityDocumentStore

    root = tmp_path / "identity"
    shutil.copytree("identity/templates", root / "templates")
    shutil.copytree("identity/core", root / "core")
    store = IdentityDocumentStore(identity_root=str(root))
    rendered = store.render_user_template(doc_key="memory", user_id=7, platform="owner", profile={})
    body = rendered.split("\n---\n", 1)[1]
    assert "- Interests: No explicit interest list yet." in body   # the default did render
    contents = {i["content"] for i in _bullet_index_items(body, doc_key="memory", owner_user_id=7)}
    assert not any(c.startswith("Interests: No explicit interest list yet") for c in contents)
    assert not any(c.startswith("No ") or c.startswith("Nothing ") for c in contents)


def _day(stamp: str) -> float:
    return time.mktime(time.strptime(stamp, "%Y-%m-%d"))


def test_a_stamped_bullet_carries_its_stamps_time_not_the_files():
    """Every write to MEMORY.md moves the file's mtime; a January line must
    not read as 'updated today' in introspect, the observatory or the search
    recency bonus. The stamp's local midnight is the line's time (as
    reflect's sweep reads it); an unstamped line takes the file's."""
    doc = (
        "## Durable Context\n- [2026-08-03] likes tea\n- no stamp here\n"
        "- [2026-08-30] [2026-08-30] double stamped\n- [2026-13-40] not a real date\n"
    )
    items = {i["content"]: i for i in _bullet_index_items(doc, doc_key="memory", owner_user_id=1, doc_mtime=1_700_000_000.0)}
    assert items["likes tea"]["created_at"] == items["likes tea"]["updated_at"] == _day("2026-08-03")
    assert items["double stamped"]["updated_at"] == _day("2026-08-30")      # the first stamp
    assert items["no stamp here"]["updated_at"] == 1_700_000_000.0           # the file's time
    assert items["not a real date"]["updated_at"] == 1_700_000_000.0        # an impossible stamp falls back
    assert items["not a real date"]["metadata"]["stamp"] == "2026-13-40"    # but is still reported as written


def test_the_item_carries_the_docs_mtime_and_the_index_item_shape():
    items = _bullet_index_items("## Durable Context\n- likes tea\n", doc_key="memory", owner_user_id=3, doc_mtime=1_700_000_000.0)
    item = items[0]
    assert item["created_at"] == item["updated_at"] == 1_700_000_000.0
    assert item["owner_user_id"] == 3
    assert set(item) >= {"item_id", "owner_user_id", "source_kind", "source_id", "title", "content", "snippet",
                         "salience", "tags", "related_refs", "content_hash", "created_at", "updated_at", "metadata"}


# --- the wiring: both documents feed the index; a search result keeps metadata --

@pytest.mark.asyncio
async def test_collect_index_items_emits_bullets_from_the_file_and_the_archive(tmp_path, monkeypatch):
    from core.memory_service import memory_workspace_service as svc

    owner_dir = tmp_path / "owners" / "7"
    owner_dir.mkdir(parents=True)
    paths = {
        "memory": owner_dir / "MEMORY.md",
        "memory_summary": owner_dir / "MEMORY_SUMMARY.md",
        "memory_archive": owner_dir / "MEMORY_ARCHIVE.md",
    }
    paths["memory"].write_text(
        "---\ndoc_type: memory\n---\n# Memory\n\n## Durable Context\n- [2026-08-03] likes tea\n\n"
        "## Important Decisions\n- decided A\n", encoding="utf-8")
    paths["memory_summary"].write_text("---\ndoc_type: memory_summary\nmemory_summary_text: a summary\n---\n", encoding="utf-8")
    paths["memory_archive"].write_text(
        "---\ndoc_type: memory_archive\n---\n# Memory Archive\n\n## Durable Context\n- Nothing archived yet.\n"
        "- [2026-01-05] the old router [archived 2026-09-28]\n\n## Active Threads\n- decided A\n", encoding="utf-8")

    async def fake_ensure_bundle(**_kwargs):
        return None

    async def no_days(**_kwargs):
        return []

    async def no_facts(**_kwargs):
        return []

    monkeypatch.setattr("core.memory_service.identity_service.ensure_user_identity_bundle", fake_ensure_bundle)
    monkeypatch.setattr("core.memory_service.identity_service.owner_dir", lambda owner_user_id: str(owner_dir))
    monkeypatch.setattr("core.memory_service.identity_service.owner_doc_path",
                        lambda owner_user_id, doc_key: str(paths[doc_key]))
    monkeypatch.setattr(svc, "list_days", no_days)
    monkeypatch.setattr("core.memory_service.brain.get_user_memories", no_facts)

    items = await svc._collect_index_items(owner_user_id=7)
    bullets = [i for i in items if i["source_kind"] == "memory_bullet"]
    by_content = {i["content"]: i for i in bullets}
    assert set(by_content) == {"likes tea", "decided A", "the old router [archived 2026-09-28]"}
    assert by_content["likes tea"]["metadata"] == {"section": "Durable Context", "doc": "memory", "stamp": "2026-08-03"}
    assert by_content["the old router [archived 2026-09-28]"]["metadata"] == {
        "section": "Durable Context", "doc": "memory_archive", "stamp": "2026-01-05"}
    # the same text in both documents is ONE item, and the file's copy wins
    assert by_content["decided A"]["metadata"]["doc"] == "memory"
    assert len(bullets) == 3
    assert by_content["likes tea"]["updated_at"] == _day("2026-08-03")                        # its stamp
    assert by_content["the old router [archived 2026-09-28]"]["updated_at"] == _day("2026-01-05")
    assert by_content["decided A"]["updated_at"] == os.path.getmtime(paths["memory"])         # unstamped: the file's time
    # the per-section items are still there, ahead of nothing lost
    assert {i["source_kind"] for i in items} >= {"curated_memory_section", "memory_bullet", "memory_summary_section"}

    # a missing archive is simply no archive items
    paths["memory_archive"].unlink()
    items = await svc._collect_index_items(owner_user_id=7)
    docs = {i["metadata"]["doc"] for i in items if i["source_kind"] == "memory_bullet"}
    assert docs == {"memory"}


def test_format_index_result_passes_metadata_through():
    from core.memory_service import memory_workspace_service as svc

    item = {"item_id": "mcb_x", "source_kind": "memory_bullet", "source_id": "memory", "title": "Durable Context",
            "content": "likes tea", "snippet": "likes tea", "salience": "medium", "tags": [], "related_refs": [],
            "updated_at": 1.0, "metadata": {"section": "Durable Context", "doc": "memory", "stamp": None}}
    out = svc._format_index_result(item, score=0.5, score_explanation="x")
    assert out["metadata"] == {"section": "Durable Context", "doc": "memory", "stamp": None}
    # a row whose metadata column came back undecoded, or empty, still formats
    assert svc._format_index_result({**item, "metadata": '{"doc": "memory"}'}, score=0.5, score_explanation="x")["metadata"] == {"doc": "memory"}
    assert svc._format_index_result({**item, "metadata": None}, score=0.5, score_explanation="x")["metadata"] == {}

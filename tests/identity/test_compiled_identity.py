"""Wave 2: assert CompiledIdentity is a single bundle with text/voice/action accessors."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.identity.models import CompiledIdentity
from core.identity.service import IdentityService


@pytest.fixture
def materialized_owner(tmp_path: Path, monkeypatch) -> tuple[str, int]:
    """Create a tmp identity tree with one owner having all 8 owner docs
    + 4 core docs materialized, and stub the brain version-recorder."""
    root = tmp_path / "identity"
    (root / "core").mkdir(parents=True)
    (root / "owners" / "1").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)

    for doc_filename, doc_type in [
        ("CORE_IDENTITY.md", "core_identity"),
        ("OPERATING_PRINCIPLES.md", "operating_principles"),
        ("CAPABILITIES_AND_BOUNDARIES.md", "capabilities_and_boundaries"),
        ("VOICE_IDENTITY.md", "voice_identity"),
    ]:
        (root / "core" / doc_filename).write_text(
            f"---\ndoc_type: {doc_type}\nplatform: global\nuser_id: 0\nversion: 1\n---\n# {doc_filename}\nbody\n",
            encoding="utf-8",
        )

    for doc_filename, doc_type in [
        ("PERSONALITY_PROFILE.md", "personality_profile"),
        ("RELATIONSHIP_STATE.md", "relationship_state"),
        ("MEMORY.md", "memory"),
        ("MEMORY_SUMMARY.md", "memory_summary"),
        ("ACTION_PREFERENCES.md", "action_preferences"),
        ("VOICE_PREFERENCES.md", "voice_preferences"),
        ("ROUTINES.md", "routines"),
        ("STANDING_ORDERS.md", "standing_orders"),
    ]:
        (root / "owners" / "1" / doc_filename).write_text(
            f"---\ndoc_type: {doc_type}\nplatform: owner\nuser_id: 1\nversion: 1\nowner_user_id: 1\n---\n# {doc_filename}\nbody\n",
            encoding="utf-8",
        )

    # Stub the brain version recorder.
    from core import database as database_module

    async def _noop(**_kwargs):
        return None

    monkeypatch.setattr(
        database_module.brain, "create_identity_document_version", _noop,
    )
    return str(root), 1


def _make_service(identity_root: str) -> IdentityService:
    """Return an IdentityService scoped to the given identity_root path."""
    from core.identity.document_store import IdentityDocumentStore
    from core.identity.materialization import IdentityBundleMaterializer

    svc = IdentityService.__new__(IdentityService)
    svc.document_store = IdentityDocumentStore(identity_root=identity_root)
    svc.materializer = IdentityBundleMaterializer(document_store=svc.document_store)
    svc.identity_root = svc.document_store.identity_root
    svc.core_root = svc.document_store.core_root
    svc.template_root = svc.document_store.template_root
    svc.user_root = svc.document_store.user_root
    svc.owner_root = svc.document_store.owner_root
    return svc


def test_compile_for_owner_returns_compiled_identity(materialized_owner) -> None:
    root, owner_id = materialized_owner
    svc = _make_service(root)
    bundle = svc.compile_for_owner(owner_id=owner_id)
    assert isinstance(bundle, CompiledIdentity)
    assert bundle.owner_id == owner_id
    assert len(bundle.core) == 4
    assert len(bundle.owner) == 8
    assert "core_identity" in bundle.core
    assert "personality_profile" in bundle.owner


def test_compile_for_owner_text_prompt_contains_personality(materialized_owner) -> None:
    root, owner_id = materialized_owner
    svc = _make_service(root)
    bundle = svc.compile_for_owner(owner_id=owner_id)
    text = bundle.text_prompt()
    assert isinstance(text, str)
    assert len(text) > 0
    # Both creator-authored and owner-evolving sections appear in text prompt.
    assert "Core Identity" in text or "core_identity" in text.lower()
    assert "Personality Profile" in text or "personality_profile" in text.lower()


# (test_compile_for_owner_voice_prompt_contains_voice_identity retired
# 2026-08-20, step 31: voice_prompt()/render_voice_prompt deleted — never
# called from any live path; the voice register rides the SHARED prompt,
# pinned by test_live_text_prompt_carries_her_voice_page below.)


def test_live_text_prompt_carries_her_voice_page(materialized_owner) -> None:
    """The LIVE reply path is context_adapter._render_identity_core ->
    compiled.text_prompt() -> render_text_prompt, whose doc list is
    hardcoded. The 08-13 unify edited PROMPT_DOCS_TEXT (a different path),
    so her self-authored voice page reached NO reply for four days —
    proven 2026-08-17 from the live logs. This pins the renderer the
    reply path actually calls."""
    root, owner_id = materialized_owner
    svc = _make_service(root)
    bundle = svc.compile_for_owner(owner_id=owner_id)
    text = bundle.text_prompt()
    assert "Voice Identity" in text or "voice_identity" in text.lower()
    # ordering: her voice sits with the creator-authored floor, before the
    # owner-evolving layers
    assert text.index("Voice Identity") < text.index("Personality Profile")


def test_compile_for_owner_text_snapshot_contains_voice_identity(materialized_owner) -> None:
    # Owner-decided 2026-08-13 ("same identity all around"): her authored
    # voice page rides the text snapshot too. NOTE (corrected 2026-08-17):
    # the claim that render_text_prompt is "dormant in production" was
    # WRONG — it is the renderer the live reply path calls, which is why
    # her page reached no reply for four days. Both are pinned now; see
    # test_live_text_prompt_carries_her_voice_page above.
    root, owner_id = materialized_owner
    svc = _make_service(root)
    bundle = svc.compile_for_owner(owner_id=owner_id)
    assert "voice_identity" in bundle.text_snapshot.documents
    assert "### Voice Identity" in bundle.text_snapshot.prompt_context


def test_compile_for_owner_action_view_returns_dict(materialized_owner) -> None:
    root, owner_id = materialized_owner
    svc = _make_service(root)
    bundle = svc.compile_for_owner(owner_id=owner_id)
    action = bundle.action_view()
    assert isinstance(action, dict)
    # action_view should contain at least policy or relationship keys
    assert "policy" in action or "relationship" in action


def test_compile_for_owner_caches_by_mtime(materialized_owner) -> None:
    """Repeated calls with no file change return the same instance."""
    root, owner_id = materialized_owner
    svc = _make_service(root)
    a = svc.compile_for_owner(owner_id=owner_id)
    b = svc.compile_for_owner(owner_id=owner_id)
    assert a is b, "cache hit should return the same instance"


def test_compile_for_owner_invalidates_on_file_change(materialized_owner) -> None:
    """Touching a markdown file invalidates the cache."""
    root, owner_id = materialized_owner
    svc = _make_service(root)
    a = svc.compile_for_owner(owner_id=owner_id)

    time.sleep(0.05)  # ensure mtime changes
    target = Path(root) / "owners" / "1" / "MEMORY.md"
    new_text = target.read_text(encoding="utf-8") + "\n# updated\n"
    target.write_text(new_text, encoding="utf-8")
    # Force mtime forward in case filesystem resolution is coarse.
    new_mtime = time.time()
    os.utime(target, (new_mtime, new_mtime))

    b = svc.compile_for_owner(owner_id=owner_id)
    assert a is not b, "cache should have invalidated on file mtime change"

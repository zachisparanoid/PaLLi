"""Wave 2: tests for core.first_contact_targets — the lean replacement for
the target-tracking logic in core/first_contact.py."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Imported at module level ON PURPOSE (2026-07-15 ordering-leak fix): the
# identity_service singleton bakes its roots from config at first import.
# If a test in this file were the process's first importer, the singleton
# would be born against this file's patched/tmp config and stay poisoned
# for every later identity-touching test. Importing at collection time
# guarantees a sane birth before any monkeypatch runs.
from core.identity.service import identity_service  # noqa: F401


@pytest.fixture
def tmp_identity(tmp_path: Path, monkeypatch) -> tuple[str, int]:
    """Set up a tmp identity tree with a synthetic FIRST_CONTACT.md and
    one owner whose docs are all template placeholders (so all targets
    are unfulfilled)."""
    root = tmp_path / "identity"
    (root / "core").mkdir(parents=True)
    (root / "owners" / "1").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)
    (root / "bootstrap").mkdir(parents=True)

    # Minimal core docs (compile_for_owner needs at least the 4 core docs).
    for doc_filename, doc_type in [
        ("CORE_IDENTITY.md", "core_identity"),
        ("OPERATING_PRINCIPLES.md", "operating_principles"),
        ("CAPABILITIES_AND_BOUNDARIES.md", "capabilities_and_boundaries"),
        ("VOICE_IDENTITY.md", "voice_identity"),
    ]:
        (root / "core" / doc_filename).write_text(
            f"---\ndoc_type: {doc_type}\nplatform: global\nuser_id: 0\nversion: 1\n---\nbody\n",
            encoding="utf-8",
        )

    # Owner docs with all-template placeholders (display_name = "{display_name}").
    (root / "owners" / "1" / "PERSONALITY_PROFILE.md").write_text(
        "---\ndoc_type: personality_profile\nplatform: owner\nuser_id: 1\nversion: 1\n"
        'display_name: "{display_name}"\nusername: "{username}"\n'
        'tone: "{tone}"\nwarmth: "{warmth}"\nhumor: "{humor}"\n'
        'response_length: "{response_length}"\n'
        'relationship_style: "{relationship_style}"\n'
        "owner_user_id: 1\n---\nbody\n",
        encoding="utf-8",
    )
    (root / "owners" / "1" / "ACTION_PREFERENCES.md").write_text(
        "---\ndoc_type: action_preferences\nplatform: owner\nuser_id: 1\nversion: 1\n"
        'autonomy_mode: "{autonomy_mode}"\n'
        'allow_silent_beneficial_actions: "{allow_silent_beneficial_actions}"\n'
        'allow_silent_low_risk_actions: "{allow_silent_low_risk_actions}"\n'
        'allow_privileged_cli_actions: "{allow_privileged_cli_actions}"\n'
        'allow_package_install_actions: "{allow_package_install_actions}"\n'
        'environment_optimization_mode: "{environment_optimization_mode}"\n'
        'external_contact_policy: "{external_contact_policy}"\n'
        'post_action_reporting_mode: "{post_action_reporting_mode}"\n'
        "owner_user_id: 1\n---\nbody\n",
        encoding="utf-8",
    )
    (root / "owners" / "1" / "VOICE_PREFERENCES.md").write_text(
        "---\ndoc_type: voice_preferences\nplatform: owner\nuser_id: 1\nversion: 1\n"
        'interaction_mode: "{interaction_mode}"\n'
        'presence_profile: "{presence_profile}"\n'
        'focus_state: "{focus_state}"\n'
        'local_transport_mode: "{local_transport_mode}"\n'
        "owner_user_id: 1\n---\nbody\n",
        encoding="utf-8",
    )

    # Synthesized FIRST_CONTACT.md modeled on the real one but trimmed.
    (root / "bootstrap" / "FIRST_CONTACT.md").write_text(
        "---\n"
        "doc_type: first_contact\n"
        "scope: bootstrap\n"
        "platform: global\n"
        "version: 1\n"
        'required_targets: ["user_name", "companion_style"]\n'
        'target_completion_rules: {"user_name": [{"source": "profile", "field": "display_name", "match": "not_in", "values": ["", "Local Operator"]}, {"source": "profile", "field": "username", "match": "not_in", "values": ["", "operator"]}], "companion_style": [{"source": "document", "doc": "personality_profile", "match": "non_default", "fields": ["tone", "warmth", "humor", "response_length"]}]}\n'
        "---\n# First Contact\nbody\n",
        encoding="utf-8",
    )

    # Stub the brain version recorder so identity_service.compile_for_owner doesn't hit a DB.
    from core import database as database_module
    async def _noop(**_kwargs):
        return None
    monkeypatch.setattr(
        database_module.brain, "create_identity_document_version", _noop,
    )

    # Repoint the identity service's roots at our tmp tree.
    monkeypatch.setattr(
        "core.config.config.IDENTITY_ROOT_PATH", str(root), raising=False,
    )
    # Force the identity_service singleton to re-resolve roots from the patched config.
    from core.identity.service import identity_service
    from core.identity.document_store import IdentityDocumentStore
    from core.identity.materialization import IdentityBundleMaterializer
    # monkeypatch (not raw assignment) so the singleton is RESTORED after the
    # test — raw assignment leaked this tmp tree into every later test that
    # touched identity docs (caught 2026-07-15 by the Wave-1a status-filter
    # test dying on this fixture's dead tmp_path).
    store = IdentityDocumentStore(identity_root=str(root))
    monkeypatch.setattr(identity_service, "document_store", store)
    monkeypatch.setattr(
        identity_service, "materializer", IdentityBundleMaterializer(document_store=store)
    )
    monkeypatch.setattr(identity_service, "identity_root", store.identity_root)
    monkeypatch.setattr(identity_service, "core_root", store.core_root)
    monkeypatch.setattr(identity_service, "template_root", store.template_root)
    monkeypatch.setattr(identity_service, "user_root", store.user_root)
    monkeypatch.setattr(identity_service, "owner_root", store.owner_root)
    if hasattr(identity_service, "_compiled_cache"):
        monkeypatch.setattr(identity_service, "_compiled_cache", {})

    return str(root), 1


def test_unfulfilled_targets_returns_required_when_all_default(tmp_identity) -> None:
    from core.first_contact_targets import unfulfilled_targets_for_owner
    _root, owner_id = tmp_identity
    targets = unfulfilled_targets_for_owner(owner_id=owner_id)
    assert "user_name" in targets
    assert "companion_style" in targets


def test_unfulfilled_targets_drops_resolved_user_name(tmp_identity, tmp_path) -> None:
    """Set display_name to a real value; user_name should drop from the list."""
    root, owner_id = tmp_identity
    pp_path = Path(root) / "owners" / "1" / "PERSONALITY_PROFILE.md"
    text = pp_path.read_text(encoding="utf-8")
    text = text.replace('display_name: "{display_name}"', 'display_name: "Sam"')
    pp_path.write_text(text, encoding="utf-8")

    # Bust the identity cache so it re-reads the file.
    from core.identity.service import identity_service
    if hasattr(identity_service, "_compiled_cache"):
        identity_service._compiled_cache = {}

    from core.first_contact_targets import unfulfilled_targets_for_owner
    targets = unfulfilled_targets_for_owner(owner_id=owner_id)
    assert "user_name" not in targets
    assert "companion_style" in targets


def test_unfulfilled_targets_returns_empty_when_first_contact_missing(monkeypatch, tmp_path) -> None:
    """If FIRST_CONTACT.md doesn't exist, return empty list (defensive)."""
    monkeypatch.setattr(
        "core.config.config.IDENTITY_ROOT_PATH", str(tmp_path), raising=False,
    )
    from core.first_contact_targets import unfulfilled_targets_for_owner
    targets = unfulfilled_targets_for_owner(owner_id=1)
    assert targets == []

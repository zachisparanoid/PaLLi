"""After E.4 Wave 3, CompiledIdentityBundle must not be importable.

E.4 (Phase E, 2026-05-13) unified the identity surface around
CompiledIdentity, which now carries both the Wave-1 snapshot triple
(text/voice/action_snapshot, provenance, warnings, to_dict()) and the
Wave-2 raw-doc dicts (core, owner, text_prompt/voice_prompt/action_view
renderer methods). The legacy CompiledIdentityBundle wrapper is gone.

The Text/Voice/Action sub-snapshot dataclasses STAY — they're not
redundant types, they're internal building blocks used by
reply_identity_context, ambient_identity_context, the web API JSON
shape (frontend depends on the snapshot.to_dict() format), and the
snapshot-getter methods on identity_service. Their structure carries
real data (preferences, action_coupling, traits, materialized,
warnings) that consumers depend on.

IdentityBundle (the raw markdown reader output, distinct from the
Wave-1 CompiledIdentityBundle wrapper) also stays — distinct type,
distinct purpose.
"""
from __future__ import annotations

import pytest


def test_compiled_identity_bundle_deleted() -> None:
    from core.identity.models import CompiledIdentity  # noqa: F401 — canonical still works
    with pytest.raises(ImportError):
        from core.identity.models import CompiledIdentityBundle  # noqa: F401


def test_raw_identity_bundle_still_present() -> None:
    """IdentityBundle (the raw read result, distinct from CompiledIdentityBundle) stays."""
    from core.identity.models import IdentityBundle  # noqa: F401


@pytest.mark.parametrize(
    "name",
    [
        "TextIdentitySnapshot",
        "VoiceIdentitySnapshot",
        "ActionIdentitySnapshot",
        "CompiledActionPolicy",
        "CompiledIdentityProvenanceEntry",
        "IdentityCompileWarning",
        "CompiledDocumentView",
    ],
)
def test_internal_building_blocks_still_present(name: str) -> None:
    """Internal building blocks of CompiledIdentity remain importable.

    The Phase E audit's recommended path proposed deleting the
    Text/Voice/Action snapshot dataclasses, but they still have real
    consumers (web API to_dict() JSON shape, snapshot getters,
    reply/ambient identity contexts). Keeping them; only the Wave-1
    Bundle wrapper goes.
    """
    import core.identity.models as models
    assert hasattr(models, name), f"{name} should still be present on core.identity.models"


def test_compiled_identity_carries_both_wave_shapes() -> None:
    """The unified CompiledIdentity exposes both Wave-1 and Wave-2 surfaces."""
    from core.identity.models import CompiledIdentity

    instance = CompiledIdentity()
    # Wave-1 surface — snapshots, policy, provenance, warnings, to_dict.
    assert hasattr(instance, "text_snapshot")
    assert hasattr(instance, "voice_snapshot")
    assert hasattr(instance, "action_snapshot")
    assert hasattr(instance, "action_policy")
    assert hasattr(instance, "provenance")
    assert hasattr(instance, "warnings")
    assert hasattr(instance, "user_id")
    assert hasattr(instance, "platform")
    assert hasattr(instance, "owner_user_id")
    assert callable(instance.to_dict)
    # Wave-2 surface — raw doc dicts and renderer methods.
    assert hasattr(instance, "core")
    assert hasattr(instance, "owner")
    assert hasattr(instance, "owner_id")
    assert hasattr(instance, "cache_key")
    assert callable(instance.text_prompt)
    # (voice_prompt left the surface 2026-08-20, step 31: deleted with
    # render_voice_prompt — the voice register rides the shared prompt.)
    assert not hasattr(instance, "voice_prompt")
    assert callable(instance.action_view)

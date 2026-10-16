"""Batch 7, task 2 (her voice, 2026-10-04; audit 4.1): the four creator docs in
identity/core/ reach her prompt as their BODIES only.

Until now each one rendered its frontmatter list first (``- core_form:
digital_parrot``, ``- reply_style: concise_and_spoken``, ...): a spec sheet
read to her on every reply, about 545 characters of it. The keys stay in the
files as metadata (code reads the voice ones through the compiled view's
frontmatter); only the rendering stops, and only for the core docs. Owner docs
(memory, personality profile, relationship state, memory summary) keep their
bullets byte for byte.

The fixture copies the REAL identity/core into tmp_path. It never reads the
repo's identity/palli/: on the owner's checkout that is her live voice page.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from core.identity.compiler import (
    compile_identity,
    render_memory_doc,
    render_prompt_excerpt,
)
from core.identity.constants import CORE_DOCS, IDENTITY_DOC_PREAMBLES
from core.identity.document_store import IdentityDocumentStore
from core.identity.frontmatter import parse_frontmatter
from core.identity.models import IdentityBundle, IdentityDocument

REPO_CORE = Path(__file__).resolve().parents[2] / "identity" / "core"

# The 15 identity keys the four core files carry in their frontmatter today.
CORE_IDENTITY_KEYS = (
    "core_form:", "primary_role:", "creator_stance:", "default_presence:",
    "initiative_style:", "default_mode:", "truthfulness:", "followthrough:",
    "action_policy_mode:", "privileged_autonomy_requires_admin_gate:", "hard_command_policy:",
    "voice_presence:", "group_behavior:", "turn_taking:", "reply_style:",
)

# Rendering order of the core layer in render_text_prompt, with each label.
CORE_LABELS = {
    "core_identity": "### Core Identity",
    "operating_principles": "### Operating Principles",
    "capabilities_and_boundaries": "### Capabilities and Boundaries",
    "voice_identity": "### Voice Identity",
}


def _seed_identity_root(tmp_path: Path) -> Path:
    """Copied from tests/core/test_identity_service.py (copy, never import),
    cut to the core layer: the real creator docs, nothing of hers or the
    owner's."""
    root = tmp_path / "identity"
    shutil.copytree(REPO_CORE, root / "core")
    (root / "owners").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def real_core_bundle(tmp_path: Path) -> IdentityBundle:
    """The four real core docs, loaded the way the live store loads them.
    voice_identity resolves palli-first; its palli root is a directory that
    does not exist, so it is the creator's fallback copy, as on a fresh
    clone and in CI."""
    root = _seed_identity_root(tmp_path)
    store = IdentityDocumentStore(identity_root=str(root))
    store.palli_root = str(tmp_path / "no-voice-page-here")
    assert not os.path.exists(store.palli_root)
    bundle = store.load_bundle(user_id=1, platform="owner")
    assert set(bundle.core_documents) == set(CORE_DOCS)
    for doc in bundle.core_documents.values():
        assert Path(doc.path).parent == root / "core"
    return bundle


def _first_lines(core_file: Path) -> tuple[str, str]:
    """The body's first line and its first prose line (not a heading), read
    from the file itself, so a rewrite of the bodies keeps this test true."""
    _, body = parse_frontmatter(core_file.read_text(encoding="utf-8"))
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    prose = next(line for line in lines if not line.startswith("#"))
    return lines[0], prose


def test_core_identity_metadata_never_reaches_her_prompt(real_core_bundle) -> None:
    text = compile_identity(real_core_bundle).text_prompt(exclude=("memory",))
    for key in CORE_IDENTITY_KEYS:
        assert key not in text


def test_each_core_doc_renders_its_label_then_its_body(real_core_bundle) -> None:
    text = compile_identity(real_core_bundle).text_prompt(exclude=("memory",))
    positions = []
    for doc_key, label in CORE_LABELS.items():
        first, prose = _first_lines(Path(real_core_bundle.core_documents[doc_key].path))
        assert label in text
        # Nothing between the label and the body: no bullet list in the way.
        assert f"{label}\n{first}" in text
        assert prose in text
        positions.append(text.index(label))
    assert positions == sorted(positions)  # core_identity, operating, capabilities, voice


def test_the_snapshots_drop_the_bullets_too(real_core_bundle) -> None:
    """The text, voice and action snapshots render each view's excerpt, which
    is render_prompt_excerpt as well: the same rule reaches them, and a core
    view's excerpt is its body alone."""
    compiled = compile_identity(real_core_bundle)
    snapshots = (compiled.text_snapshot, compiled.voice_snapshot, compiled.action_snapshot)
    for snapshot in snapshots:
        assert "### Core Identity\n" in snapshot.prompt_context
        for key in CORE_IDENTITY_KEYS:
            assert key not in snapshot.prompt_context
    for doc_key in CORE_DOCS:
        views = [s.documents[doc_key] for s in snapshots if doc_key in s.documents]
        assert views, doc_key
        for view in views:
            assert view.excerpt == view.content.strip()


def test_the_keys_stay_in_the_files_as_metadata(real_core_bundle) -> None:
    """Only the rendering stops. Every key is still in the files, and the
    compiled view's frontmatter still carries it, so the readers of the voice
    keys (ai/reply_identity_context, ai/ambient_identity_context) see what
    they saw before."""
    compiled = compile_identity(real_core_bundle)
    in_files: set[str] = set()
    in_views: set[str] = set()
    for doc_key, filename in CORE_DOCS.items():
        frontmatter, _ = parse_frontmatter((REPO_CORE / filename).read_text(encoding="utf-8"))
        in_files.update(f"{key}:" for key in frontmatter)
        view = (compiled.text_snapshot.documents.get(doc_key)
                or compiled.action_snapshot.documents.get(doc_key))
        assert view is not None, doc_key
        in_views.update(f"{key}:" for key in view.frontmatter)
    for key in CORE_IDENTITY_KEYS:
        assert key in in_files
        assert key in in_views


def test_an_owner_docs_bullets_still_render(real_core_bundle) -> None:
    """The guard: the rule is the core layer's alone. Owner docs keep their
    frontmatter bullets byte for byte, in the same prompt the core docs lost
    theirs from."""
    real_core_bundle.user_documents["memory"] = IdentityDocument(
        doc_key="memory", path="MEMORY.md",
        frontmatter={"doc_type": "memory", "version": 3, "favorite_tea": "oolong"},
        content="# Memory\n\n## Durable Context\n- a\n",
    )
    real_core_bundle.user_documents["personality_profile"] = IdentityDocument(
        doc_key="personality_profile", path="PERSONALITY_PROFILE.md",
        frontmatter={"doc_type": "personality_profile", "tone": "warm_companion"},
        content="# Personality Profile\n\nbody\n",
    )
    compiled = compile_identity(real_core_bundle)

    text = compiled.text_prompt()
    assert "### Personality Profile\n- tone: warm_companion\n\n# Personality Profile\n\nbody" in text
    assert "- favorite_tea: oolong" in text
    for key in CORE_IDENTITY_KEYS:
        assert key not in text

    preamble = IDENTITY_DOC_PREAMBLES["memory"].rstrip()
    assert render_memory_doc(compiled) == (
        f"### Memory\n{preamble}\n\n- favorite_tea: oolong\n\n# Memory\n\n## Durable Context\n- a"
    )


def test_the_rule_is_keyed_on_the_doc_not_the_key() -> None:
    """Same frontmatter, same body: a core doc renders the body alone, an
    owner doc renders bullets then body, exactly as before."""
    frontmatter = {"doc_type": "x", "core_form": "digital_parrot", "favorite_tea": "oolong"}
    body = "# Title\n\nThe body.\n"
    for doc_key in CORE_DOCS:
        core = IdentityDocument(doc_key=doc_key, path="x", frontmatter=dict(frontmatter), content=body)
        assert render_prompt_excerpt(core) == "# Title\n\nThe body."
    owner = IdentityDocument(doc_key="memory", path="x", frontmatter=dict(frontmatter), content=body)
    assert render_prompt_excerpt(owner) == (
        "- core_form: digital_parrot\n- favorite_tea: oolong\n\n# Title\n\nThe body."
    )

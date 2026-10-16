#!/usr/bin/env python3
"""How big her system prompt is, how many prohibitions it has, and which
quoted example lines she could parrot (Batch 7, her voice, Task 0).

Read-only: it builds her prompt from the code and the creator-owned
``identity/core/`` docs and prints a table. Nothing else.

Usage (from the repo root):
    venv/bin/python scripts/prompt_size_report.py

The numbers must be the same in a bare script, under pytest and on CI, because
later tasks assert against them. So :func:`measure` pins the shipped flag world
itself (:func:`pinned_world`) and restores every attribute it patched:

  - every companion faculty ON (``core.turn.companion_flags.companion_settings``
    and ``core.agent.where_you_live.companion_settings``, which is bound at
    import), with the "earned" notebook bar, and duplex and outward_senses
    taken from the world (on in :data:`SHIPPED_WORLD`);
  - ``core.agent.where_you_live.nas_root`` -> :data:`FAKE_NAS_ROOT`;
  - ``core.agent.duplex.duplex_enabled`` and
    ``core.agent.builtin_tools._forged_tools_enabled`` -> the world's
    ``duplex`` and ``forged_tools`` (True in :data:`SHIPPED_WORLD`, False in
    :data:`TEST_WORLD`, which only the CLI's FakeCtx row uses);
  - the skills loader's ``shutil`` -> a stand-in whose ``which`` answers only
    for :data:`LINUX_STAND_IN_BINARIES`.

:func:`representative_ctx` is the context every caller measures: the four
creator docs from ``identity/core/`` (her own voice page is never read: the
store's ``palli_root`` points at a missing directory, so the creator's copy
renders in any checkout), the real capabilities catalogue under the stand-in
PATH, a person line, a synthetic one-line memory core (so the code-owned
memory preamble and footer render as they do live) and no corrections.

``measure()`` returns:
  - ``stable_chars`` / ``volatile_chars`` and ``stable_text`` / ``volatile_text``;
  - ``prohibitions`` (stable only) and ``volatile_prohibitions``;
  - ``scripted_quotes``: quoted lines over stable + volatile, the skills
    catalogue's text left out; ``skills_quotes``: the catalogue's own quoted
    trigger phrases (what the person says, not lines she could parrot);
  - ``blocks``: a name -> chars map (NOT a partition; it overlaps), and
    ``block_prohibitions``: the same names -> prohibition counts.
"""
from __future__ import annotations

import inspect
import re
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# never / don't (straight or curly) / do not / "no" + an -ing word.
PROHIBITION_RE = re.compile(r"\b(?:never\b|don[’']t\b|do not\b|no (?=[a-z]+ing\b))", re.I)
# Double quotes (straight or curly), or single quotes only at word edges, so
# an apostrophe inside "LET'S" or "I'll" never ends a quote.
SCRIPTED_RE = re.compile(
    r"[\"“]([^\"”\n]{6,120})[\"”]|(?<![A-Za-z0-9])'([^\n]{6,120}?)'(?![A-Za-z0-9])"
)

FAKE_NAS_ROOT = "/srv/nas"
LINUX_STAND_IN_BINARIES = ("git", "gh", "ffmpeg", "df", "ps", "ping", "uname", "python")

# The flags that differ between the shipped install and the test world (the
# test conftest keeps duplex and the forge off; the example yaml has
# outward_senses off). Every other faculty is on in both.
SHIPPED_WORLD: Mapping[str, bool] = {"duplex": True, "forged_tools": True, "outward_senses": True}
TEST_WORLD: Mapping[str, bool] = {"duplex": False, "forged_tools": False, "outward_senses": False}

# The three creator docs the batch calls the constitution (her voice page,
# the fourth core doc, is reported on its own as ``voice_identity``).
CONSTITUTION_DOCS = ("core_identity", "operating_principles", "capabilities_and_boundaries")

# A missing directory: the store then resolves voice_identity to the
# creator's copy in identity/core/, never to her own page.
_NO_PALLI_PAGE = REPO_ROOT / "identity" / ".no-palli-page-for-measuring"
_STAND_IN_PERSON = {"display_name": "Alex", "pronouns": "they/them"}
_SYNTHETIC_MEMORY = "## Durable Context\n- Likes the plain answer first.\n"
_MEMORY_CORE_BUDGET = 6000


def count_prohibitions(text: str) -> int:
    return len(PROHIBITION_RE.findall(text or ""))


def find_scripted_quotes(text: str) -> list[str]:
    """Quoted stretches that contain a space (a file path, tool name or
    setting name has none)."""
    found = (a or b for a, b in SCRIPTED_RE.findall(text or ""))
    return [q for q in found if " " in q]


def _stand_in_which(name: str, *args: Any, **kwargs: Any) -> str | None:
    return f"/usr/bin/{name}" if name in LINUX_STAND_IN_BINARIES else None


@contextmanager
def pinned_world(world: Mapping[str, bool] = SHIPPED_WORLD) -> Iterator[None]:
    """Pin the flag world the prompt reads, then restore every attribute."""
    comp = SimpleNamespace(
        self_possession=True, boundaries=True, own_thoughts=True, repair=True,
        spontaneity=True, self_notes=True, self_notes_bar="earned", own_work=True,
        outward_senses=bool(world["outward_senses"]), duplex=bool(world["duplex"]),
    )
    # Import every patched module first: one imported inside the patch window
    # binds the stand-in (`from core.turn.companion_flags import
    # companion_settings`) and the restore puts the stand-in back (10-14).
    import core.agent.builtin_tools  # noqa: F401
    import core.agent.duplex  # noqa: F401
    import core.agent.where_you_live  # noqa: F401
    import core.turn.skills_loader  # noqa: F401

    with ExitStack() as stack:
        stack.enter_context(patch("core.turn.companion_flags.companion_settings", lambda: comp))
        stack.enter_context(patch("core.agent.where_you_live.companion_settings", lambda: comp))
        stack.enter_context(patch("core.agent.where_you_live.nas_root", lambda: FAKE_NAS_ROOT))
        stack.enter_context(patch("core.agent.duplex.duplex_enabled", lambda: bool(world["duplex"])))
        stack.enter_context(patch(
            "core.agent.builtin_tools._forged_tools_enabled", lambda: bool(world["forged_tools"]),
        ))
        stack.enter_context(patch(
            "core.turn.skills_loader.shutil", SimpleNamespace(which=_stand_in_which),
        ))
        yield


def _identity_text() -> str:
    """The four core docs as the reply path renders them (owner docs left out)."""
    from core.identity.compiler import compile_identity
    from core.identity.constants import CORE_DOCS
    from core.identity.document_store import IdentityDocumentStore
    from core.identity.models import IdentityBundle

    store = IdentityDocumentStore(identity_root=str(REPO_ROOT / "identity"))
    store.palli_root = str(_NO_PALLI_PAGE)
    bundle = IdentityBundle(user_id=1, platform="owner")
    for doc_key in CORE_DOCS:
        doc = store.load_storage_document(user_id=1, platform="owner", doc_key=doc_key)
        if doc is not None:
            bundle.core_documents[doc_key] = doc
    compiled = compile_identity(bundle)
    return compiled.text_prompt(
        exclude=("memory", "personality_profile", "relationship_state", "memory_summary"),
    ).strip()


def _person_line() -> str:
    from core.agent.context_adapter import owner_person_line

    profile = SimpleNamespace(frontmatter=dict(_STAND_IN_PERSON))
    return owner_person_line(SimpleNamespace(owner={"personality_profile": profile}))


def _memory_core_text() -> str:
    from core.identity.compiler import plan_memory_core
    from core.identity.models import IdentityDocument

    doc = IdentityDocument(doc_key="memory", path="(synthetic)", frontmatter={}, content=_SYNTHETIC_MEMORY)
    core = plan_memory_core(
        SimpleNamespace(owner={"memory": doc}), budget_chars=_MEMORY_CORE_BUDGET, refs={}, now=0.0,
    )
    return core.text.strip()


def representative_ctx() -> SimpleNamespace:
    """The shipped-representative context: the CLI and the tests both use it."""
    from core.turn.skills_loader import render_capabilities_block

    with pinned_world():
        skills = render_capabilities_block() or ""
    return SimpleNamespace(
        identity_core_text=_identity_text(),
        person_line=_person_line(),
        skills_text=skills,
        owner_memory_text=_memory_core_text(),
        corrections_text="",
        runtime_text="## Current Time\n10:00",
        memory_text="",
        facts_text="",
        is_owner=True,
    )


@dataclass
class FakeCtx:
    """Copied from tests/agent/test_system_prompt.py (never imported): the
    test world's context, for the CLI's secondary row."""

    identity_core_text: str = "I'm PaLLi."
    skills_text: str = "skills here"
    runtime_text: str = "runtime info"
    memory_text: str = "memory snapshot"
    facts_text: str = "user facts"
    spoken_register_text: str = ""


def _identity_sections(identity_text: str) -> dict[str, str]:
    """doc key -> its ``### {label}`` section of the identity text.

    Splits only at the known doc labels (IDENTITY_DOC_LABELS): a doc body
    passes through verbatim, so an inner ``### `` heading in it stays part
    of that doc."""
    from core.identity.constants import IDENTITY_DOC_LABELS

    by_label = {label: key for key, label in IDENTITY_DOC_LABELS.items()}
    known = "|".join(re.escape(label) for label in by_label)
    sections: dict[str, str] = {}
    for piece in re.split(rf"(?m)^(?=### (?:{known})[ \t]*$)", identity_text or ""):
        if not piece.startswith("### "):
            continue
        label = piece[4:].split("\n", 1)[0].strip()
        if label not in by_label:
            raise ValueError(f"identity text opens with an unknown section: {label!r}")
        sections[by_label[label]] = piece.strip()
    return sections


def _block_functions() -> list[tuple[str, Any]]:
    """Every ``*_block`` function defined in the system prompt and duplex modules."""
    import core.agent.duplex as duplex
    import core.agent.system_prompt as system_prompt

    found = []
    for module in (system_prompt, duplex):
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.endswith("_block") and fn.__module__ == module.__name__:
                found.append((name, fn))
    return found


def _block_texts(ctx: Any, quiet_choice: bool) -> dict[str, str]:
    identity = str(getattr(ctx, "identity_core_text", "") or "")
    sections = _identity_sections(identity)
    texts = {
        "identity_core": identity,
        "constitution": "\n\n".join(sections[k] for k in CONSTITUTION_DOCS if k in sections),
        **{k: sections.get(k, "") for k in CONSTITUTION_DOCS},
        "voice_identity": sections.get("voice_identity", ""),
        "person_line": str(getattr(ctx, "person_line", "") or ""),
        "skills": str(getattr(ctx, "skills_text", "") or ""),
        "owner_memory": str(getattr(ctx, "owner_memory_text", "") or ""),
    }
    for name, fn in _block_functions():
        if "quiet_choice" in inspect.signature(fn).parameters:
            texts[name] = fn(quiet_choice=quiet_choice)
        else:
            texts[name] = fn()
    return texts


def measure(ctx: Any, *, quiet_choice: bool = True, world: Mapping[str, bool] = SHIPPED_WORLD) -> dict:
    """Measure the prompt ``build_system_prompt_parts`` builds for ``ctx``."""
    from core.agent.system_prompt import build_system_prompt_parts

    with pinned_world(world):
        stable, volatile = build_system_prompt_parts(ctx, cadence_directive="", quiet_choice=quiet_choice)
        texts = _block_texts(ctx, quiet_choice)
    skills = texts["skills"]
    outside_skills = stable.replace(skills, "") if skills else stable
    return {
        "stable_chars": len(stable),
        "volatile_chars": len(volatile),
        "prohibitions": count_prohibitions(stable),
        "volatile_prohibitions": count_prohibitions(volatile),
        "stable_text": stable,
        "volatile_text": volatile,
        "scripted_quotes": find_scripted_quotes(outside_skills) + find_scripted_quotes(volatile),
        "skills_quotes": find_scripted_quotes(skills),
        "blocks": {name: len(text) for name, text in texts.items()},
        "block_prohibitions": {name: count_prohibitions(text) for name, text in texts.items()},
    }


def _row(label: str, r: dict) -> str:
    return (
        f"| {label} | {r['stable_chars']:,} | {r['prohibitions']:,} | {r['volatile_chars']:,} "
        f"| {r['volatile_prohibitions']:,} | {len(r['scripted_quotes']):,} |"
    )


def render_report() -> str:
    head = measure(representative_ctx(), quiet_choice=True)
    no_quiet = measure(representative_ctx(), quiet_choice=False)
    fake = measure(FakeCtx(), quiet_choice=False, world=TEST_WORLD)
    lines = [
        "Shipped-representative context, quiet_choice=True (the headline):",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Stable chars | {head['stable_chars']:,} |",
        f"| Volatile chars | {head['volatile_chars']:,} |",
        f"| Stable prohibitions | {head['prohibitions']:,} |",
        f"| Volatile prohibitions | {head['volatile_prohibitions']:,} |",
        f"| Scripted quotes outside skills | {len(head['scripted_quotes']):,} |",
        f"| Skills quotes | {len(head['skills_quotes']):,} |",
        "",
        "Scripted quotes outside skills (stable, then volatile):",
        "",
        *(f"{i}. \"{q}\"" for i, q in enumerate(head["scripted_quotes"], 1)),
        "",
        "Per block (a name map, not a partition: the blocks overlap):",
        "",
        "| Block | Chars | Prohibitions |",
        "|---|---|---|",
        *(
            f"| `{name}` | {chars:,} | {head['block_prohibitions'][name]:,} |"
            for name, chars in head["blocks"].items()
        ),
        "",
        "Other rows:",
        "",
        "| Context | Stable chars | Stable prohibitions | Volatile chars | Volatile prohibitions "
        "| Scripted quotes outside skills |",
        "|---|---|---|---|---|---|",
        _row("Shipped-representative, quiet_choice=True (headline)", head),
        _row("Shipped-representative, quiet_choice=False", no_quiet),
        _row("Test-world FakeCtx (duplex, forge, where-you-live off), quiet_choice=False", fake),
    ]
    return "\n".join(lines)


def main() -> int:
    print(render_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

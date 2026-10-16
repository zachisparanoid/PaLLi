"""Batch 2, task 3 (cut the cost, 2026-09-27): the memory doc renders with its
least-changing sections first. It is the LAST entry of the system prompt's
cache-stable prefix, so the part of MEMORY.md that changes least (Important
Decisions, Useful References) sits deepest in the cacheable bytes and the
~7x-a-day churn (Active Threads) is the last thing before the volatile tail.
Same words, a different order: render_memory_doc adds and drops nothing."""
from __future__ import annotations

from core.identity.compiler import (
    MEMORY_SECTION_ORDER,
    render_memory_doc,
    render_text_prompt,
)
from core.identity.constants import IDENTITY_DOC_PREAMBLES
from core.identity.models import CompiledIdentity, IdentityDocument

# The template's order (identity/templates/MEMORY.md): a pre-heading part,
# then Durable Context, Active Threads, Important Decisions, Useful References.
FILE_ORDER = (
    "# Memory\n\nThis file is my curated memory of them, not a transcript.\n\n"
    "## Durable Context\n- a\n\n## Active Threads\n- b\n\n"
    "## Important Decisions\n- c\n\n## Useful References\n- d\n"
)


def _bundle(memory_content: str | None, **frontmatter) -> CompiledIdentity:
    owner = {}
    if memory_content is not None:
        owner["memory"] = IdentityDocument(
            doc_key="memory", path="x", frontmatter=dict(frontmatter), content=memory_content,
        )
    return CompiledIdentity(owner=owner)


def _lines(text: str) -> list[str]:
    return sorted(line for line in text.splitlines() if line.strip())


def test_memory_sections_render_least_changing_first():
    text = render_memory_doc(_bundle(
        "## Durable Context\n- a\n\n## Active Threads\n- b\n\n## Important Decisions\n- c\n\n## Useful References\n- d\n"))
    order = [h for h in MEMORY_SECTION_ORDER if h in text]
    assert order == list(MEMORY_SECTION_ORDER)
    assert [text.index(h) for h in order] == sorted(text.index(h) for h in order)
    assert "- a" in text and "- b" in text and "- c" in text and "- d" in text  # nothing dropped


def test_memory_section_order_is_least_changing_first():
    assert MEMORY_SECTION_ORDER == (
        "## Important Decisions", "## Useful References", "## Durable Context", "## Active Threads",
    )


def test_memory_doc_keeps_label_preamble_and_pre_heading_part_first():
    text = render_memory_doc(_bundle(FILE_ORDER, favorite_tea="oolong"))
    preamble = IDENTITY_DOC_PREAMBLES["memory"].rstrip()
    assert text == (
        f"### Memory\n{preamble}\n\n"
        "- favorite_tea: oolong\n\n# Memory\n\nThis file is my curated memory of them, not a transcript.\n\n"
        "## Important Decisions\n- c\n\n## Useful References\n- d\n\n"
        "## Durable Context\n- a\n\n## Active Threads\n- b"
    )


def test_memory_doc_words_are_unchanged_only_reordered():
    reordered = render_memory_doc(_bundle(FILE_ORDER))
    as_today = render_text_prompt(_bundle(FILE_ORDER))  # the only doc -> today's rendering of it
    assert reordered != as_today
    assert _lines(reordered) == _lines(as_today)
    # paragraph multiset too: no paragraph split or merged
    assert sorted(reordered.split("\n\n")) == sorted(as_today.split("\n\n"))


def test_unknown_sections_follow_the_known_four_in_file_order():
    doc = "## Zeta\n- z\n\n## Active Threads\n- b\n\n## Alpha\n- a\n\n## Important Decisions\n- c\n"
    text = render_memory_doc(_bundle(doc))
    body = text.split("\n\n", 1)[1]  # after "### Memory\n<preamble>"
    assert body == "## Important Decisions\n- c\n\n## Active Threads\n- b\n\n## Zeta\n- z\n\n## Alpha\n- a"


def test_memory_doc_without_headings_renders_whole_and_unchanged():
    prose = "# Memory\n\njust prose, no sections\n"
    text = render_memory_doc(_bundle(prose))
    assert text.endswith("# Memory\n\njust prose, no sections")
    assert text == render_text_prompt(_bundle(prose))


def test_memory_doc_absent_or_empty_renders_empty():
    assert render_memory_doc(_bundle(None)) == ""
    assert render_memory_doc(_bundle("")) == ""
    assert render_memory_doc(_bundle("   \n")) == ""


def test_render_text_prompt_exclude_drops_only_the_named_doc():
    core = {"core_identity": IdentityDocument(doc_key="core_identity", path="c", frontmatter={}, content="# Core\nI am.")}
    memory = {"memory": IdentityDocument(doc_key="memory", path="m", frontmatter={}, content=FILE_ORDER)}
    bundle = CompiledIdentity(core=core, owner=memory)
    with_memory = render_text_prompt(bundle)
    without = render_text_prompt(bundle, exclude=("memory",))
    assert "### Memory" in with_memory and "## Active Threads" in with_memory
    assert "### Memory" not in without and "## Active Threads" not in without
    assert without == "### Core Identity\n# Core\nI am."
    assert render_text_prompt(bundle, exclude=()) == with_memory
    # the method on the bundle passes the exclusion through
    assert bundle.text_prompt(exclude=("memory",)) == without
    assert bundle.text_prompt() == with_memory

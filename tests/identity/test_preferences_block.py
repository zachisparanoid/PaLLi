"""Batch 5, task 7 (memory that picks, 2026-09-28; ruling R46): the
``preferences`` document reaches her replies. Reflect has written
``pref_<key>: <value>`` frontmatter rows to it since May (43 live), and no
prompt tuple ever rendered them. ``render_preferences_block`` renders them
as ``### Preferences`` + one ``- key: value`` line each, in document order,
whole lines only under a 2,000-char cap with a footer naming what was cut;
``_render_owner_memory`` appends it after the memory core, so
``owner_memory_text`` = core + preferences.
"""
from __future__ import annotations

from core.agent.context_adapter import _render_owner_memory
from core.identity.compiler import render_memory_core, render_preferences_block
from core.identity.models import CompiledIdentity, IdentityDocument
from core.turn.types import Turn, TurnContext, TurnMetadata


def _bundle(prefs: dict | None, *, memory: str | None = None) -> CompiledIdentity:
    owner = {}
    if prefs is not None:
        owner["preferences"] = IdentityDocument(
            doc_key="preferences", path="PREFERENCES.md", frontmatter=dict(prefs), content="# User Preferences\n",
        )
    if memory is not None:
        owner["memory"] = IdentityDocument(doc_key="memory", path="MEMORY.md", frontmatter={}, content=memory)
    return CompiledIdentity(owner=owner)


_TEMPLATE_KEYS = {
    "doc_type": "preferences", "platform": "owner", "user_id": 1, "version": 4,
    "last_updated_by": "system", "preferences_summary": "{preferences_summary}",
}


def test_renders_pref_keys_in_document_order_with_the_label():
    out = render_preferences_block(_bundle({
        **_TEMPLATE_KEYS,
        "pref_tea": "oolong, no milk",
        "pref_wake_time": "around 10",
        "pref_editor": "vim",
    }))
    assert out == "### Preferences\n- tea: oolong, no milk\n- wake_time: around 10\n- editor: vim"


def test_only_pref_keys_render_and_empty_values_are_skipped():
    out = render_preferences_block(_bundle({
        **_TEMPLATE_KEYS, "pref_tea": "oolong", "pref_blank": "", "pref_none": None, "pref_": "orphan",
        "pref_multi": "two\nlines  here",
    }))
    assert out == "### Preferences\n- tea: oolong\n- multi: two lines here"
    assert "preferences_summary" not in out and "version" not in out


def test_non_string_values_render_as_json_not_python_repr():
    """Fix round 1 (M3): str() gave `{'enabled': False}` and `True`."""
    out = render_preferences_block(_bundle({
        **_TEMPLATE_KEYS,
        "pref_consent": {"enabled": False, "source": "him"},
        "pref_flag": True,
        "pref_count": 3,
        "pref_ratio": 2.5,
        "pref_days": ["mon", "tue"],
        "pref_tea": "oolong,  no milk",
    }))
    assert out == (
        "### Preferences\n"
        '- consent: {"enabled": false, "source": "him"}\n'
        "- flag: true\n"
        "- count: 3\n"
        "- ratio: 2.5\n"
        '- days: ["mon", "tue"]\n'
        "- tea: oolong, no milk"
    )
    assert render_preferences_block(_bundle({**_TEMPLATE_KEYS, "pref_off": False})) == "### Preferences\n- off: false"


def test_absent_doc_or_no_pref_keys_renders_nothing():
    assert render_preferences_block(_bundle(None)) == ""
    assert render_preferences_block(_bundle(dict(_TEMPLATE_KEYS))) == ""   # the template ships no pref_ keys


def test_the_cap_cuts_whole_lines_in_order_and_names_what_was_cut():
    prefs = {**_TEMPLATE_KEYS, **{f"pref_key_{i:02d}": "v" * 60 for i in range(40)}}
    line = "- key_00: " + "v" * 60           # 70 chars; 71 with its newline
    out = render_preferences_block(_bundle(prefs), budget_chars=500)
    lines = out.split("\n")
    assert lines[0] == "### Preferences"
    shown = [ln for ln in lines[1:] if ln.startswith("- ")]
    # whole lines only, the first N in document order, nothing mid-line
    assert shown == [line.replace("key_00", f"key_{i:02d}") for i in range(len(shown))]
    assert all(len(ln) == 70 for ln in shown)
    assert len("### Preferences") + sum(len(ln) + 1 for ln in shown) <= 500
    assert len("### Preferences") + sum(len(ln) + 1 for ln in shown) + 71 > 500   # one more would not fit
    assert lines[-1] == f"({40 - len(shown)} more preferences not shown.)"
    # the default cap is 2000
    assert len(render_preferences_block(_bundle(prefs))) <= 2000 + len("(40 more preferences not shown.)") + 1


def test_no_footer_when_everything_fits():
    out = render_preferences_block(_bundle({**_TEMPLATE_KEYS, "pref_a": "1", "pref_b": "2"}))
    assert "not shown" not in out


_MEMORY = "# Memory\n\n## Durable Context\n- likes tea\n\n## Active Threads\n- [09-27] the boat\n"


def _ctx(bundle: CompiledIdentity) -> TurnContext:
    turn = Turn(surface="discord_text", owner_id=1, content="hi", metadata=TurnMetadata())
    return TurnContext(turn=turn, compiled_identity=bundle)


def test_owner_memory_text_is_the_core_then_the_preferences():
    bundle = _bundle({**_TEMPLATE_KEYS, "pref_tea": "oolong"}, memory=_MEMORY)
    core = render_memory_core(bundle, budget_chars=12000, refs={})
    assert _render_owner_memory(_ctx(bundle)) == core + "\n\n### Preferences\n- tea: oolong"


def test_owner_memory_text_without_preferences_is_the_core_alone():
    bundle = _bundle(dict(_TEMPLATE_KEYS), memory=_MEMORY)
    assert _render_owner_memory(_ctx(bundle)) == render_memory_core(bundle, budget_chars=12000, refs={})
    assert _render_owner_memory(_ctx(_bundle(None, memory=_MEMORY))) == render_memory_core(
        _bundle(None, memory=_MEMORY), budget_chars=12000, refs={},
    )


def test_owner_memory_text_with_preferences_but_no_memory_doc_is_the_preferences():
    bundle = _bundle({**_TEMPLATE_KEYS, "pref_tea": "oolong"})
    assert _render_owner_memory(_ctx(bundle)) == "### Preferences\n- tea: oolong"


def test_a_failing_preferences_render_leaves_the_core_in_place(monkeypatch):
    """Fix round 1 (M2): the preferences render shared the core's try, so a
    preferences failure blanked her whole memory band. Its own try now."""
    import core.identity.compiler as compiler
    from unittest.mock import MagicMock

    bundle = _bundle({**_TEMPLATE_KEYS, "pref_tea": "oolong"}, memory=_MEMORY)
    core = render_memory_core(bundle, budget_chars=12000, refs={})

    def boom(_bundle, **_kw):
        raise RuntimeError("preferences exploded")

    monkeypatch.setattr(compiler, "render_preferences_block", boom)
    assert _render_owner_memory(_ctx(bundle)) == core
    monkeypatch.setattr(compiler, "render_preferences_block", lambda _b, **_kw: MagicMock())
    assert _render_owner_memory(_ctx(bundle)) == core
    # and the core's own failure still blanks the slot, as before
    monkeypatch.setattr(compiler, "render_preferences_block", lambda _b, **_kw: "### Preferences\n- tea: oolong")

    def core_boom(*_a, **_kw):
        raise RuntimeError("core exploded")

    monkeypatch.setattr(compiler, "plan_memory_core", core_boom)
    assert _render_owner_memory(_ctx(bundle)) == ""

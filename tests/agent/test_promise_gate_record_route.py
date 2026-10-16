"""The failed-look nudge points at the record when the search tool is offered (R15, T11 + W3 part 1).

When a look came back empty, the note tells her where it would actually be.
Until now that was introspect with exact=true; with the memory-reach switch on
she is offered search_what_we_said, which reads every message in every chat, so
the note names that route instead. Wording is item 22 of the Memory That Reaches
words-for-the-owner list. Without the new kwarg the text is byte-identical to
before.

The runner seats use the same harness as test_im_not_sure.py: the REAL runner,
a fake llm_client.call, a fake tool registry, a fake judge.
"""
from __future__ import annotations

from core.agent import promise_gate as pg

FAILED_LOOK = (
    " Your look came back empty or with something else: that means you haven't found it, "
    "not that it's false. Say what you checked and that you couldn't confirm it, or look "
    "where it would actually be"
)
INTROSPECT_ROUTE = " (your own earlier messages are in your record: introspect with exact=true)."
SEARCH_ROUTE = " (what was said is in your record: search_what_we_said, with exact=true for the words)."


def _verdict(**kw):
    base = dict(empty_promise=False, ungrounded=True, unsupported="that the line isn't hers",
                why="the reminder holds a pointer, not the numbers", failed_look=True)
    base.update(kw)
    return pg.PromiseVerdict(**base)


def test_the_search_route_replaces_the_introspect_route_when_the_tool_is_offered():
    n = pg.nudge_text(_verdict(), can_act=True, can_search_record=True, can_introspect=True)
    assert FAILED_LOOK + SEARCH_ROUTE in n
    assert "introspect" not in n
    assert n.index("Say only what the results show") < n.index(FAILED_LOOK) < n.index("If you need to look again")
    assert n.endswith("Do not mention this note.]")


def test_without_the_search_tool_the_introspect_route_reads_as_today():
    n = pg.nudge_text(_verdict(), can_act=True, can_search_record=False, can_introspect=True)
    assert FAILED_LOOK + INTROSPECT_ROUTE in n
    assert "search_what_we_said" not in n


def test_with_neither_route_the_look_clause_just_ends():
    for kwargs in ({}, {"can_search_record": False, "can_introspect": False}):
        n = pg.nudge_text(_verdict(), can_act=True, **kwargs)
        assert FAILED_LOOK + "." in n
        assert "introspect" not in n and "search_what_we_said" not in n


def test_a_call_without_the_new_kwarg_is_byte_identical_to_todays_text():
    assert pg.nudge_text(_verdict(), can_act=True, can_introspect=True) == (
        "[system: your draft states that the line isn't hers as something you observed, but this turn's "
        "tool results do not show it — the reminder holds a pointer, not the numbers. Say only what the "
        "results show, plainly. Mention how you know something only when that would change what they'd do; "
        "if you didn't check, say so once, simply."
        + FAILED_LOOK + INTROSPECT_ROUTE
        + " If you need to look again, look again (tools are allowed). Do not mention this note.]"
    )
    assert pg.nudge_text(_verdict(), can_act=False) == (
        "[system: your draft states that the line isn't hers as something you observed, but this turn's "
        "tool results do not show it — the reminder holds a pointer, not the numbers. Say only what the "
        "results show, plainly. Mention how you know something only when that would change what they'd do; "
        "if you didn't check, say so once, simply."
        + FAILED_LOOK + ". Do not mention this note.]"
    )


# ── the runner seat (W3, part 1): the kwarg follows what the turn offers ───────

class _Registry:
    """Offers quick_tool plus the named record tools; quick_tool's look comes back empty."""

    def __init__(self, *record_tools):
        self._names = ["quick_tool", *record_tools]

    def all_names(self):
        return list(self._names)

    def schemas_for(self, ctx):
        return [{"name": n, "description": n, "input_schema": {"type": "object"}} for n in self._names]

    async def dispatch(self, tu, ctx):
        import core.agent.session as sess_mod
        return sess_mod.ToolResultBlock(tool_use_id=tu.id, content="(no matches)", is_error=False)


def _seat_note(tmp_path, monkeypatch, registry):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from tests.agent import test_grounding_check as g
    mod = g._load_runner()
    fake_call, calls = g._scripted_llm(llm_mod, sess_mod, [("tool", "looking"), "No, that isn't one of mine.",
                                                           "I can't confirm that: my search came up empty."])
    judge = g._judge_saying(_verdict())
    _result, delivered = g._run(mod, tmp_path, fake_call, monkeypatch, judge=judge, registry=registry)[:2]
    assert delivered == "I can't confirm that: my search came up empty."
    return str(calls[2].get("trailing_system_note") or "")


def test_a_failed_look_names_the_record_search_when_that_tool_is_offered(tmp_path, monkeypatch):
    note = _seat_note(tmp_path, monkeypatch, _Registry("introspect", "search_what_we_said"))
    assert FAILED_LOOK + SEARCH_ROUTE in note
    assert "introspect" not in note


def test_a_failed_look_names_introspect_when_only_introspect_is_offered(tmp_path, monkeypatch):
    note = _seat_note(tmp_path, monkeypatch, _Registry("introspect"))
    assert FAILED_LOOK + INTROSPECT_ROUTE in note
    assert "search_what_we_said" not in note

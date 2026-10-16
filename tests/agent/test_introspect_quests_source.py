# tests/agent/test_introspect_quests_source.py
"""The wishbook source: lived-experience label, closed rows included as
evidence, error-is-never-absence, flag-gated registration."""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import ai.quest_store as qs
from core.agent.builtin_tools.introspect import IntrospectTool


def _source(tmp_path, tokens=None, browse=True):
    # Flag PINNED on (the 3352e3d law): the live yaml's wanting key is an
    # environment, never test state — these tests must pass under any flip.
    tool = IntrospectTool()
    with patch.object(qs, "QUESTS_DIR", tmp_path), \
         patch(
             "core.turn.companion_flags.companion_settings",
             return_value=SimpleNamespace(wanting=True),
         ):
        return asyncio.run(tool._quests(list(tokens or []), browse))


def test_browse_lists_open_and_closed_with_status(tmp_path):
    q, _ = qs.plant(text="map the backups folder", source="hers", root=tmp_path)
    done, _ = qs.plant(text="the finished surprise", source="him-spoken", root=tmp_path)
    qs.set_status(done.id, "done", note="delivered it", root=tmp_path)
    src = _source(tmp_path)
    assert src.enabled and not src.error
    assert src.found == 2
    joined = "\n".join(src.entries)
    assert "map the backups folder" in joined
    assert "the finished surprise" in joined
    assert "done" in joined


def test_query_token_filters(tmp_path):
    qs.plant(text="learn the printer firmware", source="hers", root=tmp_path)
    qs.plant(text="reread the poem drafts", source="hers", root=tmp_path)
    src = _source(tmp_path, tokens=["printer"], browse=False)
    assert src.found == 1
    assert "printer" in src.entries[0]


def test_empty_book_is_honest_absence(tmp_path):
    src = _source(tmp_path)
    assert src.found == 0
    assert "wishbook" in src.absence


def test_store_failure_is_error_never_absence(tmp_path):
    with patch.object(qs, "load_quests", side_effect=RuntimeError("gone")):
        src = _source(tmp_path)
    assert src.error is True


def test_registration_is_flag_gated():
    # The execute() body registers _quests ONLY under companion.wanting —
    # a source pin (the plan's grep-pin form): the helper exists and the
    # gated append names the flag.
    body = inspect.getsource(IntrospectTool.execute)
    assert "_quests" in body
    assert "wanting" in body

# tests/agent/test_introspect_workbench_source.py
"""Her workbench as an introspect source (2026-08-18): the wall between her
working self and her talking self comes down on the MEMORY side. She can
look inward at her own sessions and logbook — "did I build something
called claw?" — instead of grepping a shell. Gated on companion.own_work."""
from __future__ import annotations

import asyncio
import inspect
import time
from types import SimpleNamespace
from unittest.mock import patch

import ai.workbench_store as ws
import ai.workbench_trace as wt
from core.agent.builtin_tools.introspect import IntrospectTool


def _source(tmp_path, tokens=None, browse=True, own_work=True):
    tool = IntrospectTool()
    with patch.object(ws, "WORKBENCH_DIR", tmp_path / "store"), \
         patch.object(wt, "WORKBENCH_MD", tmp_path / "WORKBENCH.md"), \
         patch(
             "core.turn.companion_flags.companion_settings",
             return_value=SimpleNamespace(own_work=own_work),
         ):
        return asyncio.run(tool._workbench(list(tokens or []), browse))


def _seed(tmp_path):
    ws.write_session(record={"started": time.time() - 3600, "duration_s": 900,
                             "signoff": "built the claw — a puzzle box",
                             "shared": "sent", "outcome": "ran"},
                     root=tmp_path / "store")
    ws.write_session(record={"started": time.time() - 7200, "duration_s": 600,
                             "signoff": "reading-7 on Dennett", "shared": False,
                             "outcome": "ran"},
                     root=tmp_path / "store")
    (tmp_path / "WORKBENCH.md").write_text(
        "# Workbench\n\n## 2026-08-14 — 4:14 AM session\n"
        "- **claw/** — a small machine with something inside. Committed as 91aa314.\n"
        "- **Active:** claw — built, tested.\n", encoding="utf-8")


def test_browse_lists_sessions_and_logbook_lines(tmp_path):
    _seed(tmp_path)
    src = _source(tmp_path)
    assert src.enabled and not src.error
    joined = "\n".join(src.entries)
    assert "built the claw" in joined
    assert "reading-7" in joined
    assert "small machine" in joined
    assert "91aa314" in joined
    assert src.found == len(src.entries) >= 3


def test_query_token_filters(tmp_path):
    _seed(tmp_path)
    src = _source(tmp_path, tokens=["dennett"], browse=False)
    assert src.found == 1 and "Dennett" in src.entries[0]


def test_empty_workbench_is_honest_absence(tmp_path):
    src = _source(tmp_path)
    assert src.found == 0 and "workbench" in src.absence


def test_store_failure_is_error_never_absence(tmp_path):
    with patch.object(ws, "read_recent", side_effect=RuntimeError("gone")):
        src = _source(tmp_path)
    assert src.error is True


def test_off_when_own_work_is_off(tmp_path):
    _seed(tmp_path)
    src = _source(tmp_path, own_work=False)
    assert src.enabled is False


def test_registration_is_flag_gated():
    body = inspect.getsource(IntrospectTool.execute)
    assert "_workbench" in body and "own_work" in body

# tests/ai/test_workbench_wanting_block.py
"""The seed's wanting block (re-spec §3 readers): present under the flag,
and OFF renders today's exact shape — no inserted bytes, the {wanting_block}
placeholder collapses to nothing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import ai.daydream_store as dds
import ai.quest_store as qs
import ai.workbench as wb


def _render(tmp_path, *, wanting: bool):
    comp = SimpleNamespace(wanting=wanting)
    with patch.object(wb, "companion_settings", return_value=comp), \
         patch.object(qs, "QUESTS_DIR", tmp_path / "quests"), \
         patch.object(dds, "DAYDREAM_DIR", tmp_path / "daydream"):
        return wb._render_session_prompt(
            root=tmp_path, sessions_left=2, active_max=2,
        )


def test_flag_on_shows_wishbook_and_intent(tmp_path):
    qs.plant(text="surprise him with a thing", source="him-spoken",
             root=tmp_path / "quests")
    dds.write_intent("pick the poem back up", root=tmp_path / "daydream")
    rendered = _render(tmp_path, wanting=True)
    assert "YOUR WISHBOOK" in rendered
    assert "surprise him with a thing" in rendered
    # 2026-08-18: the line is dated ("FROM YOUR LAST DAYDREAM (written just
    # now): …") — see test_session_prompt_dates_the_intent.
    assert "FROM YOUR LAST DAYDREAM" in rendered
    assert "pick the poem back up" in rendered
    assert "inspiration, never assignment" in rendered


def test_flag_off_renders_todays_exact_shape(tmp_path):
    rendered = _render(tmp_path, wanting=False)
    assert "WISHBOOK" not in rendered
    assert "DAYDREAM" not in rendered
    # The placeholder collapses to zero bytes: SUGGESTIONS block still
    # sits exactly one blank line above TODAY, as it does in production.
    assert "\n\nTODAY: " in rendered


def test_flag_on_without_intent_shows_no_intent_line(tmp_path):
    rendered = _render(tmp_path, wanting=True)
    assert "FROM YOUR LAST DAYDREAM" not in rendered
    assert "YOUR WISHBOOK" in rendered

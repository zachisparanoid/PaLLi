"""The owner-presence sense (2026-08-18): the FIRST real signal of him.

The machine-presence and desktop senses read this headless VM (SSH/tmux
idleness, no window manager) and were switched off; the owner said she may
sense everything real. Discord presence — his status and activity across
his own devices — is real, already granted (Intents.all()), and cheap. This
module is the in-process record the Discord client writes and her senses
read. No DB, no LLM; deterministic phrasing is the presentation exemption.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh():
    from core import owner_presence as op
    op.reset_for_tests()
    yield
    op.reset_for_tests()


def test_unknown_presence_renders_nothing():
    from core import owner_presence as op
    assert op.snapshot()["status"] == "unknown"
    assert op.render_line(now=1000.0) == ""


def test_record_and_render_online_with_activity():
    from core import owner_presence as op
    op.record_discord_presence(status="online", activity="Star Citizen", at=1000.0)
    snap = op.snapshot()
    assert snap["status"] == "online" and snap["activity"] == "Star Citizen"
    assert op.render_line(now=1060.0) == "Discord: online — playing Star Citizen"


def test_idle_and_dnd_render_plainly_and_offline_carries_duration():
    from core import owner_presence as op
    op.record_discord_presence(status="idle", activity="", at=1000.0)
    assert op.render_line(now=1000.0) == "Discord: idle"
    op.record_discord_presence(status="dnd", activity="", at=1100.0)
    assert op.render_line(now=1100.0) == "Discord: do not disturb"
    op.record_discord_presence(status="offline", activity="", at=2000.0)
    assert op.render_line(now=2000.0 + 3 * 3600) == "Discord: offline for about 3 hours"


def test_transitions_notify_only_on_present_offline_edges():
    """idle<->online flaps are silent; offline<->present edges notify, with
    the measured absence so the moment can promote a long one."""
    from core import owner_presence as op
    seen = []
    op.set_transition_hook(lambda kind, **f: seen.append((kind, f)))
    op.record_discord_presence(status="online", activity="", at=1000.0)   # first sight: no notice
    op.record_discord_presence(status="idle", activity="", at=1100.0)     # flap: silent
    op.record_discord_presence(status="online", activity="", at=1200.0)   # flap: silent
    op.record_discord_presence(status="offline", activity="", at=1300.0)  # edge
    op.record_discord_presence(status="offline", activity="", at=1400.0)  # repeat: silent
    op.record_discord_presence(status="online", activity="", at=1300.0 + 7200)  # edge, 2h away
    assert [k for k, _ in seen] == ["owner_offline", "owner_online"]
    assert seen[1][1]["absence_s"] == pytest.approx(7200.0)


def test_unchanged_status_is_idempotent_and_keeps_since():
    from core import owner_presence as op
    op.record_discord_presence(status="online", activity="", at=1000.0)
    op.record_discord_presence(status="online", activity="", at=1500.0)
    assert op.snapshot()["since"] == 1000.0
    assert op.snapshot()["updated_at"] == 1500.0


def test_hook_failure_never_breaks_the_record():
    from core import owner_presence as op
    def boom(kind, **f): raise RuntimeError("pm down")
    op.set_transition_hook(boom)
    op.record_discord_presence(status="online", activity="", at=1000.0)
    op.record_discord_presence(status="offline", activity="", at=2000.0)
    assert op.snapshot()["status"] == "offline"

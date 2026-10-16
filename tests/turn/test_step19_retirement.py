# tests/turn/test_step19_retirement.py
"""Step 19 (spec §7): the pre-arbiter lane is GONE — decide_surface,
consider_outreach, start_proactive_loop deleted; the arbiter is the one
door (its flag = the kill switch, DARK). The kept helpers still import."""
from pathlib import Path


def test_the_lane_is_gone():
    import core.turn.proactive as pro
    for name in ("start_proactive_loop", "consider_outreach", "_arbiter_owns_surfacing",
                 "_consider_outreach_via_open_threads", "_synthesize_and_deliver_open_thread_surface",
                 "_resolve_default_interval_seconds", "DEFAULT_PROACTIVE_INTERVAL_SECONDS"):
        assert not hasattr(pro, name), name
    assert not Path("core/turn/surface_decision.py").exists()


def test_the_kept_helpers_survive():
    import core.turn.proactive as pro
    for name in ("run_outreach_residuals", "_pick_proactive_channel_id",
                 "_safe_last_user_message_at", "_ensure_stake_threads",
                 "_maybe_run_arbiter_sweep", "_consider_candidate_rail",
                 "consider_proactive_action", "_deliver_proactive_to_discord",
                 "_transcribe_proactive_utterance", "_outreach_allowed",
                 "_load_unconsumed_candidate", "_record_candidate_consumption"):
        assert hasattr(pro, name), name


def test_client_no_longer_spawns_the_loop():
    text = Path("bots/discord/client.py").read_text(encoding="utf-8")
    assert "start_proactive_loop" not in text

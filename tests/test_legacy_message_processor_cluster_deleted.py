"""After C7, the legacy message_processor cluster must not be importable.

C7 (Phase C, 2026-05-13) deleted the dead-on-the-vine message_processor
cluster (12,238 LOC across 21 files + ~9,931 LOC of tests). The new spine
(core/turn/turn.process_turn) handles all live message flow.

Live entry points: bots/discord/client.py and voice/runtime.py both call
process_turn directly. PALLI_AGENT_LOOP=pipeline|react chooses between
process_turn and runner.run_turn; neither touches message_processor.
"""
import pytest


@pytest.mark.parametrize("module", [
    "core.message_processor",
    "core.message_additional_context",
    "core.message_assistant_response",
    "core.message_background_analytics",
    "core.message_generation",
    "core.message_ingest_coordination",
    "core.message_operator_routing",
    "core.message_pre_generation_gatekeeping",
    "core.message_pre_generation_routing",
    "core.message_queue_control",
    "core.message_reminder_response",
    "core.message_reply_preparation",
    "core.message_response_delivery",
    "core.message_response_execution",
    "core.message_response_metadata",
    "core.message_runtime_context",
    "core.message_settings_handler",
    "core.message_should_respond",
    "core.message_splitter",
    "core.message_turn_orchestration",
    "core.message_turn_preload",
])
def test_legacy_message_module_deleted(module):
    with pytest.raises(ImportError):
        __import__(module)

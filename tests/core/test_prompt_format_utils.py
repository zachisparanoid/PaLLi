from __future__ import annotations

from core.prompt_format import format_prompt, with_prompt_aliases


def test_with_prompt_aliases_message_and_history():
    values = {
        "user_message": "hello",
        "conversation_history": "history",
        "channel_name": "general",
        "username": "alice",
    }
    aliased = with_prompt_aliases(values)
    assert aliased["current_message"] == "hello"
    assert aliased["message"] == "hello"
    assert aliased["history_context"] == "history"
    assert aliased["channel"] == "general"
    assert aliased["user"] == "alice"


def test_format_prompt_uses_aliases():
    template = "msg={current_message}; user={user_message}; channel={channel}"
    rendered = format_prompt(template, message="yo", channel_name="dm")
    assert rendered == "msg=yo; user=yo; channel=dm"

"""2026-09-02, the desktop-Ollama evening: the text-tag adapter (the runner's
path for providers without an api_key) only recognised its own instructed
shape, ``<tool_call name="x">{...}</tool_call>``. Qwen (and every
Hermes-format model) is trained to write ``<tool_call>{"name": "x",
"arguments": {...}}</tool_call>``; a call in that shape fell through as
plain text — a tool call the runner never saw. Both shapes must parse."""
from __future__ import annotations

import json

from core.agent.sdk_adapter import _parse_response_text


def test_the_instructed_attribute_shape_still_parses():
    text, calls = _parse_response_text('hold on <tool_call name="read_file">{"path": "a.txt"}</tool_call>')
    assert text.strip() == "hold on"
    assert [(c["name"], json.loads(c["arguments"])) for c in calls] == [("read_file", {"path": "a.txt"})]


def test_the_qwen_native_shape_parses_to_the_same_call():
    raw = 'on it\n<tool_call>\n{"name": "local_shell", "arguments": {"command": "ls -la /tmp/pal_kit"}}\n</tool_call>'
    text, calls = _parse_response_text(raw)
    assert text.strip() == "on it"
    assert len(calls) == 1 and calls[0]["name"] == "local_shell"
    assert json.loads(calls[0]["arguments"]) == {"command": "ls -la /tmp/pal_kit"}
    assert calls[0]["id"].startswith("call_")


def test_native_shape_with_string_arguments_and_two_calls():
    raw = ('<tool_call>{"name": "read_file", "arguments": "{\\"path\\": \\"x\\"}"}</tool_call>'
           '<tool_call>{"name": "note_to_self", "arguments": {}}</tool_call>')
    text, calls = _parse_response_text(raw)
    assert text == ""
    assert [c["name"] for c in calls] == ["read_file", "note_to_self"]
    assert json.loads(calls[0]["arguments"]) == {"path": "x"} and calls[1]["arguments"] == "{}"


def test_a_native_block_without_a_name_stays_in_the_text():
    raw = '<tool_call>{"arguments": {"command": "ls"}}</tool_call>'
    text, calls = _parse_response_text(raw)
    assert calls == [] and "<tool_call>" in text


def test_malformed_json_in_either_shape_stays_in_the_text():
    for raw in ('<tool_call name="x">{not json</tool_call>', '<tool_call>{not json</tool_call>'):
        text, calls = _parse_response_text(raw)
        assert calls == [] and raw in text

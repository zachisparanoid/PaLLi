from __future__ import annotations

import pytest

from core import llm_json


def test_strip_markdown_fences():
    raw = "```json\n{\"a\": 1}\n```"
    assert llm_json.strip_markdown_fences(raw) == '{"a": 1}'


def test_extract_first_json_snippet_from_wrapped_text():
    raw = 'prefix text {"a": {"b": "x}y"}} trailing'
    snippet = llm_json.extract_first_json_snippet(raw)
    assert snippet == '{"a": {"b": "x}y"}}'


def test_parse_json_object_and_array():
    assert llm_json.parse_json_object('{"ok": true}') == {"ok": True}
    assert llm_json.parse_json_array("[1,2,3]") == [1, 2, 3]


def test_parse_json_object_type_error():
    with pytest.raises(TypeError):
        llm_json.parse_json_object("[1,2,3]")


def test_safe_parse_helpers():
    assert llm_json.safe_parse_json_object("not json", default={"fallback": 1}) == {"fallback": 1}
    assert llm_json.safe_parse_json("not json", default=[]) == []

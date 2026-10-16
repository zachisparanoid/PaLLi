"""
Utilities for parsing structured (JSON) outputs from LLMs.

LLMs sometimes wrap JSON in markdown fences or include extra text. These helpers:
- strip common markdown fences
- extract the first valid JSON object/array substring
- parse via json.loads()
- optionally validate with Pydantic models
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Type, TypeVar

try:
    from pydantic import BaseModel
except Exception:  # pragma: no cover
    BaseModel = object  # type: ignore

T = TypeVar("T", bound="BaseModel")


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", re.MULTILINE)


def strip_markdown_fences(text: str) -> str:
    if not isinstance(text, str):
        return ""
    cleaned = text.strip()
    if "```" not in cleaned:
        return cleaned
    # Remove leading ```json / ``` and trailing ```
    cleaned = _FENCE_RE.sub("", cleaned).strip()
    return cleaned


def extract_first_json_snippet(text: str) -> Optional[str]:
    """
    Best-effort extraction of the first JSON object/array from a text blob.
    Returns the JSON substring, or None if no JSON-looking start is found.
    """
    s = strip_markdown_fences(text)
    if not s:
        return None

    obj_i = s.find("{")
    arr_i = s.find("[")
    if obj_i == -1 and arr_i == -1:
        return None

    if obj_i == -1:
        start = arr_i
        opener, closer = "[", "]"
    elif arr_i == -1:
        start = obj_i
        opener, closer = "{", "}"
    else:
        # Choose whichever appears first in the string
        if obj_i < arr_i:
            start = obj_i
            opener, closer = "{", "}"
        else:
            start = arr_i
            opener, closer = "[", "]"

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(s)):
        ch = s[i]

        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == opener:
            depth += 1
            continue

        if ch == closer:
            depth -= 1
            if depth == 0:
                return s[start : i + 1].strip()

    # Unbalanced JSON; give the remainder as a last resort
    return s[start:].strip() or None


def parse_json(text: str) -> Any:
    snippet = extract_first_json_snippet(text)
    if snippet is None:
        raise ValueError("No JSON snippet found")
    return json.loads(snippet)


def parse_json_object(text: str) -> dict:
    data = parse_json(text)
    if not isinstance(data, dict):
        raise TypeError(f"Expected JSON object, got {type(data).__name__}")
    return data


def parse_json_array(text: str) -> list:
    data = parse_json(text)
    if not isinstance(data, list):
        raise TypeError(f"Expected JSON array, got {type(data).__name__}")
    return data


def parse_json_model(text: str, model_cls: Type[T]) -> T:
    """
    Parse the first JSON snippet and validate it using a Pydantic model.
    """
    data = parse_json(text)
    if not hasattr(model_cls, "model_validate"):
        # Pydantic v1 fallback
        return model_cls.parse_obj(data)  # type: ignore[attr-defined]
    return model_cls.model_validate(data)  # type: ignore[no-any-return]


def safe_parse_json_object(text: str, default: Optional[dict] = None) -> Optional[dict]:
    try:
        return parse_json_object(text)
    except Exception:
        return default


def safe_parse_json(text: str, default: Any = None) -> Any:
    try:
        return parse_json(text)
    except Exception:
        return default

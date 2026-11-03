from __future__ import annotations

import json
from typing import Any, Dict, Tuple


def parse_frontmatter(markdown_text: str) -> Tuple[Dict[str, Any], str]:
    text = markdown_text or ""
    if not text.startswith("---\n"):
        return {}, text

    end_marker = "\n---\n"
    end_idx = text.find(end_marker, 4)
    if end_idx == -1:
        return {}, text

    raw_frontmatter = text[4:end_idx]
    body = text[end_idx + len(end_marker):]
    data: Dict[str, Any] = {}

    for raw_line in raw_frontmatter.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        data[key.strip()] = _parse_scalar(raw_value.strip())

    return data, body.lstrip("\n")


def dump_frontmatter(frontmatter: Dict[str, Any], content: str) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {_format_scalar(value)}")
    lines.append("---")
    lines.append("")
    body = (content or "").rstrip()
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


def _parse_scalar(value: str) -> Any:
    if value == "":
        return ""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null":
        return None
    if value.startswith(("[", "{", "\"")):
        try:
            return json.loads(value)
        except Exception:
            return value.strip("\"")
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except Exception:
        return value.strip("\"")


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=True)
    text = str(value)
    if text == "":
        return "\"\""
    if any(ch in text for ch in [":", "#", "[", "]", "{", "}", ",", "\""]):
        return json.dumps(text, ensure_ascii=True)
    return text

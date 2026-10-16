"""Shared helpers for tests/core.

The live ``data/palli.yaml`` is the owner's evolving config, not a
frozen seed — tests that pin specific routing/model/knob values must
not load it. ``write_hermetic_settings_yaml`` writes a minimal,
self-contained settings document so those tests assert behavior
against values they control.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_ROUTING_TASKS = (
    "response", "analysis", "decision", "memory",
    "proactive", "reflection", "web_search", "vision",
)


def hermetic_settings_doc(
    *,
    response_provider: str = "openai",
    response_model: str = "gpt-5-mini",
    features: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a minimal valid PalliSettings document.

    Curated models: openai → gpt-4o, gpt-4o-mini, gpt-5-mini;
    deepseek → deepseek-chat, deepseek-reasoner. All routing lanes
    default to openai/gpt-4o except ``response`` (parameterized).
    """
    default_target = {"provider": "openai", "model": "gpt-4o"}
    routing = {task: dict(default_target) for task in _ROUTING_TASKS}
    routing["response"] = {"provider": response_provider, "model": response_model}
    doc: Dict[str, Any] = {
        "version": 1,
        "providers": {
            "openai": {
                "models": [
                    {"id": "gpt-4o"},
                    {"id": "gpt-4o-mini"},
                    {"id": "gpt-5-mini"},
                ],
            },
            "deepseek": {
                "models": [
                    {"id": "deepseek-chat"},
                    {"id": "deepseek-reasoner"},
                ],
            },
        },
        "llm": {
            "default_provider": "openai",
            "routing": routing,
        },
        "voice": {
            "engine": "classic",
            "stt_provider": "faster_whisper",
            "tts_provider": "pyttsx3",
            "interaction_mode": "direct_address",
        },
        "policy": {
            "proactive": {"max_per_day": 5, "min_gap_hours": 2},
            "operator": {},
        },
    }
    if features is not None:
        doc["features"] = dict(features)
    return doc


def write_hermetic_settings_yaml(target: Path, **kwargs: Any) -> Path:
    """Write a hermetic settings doc to ``target`` and return it."""
    doc = hermetic_settings_doc(**kwargs)
    target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return target

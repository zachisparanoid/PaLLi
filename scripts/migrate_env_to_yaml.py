#!/usr/bin/env python3
"""One-shot migration: read .env.local LLM routing and populate data/palli.yaml.

Usage:
    python scripts/migrate_env_to_yaml.py              # dry-run (show what would change)
    python scripts/migrate_env_to_yaml.py --apply       # actually write changes

Safe to run multiple times. Only overwrites fields it finds in .env.local;
fields already set in the YAML that aren't in .env.local are preserved.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure repo root is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values  # noqa: E402

from core.settings_service import SettingsService, SettingsServiceError  # noqa: E402


YAML_PATH = ROOT / "data" / "palli.yaml"
ENV_LOCAL_PATH = ROOT / ".env.local"

# Map of .env keys → settings dot-paths
ROUTING_MAP = {
    "LLM_PROVIDER": "llm.default_provider",
    "LLM_RESPONSE_PROVIDER": "llm.routing.response.provider",
    "LLM_RESPONSE_MODEL": "llm.routing.response.model",
    "LLM_ANALYSIS_PROVIDER": "llm.routing.analysis.provider",
    "LLM_ANALYSIS_MODEL": "llm.routing.analysis.model",
    "LLM_MEMORY_PROVIDER": "llm.routing.memory.provider",
    "LLM_MEMORY_MODEL": "llm.routing.memory.model",
    "LLM_DECISION_PROVIDER": "llm.routing.decision.provider",
    "LLM_DECISION_MODEL": "llm.routing.decision.model",
    "LLM_PROACTIVE_PROVIDER": "llm.routing.proactive.provider",
    "LLM_PROACTIVE_MODEL": "llm.routing.proactive.model",
    "LLM_REFLECTION_PROVIDER": "llm.routing.reflection.provider",
    "LLM_REFLECTION_MODEL": "llm.routing.reflection.model",
    "LLM_WEB_SEARCH_PROVIDER": "llm.routing.web_search.provider",
    "LLM_WEB_SEARCH_MODEL": "llm.routing.web_search.model",
    "LLM_VISION_PROVIDER": "llm.routing.vision.provider",
    "LLM_VISION_MODEL": "llm.routing.vision.model",
}


def build_changes() -> dict[str, str]:
    """Read .env.local and build the settings dot-path changes."""
    if not ENV_LOCAL_PATH.exists():
        print(f"No .env.local found at {ENV_LOCAL_PATH}; nothing to migrate.")
        return {}

    env_values = dotenv_values(ENV_LOCAL_PATH)
    changes: dict[str, str] = {}

    for env_key, dot_path in ROUTING_MAP.items():
        value = str(env_values.get(env_key) or "").strip()
        if value:
            changes[dot_path] = value

    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate .env.local LLM routing to data/palli.yaml")
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run)")
    args = parser.parse_args()

    changes = build_changes()
    if not changes:
        print("No LLM routing values found in .env.local. Nothing to do.")
        return

    print(f"Found {len(changes)} routing values in .env.local:")
    for path, value in sorted(changes.items()):
        print(f"  {path} = {value}")

    if not args.apply:
        print("\nDry run. Pass --apply to write these to data/palli.yaml.")
        return

    svc = SettingsService(yaml_path=YAML_PATH)
    svc.load()

    try:
        proposal = svc.propose(changes, reason="migrate from .env.local")
    except SettingsServiceError as exc:
        print(f"\nMigration failed validation: {exc}")
        print("The .env.local values may reference providers or models not in the YAML catalog.")
        print("Add them to data/palli.yaml manually, then re-run this script.")
        sys.exit(1)

    if not proposal.diff:
        print("\nNo changes needed — YAML already matches .env.local.")
        return

    print(f"\nApplying {len(proposal.diff)} changes:")
    for entry in proposal.diff:
        print(f"  {entry['path']}: {entry['old']} → {entry['new']}")

    result = svc.apply(proposal.change_id)
    print(f"\nDone. Rollback token: {result.rollback_token}")
    print("Settings will take effect on next PaLLi startup (or immediately if PaLLi is running with hot-reload).")


if __name__ == "__main__":
    main()

"""Utility for loading prompts from the prompts directory."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Every prompt the running code can ask for, grouped by the subsystem that
# owns it. This is the startup banner's whole idea of a healthy prompt set:
# a name here with no file logs [error] at boot, which is the point — so it
# must stay independent of `prompts/*.txt` rather than be globbed from it.
# Kept honest by TestEveryUsedPromptIsWatched (tests/ai/test_prompt_loader.py),
# which walks every load_prompt() call and fails the gate on drift in either
# direction. Add your prompt here when you add its organ. Prompts nothing
# loads any more live in prompts/archive/ (Batch 8, 2026-10-01), which no
# loader reads.
EXPECTED_PROMPTS = [
    # --- the turn spine: he is waiting on these ---
    "promise_check",
    "turn_read",
    "first_look",
    "memory_reach_widen",
    "memory_reach_keep",
    "turn_reflect",
    "tonight_said",
    "stake_said",
    "own_life_room_check",
    "reply_govern",
    "steering_judgment",
    "steering_supersede",
    "ambient_arrival_generation",
    # --- perception ---
    "vision_analysis",
    "voice_interjection_decision",
    # --- inner life, between conversations ---
    "inner_monologue",
    "inner_monologue_wanting",
    "monologue_weekly_digest",
    "monologue_monthly_digest",
    "present_moment_appraisal",
    "present_moment_appraisal_affect",
    "present_moment_appraisal_arbiter",
    "present_moment_appraisal_wanting",
    "daydream",
    "workbench_session",
    "curiosity_research",
    "arbiter_freshness",
    "group_opener",
    "owner_picture",
    "night_cycle_disclosure_harvest",
    # --- the night cycle (06:30 since 2026-10-05; 04:00 before) ---
    "night_cycle_daily_meaning",
    "consolidation_day_gist",
    "consolidation_day_gist_continuity",
    "consolidation_fact_gist",
    "consolidation_reconcile",
    "night_cycle_views",
    "night_cycle_callbacks_harvest",
    "tonight_pick",
    # --- selfhood organs ---
    "stake_appraisal",
    "stake_review",
    "narrative_self_gate",
    "narrative_self_my_story",
    "narrative_self_story_of_us",
    "narrative_self_care_spine",
    "narrative_self_voice_identity",
    "narrative_fears_relook",
    "passions_nightly",
    "passions_interests",
    "fears_first_authorship",
    # --- measuring, by hand ---
    "presence_judge",
    "memory_reach_judge",
]


class PromptLoader:
    """Centralized prompt loading with caching."""

    def __init__(self, prompts_dir: str = "prompts"):
        self.prompts_dir = prompts_dir
        self._cache: Dict[str, str] = {}

    def load_prompt(self, prompt_name: str) -> Optional[str]:
        """
        Load a prompt from file.

        Args:
            prompt_name: Name of prompt file (without .txt extension)

        Returns:
            Prompt content as string, or None if not found
        """
        if prompt_name in self._cache:
            return self._cache[prompt_name]

        prompt_path = os.path.join(self.prompts_dir, f"{prompt_name}.txt")

        try:
            if os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as handle:
                    content = handle.read().strip()
                    if content:
                        self._cache[prompt_name] = content
                        logger.debug("Loaded prompt: %s", prompt_name)
                        return content
                    logger.warning("Prompt file %s.txt is empty", prompt_name)
                    return None

            logger.warning("Prompt file not found: %s", prompt_path)
            return None
        except Exception as exc:
            logger.error("Error loading prompt %s: %s", prompt_name, exc)
            return None

    def reload_prompt(self, prompt_name: str) -> Optional[str]:
        """Reload a prompt from file, bypassing cache."""
        if prompt_name in self._cache:
            del self._cache[prompt_name]
        return self.load_prompt(prompt_name)

    def clear_cache(self) -> None:
        """Clear all cached prompts."""
        self._cache.clear()
        logger.info("Prompt cache cleared")

    def inspect_prompt_inventory(
        self,
        expected_prompts: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        """
        Inspect prompt-file readiness without mutating the cache.

        Returns a structured inventory used by startup preflight and diagnostics.
        """
        prompt_names = list(expected_prompts or EXPECTED_PROMPTS)
        status: Dict[str, bool] = {}
        missing: list[str] = []
        empty: list[str] = []
        errors: Dict[str, str] = {}

        for prompt_name in prompt_names:
            prompt_path = os.path.join(self.prompts_dir, f"{prompt_name}.txt")
            if not os.path.exists(prompt_path):
                status[prompt_name] = False
                missing.append(prompt_name)
                continue

            try:
                with open(prompt_path, "r", encoding="utf-8") as handle:
                    content = handle.read().strip()
            except Exception as exc:
                status[prompt_name] = False
                errors[prompt_name] = f"{type(exc).__name__}: {exc}"
                continue

            if content:
                status[prompt_name] = True
            else:
                status[prompt_name] = False
                empty.append(prompt_name)

        return {
            "expected_prompts": prompt_names,
            "status": status,
            "missing": missing,
            "empty": empty,
            "errors": errors,
            "loaded_count": sum(1 for loaded in status.values() if loaded),
            "total_count": len(prompt_names),
        }

    def log_all_prompts(self) -> Dict[str, bool]:
        """
        Log the status of all expected prompt files at startup.

        Returns:
            Dict mapping prompt names to loaded status (True/False)
        """
        inventory = self.inspect_prompt_inventory()
        expected_prompts = list(inventory.get("expected_prompts") or [])
        status = dict(inventory.get("status") or {})
        missing = set(inventory.get("missing") or [])
        empty = set(inventory.get("empty") or [])
        errors = dict(inventory.get("errors") or {})

        logger.info("=" * 60)
        logger.info("PROMPT FILES LOADING STATUS")
        logger.info("=" * 60)

        for prompt_name in expected_prompts:
            if prompt_name in errors:
                logger.error("[error] %s.txt - %s", prompt_name, errors[prompt_name])
            elif prompt_name in missing:
                logger.error("[error] %s.txt - file not found", prompt_name)
            elif prompt_name in empty:
                logger.warning("[warning] %s.txt - empty file", prompt_name)
            else:
                logger.info("[ok] %s.txt - loaded", prompt_name)

        logger.info("=" * 60)
        logger.info(
            "TOTAL: %s/%s prompts loaded successfully",
            int(inventory.get("loaded_count") or 0),
            int(inventory.get("total_count") or len(expected_prompts)),
        )
        logger.info("=" * 60)

        return status


# Global prompt loader instance
prompt_loader = PromptLoader()

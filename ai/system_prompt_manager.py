"""
System prompt management for preset loading.
"""

import os
import json
import logging
from typing import Optional, Dict
from core.identity import identity_service

logger = logging.getLogger(__name__)


class SystemPromptManager:
    """Manager for loading and reloading system prompt presets."""

    def __init__(self):
        self.preset_prompts = {}
        self._load_preset_prompts()

    def _load_preset_prompts(self):
        """Load preset prompts from JSON file"""
        try:
            preset_file = "prompts/preset_prompts.json"
            if os.path.exists(preset_file):
                with open(preset_file, "r", encoding="utf-8") as f:
                    self.preset_prompts = json.load(f)
                    logger.info(f"✅ Loaded {len(self.preset_prompts)} preset prompts")
            else:
                logger.warning("⚠️ preset_prompts.json not found - using basic defaults")
                self.preset_prompts = {
                    "default": "You are PaLLi, a Personal AI Learning & Listening Assistant."
                }
        except Exception as e:
            logger.error(f"❌ Error loading preset prompts: {e}")
            self.preset_prompts = {
                "default": "You are PaLLi, a Personal AI Learning & Listening Assistant."
            }

    def reload_presets(self):
        """Reload preset prompts from file"""
        self._load_preset_prompts()

    async def get_system_prompt(self, user_id: Optional[int] = None, platform: str = "discord") -> str:
        """Get the current system prompt for a user"""
        base_prompt = ""
        profile = None
        # Check if user has a preset selection in database
        if user_id:
            try:
                from core.database import brain
                profile = await brain.get_user_profile(user_id, platform)
                preset_name = profile.get("personality_preset")

                if preset_name and preset_name in self.preset_prompts:
                    base_prompt = self.preset_prompts[preset_name]
            except Exception as e:
                logger.error(f"Error getting user preset from database: {e}")

        # Fall back to default preset
        if not base_prompt and "default" in self.preset_prompts:
            base_prompt = self.preset_prompts["default"]

        # Final fallback
        if not base_prompt:
            base_prompt = (
                "You are PaLLi, a Personal AI Learning & Listening Assistant. "
                "You are empathetic, adaptive, and learn from each interaction to provide "
                "increasingly personalized experiences. You maintain conversation context "
                "and build meaningful relationships with users."
            )
        identity_block = ""
        if user_id and identity_service.identity_enabled():
            try:
                identity_context = await identity_service.compose_prompt_context(
                    user_id=user_id,
                    platform=platform,
                    modality="text",
                    task_type="response",
                    profile=profile,
                )
                if identity_context:
                    identity_block = "\n\n## Identity Context\n" + identity_context
            except Exception as exc:
                logger.error(f"Error composing identity prompt context: {exc}")

        return base_prompt + identity_block

    async def set_user_preset(self, user_id: int, platform: str, preset_name: str) -> bool:
        """Set a user's system prompt preset in the database"""
        if preset_name in self.preset_prompts:
            try:
                from core.database import brain
                # Update the user's profile with the new preset
                await brain.update_user_profile(user_id, platform, {"personality_preset": preset_name})
                logger.info(f"✅ User {user_id} switched to '{preset_name}' preset")
                return True
            except Exception as e:
                logger.error(f"Error setting user preset in database: {e}")
                return False
        return False

    async def get_user_preset(self, user_id: int, platform: str = "discord") -> Optional[str]:
        """Get a user's current preset name from database"""
        try:
            from core.database import brain
            profile = await brain.get_user_profile(user_id, platform)
            return profile.get("personality_preset")
        except Exception as e:
            logger.error(f"Error getting user preset from database: {e}")
            return None

    def get_available_presets(self) -> Dict[str, str]:
        """Get all available preset prompts"""
        return self.preset_prompts.copy()

# Global system prompt manager instance
system_prompt_manager = SystemPromptManager()

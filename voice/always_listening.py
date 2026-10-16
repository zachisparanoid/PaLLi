"""
Voice runtime facade used by Discord command handlers.
"""

from __future__ import annotations

import logging
from typing import Dict

from discord.ext import voice_recv

from voice.bootstrap import require_voice_runtime_ready
from voice.control import voice_session_control_service
from voice.discord_transport import DiscordVoiceTransport
from voice.preferences import get_shared_voice_preferences
from voice.runtime import VoiceRuntimeManager

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")


class VoiceProcessor:
    """Thin compatibility facade around the platform-neutral voice runtime."""

    def __init__(self) -> None:
        self.runtime = VoiceRuntimeManager()

    async def start_listening(
        self,
        voice_client: voice_recv.VoiceRecvClient,
        channel_id: int,
        text_channel_id: int,
        initiator_user_id: int | None = None,
        account_user_id: int | None = None,
        account_features: Dict[str, bool] | None = None,
    ) -> bool:
        status = require_voice_runtime_ready()
        if not status.ok:
            essential_logger.error("❌ Voice runtime unavailable: %s", "; ".join(status.messages))
            return False

        if not voice_client or not voice_client.is_connected():
            essential_logger.error("❌ Voice client is not connected")
            return False

        transport = DiscordVoiceTransport(
            voice_client=voice_client,
            text_channel_id=text_channel_id,
        )
        await self.runtime.start_session(
            channel_id=channel_id,
            text_channel_id=text_channel_id,
            transport=transport,
            initiator_user_id=initiator_user_id,
            account_user_id=account_user_id,
            account_features=account_features,
        )
        _, prefs = await get_shared_voice_preferences(account_user_id=account_user_id)
        session = self.runtime.get_session_by_channel(channel_id, platform="discord")
        if session and session.runtime_session_key:
            voice_session_control_service.apply_updates(
                runtime_session_key=session.runtime_session_key,
                updates=prefs,
            )
        else:
            self.runtime.apply_voice_preferences(channel_id, prefs)
        essential_logger.info("✅ Voice runtime started for channel %s", channel_id)
        return True

    def set_listening_personality(self, channel_id: int, personality: str) -> bool:
        return self.runtime.set_listening_personality(channel_id, personality)

    def set_interaction_mode(self, channel_id: int, interaction_mode: str) -> bool:
        return self.runtime.set_interaction_mode(channel_id, interaction_mode)

    def set_presence_profile(self, channel_id: int, presence_profile: str) -> bool:
        return self.runtime.set_presence_profile(channel_id, presence_profile)

    def set_focus_state(self, channel_id: int, focus_state: str) -> bool:
        return self.runtime.set_focus_state(channel_id, focus_state)

    def get_session_snapshot(self, channel_id: int) -> dict | None:
        return self.runtime.get_session_snapshot(channel_id)

    async def stop_listening(self, channel_id: int) -> bool:
        return await self.runtime.stop_session(channel_id)

    async def stop_all_sessions(self) -> None:
        await self.runtime.stop_all_sessions()

    def get_session_count(self) -> int:
        return self.runtime.get_session_count()

    def is_session_active(self, channel_id: int) -> bool:
        return self.runtime.is_session_active(channel_id)


voice_processor = VoiceProcessor()

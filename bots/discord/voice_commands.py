"""Voice slash-commands for the slim PalliDiscordClient (v1 rewire, 2026-07-11).

The voice conversation runtime (voice/) was parked for exactly one reason:
its only entry point — setup_commands in bots/discord/discord_commands.py —
requires a commands.Bot and was never called by the live plain
discord.Client surface. This module is that entry point re-homed: it
registers ONLY the voice subset on the app_commands.CommandTree owned by
PalliDiscordClient (bots/discord/client.py).

Deliberately NOT ported (v1 scope — see docs/superpowers/specs/
2026-07-11-voice-v1-rewire-design.md): voice_personality (legacy alias of
voice_presence), reset_voice (references unported legacy), link_server /
unlink / proactive_* / toggle_* (not voice; several touch bot.proactive_agent,
which the slim client doesn't have).
"""
from __future__ import annotations

import logging
import time
import traceback
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import voice_recv

from bots.common.commands import owner_only_line, resolve_caller
from core.config import config
# Module-level in the parked file and verified importable; imported rather
# than duplicated — it owns the guild→account DB lookups.
from bots.discord.discord_commands import resolve_voice_account_context_for_guild
from voice.always_listening import voice_processor
from voice.bootstrap import (
    describe_voice_connection_state,
    require_voice_runtime_ready,
)
from voice.control import voice_session_control_service
from voice.preferences import (
    get_shared_voice_preferences,
    update_shared_voice_preferences,
)

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")


# ── preference helpers (ported from setup_commands' closures,
#    discord_commands.py:98-146) ─────────────────────────────────────────

async def _resolve_voice_account_user_id(
    interaction: discord.Interaction,
) -> Optional[int]:
    if interaction.guild is None or interaction.channel is None:
        return None
    try:
        account_user_id, _, block_reason = await resolve_voice_account_context_for_guild(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
        )
        if block_reason:
            return None
        return account_user_id
    except Exception:
        logger.debug("Failed to resolve voice account context", exc_info=True)
        return None


async def _persist_voice_preferences(
    interaction: discord.Interaction,
    *,
    updates: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    account_user_id = await _resolve_voice_account_user_id(interaction)
    return await update_shared_voice_preferences(
        updates=updates,
        actor_type="user",
        actor_id=str(interaction.user.id),
        reason=reason,
        account_user_id=account_user_id,
    )


async def _get_saved_voice_preferences(
    interaction: discord.Interaction,
) -> Dict[str, Any]:
    account_user_id = await _resolve_voice_account_user_id(interaction)
    _, prefs = await get_shared_voice_preferences(account_user_id=account_user_id)
    return prefs


async def _maybe_apply_live_voice_preferences(
    interaction: discord.Interaction,
    prefs: Dict[str, Any],
) -> bool:
    if (
        not interaction.guild
        or not interaction.guild.voice_client
        or not interaction.guild.voice_client.channel
    ):
        return False
    snapshot = voice_processor.get_session_snapshot(
        interaction.guild.voice_client.channel.id
    )
    if not snapshot:
        return False
    applied = voice_session_control_service.apply_updates(
        runtime_session_key=str(snapshot.get("runtime_session_key") or ""),
        updates=prefs,
    )
    return applied is not None


async def _owner_first(interaction: discord.Interaction) -> bool:
    """Batch 8, task 2b (B8-2, ruling T2b-7): the voice commands are the
    owner's. Asked FIRST, before anything is deferred or said: True for the
    owner (nothing has happened yet, so the command goes on exactly as
    before). Anyone else gets a PRIVATE answer — an ephemeral defer and the
    owner-only line — and False. Fails closed: if the owner check cannot
    answer, the caller is not the owner."""
    try:
        caller = await resolve_caller(
            platform="discord", platform_user_id=interaction.user.id)
        if caller.is_owner:
            return True
    except Exception:
        logger.exception("voice commands: could not tell who is calling; treated as a guest")
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(owner_only_line("Voice commands"), ephemeral=True)
    return False


def register_voice_commands(tree: app_commands.CommandTree) -> None:
    """Register the v1 voice command set. Ports of discord_commands.py:351-790."""

    @tree.command(
        name="join_voice",
        description="Join your current voice channel for always-listening",
    )
    async def join_voice(interaction: discord.Interaction):
        if not await _owner_first(interaction):
            return
        await interaction.response.defer()

        if interaction.guild is None:
            await interaction.followup.send(
                "Voice commands only work in a server — hop into one and try again."
            )
            return

        account_user_id = None
        account_features = None

        if not interaction.user.voice:
            await interaction.followup.send(
                "❌ You need to be in a voice channel first!"
            )
            return

        if not config.ENABLE_VOICE_FEATURES:
            await interaction.followup.send(
                "❌ Voice features are currently disabled."
            )
            return

        # Local mode: guild linking optional; honor overrides when mapped.
        guild = interaction.guild
        if guild:
            try:
                account_user_id, account_features, block_reason = (
                    await resolve_voice_account_context_for_guild(
                        guild_id=guild.id,
                        channel_id=interaction.channel.id,
                    )
                )
                if block_reason:
                    await interaction.followup.send(block_reason, ephemeral=True)
                    return
            except Exception as e:
                logger.error(
                    f"Error checking account voice entitlement for guild {guild.id}: {e}"
                )

        voice_channel = interaction.user.voice.channel
        essential_logger.info(
            "🎤 Voice connect attempt: channel=%s(%s) user=%s(%s)",
            voice_channel.name, voice_channel.id,
            interaction.user.name, interaction.user.id,
        )

        try:
            status = require_voice_runtime_ready()
            if not status.ok:
                await interaction.followup.send(
                    "❌ Voice runtime is not ready on this host.\n\n"
                    + "\n".join(f"- {message}" for message in status.messages[:5]),
                    ephemeral=True,
                )
                return

            if interaction.guild.voice_client:
                if interaction.guild.voice_client.channel.id == voice_channel.id:
                    await interaction.followup.send(
                        f"⚠️ Already connected to {voice_channel.name}!"
                    )
                    return
                # Stop the old listening session BEFORE disconnecting — a
                # bare disconnect zombies the sinks bound to the old
                # connection. Best-effort: a stop failure never blocks the
                # move itself.
                try:
                    await voice_processor.stop_listening(
                        interaction.guild.voice_client.channel.id
                    )
                except Exception:
                    essential_logger.warning(
                        "⚠️ stop_listening before channel move failed; continuing"
                    )
                await interaction.guild.voice_client.disconnect()

            start_time = time.time()
            try:
                voice_client = await voice_channel.connect(
                    cls=voice_recv.VoiceRecvClient
                )
                essential_logger.info(
                    "✅ Voice connection established in %.2fs",
                    time.time() - start_time,
                )
            except Exception as e:
                essential_logger.error(
                    "❌ Voice connection failed after %.2fs: %s",
                    time.time() - start_time, e,
                )
                await interaction.followup.send(
                    f"❌ Failed to connect to voice channel: {str(e)}"
                )
                return

            connection_state = describe_voice_connection_state(voice_client)
            essential_logger.info(
                "🎤 Voice encryption state: davey_available=%s dave_session_ready=%s can_encrypt=%s",
                status.details.get("davey_available"),
                connection_state.get("dave_session_ready"),
                connection_state.get("can_encrypt"),
            )
            if not hasattr(voice_client, "listen"):
                await interaction.followup.send(
                    "❌ Voice client does not support listening"
                )
                return

            start_time = time.time()
            try:
                success = await voice_processor.start_listening(
                    voice_client,
                    voice_channel.id,
                    interaction.channel.id,
                    interaction.user.id,
                    account_user_id=account_user_id,
                    account_features=account_features,
                )
                if not success:
                    await voice_client.disconnect()
                    await interaction.followup.send(
                        "❌ Failed to start voice processing"
                    )
                    return
                essential_logger.info(
                    "✅ Always-listening started in %.2fs",
                    time.time() - start_time,
                )
            except Exception as e:
                essential_logger.error(
                    "❌ Always-listening failed: %s\n%s", e, traceback.format_exc()
                )
                await voice_client.disconnect()
                await interaction.followup.send(
                    f"❌ Failed to start voice processing: {str(e)}"
                )
                return

            snapshot = voice_processor.get_session_snapshot(voice_channel.id) or {}
            await interaction.followup.send(
                f"✅ Joined {voice_channel.name} and started the streaming voice runtime.\n"
                f"Mode: `{snapshot.get('interaction_mode', 'direct_address')}` | "
                f"Presence: `{snapshot.get('presence_profile', 'balanced')}` | "
                f"Focus: `{snapshot.get('focus_state', 'responsive')}`"
            )

        except Exception as e:
            essential_logger.error("❌ Error joining voice channel: %s", e)
            await interaction.followup.send(
                f"❌ Failed to join voice channel: {str(e)}\n"
                f"Try `/leave_voice` and then retry."
            )

    @tree.command(
        name="leave_voice", description="Leave voice channel and stop listening"
    )
    async def leave_voice(interaction: discord.Interaction):
        if not await _owner_first(interaction):
            return
        await interaction.response.defer()

        if interaction.guild is None:
            await interaction.followup.send(
                "Voice commands only work in a server — hop into one and try again."
            )
            return

        if not interaction.guild.voice_client:
            await interaction.followup.send("❌ I'm not in a voice channel.")
            return

        voice_client = interaction.guild.voice_client
        channel_name = voice_client.channel.name if voice_client.channel else "Unknown"
        channel_id = voice_client.channel.id if voice_client.channel else None

        try:
            snapshot = (
                voice_processor.get_session_snapshot(channel_id) if channel_id else None
            )
            if snapshot and snapshot.get("runtime_session_key"):
                stopped = await voice_session_control_service.stop(
                    runtime_session_key=str(snapshot.get("runtime_session_key") or ""),
                )
                success = stopped is not None
            else:
                success = await voice_processor.stop_listening(channel_id)
            if not success:
                essential_logger.warning("⚠️ Voice processor stop failed")

            if voice_client.is_connected():
                await voice_client.disconnect()

            await interaction.followup.send(
                f"✅ Left {channel_name} and stopped listening."
            )
        except Exception as e:
            essential_logger.error(
                "❌ Error leaving voice channel: %s\n%s", e, traceback.format_exc()
            )
            await interaction.followup.send(
                f"❌ Error leaving voice channel: {str(e)}"
            )

    @tree.command(
        name="voice_mode",
        description="Choose whether PaLLi waits for direct address or listens passively",
    )
    @app_commands.describe(mode="How PaLLi should decide when to respond in voice")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Direct Address", value="direct_address"),
        app_commands.Choice(name="Passive", value="passive"),
    ])
    async def voice_mode(
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ):
        if not await _owner_first(interaction):
            return
        await interaction.response.defer()

        if interaction.guild is None:
            await interaction.followup.send(
                "Voice commands only work in a server — hop into one and try again."
            )
            return

        prefs = await _persist_voice_preferences(
            interaction,
            updates={"interaction_mode": mode.value},
            reason="discord_voice_mode",
        )
        live_applied = await _maybe_apply_live_voice_preferences(interaction, prefs)
        suffix = (
            "Applied to the current voice session."
            if live_applied else "Saved for the next voice session."
        )
        await interaction.followup.send(
            f"✅ Voice mode set to `{prefs['interaction_mode']}`. {suffix}"
        )

    @tree.command(
        name="voice_presence",
        description="Choose how socially available PaLLi feels in voice",
    )
    @app_commands.describe(profile="How socially available PaLLi should feel")
    @app_commands.choices(profile=[
        app_commands.Choice(name="Quiet", value="quiet"),
        app_commands.Choice(name="Balanced", value="balanced"),
        app_commands.Choice(name="Social", value="social"),
    ])
    async def voice_presence(
        interaction: discord.Interaction,
        profile: app_commands.Choice[str],
    ):
        if not await _owner_first(interaction):
            return
        await interaction.response.defer()

        if interaction.guild is None:
            await interaction.followup.send(
                "Voice commands only work in a server — hop into one and try again."
            )
            return

        prefs = await _persist_voice_preferences(
            interaction,
            updates={"presence_profile": profile.value},
            reason="discord_voice_presence",
        )
        live_applied = await _maybe_apply_live_voice_preferences(interaction, prefs)
        suffix = (
            "Applied to the current voice session."
            if live_applied else "Saved for the next voice session."
        )
        await interaction.followup.send(
            f"✅ Voice presence set to `{prefs['presence_profile']}`. {suffix}"
        )

    @tree.command(
        name="voice_focus",
        description="Choose whether passive mode stays responsive or quiet",
    )
    @app_commands.describe(focus="How passive mode should behave when idle")
    @app_commands.choices(focus=[
        app_commands.Choice(name="Responsive", value="responsive"),
        app_commands.Choice(name="Quiet", value="quiet"),
    ])
    async def voice_focus(
        interaction: discord.Interaction,
        focus: app_commands.Choice[str],
    ):
        if not await _owner_first(interaction):
            return
        await interaction.response.defer()

        if interaction.guild is None:
            await interaction.followup.send(
                "Voice commands only work in a server — hop into one and try again."
            )
            return

        prefs = await _persist_voice_preferences(
            interaction,
            updates={"focus_state": focus.value},
            reason="discord_voice_focus",
        )
        live_applied = await _maybe_apply_live_voice_preferences(interaction, prefs)
        suffix = (
            "Applied to the current voice session."
            if live_applied else "Saved for the next voice session."
        )
        await interaction.followup.send(
            f"✅ Voice focus set to `{prefs['focus_state']}`. {suffix}"
        )

    @tree.command(
        name="voice_status",
        description="Show the saved voice defaults and current live voice state",
    )
    async def voice_status(interaction: discord.Interaction):
        if not await _owner_first(interaction):
            return
        await interaction.response.defer()

        if interaction.guild is None:
            await interaction.followup.send(
                "Voice commands only work in a server — hop into one and try again."
            )
            return

        prefs = await _get_saved_voice_preferences(interaction)
        snapshot = None
        if (
            interaction.guild
            and interaction.guild.voice_client
            and interaction.guild.voice_client.channel
        ):
            snapshot = voice_processor.get_session_snapshot(
                interaction.guild.voice_client.channel.id
            )

        embed = discord.Embed(
            title="🎤 Voice Status",
            description="Saved defaults plus the current live session, if one is active.",
            color=0x3498DB,
        )
        embed.add_field(
            name="Saved Defaults",
            value=(
                f"mode: `{prefs.get('interaction_mode', 'direct_address')}`\n"
                f"presence: `{prefs.get('presence_profile', 'balanced')}`\n"
                f"focus: `{prefs.get('focus_state', 'responsive')}`\n"
                f"local mode: `{prefs.get('local_transport_mode', 'push_to_talk')}`\n"
                f"group voice: `{prefs.get('group_voice_behavior', 'socially_adaptive')}`"
            ),
            inline=False,
        )
        if snapshot:
            embed.add_field(
                name="Live Session",
                value=(
                    f"floor: `{snapshot.get('floor_state', snapshot.get('state', 'idle'))}`\n"
                    f"mode: `{snapshot.get('interaction_mode', 'direct_address')}`\n"
                    f"presence: `{snapshot.get('presence_profile', 'balanced')}`\n"
                    f"focus: `{snapshot.get('focus_state', 'responsive')}`\n"
                    f"context: `{snapshot.get('social_context', 'discord_group')}`\n"
                    f"participants: `{snapshot.get('human_participant_count', 0)} human / {snapshot.get('bot_participant_count', 0)} bot`\n"
                    f"decision: `{snapshot.get('last_decision_reason', 'none') or 'none'}`\n"
                    f"initiative: `{snapshot.get('initiative_source', 'none')}`\n"
                    f"posture: `{snapshot.get('reply_posture', 'brief_companion')}`\n"
                    f"repair: `{snapshot.get('last_repair_reason', 'none') or 'none'}`\n"
                    f"interrupted: `{bool(snapshot.get('last_response_interrupted'))}`"
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Live Session",
                value="No active Discord voice session right now.",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

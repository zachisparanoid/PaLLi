"""Discord's voice-account resolver, and the glue for the three shared
commands (pruned 2026-09-14, telegram door task 3).

What this file WAS: ``setup_commands(bot: commands.Bot)`` — 22 SaaS-era
slash commands (server linking, six toggles, plan info, personality presets,
a duplicate voice set) behind an entry point NOTHING called. The live
surface is a plain ``discord.Client`` that owns its own
``app_commands.CommandTree`` (bots/discord/client.py), so 1,600 lines of it
could not run: the voice six had already been re-homed in
bots/discord/voice_commands.py, and ``insights``, ``link_account`` and
``proactive_consent`` — the three the owner's spec keeps — were on no door
at all.

What it IS: two things, both live.

  * ``resolve_voice_account_context_for_guild`` — the guild→account lookup
    voice_commands.py:30 imports and tests/bots/test_discord_local_mode.py
    pins. Unchanged.
  * ``register_common_commands`` — those three commands, registered on the
    tree ALWAYS (they are not voice, so ENABLE_VOICE_FEATURES must not
    decide whether they exist) and answering with plain text in an ephemeral
    followup. The WORDS come from bots/common/commands.py, which the
    Telegram door calls too: one word, one meaning, at both doors. The old
    embeds are gone — Telegram has no such thing, and a shared renderer that
    emitted them would have forced the other door to strip them back out.
"""

import logging
from typing import Any, Dict, Optional

import discord
from discord import app_commands

from bots.common.commands import (
    TROUBLE_LINE,
    insights_text,
    link_platform_account,
    proactive_status,
    resolve_caller,
    set_proactive_consent,
)
from core.config import config
from core.database import brain
from core.local_mode import local_operator_open_discord_enabled

logger = logging.getLogger(__name__)

PLATFORM = "discord"


async def resolve_voice_account_context_for_guild(
    *,
    guild_id: int,
    channel_id: int,
) -> tuple[Optional[int], Optional[Dict[str, Any]], Optional[str]]:
    """
    Resolve optional account context for Discord voice in a local-first way.

    Returns `(account_user_id, account_features, block_reason)`.
    In local mode, unmapped guilds are allowed and simply return no account context.
    """
    dev_guild_ok = guild_id in getattr(config, "DEV_ALLOW_UNLINKED_GUILDS", [])
    dev_channel_ok = channel_id in getattr(config, "DEV_ALLOW_UNLINKED_CHANNELS", [])
    local_open = local_operator_open_discord_enabled()

    mapping = await brain.get_account_for_discord_guild(guild_id)
    if not mapping:
        if local_open or dev_guild_ok or dev_channel_ok:
            return None, None, None
        return None, None, (
            "❌ This server isn't linked to a PaLLi account yet, so voice mode is disabled.\n\n"
            "An admin can link it using `/link_account <your_account_id>` from the web dashboard, "
            "or you can temporarily allow this guild via the DEV_ALLOW_UNLINKED_GUILDS setting "
            "while you're still in development."
        )

    account_user_id = mapping["account_user_id"]

    if getattr(config, "REQUIRE_HAS_ACCESS_FOR_VOICE", True) and not local_open:
        app_user = await brain.get_app_user_by_id(account_user_id)
        if not app_user or not bool(app_user.get("has_access")):
            return account_user_id, None, (
                "❌ Voice mode is currently gated behind the account access flag for this local PaLLi instance.\n\n"
                "Open the local dashboard and update the linked account settings to enable voice features."
            )

    account_features = await brain.get_account_features(account_user_id)
    if not account_features.get("allow_voice", True):
        detail = (
            "❌ Voice features aren't enabled for the mapped local account for this server."
            if local_open
            else "❌ Voice features aren't enabled for this PaLLi account.\n\n"
            "Enable voice for this account in your configuration to use always-listening here."
        )
        return account_user_id, account_features, detail

    return account_user_id, account_features, None


def register_common_commands(tree: app_commands.CommandTree) -> None:
    """Register the three commands both doors share.

    Same shape as bots/discord/voice_commands.py:113 — closures over the
    tree, no ``commands.Bot`` anywhere near it. Called unconditionally from
    PalliDiscordClient.__init__, before the voice registration.
    """

    @tree.command(
        name="insights",
        description=(
            "See what PaLLi has learned about you - your personality, "
            "interests, and memories"
        ),
    )
    async def insights(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            caller = await resolve_caller(
                platform=PLATFORM, platform_user_id=interaction.user.id)
            text = await insights_text(caller)
        except Exception:
            logger.exception("discord commands: /insights failed")
            text = TROUBLE_LINE
        await interaction.followup.send(text, ephemeral=True)

    @tree.command(
        name="link_account",
        description=(
            "Link your Discord user to your PaLLi web account using a "
            "pairing code"
        ),
    )
    @app_commands.describe(
        code="The 6-character pairing code from your web dashboard",
    )
    async def link_account(interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        try:
            text = await link_platform_account(
                platform=PLATFORM,
                platform_user_id=interaction.user.id,
                code=code,
            )
        except Exception:
            logger.exception("discord commands: /link_account failed")
            text = TROUBLE_LINE
        await interaction.followup.send(text, ephemeral=True)

    @tree.command(
        name="proactive_consent",
        description="Reach-outs: leave empty to read the setting, or set it",
    )
    @app_commands.describe(
        enabled="True to allow proactive messages, false to opt out (omit to read it)",
    )
    async def proactive_consent(
        interaction: discord.Interaction,
        enabled: Optional[bool] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            caller = await resolve_caller(
                platform=PLATFORM, platform_user_id=interaction.user.id)
            # Omitted READS (review round 1, minor 4). It used to toggle —
            # inherited from the dead handler — while Telegram's bare
            # /proactive reported. One word cannot mean "tell me" at one door
            # and "flip it" at the other, or a person turns his reach-outs off
            # by asking whether they are on.
            text = (await proactive_status(caller) if enabled is None
                    else await set_proactive_consent(caller, bool(enabled)))
        except Exception:
            logger.exception("discord commands: /proactive_consent failed")
            text = TROUBLE_LINE
        await interaction.followup.send(text, ephemeral=True)

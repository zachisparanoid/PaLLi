"""Validate channel credentials without sending messages or changing configuration."""
from __future__ import annotations

from urllib.parse import urlencode, urlsplit

import aiohttp
from fastapi import HTTPException


def owner_identifier(value: str, channel: str) -> str:
    value = value.strip()
    maximum = 2**64 - 1 if channel == "discord" else 2**63 - 1
    if len(value) > 20 or not value.isascii() or not value.isdigit() or not 0 < int(value) <= maximum:
        raise HTTPException(400, detail=f"Enter your numeric {channel.title()} user ID.")
    return value


def phone_address(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except ValueError:
        raise HTTPException(400, detail="Enter a valid address your phone can reach.") from None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}):
        raise HTTPException(400, detail="Use a LAN, tailnet or HTTPS address your phone can reach.")
    from core.server_access import is_loopback
    if is_loopback(parsed.hostname):
        raise HTTPException(400, detail="Use a LAN, tailnet or HTTPS address your phone can reach.")
    return f"{parsed.scheme}://{parsed.netloc}"


async def validate_bot(channel: str, token: str) -> dict[str, str]:
    async def get_json(session, url, headers=None):
        async with session.get(url, headers=headers) as response:
            if response.status == 429:
                raise HTTPException(400, detail="The service is busy. Wait a moment and try again.")
            if response.status != 200:
                raise HTTPException(400, detail="That bot token was not accepted. Check it and try again.")
            return await response.json()

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
            if channel == "discord":
                headers = {"Authorization": f"Bot {token}"}
                user = await get_json(session, "https://discord.com/api/v10/users/@me", headers)
                if not user.get("bot"):
                    raise HTTPException(400, detail="Use a Discord bot token.")
                app = await get_json(session, "https://discord.com/api/v10/oauth2/applications/@me", headers)
                if app.get("bot_require_code_grant"):
                    raise HTTPException(400, detail="Turn off Requires OAuth2 Code Grant in the bot settings, then save again.")
                application_id = str(app.get("id", ""))
                if not application_id.isdigit():
                    raise HTTPException(400, detail="Discord did not return this bot's application ID.")
                # Text, attachments, reactions and optional voice. Never Administrator.
                permissions = sum([64, 1024, 2048, 16384, 32768, 65536, 1048576, 2097152])
                invite = "https://discord.com/oauth2/authorize?" + urlencode({
                    "client_id": application_id, "scope": "bot applications.commands",
                    "permissions": permissions, "integration_type": 0,
                })
                return {"name": str(user.get("username") or app.get("name") or "Discord bot"), "link": invite,
                        "application_id": application_id}
            result = await get_json(session, f"https://api.telegram.org/bot{token}/getMe")
            bot = result.get("result") or {}
            if not result.get("ok") or not bot.get("is_bot"):
                raise HTTPException(400, detail="Use a Telegram bot token from BotFather.")
            username = str(bot.get("username") or "")
            if not username or not all(c.isascii() and (c.isalnum() or c == "_") for c in username):
                raise HTTPException(400, detail="Telegram did not return a bot username.")
            return {"name": username, "link": f"https://t.me/{username}"}
    except HTTPException:
        raise
    except Exception:
        # Exceptions may contain Telegram's token-bearing request URL.
        raise HTTPException(400, detail="Could not reach the bot service. Check the connection and try again.") from None

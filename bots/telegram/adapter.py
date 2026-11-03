"""The bridge between core.channel_runtime and a Telegram chat (telegram
door, 2026-09-14, task 2).

Everything that reaches OUT to him without a turn to answer — reminders
(core/reminders/binding.py's non-Discord envelope path), the arbiter's
reach-outs through ``record_outbound``, ``_deliver_proactive``, the
delegation bridge — ends at
``channel_runtime.send_envelope(platform=…, surface_id=…)``. None of them
knows what Discord is.

So registering ONE adapter under ``"telegram"`` is the entire outbound half
of this door: a reminder set from a Telegram chat carries
``platform="telegram"`` and fires in that chat, with no new code anywhere in
the scheduler. The mirror of ``DiscordChannelAdapter`` in
bots/discord/client.py, minus the readiness check — a Bot object needs no
gateway connection to send.

And like that mirror, it RAISES when the send did not happen (final review,
I1). Everything on the other side of this seam reads a clean return as
custody: ``core/turn/outbound.py`` records the DB row, the transcript line
and the deed only after ``send_envelope`` returns, and
``core/reminders/binding.py`` marks the reminder delivered and stops owing
it. bots/telegram/reply.py swallows its send failures on purpose — one
unreachable chat must not close the door on a reply — so this is where
"nothing landed" has to become an exception, or *Forbidden: bot was blocked
by the user* is filed as a thing she said.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from bots.telegram.reply import last_send_error, send_turn_reply
from core.channel_runtime import ChannelDeliveryEnvelope

logger = logging.getLogger(__name__)


class TelegramChannelAdapter:
    def __init__(self, bot: Any) -> None:
        self._bot = bot

    async def send_envelope(
        self, *, surface_id: str, envelope: ChannelDeliveryEnvelope,
    ) -> Dict[str, Any]:
        try:
            chat_id = int(surface_id)
        except (TypeError, ValueError) as exc:
            # Never a guess: a surface id that is not a chat id would
            # deliver his reminder into somebody else's chat, or nobody's.
            raise RuntimeError(
                f"TelegramChannelAdapter: invalid surface_id {surface_id!r}"
            ) from exc

        text = envelope.text or ""
        attachments = tuple(
            str(getattr(item, "path", "") or "")
            for item in (envelope.attachments or [])
            if str(getattr(item, "path", "") or "")
        )
        if not text.strip() and not attachments:
            # Nothing to send is not a failed send.
            return {"sent": False, "chat_id": chat_id}

        delivered = await send_turn_reply(self._bot, chat_id, text,
                                          attachments=attachments)
        if not delivered:
            # The notice and the log already happened inside reply.py; this
            # is the same failure said in the one language the custody legs
            # and the scheduler understand.
            raise RuntimeError(
                f"TelegramChannelAdapter: nothing was delivered to chat "
                f"{chat_id}: {last_send_error() or 'no send succeeded'}"
            )
        # ``sent`` is text-OR-attachments (final review, M8): a reminder whose
        # whole body is a file used to come back sent=False after the file
        # had gone — the same untruth pointing the other way.
        return {"sent": True, "chat_id": chat_id}

"""Dashboard surface modules.

The dashboard is single-owner local mode. Inbound chat lands at
``api/chat.py`` (POST /api/chat/message); outbound delivery goes
through ``bots/dashboard/channel_adapter.py``, which registers itself
with the channel runtime so delegation envelopes (Tier-2 results, voice
mirror text, reminder fires) can reach the dashboard frontend via the
HTTP response or websocket.
"""

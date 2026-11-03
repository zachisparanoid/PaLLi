"""Her Hands (step 33): the live-client handle and fuzzy resolvers.

The tools in core/agent/builtin_tools/her_hands.py import this lazily.
No heavy imports at module load; no discord import needed — everything
duck-types off the client object the Discord layer registers at ready.

Resolution contract: (match, candidates). match is None when nothing is
good enough OR more than one thing is equally good — the candidates list
(nearest names, <=5) goes back to HER, and her judgment decides. Fuzzy
matching is deterministic code; reading "Edddy" as Eddie is her call.
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_client: Any = None


def register_client(client: Any) -> None:
    global _client
    _client = client


def get_client() -> Any:
    return _client


_telegram_bot: Any = None


def register_telegram_bot(bot: Any) -> None:
    """The Telegram door's bot (step 2, part 8), for the lookups core makes
    through it ("who's in this group?"); None when the door is down."""
    global _telegram_bot
    _telegram_bot = bot


def get_telegram_bot() -> Any:
    return _telegram_bot


def client_connected() -> bool:
    """Is Discord there to reach? The client is registered on its first
    on_ready and never cleared, so ask it: ready, and not closed (Fable's
    final review, 4). A connection that drops and is reconnecting can still
    read as ready; a send then fails and the tool says so. A stand-in
    without these methods (the tests' fakes) counts as there."""
    c = get_client()
    if c is None:
        return False
    ready, closed = getattr(c, "is_ready", None), getattr(c, "is_closed", None)
    try:
        if callable(closed) and closed():
            return False
        return bool(ready()) if callable(ready) else True
    except Exception:
        return False


def _rank(name: str, items: list[tuple[str, Any]]) -> tuple[Optional[Any], list[str]]:
    """exact > unique-startswith > unique-contains > difflib-close."""
    q = (name or "").strip().lower().lstrip("#")
    if not q or not items:
        return None, []
    exact = [obj for label, obj in items if label.lower() == q]
    if len(exact) == 1:
        return exact[0], []
    starts = [(label, obj) for label, obj in items if label.lower().startswith(q)]
    if len({id(o) for _, o in starts}) == 1 and starts:
        return starts[0][1], []
    contains = [(label, obj) for label, obj in items if q in label.lower()]
    if len({id(o) for _, o in contains}) == 1 and contains:
        return contains[0][1], []
    pool = starts or contains
    if pool:
        return None, sorted({label for label, _ in pool})[:5]
    close = difflib.get_close_matches(q, [label.lower() for label, _ in items], n=5, cutoff=0.6)
    if len(close) == 1:
        for label, obj in items:
            if label.lower() == close[0]:
                return obj, []
    if close:
        pretty = [label for label, _ in items if label.lower() in close]
        return None, sorted(set(pretty))[:5]
    near = difflib.get_close_matches(q, [label.lower() for label, _ in items], n=5, cutoff=0.3)
    pretty = [label for label, _ in items if label.lower() in near]
    return None, sorted(set(pretty))[:5]


def _guilds() -> list:
    c = get_client()
    return list(getattr(c, "guilds", []) or []) if c is not None else []


def resolve_voice_channel(name: str):
    items = [(ch.name, ch) for g in _guilds() for ch in getattr(g, "voice_channels", [])]
    return _rank(name, items)


def resolve_text_channel(name: str):
    items = [(ch.name, ch) for g in _guilds() for ch in getattr(g, "text_channels", [])]
    return _rank(name, items)


def parse_raw_id(text: str) -> Optional[int]:
    """'123456789012345678', '<#…>', '<@…>', '<@!…>' → the id; else None.

    list_members hands her raw ids and pasted Discord mentions arrive in
    these shapes — both must work as send_message destinations (§1.5).
    """
    t = (text or "").strip()
    # Mention syntax is unambiguous at any length; a BARE number only reads
    # as an id when it's snowflake-shaped (>=15 digits), so a channel that
    # happens to be named "2024" stays reachable by name.
    m = re.fullmatch(r"<[#@]!?(\d{1,25})>", t) or re.fullmatch(r"(\d{15,25})", t)
    return int(m.group(1)) if m else None


def find_text_channel_by_id(channel_id: int):
    for g in _guilds():
        for ch in getattr(g, "text_channels", []):
            if int(getattr(ch, "id", 0) or 0) == int(channel_id):
                return ch
    return None


def find_member_by_id(member_id: int):
    for g in _guilds():
        for m in getattr(g, "members", []):
            if int(getattr(m, "id", 0) or 0) == int(member_id):
                return m
    return None


def resolve_member(name: str):
    items = []
    for g in _guilds():
        for m in getattr(g, "members", []):
            items.append((getattr(m, "display_name", "") or "", m))
            items.append((getattr(m, "name", "") or "", m))
    # dedupe labels pointing at the same member with same label
    seen = set()
    uniq = []
    for label, obj in items:
        key = (label.lower(), id(obj))
        if label and key not in seen:
            seen.add(key)
            uniq.append((label, obj))
    return _rank(name, uniq)


def _member_entry(m: Any) -> dict:
    return {
        "name": getattr(m, "name", ""),
        "display_name": getattr(m, "display_name", "") or getattr(m, "name", ""),
        "id": getattr(m, "id", 0),
        "bot": bool(getattr(m, "bot", False)),
        "status": str(getattr(m, "status", "") or ""),
    }


def members_here(query: str = "", *, channel_id: Any = None) -> tuple[list[dict], bool]:
    """The people she can see, and whether the list is for one channel.

    Step 2, part 7 (2026-10-08): with ``channel_id`` a server channel she can
    see, the people who can see that channel (discord.py's channel.members);
    else everyone on her servers. Each person once, however many servers
    they share with her. The query narrows by name or display name."""
    q = (query or "").strip().lower()
    pool: list = []
    scoped = False
    c = get_client()
    if channel_id and c is not None and callable(getattr(c, "get_channel", None)):
        try:
            channel = c.get_channel(int(channel_id))
            members = list(getattr(channel, "members", None) or []) if channel is not None else []
        except Exception:
            members = []
        if members:
            pool, scoped = members, True
    if not scoped:
        pool = [m for g in _guilds() for m in getattr(g, "members", [])]
    out: list[dict] = []
    seen: set = set()
    for m in pool:
        key = getattr(m, "id", None) or id(m)
        if key in seen:
            continue
        seen.add(key)
        entry = _member_entry(m)
        if not q or q in (entry["name"] + " " + entry["display_name"]).lower():
            out.append(entry)
    if q:
        out.sort(key=lambda e: (q not in e["display_name"].lower(), e["display_name"]))
    return out, scoped


def list_all_members(query: str = "") -> list[dict]:
    q = (query or "").strip().lower()
    out = []
    seen = set()
    for g in _guilds():
        for m in getattr(g, "members", []):
            # One person in two servers is listed once (2026-10-08).
            key = getattr(m, "id", None) or id(m)
            if key in seen:
                continue
            seen.add(key)
            entry = {
                "name": getattr(m, "name", ""),
                "display_name": getattr(m, "display_name", "") or getattr(m, "name", ""),
                "id": getattr(m, "id", 0),
                "bot": bool(getattr(m, "bot", False)),
                # Presence intent is on (Intents.all()), so Member.status is
                # real; empty string when the object carries none (fakes,
                # partial members) — renderers treat empty as unknown.
                "status": str(getattr(m, "status", "") or ""),
            }
            hay = (entry["name"] + " " + entry["display_name"]).lower()
            if not q or q in hay:
                out.append(entry)
    if q:
        out.sort(key=lambda e: (q not in e["display_name"].lower(), e["display_name"]))
    return out


def fallback_text_channel(guild: Any):
    sys_ch = getattr(guild, "system_channel", None)
    if sys_ch is not None:
        return sys_ch
    channels = list(getattr(guild, "text_channels", []) or [])
    return channels[0] if channels else None


async def active_voice_moment() -> Optional[dict]:
    """The voice-moment predicate (step 32, spec §2): is she in a LIVE voice
    session with HIM in the room, right now?

    Deterministic, timing/medium not meaning: connected voice client → its
    channel → a live listening session (non-empty runtime_session_key from
    the voice processor) → the owner's Discord id among the channel's
    members. Any miss returns None and the arbiter's text path proceeds
    byte-identical to today. Never raises — a probe failure is a None with
    a WARNING, never a broken release consideration.

    ``alone_with_her`` (the owner, 2026-10-13: a message for him alone never
    reaches a group): True only when every member of the call is him or her
    own account. A friend on the call, a member whose id cannot be read, or
    her own account unknown (she could not tell herself from a friend) makes
    it False: the arbiter then sends her say or share as text to his private
    chat, while he still counts as present."""
    try:
        client = get_client()
        if client is None:
            return None
        vc = None
        for g in (getattr(client, "guilds", []) or []):
            vc = getattr(g, "voice_client", None)
            if vc is not None:
                break
        channel = getattr(vc, "channel", None) if vc is not None else None
        chan_id = getattr(channel, "id", None) if channel is not None else None
        if not chan_id:
            return None

        from voice.always_listening import voice_processor

        snapshot = voice_processor.get_session_snapshot(chan_id) or {}
        key = str(snapshot.get("runtime_session_key") or "")
        if not key:
            return None

        from core.local_mode import get_local_owner_refs

        refs = await get_local_owner_refs()
        owner_did = int(refs.get("discord_user_id") or 0)
        if not owner_did:
            return None
        members = getattr(channel, "members", []) or []
        member_ids = [int(getattr(m, "id", 0) or 0) for m in members]
        if owner_did not in member_ids:
            return None
        her_did = int(getattr(getattr(client, "user", None), "id", 0) or 0)
        alone = all(mid and mid in (owner_did, her_did) for mid in member_ids)
        return {
            "voice_channel_id": int(chan_id),
            "runtime_session_key": key,
            "alone_with_her": bool(alone),
        }
    except Exception:
        logger.warning("active_voice_moment: probe failed", exc_info=True)
        return None

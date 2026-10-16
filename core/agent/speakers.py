"""Who sent a line (step 2 of the release order, part 2; 2026-10-08).

Until now a transcript row held only its role, text and time. In a shared room a
guest was recognised only by the "[Name]: " the door typed into the text, and the
owner by having no tag at all — so any path that forgot the tag (a forward, a
join notice, a merged burst) silently turned someone else's words into the
owner's. Each new user row now carries a speaker record, built at intake from the
account the message came from, never from its text:

    {"kind": "owner" | "guest" | "hidden" | "system",
     "platform": "discord" | "telegram" | "voice" | "web" | "",
     "id": the account id as text ("" when the app hides it),
     "name": the name they show in the room, "handle": their account name,
     "to": [{"id", "name", "how": "reply" | "mention"}]   # only when the app says so}

A plain dict so it round-trips through the JSONL transcript unchanged, written only
when set (an ordinary row grows no key: the history prefix is what the provider
caches). ``normalize_speaker`` is total: whatever is on disk or on a turn, it
returns a clean record or None and never raises — a bad value costs the record,
never the row. Rendering it for the model is a separate, later pass.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from typing import Any

KINDS = ("owner", "guest", "hidden", "system")
HOW = ("reply", "mention")
MAX_NAME = 64
MAX_ID = 40
MAX_TO = 10


def _one_line(value: Any, limit: int) -> str:
    """Text on one line, whitespace collapsed, bounded. Anything that isn't a
    string or a number reads as empty."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return " ".join(str(value).split())[:limit]


def _addressee(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    out = {"id": _one_line(raw.get("id"), MAX_ID), "name": _one_line(raw.get("name"), MAX_NAME),
           "how": raw.get("how") if raw.get("how") in HOW else ""}
    return out if (out["id"] or out["name"]) else None


def normalize_speaker(raw: Any) -> dict[str, Any] | None:
    """A clean speaker record, or None. Never raises."""
    try:
        if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
            return None
        out: dict[str, Any] = {
            "kind": raw["kind"],
            "platform": _one_line(raw.get("platform"), 16),
            "id": _one_line(raw.get("id"), MAX_ID),
            "name": _one_line(raw.get("name"), MAX_NAME),
            "handle": _one_line(raw.get("handle"), MAX_NAME),
        }
        to = raw.get("to")
        if isinstance(to, list):
            kept = [a for a in (_addressee(x) for x in to[:MAX_TO]) if a]
            if kept:
                out["to"] = kept
        return out
    except Exception:
        return None


def make_speaker(kind: str, *, platform: str, account_id: Any = "", name: Any = "",
                 handle: Any = "", to: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Build a record at intake. The id and name come from the account the
    message arrived from — the app's, never anything typed in the message."""
    return normalize_speaker({"kind": kind, "platform": platform, "id": account_id,
                              "name": name, "handle": handle, "to": to or []})


def speaker_of(turn: Any) -> dict[str, Any] | None:
    """The speaker record a turn carries, or None."""
    try:
        return normalize_speaker(getattr(getattr(turn, "metadata", None), "speaker", None))
    except Exception:
        return None


# ── who said each line, for the model (part 3) ───────────────────────────────
# Rendered last, on the payload copy, in shared rooms with she_decides on (the
# runner decides that). The owner's line carries OWNER_MARK, built only from a
# speaker record whose kind the door set from the account; a guest's name and a
# guest's words can never carry it (the star is defused in both).
OWNER_MARK = "\u2605owner"           # ★owner
_STAR = "\u2605"
_GUEST_TAG = re.compile(r"^\[[^\n]+?\]: ")          # the door's "[Name]: " (core.owner_gate)
_OWNER_LABEL = re.compile(r"^\[[^\]\n]*" + _STAR + r"owner\]: ")


def is_shared_room(turn: Any) -> bool:
    """A chat door's group conversation: not a DM, with a room id."""
    try:
        md = getattr(turn, "metadata", None)
        return (str(getattr(turn, "surface", "") or "") in ("discord_text", "telegram_text")
                and md is not None and not bool(getattr(md, "is_dm", False))
                and bool(getattr(md, "channel_id", None)))
    except Exception:
        return False


# Fable's checkpoint review (2026-10-08, finding 1): removing ★ alone let a
# look-alike (☆ ⭐ ✭ 🌟 …) or a bare "owner" pass for the mark. Every symbol,
# variation selector and format character goes from a shown name; a guest
# whose name still reads "owner" is labelled a guest; a symbol right before
# "owner" in anything a guest types is neutralised.
_OWNERISH = re.compile(r"\bowner\b", re.I)
_MARKISH = re.compile(r"([^\w\s]+)(\s*owner)", re.I)


def _is_symbol(ch: str) -> bool:
    code = ord(ch)
    return (unicodedata.category(ch) in ("So", "Sk", "Cf")
            or 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF)


def _clean_name(name: Any) -> str:
    """A name as a label: one line, without brackets, colons or any symbol."""
    text = unicodedata.normalize("NFC", str(name or ""))
    text = "".join(" " if (c in "[]:" or _is_symbol(c)) else c for c in text)
    return " ".join(text.split())[:MAX_NAME]


def _guest_name(name: Any) -> str:
    n = _clean_name(name)
    return f"{n} (guest)" if n and _OWNERISH.search(n) else n


def clean_name(name: Any) -> str:
    """A name as she shows it anywhere (a label, an @mention): one line, no
    brackets, colons or star, so it can never read as the owner's mark."""
    return _clean_name(name)


def _neutral(chars: str) -> str:
    return re.sub(r"\*{2,}", "*", "".join("*" if _is_symbol(c) else c for c in chars))


# Zero-width characters (Fable's final review, 6): "o\u200bwner" must read as
# "owner" to the look-alike check, so a guest's text loses them first.
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060\ufeff]")


def _defuse(text: str) -> str:
    text = _ZERO_WIDTH.sub("", str(text)).replace(_STAR, "*")
    return _MARKISH.sub(lambda m: _neutral(m.group(1)) + m.group(2), text)


def defuse(text: str) -> str:
    """A guest's words as the room shows them: no ★, no symbol right before
    "owner", no zero-width characters, so they can never carry the owner's
    mark (the rule ``label_speakers_for_llm`` uses; memory that reaches
    renders a guest's lines from the record with it too, R15 W2)."""
    return _defuse(text)


def owner_label(name: Any = "") -> str:
    n = _clean_name(name)
    return f"[{n} {OWNER_MARK}]: " if n else f"[{OWNER_MARK}]: "


def _doors_tag(text: str, speaker: dict[str, Any]) -> str:
    """The row's leading "[Name]: " when it is the tag the door wrote for this
    sender (``core.owner_gate.guest_content``: the name they show, or their
    account name — Telegram's rows before 2026-10-13 — or "guest" when the app
    gave none); "" otherwise, so a guest's own "[note]: " is never taken for
    it (Fable's checkpoint review, 7)."""
    tag = _GUEST_TAG.match(text)
    if not tag:
        return ""
    name = _one_line(tag.group(0)[1:-3], MAX_NAME)
    return tag.group(0) if name in {speaker.get("name"), speaker.get("handle"), "guest"} - {""} else ""


def _label_line(text: str, speaker: dict[str, Any] | None, *,
                after_records: bool = False) -> str | None:
    """The line as she reads it, or None to leave it as it is."""
    if speaker:
        kind = speaker.get("kind")
        if kind == "owner":
            return None if _OWNER_LABEL.match(text) else owner_label(speaker.get("name")) + text
        if kind in ("guest", "hidden"):
            body = text[len(tag):] if (tag := _doors_tag(text, speaker)) else text
            fallback = "someone" if kind == "guest" else "someone the app doesn't name"
            return f"[{_guest_name(speaker.get('name')) or fallback}]: {_defuse(body)}"
        return None                                   # a system line is nobody's
    # Rows written before speakers existed read as they always did: a stored
    # tag is that guest (cleaned and defused); an untagged line in a shared
    # room was the owner's; anything else starting with "[" is a system note,
    # nobody's. Once a room has records, a row without one is "someone",
    # never the owner (Fable's checkpoint, finding 2): every writer sets one,
    # so a missing record is a writer's slip, not the owner.
    tag = _GUEST_TAG.match(text)
    if tag:
        name = tag.group(0)[1:-3]
        new = f"[{_guest_name(name) or 'someone'}]: {_defuse(text[tag.end():])}"
        return None if new == text else new
    if text.startswith("["):
        return None
    return ("[someone]: " if after_records else owner_label("")) + text


def label_speakers_for_llm(messages: list[Any], *, recorded: bool = False) -> list[Any]:
    """Each user line with who said it, in front of its first text block.
    Pure (rows are copied, never mutated) and idempotent; her lines, tool calls
    and tool results are left alone. ``recorded``: rows before these (left
    out) already had a sender record."""
    out: list[Any] = []
    for m in messages or []:
        if getattr(m, "role", None) != "user":
            out.append(m)
            continue
        speaker = getattr(m, "speaker", None)
        after_records, recorded = recorded, recorded or bool(speaker)
        content = list(getattr(m, "content", []) or [])
        idx = next((i for i, b in enumerate(content)
                    if isinstance(getattr(b, "text", None), str) and b.text.strip()), None)
        if idx is None:
            out.append(m)
            continue
        new = _label_line(content[idx].text, speaker, after_records=after_records)
        if new is None or new == content[idx].text:
            out.append(m)
            continue
        content[idx] = replace(content[idx], text=new)
        out.append(replace(m, content=content))
    return out


_LABEL_NAME = re.compile(r"^\[([^\]\n]+)\]: ")


def label_names(messages: list[Any], *, last: int | None = None) -> list[str | None]:
    """Per row, who said it as she reads it ("Robin ★owner", "Sam"), by the
    labels above (with their room context); None for her lines, system
    notes, a sender the app hides, and "someone". With ``last``, only the
    last rows are labelled (Fable's final review, 7: the whole room file took
    346 ms at 20,000 rows); the rows before them only say whether the room
    had records yet, so each label reads as it would in the whole file."""
    rows = list(messages or [])
    recorded = False
    if last is not None and len(rows) > last:
        head, rows = rows[:-last], rows[-last:]
        recorded = any(getattr(m, "role", None) == "user" and getattr(m, "speaker", None)
                       for m in head)
    return [_name_on(m) for m in label_speakers_for_llm(rows, recorded=recorded)]


def _name_on(labeled: Any) -> str | None:
    """Who a row labelled above says said it, as ``label_names`` names them."""
    name = _label_on(labeled)
    return None if name == "someone" else name


def _label_on(labeled: Any) -> str | None:
    """The name on a row's label as written, "someone" included."""
    speaker = getattr(labeled, "speaker", None)
    if getattr(labeled, "role", None) != "user" or (
            speaker and speaker.get("kind") not in ("owner", "guest")):
        return None
    text = next((b.text for b in getattr(labeled, "content", []) or []
                 if isinstance(getattr(b, "text", None), str) and b.text.strip()), "")
    match = _LABEL_NAME.match(text)
    return match.group(1) if match else None


def names_shown(labeled: list[Any]) -> frozenset[str]:
    """Every label on rows already labelled for her (the runner's payload),
    for the reply filter (Fable's checkpoint review, 6). "someone" counts here,
    so a copy of it leaves her reply (Fable's R17 review), though it names no one."""
    return frozenset(n for n in map(_label_on, labeled or []) if n)

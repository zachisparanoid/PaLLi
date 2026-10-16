"""`where_did_that_come_from` — Shared Idiolect (organ 4.12), spec §3.

Her hand for checking before claiming: a real search of this
conversation's own record for where a word or bit started. Found ⇒ the
receipt, in fields she can speak from in her own voice; not found ⇒ an
honest nothing. `keep=True` writes the entry through the store's rail
(no receipt, no entry; a duplicate is refused). Owner-only: "ours" is
this relationship's record, not a guest's.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core.agent.tools import ToolContext

# The phrase is model-authored: the lookup's 3..80 bound gates the SEARCH,
# and without a cap here the not-found line echoes an unbounded string back.
PHRASE_ECHO_CAP = 80


def _day(when: str) -> str:
    """The day he would call it — his clock, not the transcript's.

    Session timestamps are written in UTC (core/agent/session.py) and he is
    nocturnal, so 16% of live rows fall on a different calendar day in UTC
    than in his zone. The house precedent is explicit
    (core/agent/context_adapter.py): the 2026-07-10 bug PaLLi caught herself
    was raw UTC digits framed as his local time, four hours off.
    """
    try:
        parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        return when
    return parsed.astimezone().strftime("%b %d, %Y")


class WhereDidThatComeFromTool:
    name = "where_did_that_come_from"
    description = (
        "Check before you claim. When a word, nickname or running bit comes up and you "
        "want to know whether it is genuinely yours together — and where it started — "
        "call this with the phrase. It searches this conversation's own record for the "
        "earliest time either of you said it and hands you the receipt (who, when, the "
        "line), or tells you honestly that nothing carries it. Pass keep=true to write it "
        "into your book of callbacks with that receipt. Never claim a shared history you "
        "haven't checked; ask them, or check."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "phrase": {"type": "string",
                       "description": "the word, name or bit, as you'd say it"},
            "keep": {"type": "boolean",
                     "description": "write it into your book with the receipt (default false)"},
            "kind": {"type": "string", "enum": ["nickname", "bit", "shorthand"],
                     "description": "what it is (default bit)"},
        },
        "required": ["phrase"],
    }
    requires_approval = False
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from ai import callbacks_lookup as lk
        from ai.callbacks_store import KINDS, find_open_by_bit, plant

        if not getattr(ctx, "is_owner", False):
            return "This is about what's ours — it only answers in the owner's conversation."
        surface = str(getattr(ctx, "surface", "") or "")
        # Only the surfaces whose transcripts are the two of them talking. A
        # reminder-fire or proactive transcript's `user` rows are the
        # scheduler's synthesized text — and since §16 they quote her own
        # recent lines — so searching one would stamp him with her words.
        if surface not in lk.CONVERSATIONAL_SURFACES:
            return "This isn't a conversation record I can search."
        phrase = str(input.get("phrase") or "").strip()
        if not phrase:
            return "Give me the phrase to look for."
        session = lk.session_path_for(surface=surface,
                                      channel_id=int(getattr(ctx, "channel_id", 0) or 0),
                                      owner_id=int(getattr(ctx, "turn_owner_id", 0) or 0))
        receipt = await lk.find_origin_async(phrase, session_path=session)
        if receipt is None:
            return (f'Nothing in this conversation\'s record carries "{phrase[:PHRASE_ECHO_CAP]}"'
                    " — I don't have where that started. Ask them, or let it be new.")
        who = "they said it first" if receipt["who"] == "him" else "I said it first"
        lines = [f'Found it: {who}, {_day(receipt["when"])} — "{receipt["line"]}"']
        if input.get("keep"):
            kind = str(input.get("kind") or "bit")
            if kind not in KINDS:
                kind = "bit"
            if find_open_by_bit(phrase) is not None:
                lines.append("It's already in your book.")
            else:
                born = plant(bit=phrase, kind=kind, coined_by=receipt["who"], receipt=receipt)
                lines.append(f"Kept — it's ours now (id {born.id})." if born
                             else "Couldn't keep it — the book refused the entry.")
        return "\n".join(lines)

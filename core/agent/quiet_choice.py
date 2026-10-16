"""She decides whether to answer: the quiet choice (Batch 8, task 2a — B8-2).

She takes part in conversations as a participant who knows she is an AI,
and the owner's principle is that code gives her abilities, memory and
moments to reconsider — never the decision. Staying quiet is one of her
abilities: when nothing from her is wanted (they asked her not to reply, or
the message isn't hers to answer), her reply call can choose it.

How (ruling T2-2): a ``stay_quiet`` CONTROL CALL. The runner appends its
schema to the tools of a human conversation turn — the owner's (``user``),
the dashboard's (``dashboard_user``) and a guest's (``guest``) — and
recognises the call itself, before any tool registry is consulted. So a
guest, who has no tool registry at all, can still choose quiet and still
reaches no tool. Her own runs (reminder fires, her workbench and forge
sessions, her monologue and outreach) are never offered it: a reminder is
not a reply, and those runs keep today's behaviour exactly.

Why a call and not an empty reply: an empty completion is re-asked by the
fumble reflex whenever the model shows reasoning (core/agent/llm_client.py),
and her live brain always does. A call is unambiguous, carries her reason as
a structured input, and the round ends right there.

Code makes the choice binding (runner.py): nothing is sent on any door — no
words, no emoji, no spoken line — the message stays in her transcript, no
further model call happens, and the choice counts as answered. Dispatch
writes it to her record (core/quiet_record.py) with her reason.

A call made in the SAME step as stay_quiet (ruling T2-2a, the 2a review) is
real work she was asked for — "remind me at 5, no need to reply", "send me
the notes, don't reply" — so it runs through the normal path (the registry,
its approval hook included) and its result goes beside the quiet result. If
every one of them went through, the quiet choice stands. If any failed or
was refused, the quiet call is answered "not yet" and the run goes on, so
she decides again with the failure in view: a moment to reconsider, never a
reminder or a file silently lost. That re-decision has a budget (second read
F2, 2026-10-04): after two "not yet"s in a row for the same tool, she is told
it keeps failing, and her next quiet choice ends the run.

Everything reads the switch through ``she_decides_enabled()`` called via
this module's attribute, so the suite's autouse pin can hold it off.
"""
from __future__ import annotations

from typing import Any

STAY_QUIET = "stay_quiet"

# The human conversation turns: the only runs offered the quiet choice.
QUIET_ORIGINS = ("user", "dashboard_user", "guest")

_REASON_MAX_CHARS = 300

STAY_QUIET_SCHEMA: dict[str, Any] = {
    "name": STAY_QUIET,
    "description": (
        "Stay quiet: send no reply to this message. Nothing is sent — no "
        "words, no emoji — and their message stays in your record. Use it "
        "when nothing from you is wanted here: they asked you not to reply, "
        "or the message isn't yours to answer. If you were also asked to do "
        "something, such as sending a file or setting a reminder, call that "
        "tool too; it still runs. Your reason is kept in your record of "
        "quiet choices."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "One short line, in your own words: why you are staying quiet.",
            },
        },
        "required": ["reason"],
    },
}

# The tool result she reads back: the call worked, and nothing went out.
QUIET_RESULT = "You stayed quiet. Nothing was sent."
# Second read F8 (2026-10-04): her quiet choice stands, but a voice message
# she made this reply still goes out (ruling FW-3, confirmed by the owner
# 10-04) — so "Nothing was sent" would be false. Her record says she spoke:
# the runner writes the spoken words as her reply, and dispatch writes no
# quiet row for the turn.
QUIET_SPOKE_RESULT = ("No text reply goes out — your voice message is your reply, and it "
                      "goes out on its own.")
# A call beside it failed or was refused: she decides again (ruling T2-2a).
NOT_YET_RESULT = "Not yet — {tools} didn't go through; decide again with that in view."
# Second read F2 (2026-10-04): the re-decision's budget. "Not yet" was
# answered with no bound short of the 50-step safety limit, so a tool that
# failed every time beside her quiet choice cost about fifty full reply calls
# and ended with no reply, no reminder and no quiet record. After two "not
# yet"s in a row for the same failing tool, the quiet call is answered with
# this instead, and her next stay_quiet ends the run whatever fails beside it
# (core/agent/runner.py). A cost floor: what she does next is still hers.
NOT_YET_BUDGET = 2
KEEPS_FAILING_RESULT = ("This keeps failing: {tools} didn't go through again. Don't try it "
                        "again in this reply — tell them it didn't work, or stay quiet; a "
                        "quiet choice now ends this reply.")
# A turn with no tool registry (a guest's) named a tool beside the quiet choice.
NO_TOOLS_RESULT = "Not available: you have no tools on this turn."


def she_decides_enabled() -> bool:
    """companion.she_decides, fail-closed: unbooted settings, a missing
    field or any error read as OFF — today's behaviour."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "she_decides", False))
    except Exception:
        return False


# Stable alias: tests reach the real accessor past the suite's autouse pin.
_she_decides_enabled_impl = she_decides_enabled

# companion.room_wait_seconds: the schema default and floor (task 2a).
ROOM_WAIT_DEFAULT_SECONDS = 45
ROOM_WAIT_FLOOR_SECONDS = 5


def room_wait_seconds() -> int:
    """companion.room_wait_seconds (Batch 8, task 2b — ruling T2b-2): how
    long a room's "wait" lasts before she reads the room once more — a
    moment to reconsider, not a rule. Unbooted settings or any error read as
    the schema default; never below the schema's floor."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        raw = getattr(comp, "room_wait_seconds", None) if comp is not None else None
        value = ROOM_WAIT_DEFAULT_SECONDS if raw is None else int(raw)
    except Exception:
        value = ROOM_WAIT_DEFAULT_SECONDS
    return max(ROOM_WAIT_FLOOR_SECONDS, value)


def offered(turn: Any) -> bool:
    """Is the quiet choice offered on this turn? The switch is on, and it is
    a human conversation turn (never a reminder fire, her workbench or forge,
    her monologue or outreach). The switch is looked up in this module's
    globals at call time, so the suite's pin and a test's patch both reach it."""
    if not she_decides_enabled():
        return False
    md = getattr(turn, "metadata", None)
    origin = str(getattr(md, "origin", "user") or "user") if md is not None else "user"
    surface = str(getattr(turn, "surface", "") or "")
    return origin in QUIET_ORIGINS and surface != "workbench"


def find_call(tool_uses: Any) -> Any | None:
    """The stay_quiet call among a response's tool calls, or None."""
    for tu in tool_uses or ():
        if str(getattr(tu, "name", "") or "") == STAY_QUIET:
            return tu
    return None


def reason_of(tool_use: Any) -> str:
    """Her one line, on one line and capped; "" when she gave none (the
    choice still binds)."""
    raw = getattr(tool_use, "input", None)
    reason = raw.get("reason") if isinstance(raw, dict) else None
    text = " ".join(str(reason or "").split())
    if len(text) > _REASON_MAX_CHARS:
        text = text[: _REASON_MAX_CHARS - 1].rstrip() + "…"
    return text


def failed_names(other_calls: Any, other_results: Any) -> list[str]:
    """The calls beside the quiet choice that failed or were refused, by
    name, in order, once each."""
    names: list[str] = []
    for tu, res in zip(other_calls or (), other_results or ()):
        name = str(getattr(tu, "name", "") or "a tool")
        if getattr(res, "is_error", False) and name not in names:
            names.append(name)
    return names


def round_results(tool_uses: Any, quiet_call: Any, other_results: Any = (),
                  failed: Any = (), *, keeps_failing: bool = False,
                  spoke: bool = False) -> list[Any]:
    """The step's tool results, in call order, so the round closes on disk:
    each other call's own result (it ran through the registry), and the quiet
    call's — "You stayed quiet" when everything beside it went through
    (``spoke``: a voice message she made still goes out, so it says that
    instead — second read F8), "Not yet — … decide again" when something did
    not, and "This keeps failing" once the re-decision's budget is spent
    (``keeps_failing``, second read F2). A second stay_quiet in the same step
    is the same choice and gets the same plain result (final fix wave, ruling
    FW-5); it never went to the registry."""
    from core.agent.session import ToolResultBlock

    failed = list(failed or ())
    template = KEEPS_FAILING_RESULT if keeps_failing else NOT_YET_RESULT
    quiet_text = (template.format(tools=" and ".join(failed)) if failed
                  else QUIET_SPOKE_RESULT if spoke else QUIET_RESULT)
    others = iter(list(other_results or ()))
    out: list[Any] = []
    for tu in tool_uses or ():
        if tu is quiet_call or str(getattr(tu, "name", "") or "") == STAY_QUIET:
            out.append(ToolResultBlock(tool_use_id=tu.id, content=quiet_text))
        else:
            out.append(next(others))
    return out

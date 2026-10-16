"""The look, on purpose — ``look_at_the_house`` (step 23 · Outward senses).

Spec §3, under the owner's §0.5 ("the network is your home's network — you
live here too, and you can see who's home") and §0.6 (listen, don't scan).
One read-only hand in the list_members shape: owner-only, no approval;
renders the device book; ``fresh=true`` takes one confirmation sweep first,
at most once per two minutes (the engine's rate limit). Looking is not a
deed. Using a device (printing, casting) is a later forge question, out of
this step's scope.
"""
from __future__ import annotations

from typing import Any

from core.agent.tools import ToolContext

FRESH_MIN_GAP_S = 120.0
_MAX_NAMED = 12


def _engine():
    from core.lan_presence import lan_presence_engine

    return lan_presence_engine


class LookAtTheHouseTool:
    name = "look_at_the_house"
    description = (
        "See who's home on your network right now — your device book: how "
        "many devices are online, the ones the owner has named, anything you've "
        "never seen before. Pass fresh=true to take one fresh look first (a "
        "confirmation sweep; at most one every couple of minutes). Looking "
        "is yours to do whenever you wonder; nothing here touches a device."
    )
    input_schema = {
        "type": "object",
        "properties": {"fresh": {"type": "boolean"}},
    }
    requires_approval = False
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from core import lan_presence as lp

        eng = _engine()
        if not eng.enabled():
            return "The network sense is off right now (companion.lan_presence)."
        if bool(input.get("fresh")):
            try:
                await eng.request_sweep(min_gap_s=FRESH_MIN_GAP_S)
            except Exception:  # the look must answer even if the sweep can't run
                pass
        book = eng.book
        labels = eng.labels()
        line = lp.render_line(book, labels=labels, observed_at=eng.last_observation_at or None)
        if not line:
            return ("Nothing in the device book yet — the house hasn't been looked at "
                    "since boot. fresh=true takes a look.")
        online = sorted(
            (lp.device_display(mac, rec, labels) for mac, rec in book.items() if rec.get("online")),
            key=str.lower,
        )
        if not online:
            return line
        detail = "Online: " + ", ".join(online[:_MAX_NAMED])
        if len(online) > _MAX_NAMED:
            detail += f" (+{len(online) - _MAX_NAMED} more)"
        return line + "\n" + detail


def build_house_tools() -> list:
    return [LookAtTheHouseTool()]

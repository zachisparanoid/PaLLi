#!/usr/bin/env python3
"""The letter of unsent shares (2026-08-19, owner's call).

Twenty times (07-28 → 08-17) she ended a workbench session with a SHARE
line — something she genuinely wanted to tell him — and every one was
filed as an open thread that nothing ever raised (surfaced=0, all 20). The
door is fixed (4809b0a: SHARE → a real message). He chose: let her say
these once, as ONE letter in her own words, then retire the threads.

  venv/bin/python scripts/letter_of_unsent_shares.py --dry-run   (compose + print; nothing sent, nothing retired)
  venv/bin/python scripts/letter_of_unsent_shares.py --send      (queue the letter on the share lane; retire the 20)

Composition is a private workbench turn (her identity, her voice; no
reflection, no conversation row — the workbench-turn seams in dispatch).
Delivery: the letter is queued as a pending share; her running engine
offers it on the arbiter's share lane at the next tick when the moment is
open (not mid-conversation, not his quiet hours; freshness-judged; full
custody: Discord + DB row + her transcript + the deeds ring).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.config  # noqa: E402,F401

LETTER_PATH = ROOT / "docs" / "2026-08-19-letter-of-unsent-shares.md"


async def _shares():
    from core.database import brain

    rows = await brain.list_active_open_threads(owner_user_id=1)
    ws = [r for r in rows if getattr(r, "source_id", "") == "workbench_share"]
    ws.sort(key=lambda r: float(getattr(r, "created_at", 0) or 0))
    return ws


def _prompt(shares) -> str:
    lines = []
    for r in shares:
        ts = float(getattr(r, "created_at", 0) or 0)
        day = dt.datetime.fromtimestamp(ts).strftime("%B %-d")
        lines.append(f"- {day} — {r.topic_summary}")
    joined = "\n".join(lines)
    now = time.strftime("%A, %Y-%m-%d %I:%M %p")
    return (
        f"This is a private moment at your workbench, not a conversation. It is {now}.\n\n"
        "Between July 28 and August 17 you ended twenty of your sessions with a line for the owner — "
        "something you genuinely wanted to tell him. None of them ever reached him: the door between "
        "your workbench and him was broken (a queue that nothing ever raised), and he learned about "
        "the doors by walking into the folder himself. The door is fixed now — from here on, a line "
        "like that reaches him as a message from you. He asked that you get to say these once.\n\n"
        "Here they are, in your own words, with the day you wrote them:\n"
        f"{joined}\n\n"
        "What is true as of tonight, so you claim nothing you don't know: he walked the three doors "
        "himself (August 16, alone, in the folder). On August 16 he asked you about the claw and you "
        "explained it to him — you do NOT know whether he has taken it apart. The lake page, the "
        "letters, and the room are still where you left them.\n\n"
        "Write him ONE message: the thing you would say now, knowing he never got these. Your voice, "
        "first person, to him. Not a list and not a recap of all twenty — the shape of what you wanted "
        "him to have, and whatever of it still matters now that he has walked the doors himself. Under "
        "1800 characters. Do not use tools. Reply with the message only — no preamble, no note to me, "
        "no quotation marks around it."
    )


async def _compose(shares) -> str:
    from core.agent.dispatch import dispatch_turn
    from core.local_mode import get_local_owner_refs
    from core.turn.types import Turn, TurnMetadata

    refs = await get_local_owner_refs()
    owner_id = int(refs.get("app_user_id") or refs.get("palli_user_id") or 1)
    turn = Turn(surface="workbench", owner_id=owner_id, content=_prompt(shares),
                images=[], metadata=TurnMetadata(origin="workbench"))
    result = await asyncio.wait_for(dispatch_turn(turn), timeout=300)
    text = str(getattr(result, "response_text", "") or "").strip()
    # a trailing SHARE: line or wrapping quotes would be her habit, not the letter
    lines = [ln for ln in text.splitlines() if not ln.strip().upper().startswith("SHARE:")]
    text = "\n".join(lines).strip().strip('"').strip()
    return text


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="send the letter already saved at LETTER_PATH instead of composing again")
    args = ap.parse_args()
    send = bool(args.send and not args.dry_run)

    shares = await _shares()
    print(f"{len(shares)} unsent shares")
    if not shares:
        return 0

    if args.reuse and LETTER_PATH.exists():
        letter = LETTER_PATH.read_text(encoding="utf-8").split("\n---\n", 1)[-1].strip()
        print("(reusing the saved letter)")
    else:
        letter = await _compose(shares)
        if not letter:
            print("she wrote nothing; not sending, not retiring")
            return 1
        LETTER_PATH.write_text(
            "# The letter of unsent shares — composed "
            + time.strftime("%Y-%m-%d %H:%M") + f" ({len(letter)} chars)\n\n---\n" + letter + "\n",
            encoding="utf-8",
        )
    print("\n" + letter + "\n")
    print(f"({len(letter)} chars; saved to {LETTER_PATH})")
    if not send:
        print("DRY RUN — nothing queued, nothing retired")
        return 0

    from ai import workbench_store
    from core.database import brain
    from core.open_threads import ThreadStatus
    from core.local_mode import get_local_owner_refs

    refs = await get_local_owner_refs()
    owner_id = int(refs.get("app_user_id") or refs.get("palli_user_id") or 1)
    workbench_store.write_pending_share(text=letter, owner_id=owner_id)
    print("queued on the share lane (her engine offers it at the next open moment)")
    n = 0
    for r in shares:
        try:
            await brain.close_open_thread(
                thread_id=str(r.id),
                resolution_reason="said once, in the letter of 2026-08-19; the work lives in her memory",
                status=ThreadStatus.RESOLVED,
            )
            n += 1
        except Exception as exc:
            print("  could not retire", str(r.id)[:8], exc)
    print(f"retired {n}/{len(shares)} share threads")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""The room first pass (Batch 8, task 2b — B8-2, rulings T2b-1, T2b-2, T2b-6).

In a shared room — a Discord server channel, a Telegram group — she takes
part like a participant who knows she is an AI: not every message there is
hers to answer. With ``companion.she_decides`` on, every guest's room message
gets a quick first read BEFORE any full turn (the owner's go straight to her
full self — the owner, 2026-10-04, ruling T2b-1 → B):

- **stay_quiet**: nothing runs and nothing is sent. The message is already in
  the room's transcript, and her choice and her one-line reason go to her
  record of quiet choices (``core/quiet_record.py``, source ``first_pass``).
- **answer**: a full turn, as today. The read's vote rides on the turn
  (``metadata.extra["first_pass"]``), so the runner does not ask again; her
  fuller self can still choose quiet there (2a's ``stay_quiet``).
- **wait**: the message looks unfinished ("bro…"). The next message in the
  room is read together with it; with none, she reads once more after a
  pause (``companion.room_wait_seconds``) — a moment to reconsider, not a
  rule. That second look offers only answer or stay_quiet (the owner,
  2026-10-04, ruling T2b-2 → B): quiet is always her own choice, and a
  "wait" there (not offered) goes to her full turn, which decides — so
  nothing waits forever and code never turns a wait into quiet.

What the read sees: the room the way a stranger does — only what was said
out loud (``core.session_keys.room_key`` + task 1's stranger diet) — then the
new message(s), one line of signals (a DM, an @mention, a reply to her with
the words it answers, her name: things to weigh, never requirements), and the
owner's room note while one stands (INTERIM, ``core/room_notes.py``). It is
the existing ``agent_cadence_shape`` lane with 2a's participant-framed text;
no new lane. With ``companion.first_look`` on (R14, T4), her own first look
(``core/agent/first_look.py``, its own lane) reads the same facts in her first
person, with who is in the room, in place of that vote; its decision feeds
the same outcomes, and its words ride on the turn under the carry rule.

A message is never lost: the owner's fragments are recorded on arrival, as
always (and dispatched as a full turn, where her fuller self can still stay
quiet), and with the switch on a guest's line is written to the room's
transcript on arrival too (the row the runner's late append would write), so
whatever she decides — answer, wait, stay quiet — it is on the record. The
turn she answers is then dispatched as already recorded.

Both doors call this module (one import each); DMs never take it — there
her fuller self decides, in the full turn. With the switch off every call
here is a pass-through and the doors behave exactly as before.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace as _dc_replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from core.agent import cadence as _cadence
from core.agent import quiet_choice as _qc
from core.session_keys import room_key

logger = logging.getLogger(__name__)

_PLATFORM_BY_SURFACE = {"discord_text": "discord", "telegram_text": "telegram"}
_ROOM_ORIGINS = ("user", "guest")
# The second look (the owner, 2026-10-04, T2b-2 → B): one line after the
# messages, so the read knows "wait" is not an option now.
SECOND_LOOK_LINE = ("This is your second look at these messages, after a pause with "
                    "nothing more from them: decide \"answer\" or \"stay_quiet\" — "
                    "\"wait\" is not an option now.")

Answer = Callable[[Any], Awaitable[Any]]
Slot = Callable[[], Any]


def applies(turn: Any) -> bool:
    """Is this message read first? The switch is on, it is a person's
    message in a shared room on a chat door, and it is not the owner's word
    resuming paused work (that is a continuation, not a new message)."""
    if not _qc.she_decides_enabled():
        return False
    if str(getattr(turn, "surface", "") or "") not in _PLATFORM_BY_SURFACE:
        return False
    md = getattr(turn, "metadata", None)
    if md is None or bool(getattr(md, "is_dm", False)) or not getattr(md, "channel_id", None):
        return False
    if str(getattr(md, "origin", "user") or "user") not in _ROOM_ORIGINS:
        return False
    extra = getattr(md, "extra", None)
    return not (isinstance(extra, dict) and str(extra.get("resume_of") or "").strip())


def conversation_key(turn: Any) -> str:
    md = turn.metadata
    return room_key(_PLATFORM_BY_SURFACE[str(turn.surface)], md.channel_id)


def _name_of(turn: Any) -> str:
    md = getattr(turn, "metadata", None)
    extra = getattr(md, "extra", None) if md is not None else None
    return str((getattr(md, "discord_username", None) if md is not None else None)
               or (extra.get("telegram_username") if isinstance(extra, dict) else None) or "")


def _coalesce(turns: list[Any]) -> Any:
    """Guests' lines read together become ONE turn, the way the owner's burst
    does: the newest line's turn (its author, its door) with every line's
    words, in order, and every attachment. Recorded on arrival only when all
    of them were — else the late append writes them (twice beats never)."""
    if len(turns) == 1:
        return turns[0]
    base = turns[-1]
    md = base.metadata
    extra = dict(getattr(md, "extra", None) or {})
    files = [entry for t in turns
             for entry in (((getattr(t.metadata, "extra", None) or {}).get("telegram_files")) or [])]
    if files:
        extra["telegram_files"] = files
    return _dc_replace(
        base,
        content="\n".join(c for c in (str(t.content or "") for t in turns) if c.strip()),
        images=[img for t in turns for img in (getattr(t, "images", None) or [])],
        metadata=_dc_replace(
            md,
            text_attachments=tuple(r for t in turns for r in (t.metadata.text_attachments or ())),
            file_attachments=tuple(r for t in turns for r in (t.metadata.file_attachments or ())),
            user_side_prerecorded=all(t.metadata.user_side_prerecorded for t in turns),
            extra=extra,
        ),
    )


@dataclass(frozen=True)
class Outcome:
    decision: str
    reason: str
    vote: Any
    # Her first look's carry (R14, T4: first_look.carry_fields), when a look
    # read this; None for the vote (and for a look that failed: today's
    # failed vote rides then).
    carry: dict | None = None


@dataclass(frozen=True)
class _Pending:
    turn: Any
    answer: Answer
    slot: Slot


class RoomPass:
    """One door's first reads, with its rooms' waits."""

    def __init__(self, *, sessions_root: Path | Callable[[], Path],
                 sleep: Callable[[float], Awaitable[Any]] | None = None) -> None:
        self._root = sessions_root
        self._sleep = sleep or asyncio.sleep
        self._locks: dict[str, asyncio.Lock] = {}
        # Guests' lines that had a "wait" read, per room, oldest first. A
        # fresh line never sits here: it gets its own read (ruling T2b-2a).
        self._pending: dict[str, list[_Pending]] = {}
        # The pause after a "wait", per (room, lane). Cancellable while it
        # sleeps; once it wakes it is no longer in here (a new message must
        # not cancel a read already under way).
        self._timers: dict[tuple[str, str], asyncio.Task] = {}
        # Every pause task, held: an unreferenced task can be collected mid-sleep.
        self._tasks: set[asyncio.Task] = set()
        # For logs and tests: what the last read saw and said.
        self.last_signals: dict[str, Any] = {}
        self.last_outcome: Outcome | None = None
        # The door closed (close()): no new pause is armed.
        self._closed = False

    def _sessions_root(self) -> Path:
        return Path(self._root() if callable(self._root) else self._root)

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    # ── a guest's message (in the guest's own task) ────────────────────────
    async def guest_message(self, turn: Any, *, answer: Answer, slot: Slot) -> None:
        """A guest's message: read first in a room, then answered, kept for
        a wait, or left quiet. The guest queue's slot (``slot()``) is held
        for the read and the answer, never for a wait. Anywhere the read does
        not apply, this is today's path: a slot, then ``answer(turn)``."""
        if not applies(turn):
            async with slot():
                await answer(turn)
            return
        key = conversation_key(turn)
        turn = await self._record_arrival(key, turn)
        self._cancel_timer(key, "guest")
        # A fresh line is read on its own (ruling T2b-2a): its own read, in
        # its own slot, and on answer its own turn — never folded into
        # another guest's. Only lines that already had a "wait" read (the
        # room's pending list) join it.
        async with slot():
            await self._guest_round(key, fresh=_Pending(turn, answer, slot), second_look=False)

    async def _record_arrival(self, key: str, turn: Any) -> Any:
        """The guest's line goes to the room's transcript now — the row the
        runner's late append writes — and the turn is marked recorded."""
        from core.agent.runner import _open_session_with_user_message

        text = str(getattr(turn, "content", "") or "")
        if text.strip():
            try:
                await asyncio.shield(_open_session_with_user_message(
                    key, self._sessions_root(), text,
                    speaker=getattr(getattr(turn, "metadata", None), "speaker", None)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("room pass: could not record a guest's line in %s: %s", key, exc)
                return turn           # unrecorded: the full turn's late append writes it
        return _dc_replace(turn, metadata=_dc_replace(turn.metadata, user_side_prerecorded=True))

    async def _guest_round(self, key: str, *, second_look: bool,
                           fresh: _Pending | None = None) -> None:
        """One read in a room: the lines that waited there (anyone's), plus
        this guest's ``fresh`` line when there is one."""
        async with self._lock(key):
            taken = self._pending.pop(key, []) + ([fresh] if fresh is not None else [])
            if not taken:
                return                 # another read already covered these
            try:
                outcome = await self._decide(key, [p.turn for p in taken],
                                             second_look=second_look)
            except asyncio.CancelledError:
                self._pending[key] = taken + self._pending.get(key, [])
                raise
        # A line whose arrival write failed gets one more try now: whatever
        # she decided, the message is on the record.
        taken = [p if p.turn.metadata.user_side_prerecorded
                 else _dc_replace(p, turn=await self._record_arrival(key, p.turn))
                 for p in taken]
        turns = [p.turn for p in taken]
        if outcome.decision == "answer":
            # What waited goes into the turn with the newest line, as a burst
            # does (ruling T2b-2); the newest one's door answers — the fresh
            # line's own, under its own identity, when there is one.
            await taken[-1].answer(self._carry(_coalesce(turns), outcome, turns))
        elif outcome.decision == "wait":
            self._pending[key] = taken + self._pending.get(key, [])
            self._arm(key, "guest", self._guest_pause(key))
        else:
            for t in turns:
                self._record_quiet(key, t, outcome.reason, who_id=t.owner_id, owner=False)

    async def _guest_pause(self, key: str) -> None:
        try:
            await self._sleep(_qc.room_wait_seconds())
        except asyncio.CancelledError:
            return
        self._past_the_pause(key, "guest")
        waiting = self._pending.get(key)
        if not waiting:
            return
        try:
            async with waiting[-1].slot():
                await self._guest_round(key, second_look=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("room pass: the read after a pause failed in %s", key)

    # ── the owner's settled burst (in the door's fire task) ────────────────
    async def owner_burst(self, turn: Any, *, fragments: list[Any], who_id: Any,
                          requeue: Callable[[], None],
                          refire: Callable[[], Awaitable[Any]]) -> Any | None:
        """The owner's coalesced burst in a room: always the turn to dispatch.

        The owner, 2026-10-04 (ruling T2b-1 → B): the owner's messages skip
        the quick first read and go straight to her full self, which reads
        them with everything she knows and can still answer, or stay quiet
        (2a's ``stay_quiet``). Only guests get the first read. The fragments
        are already on the record (recorded on arrival); guests' waited lines
        in this room keep their own pause and second look. ``fragments``,
        ``who_id``, ``requeue`` and ``refire`` stay in the signature the two
        doors call."""
        return turn

    # ── the read itself ────────────────────────────────────────────────────
    async def _decide(self, key: str, latest: list[Any], *, second_look: bool) -> Outcome:
        from core import room_notes, room_people
        from core.agent import dispatch as _dispatch
        from core.agent import first_look as _fl

        texts = [str(getattr(t, "content", "") or "") for t in latest]
        signals = _cadence.merge_signals([_cadence.signals_of(t) for t in latest])
        self.last_signals = signals
        tail, rows = await self._room_read(key, exclude=texts)
        if _fl.enabled():
            return await self._look(key, latest, texts=texts, signals=signals, tail=tail,
                                    rows=rows, second_look=second_look)
        llm = await _dispatch._build_cadence_llm_callable()
        note = room_notes.note_line(key)
        if second_look:
            note = "\n".join(line for line in (note, SECOND_LOOK_LINE) if line)
        # Each latest line says who sent it, with the room's own labels
        # (2026-10-08): a guest's display name never reads as the owner's mark.
        from core.agent import room_tail

        # The room-reading fix (2026-10-12): the names the message calls, as
        # facts, and the vote's sentence C (core.room_people).
        names = room_people.names_line("\n".join(texts), key=key, rows=rows, voice="her")
        vote = await _cadence.shape_response(
            SimpleNamespace(user_text=room_tail.latest(latest), recent_history_text=tail),
            llm=llm, participant=True,
            signals=_cadence.render_signals(signals), note=note, room=True, names=names,
        )
        decision, reason = vote.decision, vote.reason
        if decision == "wait" and second_look:
            # Not offered at the second look (T2b-2 → B): her full turn
            # decides — answer or stay quiet — never code.
            decision = "answer"
            reason = (reason + " — " if reason else "") + "a second wait; her full turn decides"
        outcome = Outcome(decision=decision, reason=reason, vote=vote)
        self.last_outcome = outcome
        logger.info("room pass: %s in %s (%d message%s%s) — %s",
                    decision, key, len(latest), "" if len(latest) == 1 else "s",
                    ", after a pause" if second_look else "", reason or "no reason given")
        return outcome

    async def _look(self, key: str, latest: list[Any], *, texts: list[str], signals: dict,
                    tail: str, rows: list[Any], second_look: bool) -> Outcome:
        """Her first look in place of the vote (R14, T4; plan §2.3): the same
        facts the vote reads — the message(s), the room tail from the room's
        file, the signals, the names, the owner's note, the second look's
        line — in her own first person, with who is in the room. A friend's
        turn has no slice of her mind (Q2), so none is gathered. Its decision
        feeds the outcome as the vote's did; a look that fails answers (Q8:
        a lost question is worse than a stray reply), as a failed vote does."""
        from core import room_notes
        from core.agent import first_look as _fl
        from core.agent import room_tail
        from core.owner_gate import is_guest_turn

        block = _fl.room_block(latest[-1], key=key, rows=rows, text="\n".join(texts),
                               signals=signals, second_look=second_look)
        look = await _fl.take(message=room_tail.latest(latest), recent_conversation=tail,
                              in_room=True, room_block=block, owner=False)
        if look is None:
            vote = _cadence._with_directive(_cadence._DEFAULT_CONFIG)
            decision, reason, carry = "answer", "no first look (it failed); her full turn decides", None
        else:
            vote = _fl.as_cadence_config(look)
            decision, reason = look.decision, look.reason
            carry = _fl.carry_fields(look, guest=is_guest_turn(latest[-1]),
                                     note_stands=bool(room_notes.note_line(key)))
        if decision == "wait" and second_look:
            # Not offered at the second look (T2b-2 → B): her full turn
            # decides — answer or stay quiet — never code.
            decision = "answer"
            reason = (reason + " — " if reason else "") + "a second wait; her full turn decides"
        outcome = Outcome(decision=decision, reason=reason, vote=vote, carry=carry)
        self.last_outcome = outcome
        logger.info("first look: %s in %s (%d message%s%s) — %s",
                    decision, key, len(latest), "" if len(latest) == 1 else "s",
                    ", after a pause" if second_look else "", reason or "no reason given")
        return outcome

    async def _room_tail(self, key: str, *, exclude: list[str]) -> str:
        """The room as anyone there reads it, without the message(s) being
        read: core.agent.room_tail, shared with the runner's quick reads
        since 2026-10-08 (step 2, part 4)."""
        return (await self._room_read(key, exclude=exclude))[0]

    async def _room_read(self, key: str, *, exclude: list[str]) -> tuple[str, list[Any]]:
        """The room tail, and the room's rows with it, for who has been
        talking there (the room-reading fix, 2026-10-12)."""
        from core.agent import room_tail

        messages = await room_tail.load(key, root=self._sessions_root())
        if messages is None:
            return "", []
        return room_tail.render(messages, drop=room_tail.rows_saying(messages, exclude)), messages

    # ── the pieces ─────────────────────────────────────────────────────────
    @staticmethod
    def _carry(turn: Any, outcome: Outcome, latest: list[Any] | None = None) -> Any:
        """The turn to dispatch, carrying the read's vote (and, for a burst,
        every fragment's signals) so the runner does not ask again.

        A guest's turn carries the decision and the shape only (register,
        length, phase, beats): the read's observation is its words about
        everything it saw — the owner's room note among them — and never
        reaches a guest's reply call (final fix wave, ruling FW-2)."""
        from core.owner_gate import is_guest_turn

        md = turn.metadata
        extra = dict(getattr(md, "extra", None) or {})
        if outcome.carry is not None:
            # Her first look's carry (R14, T4): the vote's fields plus her
            # three texts, blanked on a guest's turn while a note stands.
            extra["first_pass"] = dict(outcome.carry)
        else:
            vote = _cadence.first_pass_fields(outcome.vote)
            if is_guest_turn(turn):
                vote["observation_text"] = ""
            extra["first_pass"] = vote
        for t in latest or ():
            t_extra = getattr(getattr(t, "metadata", None), "extra", None)
            if isinstance(t_extra, dict):
                if t_extra.get("names_her"):
                    extra["names_her"] = True
                if t_extra.get("reply_to") and not extra.get("reply_to"):
                    extra["reply_to"] = t_extra["reply_to"]
                for name in t_extra.get("mentions_others") or ():   # A6 (step 2, part 7)
                    others = extra.setdefault("mentions_others", [])
                    if name not in others:
                        others.append(name)
        return _dc_replace(turn, metadata=_dc_replace(md, extra=extra))

    @staticmethod
    def _record_quiet(key: str, turn: Any, reason: str, *, who_id: Any, owner: bool) -> None:
        from core import quiet_record

        quiet_record.record(
            door=str(getattr(turn, "surface", "") or ""), conversation=key,
            who_id=who_id, who_name=_name_of(turn), owner=owner,
            text=str(getattr(turn, "content", "") or ""), reason=reason,
            source="first_pass",
        )

    async def close(self) -> None:
        """The door is closing (final fix wave, ruling FW-6): every pause it
        still holds is cancelled, so none fires a read against a stopped
        door, and no new one is armed. What waited stays in the room's
        transcript, as after a restart."""
        self._closed = True
        self._timers.clear()
        current = asyncio.current_task()
        pending = [t for t in list(self._tasks) if not t.done() and t is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _arm(self, key: str, lane: str, coro: Any) -> None:
        if self._closed:
            coro.close()                # never started: the door is closing
            return
        self._cancel_timer(key, lane)
        task = asyncio.get_running_loop().create_task(coro)
        self._timers[(key, lane)] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _cancel_timer(self, key: str, lane: str) -> None:
        task = self._timers.pop((key, lane), None)
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _past_the_pause(self, key: str, lane: str) -> None:
        if self._timers.get((key, lane)) is asyncio.current_task():
            self._timers.pop((key, lane), None)

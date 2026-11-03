"""The owner's private-chat lane: one burst is one turn; a message while
she is replying either cancels the reply (barge-in) or, with
companion.converse_while_working on, goes through the steering fork — folded
into the running task, a sidecar, a supersede, or a cooperative stop — and
duplex pauses resume or expire. Moved out of bots/telegram/client.py for the
mobile door (2026-10-13) so the phone app behaves as Telegram does, from one
copy. Everything a door owns is a hook (LaneHooks)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from bots.common import reply_wait
from bots.common import steering as steering_fork
from bots.common.burst import BurstCoalescer
from core.agent import steering as _steering
from core.agent.runner import _derive_session_key, _open_session_with_user_message
from core.agent.steering_reply import judge_mid_task, judge_supersede

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LaneHooks:
    """What the lane asks of its door. Each is looked up when it is called."""

    door: str                                         # the door's name (telegram, mobile): the session-key prefix and reply_wait's door
    log_name: str                                     # the log prefix; Telegram keeps its old one (telegram client)
    dispatch: Callable[[Any], Awaitable[Any]]         # (turn) -> its result: the door's turn dispatch
    record: Callable[..., Awaitable[None]]            # (turn, *, sessions_root, transcript=True): the record on arrival
    send_text: Callable[[int, str], Awaitable[Any]]   # a mid-task line into the chat
    deliver: Callable[..., Awaitable[None]]           # (chat_id, result, *, is_voice=False, waiting_since=None, during_run=0, wait_kind="")
    typing: Callable[[int], Any]                      # chat_id -> an async context manager
    owner_ok: Callable[[int, set], Awaitable[bool]]   # (chat_id, authors) -> are they the owner alone?
    owner_burst: Callable[..., Awaitable[Any]]        # (turn, *, fragments, who_id, requeue, refire) -> turn | None
    sessions_root: Callable[[], Path]


class OwnerLane:
    """Coalesces a rapid burst of the owner's messages into one turn.

    Fragments are recorded on ARRIVAL (so a barge-in can never erase words
    he actually sent), buffered per chat, and fed to a BurstCoalescer that
    owns the settle timing; and a message arriving while she is mid-reply
    either steers the running turn or cancels it, judged at fire time.
    """

    def __init__(self, hooks: LaneHooks, *, settle: float, typing_window: float, max_hold: float,
                 on_fire: Callable[[int], Awaitable[None]] | None = None,
                 end_typing_on_message: bool = False) -> None:
        self._hooks = hooks
        # What the coalescer (and a room read's refire) calls when a burst
        # settles. A door may route it through its own method, so a test that
        # replaces that method replaces what fires.
        self._on_fire = on_fire or self.fire_burst
        # A door that hears "is typing" ends the author's typing with their
        # message; one that never hears it leaves the settle window to it.
        self._end_typing_on_message = end_typing_on_message

        # chat_id -> the asyncio task currently dispatching a coalesced reply
        # for that chat. arrive reads this map to barge-in cancel when a
        # new message arrives mid-reply; fire_burst registers itself on
        # entry and clears it on exit.
        self.inflight_tasks: dict[int, asyncio.Task] = {}
        # Step 27 Phase B (spec §3): a paused run's expiry clock, per chat.
        self.pause_timers: dict[int, asyncio.Task] = {}
        # Step 27 Phase B (spec §1/§4): live sidecars per chat —
        # child_key → (kind, task). Stop fans out to the task ones.
        self.sidecars: dict[int, dict[str, tuple[str, asyncio.Task]]] = {}
        # chat_id -> [Turn, ...] fragments recorded-on-arrival since the last
        # settle. fire_burst pops this and coalesces it into one turn.
        self.burst_fragments: dict[int, list] = {}
        # chat_id -> {the door's raw author ids} for the fragments buffered
        # since the last settle. The steering fork's owner-only gate (spec
        # §5.1) reads this at fire time: Turn.owner_id is the configured app
        # owner on his turns, so it cannot distinguish authors — the raw
        # author id can. Fail-closed: empty or mixed authorship never enters
        # the steer lane.
        self.burst_authors: dict[int, set[int]] = {}
        # chat_id -> [Turn, ...] fragments whose arrival-time transcript
        # append was SKIPPED because they took the steer lane (spec §4e: the
        # runner is the sole transcript writer during a run). fire_burst
        # repairs these before a normal dispatch, so no word of his can
        # vanish from the record.
        self.burst_unappended: dict[int, list] = {}
        # chat_id -> (first, last) arrival of the buffered fragments
        # (time.monotonic(), 2026-10-12): the wait behind the reply. Logged with
        # the reply.
        self.burst_times: dict[int, reply_wait.BurstTimes] = {}
        # chat_id -> how many of the owner's messages have come in (Fable's
        # review, 10-12), for the wait line of a run that more came into.
        self.owner_messages_in: dict[int, int] = {}
        # The cooperative stops' hard-cancel backstops. Held: an
        # unreferenced task can be garbage-collected mid-sleep and never fire.
        self.duplex_stop_backstops: set[asyncio.Task] = set()
        self.coalescer = BurstCoalescer(on_fire=lambda chat_id: self._on_fire(chat_id), settle=settle,
                                        typing_window=typing_window, max_hold=max_hold)

    def note_typing(self, chat_id: int, user_id: int | None = None) -> None:
        self.coalescer.note_typing(chat_id, user_id)

    def _note_message(self, chat_id: int, author_id: int) -> None:
        if self._end_typing_on_message:
            self.coalescer.note_message(chat_id, author_id)
        else:
            self.coalescer.note_message(chat_id)

    async def arrive(self, chat_id: int, turn: Any, *, author_id: int, has_attachment: bool,
                     resolve: Callable[[Any], Awaitable[Any]]) -> None:
        """One of HIS messages, past the door's owner gate: the barge-in or
        steer decision for a run in flight, then the door's ``resolve`` (its
        files down, its voice heard), the record on arrival, and the burst."""
        # Barge-in vs steer (spec §4b): a message arriving while she is
        # mid-reply used to always cancel the in-flight coalesced reply. With
        # companion.converse_while_working ON, an OWNER-authored non-stop
        # message lets the run keep going — the coalescer routes the burst
        # through the judged fork at fire time. Only he reaches this far.
        # Flag OFF is the legacy branch below, unchanged.
        session_key = f"{self._hooks.door}:{chat_id}"
        prior = self.inflight_tasks.get(chat_id)
        run_in_flight = prior is not None and not prior.done()
        steer_lane = False
        if run_in_flight and steering_fork._flag_on():
            content = str(getattr(turn, "content", "") or "")
            is_stop = steering_fork.is_stop_command(content)
            # Attachment-carrying messages (photos, files, voice) never take
            # the steer lane: the fork declines them at fire time (spec §5.2),
            # so skipping their arrival append here would only orphan them.
            if not is_stop and not has_attachment:
                # Phase-owned routing (2026-08-04): only a run that has
                # actually spoken (generating) or actually worked (a real
                # tool call) earns the deferred lane. A run still in PREP has
                # shown him nothing — cancelling and recoalescing is strictly
                # better than steering it.
                steer_lane = (
                    _steering.is_working(session_key)
                    or _steering.get_run_phase(session_key) == "generating"
                )
            if not steer_lane:
                self.inflight_tasks.pop(chat_id, None)
                logger.info(
                    "%s: cancelling in-flight reply for chat %s (%s)",
                    self._hooks.log_name, chat_id, "stop command" if is_stop else "barge-in",
                )
                from core.agent import duplex as _dx
                if is_stop and steering_fork.stop_mode(
                    duplex_on=_dx.duplex_enabled(),
                    working=_steering.is_working(session_key),
                ) == "cooperative":
                    # Step 27 (spec §3): stop at the next tool boundary; the
                    # hard cancel stays as the backstop after the grace.
                    _steering.request_stop(session_key)
                    # Phase B (spec §3, correction 8): the stop reaches the
                    # TASK sidecars too — a conversation in flight finishes.
                    for _ck in steering_fork.sidecar_keys_for(
                            session_key, self.sidecars.get(chat_id, {})):
                        _steering.request_stop(_ck)
                    _grace = float(_dx._knob("duplex_stop_grace_seconds", 30))

                    async def _hard_cancel_after(task=prior, grace=_grace):
                        await asyncio.sleep(grace)
                        if not task.done():
                            logger.warning(
                                "%s: cooperative stop overran "
                                "%.0fs; hard cancel", self._hooks.log_name, grace)
                            task.cancel()
                    # Hold the reference: an unreferenced task can be
                    # garbage-collected mid-sleep and never fire.
                    _bg = self.duplex_stop_backstops
                    _t = asyncio.create_task(_hard_cancel_after())
                    _bg.add(_t)
                    _t.add_done_callback(_bg.discard)
                else:
                    prior.cancel()
                # Queued steers were about work that is now dead (spec §5.3).
                _steering.clear(session_key)
                steering_fork.reset_reply_budget(session_key)
        elif run_in_flight:
            self.inflight_tasks.pop(chat_id, None)
            logger.info(
                "%s: barge-in — cancelling in-flight reply for "
                "chat %s", self._hooks.log_name, chat_id,
            )
            prior.cancel()

        # The door's own preparation, after intake and before the record:
        # the fragment written to his transcript on arrival must already be
        # what he sent — a file at a local path, a voice note as what he SAID.
        turn = await resolve(turn)

        # Record each fragment on arrival (transcript shielded, DB detached),
        # then buffer it and feed the coalescer. The single coalesced turn
        # dispatched on settle carries user_side_prerecorded=True so the
        # engine doesn't re-append/re-persist the user side. Steered fragments
        # skip the transcript half only (spec §4e).
        await self._hooks.record(
            turn, sessions_root=self._hooks.sessions_root(), transcript=not steer_lane,
        )
        self.burst_fragments.setdefault(chat_id, []).append(turn)
        if steer_lane:
            self.burst_unappended.setdefault(chat_id, []).append(turn)
        self.burst_authors.setdefault(chat_id, set()).add(int(author_id))
        self.burst_times[chat_id] = reply_wait.stamp(self.burst_times.get(chat_id))
        self.owner_messages_in[chat_id] = self.owner_messages_in.get(chat_id, 0) + 1
        self._note_message(chat_id, author_id)

    def requeue_burst(self, chat_id: int, fragments: list, authors: set, times: Any = None) -> None:
        """Batch 8, task 2b (ruling T2b-2): a room read said "wait" — the
        owner's fragments go back into the burst (they are already on the record),
        ahead of anything that arrived meanwhile, for the next read. Their
        arrival times go back with them (2026-10-12)."""
        self.burst_fragments[chat_id] = list(fragments) + self.burst_fragments.get(
            chat_id, [])
        self.burst_authors.setdefault(chat_id, set()).update(authors)
        merged = reply_wait.merge(times, self.burst_times.get(chat_id))
        if merged is not None:
            self.burst_times[chat_id] = merged

    async def fire_burst(self, chat_id: int) -> None:
        """Coalesce a settled burst's buffered fragments into ONE turn and
        dispatch it as an owned in-flight task (so arrive can barge-in
        cancel it). No-op when the buffer is empty."""
        fragments = self.burst_fragments.pop(chat_id, [])
        authors = self.burst_authors.pop(chat_id, set())
        unappended = self.burst_unappended.pop(chat_id, [])
        times = self.burst_times.pop(chat_id, None)
        if not fragments:
            return
        base = fragments[-1]
        joined = "\n".join(
            str(f.content or "") for f in fragments if str(f.content or "").strip()
        )
        images = [
            img for f in fragments for img in (getattr(f, "images", []) or [])
        ]
        # Every bucket is re-collected across the burst: pre-fix on Discord
        # only the LAST fragment's attachments survived the coalesce, so a
        # file dropped mid-burst was silently lost.
        text_attachments = tuple(
            r for f in fragments
            for r in (getattr(f.metadata, "text_attachments", ()) or ())
        )
        file_attachments = tuple(
            r for f in fragments
            for r in (getattr(f.metadata, "file_attachments", ()) or ())
        )
        # A6 (step 2, part 8): an @mention of someone else in any fragment is
        # the burst's; the base keeps only the last fragment's extras.
        extra = dict(getattr(base.metadata, "extra", None) or {})
        others = [n for n in dict.fromkeys(
            n for f in fragments
            for n in ((getattr(f.metadata, "extra", None) or {}).get("mentions_others") or ()))]
        if others:
            extra["mentions_others"] = others
        # The room-reading fix (2026-10-12): in a group, her name, a reply and
        # an @mention in any fragment are the burst's too; a reply to her is
        # named first. A DM's burst is today's (the owner's word: private
        # chats stay exactly as they are).
        burst_flags: dict = {}
        if not getattr(base.metadata, "is_dm", False):
            frag_extras = [getattr(f.metadata, "extra", None) or {} for f in fragments]
            if any(e.get("names_her") for e in frag_extras):
                extra["names_her"] = True
            replies = [e["reply_to"] for e in frag_extras if isinstance(e.get("reply_to"), dict)]
            if replies:
                extra["reply_to"] = next((r for r in replies if r.get("to_her") is True),
                                         replies[0])
            burst_flags = {
                "is_mention": any(bool(getattr(f.metadata, "is_mention", False))
                                  for f in fragments),
                "is_reply_to_bot": any(bool(getattr(f.metadata, "is_reply_to_bot", False))
                                       for f in fragments),
            }
        md = _dc_replace(
            base.metadata, user_side_prerecorded=True,
            text_attachments=text_attachments, file_attachments=file_attachments, extra=extra,
            **burst_flags,
        )
        coalesced = _dc_replace(base, content=joined, images=images, metadata=md)

        # Mid-task fork (spec §4b): with a run still in flight at fire time,
        # this burst is steering input, not a fresh turn.
        superseded = False
        prior = self.inflight_tasks.get(chat_id)
        if prior is not None and not prior.done():
            # The 2026-08-23 seam: a plain reply's task outlives its
            # generation by a second or two (delivery, the sweep). A burst
            # that fires in that tail is NOT mid-task input — it waits for the
            # task and becomes an ordinary turn.
            _sk = _derive_session_key(coalesced)
            if steering_fork.route_mid_run(
                working=_steering.is_working(_sk),
                phase=_steering.get_run_phase(_sk),
                prior_done=prior.done(),
            ) == "wait_then_normal":
                logger.info(
                    "%s: prior reply finishing (no tool calls, not "
                    "generating) — waiting, then a normal turn for chat %s",
                    self._hooks.log_name, chat_id,
                )
                await asyncio.wait({prior}, timeout=steering_fork.TAIL_WAIT_SECONDS)
        if prior is not None and not prior.done():
            session_key = _derive_session_key(coalesced)
            owner_ok = await self._hooks.owner_ok(chat_id, authors)

            async def _send(text: str) -> None:
                await self._hooks.send_text(chat_id, text)
                # A quick reply while she works is timed too (10-12).
                reply_wait.log_reply_wait(self._hooks.door, chat_id, times, kind="mid-task reply")

            # Phase-owned routing (2026-08-04): a conversational run that is
            # mid-generation (zero tool calls) is not work — steering's
            # one-liners are the wrong tool for it. One judged binary instead:
            # does this change what she is saying right now? Supersede cancels
            # and regenerates from the full bundle. The judge fails open.
            if (
                steering_fork._flag_on()
                and owner_ok
                and not _steering.is_working(session_key)
                and _steering.get_run_phase(session_key) == "generating"
            ):
                if await judge_supersede(coalesced, session_key=session_key):
                    superseded = True
                    self.inflight_tasks.pop(chat_id, None)
                    logger.info(
                        "%s: supersede — cancelling in-flight "
                        "reply for chat %s", self._hooks.log_name, chat_id,
                    )
                    prior.cancel()
                    _steering.clear(session_key)
                    steering_fork.reset_reply_budget(session_key)
                    # Fall through to the transcript repair + normal dispatch.
                else:
                    bundle = _steering.SteerBundle(
                        turn=coalesced, enqueued_at=time.time(),
                    )
                    if _steering.enqueue(session_key, bundle):
                        steering_fork.arm_deadline(
                            session_key, bundle, self._hooks.dispatch,
                            sessions_root=self._hooks.sessions_root(),
                        )
                        if prior.done():
                            # Fast-run race: the reply finished while we
                            # judged — sweep now so the follow-up fires.
                            try:
                                await steering_fork.sweep_after_run(
                                    session_key, dispatch=self._hooks.dispatch,
                                    sessions_root=self._hooks.sessions_root())
                            except Exception:
                                logger.exception(
                                    "%s: steering sweep failed", self._hooks.log_name)
                        return
                    # Queue full: fall through.

            if not superseded:
                async def _launch_sidecar(turn, *, kind, on_done):
                    # Step 27 Phase B (spec §1/§4): a full turn of her beside
                    # the run, on a child session; its result rides the same
                    # delivery path as the run's own.
                    from core.agent import sidecar as _sc
                    _reg = self.sidecars.setdefault(chat_id, {})

                    async def _deliver(result):
                        await self._hooks.deliver(
                            chat_id, result, waiting_since=times,
                            wait_kind="side conversation" if kind == "talk" else "side task")

                    _h = _sc.run_sidecar(parent_key=session_key, turn=turn,
                                         kind=kind, dispatch=self._hooks.dispatch,
                                         deliver=_deliver,
                                         sessions_root=self._hooks.sessions_root(),
                                         on_done=on_done)
                    if _h is not None:
                        _reg[_h.key] = (kind, _h.task)
                        logger.info("%s: %s sidecar %s launched "
                                    "beside %s", self._hooks.log_name, kind, _h.key, session_key)
                    return _h

                taken = await steering_fork.handle_mid_task(
                    coalesced, session_key=session_key, is_owner=owner_ok,
                    send=_send, run_in_flight=True, dispatch=self._hooks.dispatch,
                    sessions_root=self._hooks.sessions_root(),
                    launch_sidecar=_launch_sidecar,
                )
                if taken:
                    if prior.done():
                        # Fast-run race (spec §5.4): the run finished while the
                        # judgment was in flight, so nothing will ever drain
                        # this bundle. Sweep it into a normal turn now.
                        try:
                            await steering_fork.sweep_after_run(
                                session_key, dispatch=self._hooks.dispatch,
                                sessions_root=self._hooks.sessions_root())
                        except Exception:
                            logger.exception(
                                "%s: steering sweep failed", self._hooks.log_name)
                    return
                if not prior.done():
                    # Fork declined (judged stop, non-owner, attachments,
                    # queue full): keep today's contract — cancel the run,
                    # then dispatch this burst as a normal turn below. Without
                    # this cancel the old run and the new dispatch would
                    # interleave writes on ONE session.
                    self.inflight_tasks.pop(chat_id, None)
                    logger.info(
                        "%s: steering fork declined — cancelling "
                        "in-flight reply for chat %s", self._hooks.log_name, chat_id,
                    )
                    prior.cancel()
                    _steering.clear(session_key)
                    steering_fork.reset_reply_budget(session_key)

        # Transcript repair for the non-taken paths: fragments that skipped
        # their arrival append (steer lane, spec §4e) are about to dispatch as
        # a NORMAL prerecorded turn, whose runner path appends nothing. Write
        # them now, in arrival order, so his words reach the record exactly
        # once whichever lane the burst ends up on.
        for _t in unappended:
            _text = str(getattr(_t, "content", "") or "")
            if not _text.strip():
                continue
            try:
                await asyncio.shield(_open_session_with_user_message(
                    _derive_session_key(_t), self._hooks.sessions_root(), _text,
                    speaker=getattr(getattr(_t, "metadata", None), "speaker", None),
                ))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "%s: steer-lane transcript repair failed", self._hooks.log_name)

        # Step 27 Phase B (spec §3): a paused run waits on his word. The judge
        # reads his message against the paused work: "resume" continues it;
        # "stop" closes the pause and its clock; anything else is an ordinary
        # turn and the pause stands. Either way the message dispatches below,
        # carrying a trailing note the runner renders for the model.
        from core.agent import duplex as _dx
        _sk_p = _derive_session_key(coalesced)
        _paused = _steering.get_paused(_sk_p) if _dx.duplex_enabled() else None
        if _paused is not None:
            _pj = await judge_mid_task(coalesced, session_key=_sk_p,
                                       paused_doing=_paused.now_doing)
            _route = steering_fork.route_paused(action=_pj.action)
            _pb_extra = dict(getattr(coalesced.metadata, "extra", None) or {})
            if _route == "resume":
                _steering.clear_paused(_sk_p)
                _pt = self.pause_timers.pop(chat_id, None)
                if _pt is not None:
                    _pt.cancel()
                _pb_extra["resume_of"] = _paused.now_doing
                logger.info("%s: resuming paused run on chat %s",
                            self._hooks.log_name, chat_id)
            elif _route == "stop":
                _steering.clear_paused(_sk_p)
                _pt = self.pause_timers.pop(chat_id, None)
                if _pt is not None:
                    _pt.cancel()
                _steering.clear(_sk_p)
                logger.info("%s: paused run dropped on his word, "
                            "chat %s", self._hooks.log_name, chat_id)
            else:
                _pb_extra["paused_task"] = _paused.now_doing
            coalesced = _dc_replace(
                coalesced,
                metadata=_dc_replace(coalesced.metadata, extra=_pb_extra))

        current = asyncio.current_task()
        self.inflight_tasks[chat_id] = current
        _terminated_by = ""
        try:
            # She decides (Batch 8, task 2b — rulings T2b-1, T2b-2): in a
            # group with companion.she_decides on, the owner's burst is read
            # first, BEFORE "typing…" shows — answer runs the turn below, stay
            # quiet or wait runs nothing (the words are already on the record). A
            # pass-through otherwise.
            _turn = await self._hooks.owner_burst(
                coalesced, fragments=fragments, who_id=next(iter(authors), None),
                requeue=lambda: self.requeue_burst(chat_id, fragments, authors, times),
                refire=lambda: self._on_fire(chat_id),
            )
            if _turn is None:
                return
            _messages_before = self.owner_messages_in.get(chat_id, 0)
            async with self._hooks.typing(chat_id):
                result = await self._hooks.dispatch(_turn)
            _terminated_by = str(getattr(result, "terminated_by", "") or "")
            # The burst's LAST message decides (task 5): if he finished by
            # speaking, she may answer out loud. His own switch, per chat.
            await self._hooks.deliver(
                chat_id, result,
                is_voice=bool(getattr(coalesced.metadata, "is_voice", False)),
                waiting_since=times,
                during_run=self.owner_messages_in.get(chat_id, 0) - _messages_before,
            )
        except asyncio.CancelledError:
            logger.info(
                "%s: coalesced reply cancelled (barge-in) chat %s",
                self._hooks.log_name, chat_id,
            )
            raise
        except Exception:
            logger.exception("%s: coalesced turn failed", self._hooks.log_name)
        finally:
            if self.inflight_tasks.get(chat_id) is current:
                self.inflight_tasks.pop(chat_id, None)
            if chat_id in self.sidecars:
                self.sidecars[chat_id] = {
                    k: v for k, v in self.sidecars[chat_id].items()
                    if not v[1].done()}
            if _terminated_by == "paused":
                # Step 27 Phase B (spec §3): the run set its work down on his
                # word. No sweep — leftovers drain at the next run on this
                # session. Arm the honest expiry: a pause nobody returns to is
                # told once, never left silent, and told on THIS door (the
                # platform is named, or the line would go out of Discord).
                try:
                    from core.agent import duplex as _dxx
                    _old = self.pause_timers.pop(chat_id, None)
                    if _old is not None:
                        _old.cancel()
                    self.pause_timers[chat_id] = steering_fork.arm_pause_expiry(
                        _derive_session_key(coalesced),
                        ttl_minutes=int(_dxx._knob("duplex_pause_ttl_minutes", 120)),
                    )
                    logger.info("%s: run paused on chat %s; "
                                "expiry armed", self._hooks.log_name, chat_id)
                except Exception:
                    logger.exception(
                        "%s: pause expiry arming failed", self._hooks.log_name)
            elif steering_fork._flag_on():
                # Post-run sweep (spec §4f): whatever this run never answered
                # becomes a real turn now, oldest first.
                try:
                    await steering_fork.sweep_after_run(
                        _derive_session_key(coalesced), dispatch=self._hooks.dispatch,
                        sessions_root=self._hooks.sessions_root())
                except Exception:
                    logger.exception("%s: steering sweep failed", self._hooks.log_name)

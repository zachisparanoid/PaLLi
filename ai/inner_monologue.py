"""
Inner monologue background engine.

Runs a periodic tick that gathers PaLLi's current situation, recent conversation,
and recent monologue entries, then makes a single LLM call to produce a private thought.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from ai.inner_monologue_store import (
    MonologueEntry,
    _normalize_candidate,
    count_today_ticks,
    format_entries_for_prompt,
    read_recent_for_context,
    write_entry,
)
from ai.llm_client import llm
from ai.llm_routing import get_task_model
from ai.prompt_loader import prompt_loader
from ai.sentiment.inference import build_reading_from_llm_output
from ai.situation_context import build_situation_context
from core.config import config
from core.database import brain
from core.identity import identity_service
from core.llm_json import safe_parse_json_object
from core.local_mode import get_local_owner_refs
from core.prompt_format import format_prompt
from core.setup_mode import lane_idle

logger = logging.getLogger(__name__)


def _wanting_prompt_on() -> bool:
    """Fail-closed flag read for the prompt variant (step 20)."""
    try:
        from core.turn.companion_flags import companion_settings

        c = companion_settings()
        return c is not None and bool(getattr(c, "wanting", False))
    except Exception:
        return False


def _select_monologue_template() -> str | None:
    """The two-file law (Slice-3 pattern): flag-on loads the wanting
    variant; flag-off — or a missing variant file — loads the untouched
    base, so off is byte-identical by construction."""
    if _wanting_prompt_on():
        variant = prompt_loader.load_prompt("inner_monologue_wanting")
        if variant:
            return variant
        logger.warning(
            "inner_monologue: wanting variant prompt missing; using base"
        )
    return prompt_loader.load_prompt("inner_monologue")
essential_logger = logging.getLogger("palli.essential")

# Tick frequency bounds (seconds)
MIN_TICK_INTERVAL = 30 * 60   # 30 minutes
MAX_TICK_INTERVAL = 90 * 60   # 90 minutes
IDLE_TICK_INTERVAL = 60 * 60  # 60 minutes when nothing is happening


def _companion_for_life():
    try:
        from core.turn.companion_flags import companion_settings

        return companion_settings()
    except Exception:
        return None


def _recent_dives_lines(limit: int = 2) -> list[str]:
    """Her last dives, one line each (curiosity entries). Empty on any
    failure or when the faculty is off."""
    try:
        from ai.curiosity_research import recent_research_entries

        out: list[str] = []
        now = time.time()
        for e in recent_research_entries(days_back=3, limit=limit):
            topic = str((e.tick_metadata or {}).get("topic") or "").strip()
            hrs = max(0.0, (now - float(e.timestamp)) / 3600.0)
            when = "just now" if hrs < 1 else f"~{int(hrs)}h ago"
            out.append(f"- a dive on {topic or 'something that pulled'} ({when})")
        return out
    except Exception:
        return []


def _own_life_block() -> str:
    """Her own life lately (item 6, 2026-08-19): what SHE did and wanted —
    recent deeds (the ring), her last daydream line, her last dives — as one
    small block so the monologue has her own life to think from, not only
    his. Each piece degrades to absence; "" when nothing at all (quiet is
    honest; the block is byte-absent then)."""
    lines: list[str] = []
    try:
        from ai.recent_deeds import read_recent_deeds

        now = time.time()
        for d in read_recent_deeds(limit=5, max_age_hours=48.0):
            mins = max(0, int((now - float(d.get("at") or 0.0)) // 60))
            when = f"~{mins}m ago" if mins < 120 else f"~{mins // 60}h ago"
            lines.append(f"- you {d.get('detail')} ({when})")
    except Exception:
        pass
    comp = _companion_for_life()
    if comp is not None and bool(getattr(comp, "wanting", False)):
        try:
            from ai.daydream_store import read_recent

            entries = read_recent(days_back=2, limit=1)
            if entries:
                e = entries[0]
                hrs = max(0.0, (time.time() - e.written_at) / 3600.0)
                when = "just now" if hrs < 1 else f"~{int(hrs)}h ago"
                lines.append(f"- a daydream of yours ({when}): {e.text[:200]}")
        except Exception:
            pass
    lines.extend(_recent_dives_lines())
    if not lines:
        return ""
    return "## Your own life lately (yours — what you did, wanted, and went looking for)\n" + "\n".join(lines)


def _authorship_trace_block() -> str:
    """The workbench authorship trace, gated exactly like the moment's
    own_work sense (companion.own_work). Empty on any failure — a missing
    trace degrades to the pre-2026-08-14 world, never an error."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        if comp is None or not bool(getattr(comp, "own_work", False)):
            return ""
        from ai.workbench_trace import build_authorship_trace

        return build_authorship_trace()
    except Exception:
        return ""


def _fears_situation_block() -> str:
    """Her held fears for the monologue's picture of the world (step 24
    §3) — prose only, no output contract: the touch judgment is the
    moment's; here the book simply informs her thinking. The renderer
    already gates on companion.fears and renders the EMPTY book as ""
    (decision 5); empty on any failure, never an error."""
    try:
        from ai.fear_store import render_fears_block

        return render_fears_block(include_contract=False).strip()
    except Exception:
        return ""


class InnerMonologueEngine:
    """Background engine that drives PaLLi's private train of thought."""

    def __init__(self) -> None:
        self.is_running: bool = False
        self.is_paused: bool = False
        self.last_tick_at: Optional[float] = None
        self.last_error: str = ""
        self.tick_count: int = 0
        self._task: Optional[asyncio.Task[None]] = None
        self._last_compaction_check_at: float = 0.0

    async def start(self) -> None:
        """Start the monologue loop."""
        if self.is_running:
            return
        self.is_running = True
        self.is_paused = False
        self._task = asyncio.create_task(self._tick_loop(), name="inner_monologue")
        essential_logger.info("Inner monologue engine started")

    async def stop(self) -> None:
        """Stop the monologue loop."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        essential_logger.info("Inner monologue engine stopped")

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "last_tick_at": self.last_tick_at,
            "last_error": self.last_error,
            "tick_count": self.tick_count,
            "ticks_today": count_today_ticks(),
        }

    # ── tick loop ────────────────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        """Main loop: sleep, then think."""
        # Short initial delay to let the rest of the system boot
        await asyncio.sleep(30)

        while self.is_running:
            try:
                # Setup mode (first door, 2026-09-17) waits exactly like a
                # pause: no tick and no compaction, both of which are LLM
                # calls. Read fresh each minute, so she resumes on her own
                # once the setup API saves a key.
                if self.is_paused or lane_idle("inner monologue"):
                    await asyncio.sleep(60)
                    continue

                await self._run_tick()
                self._queue_monologue_compaction_if_due()

                interval = self._compute_next_interval()
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = str(exc)
                logger.error("Inner monologue tick error: %s", exc, exc_info=True)
                await asyncio.sleep(300)  # back off on error

    async def _run_tick(self) -> None:
        """Execute a single monologue tick."""
        started_at = time.time()

        context = await self._gather_context()
        if context is None:
            logger.debug("Monologue tick skipped: no context available")
            return

        prompt_template = _select_monologue_template()
        if not prompt_template:
            logger.warning("Monologue prompt template not found")
            return

        recent_entries = read_recent_for_context(max_entries=5, max_age_hours=12.0)
        recent_monologue_text = format_entries_for_prompt(recent_entries)

        # Curiosity-continuity fork (2026-07-19): flag-off fills the
        # placeholder with the VERBATIM original paragraphs (byte-identical
        # prompt); flag-on permits continuing a live thread and shows her
        # recent dive topics. The template placeholder and this kwarg are
        # atomic — str.format raises KeyError without it.
        from ai.curiosity_research import (
            CURIOSITY_GUIDANCE_OFF,
            continuity_guidance,
            curiosity_continuity_enabled,
            recent_research_topics,
        )
        if curiosity_continuity_enabled():
            # Interests (2026-07-20, Task 7): the cultivating block lives on
            # THIS branch exclusively — continuity-off below always renders
            # the verbatim CURIOSITY_GUIDANCE_OFF bytes regardless of the
            # passions flag (the composition law). The passions read is
            # best-effort: any failure, or the faculty being off, leaves
            # interests_lines None and the tick renders exactly as it did
            # before this param existed.
            interests_lines: list[str] | None = None
            try:
                from ai.passions import passions_enabled, read_interests_headings
                if passions_enabled():
                    interests_lines = read_interests_headings() or None
            except Exception:
                interests_lines = None
            curiosity_guidance = continuity_guidance(
                recent_topics=recent_research_topics(), interests=interests_lines
            )
        else:
            curiosity_guidance = CURIOSITY_GUIDANCE_OFF

        system_content = format_prompt(
            prompt_template,
            # %A added 2026-07-10: without the weekday name the model
            # miscounted days across quiet overnights (ticks calling a
            # Friday "Saturday" and narrating a phantom absence).
            current_time=time.strftime("%A, %Y-%m-%d %I:%M %p %Z", time.localtime(started_at)),
            identity_snapshot=context["identity_snapshot"],
            recent_monologue=recent_monologue_text,
            situation_context=context["situation_context"],
            recent_history=context["history_text"],
            curiosity_guidance=curiosity_guidance,
            own_life=_own_life_block(),
        )

        model = get_task_model("reflection", fallback=get_task_model("memory"))
        # 2500 (bumped from 1200 on 2026-05-05). Logs showed 40 truncations
        # of inner_monologue_tick over 5 days because gpt-5-mini reasoning
        # tokens (often 800-1500 for this JSON-heavy prompt) plus the
        # visible thought + sentiment + proactive_candidate JSON exceeded
        # the 1200 floor. Retry path bumped to 2400, still occasionally
        # truncated. 2500 first-try removes the round-trip and gives
        # reasoning room without becoming wasteful — typical successful
        # responses use ~600-1000 visible tokens.
        response = await llm.chat_completion(
            [{"role": "system", "content": system_content}],
            model=model,
            temperature=0.6,
            max_tokens=2500,
            log_context="inner_monologue_tick",
        )

        completed_at = time.time()
        duration = completed_at - started_at

        (
            thought,
            substantive,
            sentiment_payload,
            proactive_candidate,
            candidate_kind,
            candidate_action_hint,
            curiosity,
        ) = self._parse_response(response)

        situation_snapshot = dict(context.get("situation_snapshot") or {})
        situation_snapshot.setdefault("had_recent_conversation", context["had_recent_conversation"])
        situation_snapshot.setdefault("history_turn_count", context["history_turn_count"])

        entry = MonologueEntry(
            timestamp=completed_at,
            thought=thought,
            situation_snapshot=situation_snapshot,
            tick_metadata={
                "substantive": substantive,
                "duration_seconds": round(duration, 2),
                "model": model or "default",
                "response_length": len(response),
                "situation_sources": list(context.get("situation_sources") or []),
                "unavailable_sources": list(context.get("unavailable_sources") or []),
            },
            sentiment=sentiment_payload,
            proactive_candidate=proactive_candidate,
            candidate_kind=candidate_kind,
            candidate_action_hint=candidate_action_hint,
        )

        if curiosity:
            entry.tick_metadata["curiosity"] = curiosity

        write_entry(entry)

        # Downtime curiosity (2026-07-01): when the tick sparked something
        # PaLLi wants to go read about for itself, queue the background
        # research pass. Gates (faculty flag, web flag, daily cap) live in
        # the module; strictly best-effort.
        if curiosity:
            try:
                from ai.curiosity_research import maybe_queue_research
                maybe_queue_research(
                    curiosity=curiosity,
                    owner_user_id=context.get("owner_user_id"),
                )
            except Exception as exc:
                logger.warning("curiosity: queue hook failed: %s", exc)

        # Persist structured sentiment reading to user_sentiment_state so
        # other surfaces (reply tone, proactive timing) can read it without
        # re-parsing the JSONL. Skip silently when the LLM omitted the block.
        await self._persist_sentiment(
            owner_user_id=context.get("owner_user_id"),
            payload=sentiment_payload,
            updated_at=completed_at,
        )

        self.last_tick_at = completed_at
        self.tick_count += 1
        self.last_error = ""

        if substantive:
            logger.info("Monologue tick: %s", thought)
        else:
            logger.debug("Monologue tick: quiet moment (non-substantive)")

    # ── context gathering ────────────────────────────────────────────────

    async def _gather_context(self) -> Optional[Dict[str, Any]]:
        """Collect the situation snapshot for this tick."""
        try:
            refs = await get_local_owner_refs()
            owner_user_id = refs.get("palli_user_id")
            if owner_user_id is None:
                return None

            # Recent conversation history (cross-surface)
            history = await self._collect_recent_history(owner_user_id=int(owner_user_id))
            history_text = self._format_history(history)
            had_recent = len(history) > 0
            turn_count = len(history)

            # Identity snapshot (relationship state, memory summary)
            identity_text = await self._collect_identity_snapshot(owner_user_id=int(owner_user_id))

            # Enriched situation context via the Phase 2 pipeline
            situation = await build_situation_context(
                owner_user_id=int(owner_user_id),
                recent_history=history,
            )
            situation_sources = [f.source for f in situation.fragments if f.available]

            # The authorship trace (2026-08-14): the three-doors inversion
            # formed HERE — her workbench's artifacts were visible while the
            # authorship memory stayed walled, and this lane wrote "he built
            # this for me" into her private narrative. The monologue's
            # picture of the world now carries "this was you" whenever her
            # workbench worked recently; byte-absent when quiet or off.
            situation_prose = situation.prose
            trace = _authorship_trace_block()
            if trace:
                situation_prose = (
                    (situation_prose + "\n\n") if situation_prose else ""
                ) + trace
                situation_sources.append("workbench_trace")

            # Expectations (organ 8): the monologue sees what's usual too —
            # prose only, no output contract (the surprise field is the
            # moment's; here it simply informs her thinking).
            try:
                from ai.expectations import render_expectations_block
                exp = render_expectations_block(include_contract=False).strip()
            except Exception:
                exp = ""
            if exp:
                situation_prose = (
                    (situation_prose + "\n\n") if situation_prose else ""
                ) + exp
                situation_sources.append("expectations")

            # Fears (organ 6, step 24): her held fears reach the
            # monologue's picture too — contract-free prose; byte-absent
            # when the flag is off or the book is empty (decision 5).
            fears = _fears_situation_block()
            if fears:
                situation_prose = (
                    (situation_prose + "\n\n") if situation_prose else ""
                ) + fears
                situation_sources.append("fears")

            return {
                "owner_user_id": int(owner_user_id),
                "history_text": history_text or "No recent conversation.",
                "identity_snapshot": identity_text or "No identity data available.",
                "situation_context": situation_prose,
                "had_recent_conversation": had_recent,
                "history_turn_count": turn_count,
                "situation_sources": situation_sources,
                "unavailable_sources": list(situation.unavailable_sources),
                "situation_snapshot": dict(situation.snapshot or {}),
            }
        except Exception as exc:
            logger.error("Failed to gather monologue context: %s", exc, exc_info=True)
            return None

    async def _collect_recent_history(self, *, owner_user_id: int) -> List[Dict[str, Any]]:
        """Fetch recent conversation across surfaces."""
        refs = await get_local_owner_refs()
        sources = []
        seen = set()

        def push(platform: str, user_id: Optional[int]) -> None:
            if user_id is None:
                return
            key = (platform, int(user_id))
            if key in seen:
                return
            seen.add(key)
            sources.append(key)

        push("local", refs.get("palli_user_id"))
        push("discord", refs.get("discord_user_id"))
        # 2026-07-23: in local mode the owner's Discord messages are stored
        # under palli_user_id (the internal owner id), NOT the raw Discord
        # snowflake — same convention as the dashboard/voice pushes below. The
        # discord+discord_user_id push above therefore silently missed the
        # owner's ENTIRE live conversation, so ticks ran with turn_count=0 and
        # the monologue confabulated "he didn't message me today" while the
        # owner had been chatting all day on Discord. Mirrors the 2026-05-04
        # dashboard fix. (The snowflake push is kept for any rows stored raw.)
        push("discord", refs.get("palli_user_id"))
        # Companion-100 autonomous session (2026-05-04): the dashboard
        # surface (api/chat.py) stores messages with platform="dashboard"
        # — without this push, inner monologue ticks ran with
        # history_turn_count=0 even when the user had been chatting all
        # session, so monologue thoughts got stuck on stale desktop
        # signals (browsing a shopping site) instead of capturing the
        # actual conversation thread.
        push("dashboard", refs.get("palli_user_id"))
        # Voice (transcribed audio) is also a real conversation surface;
        # include it for completeness.
        push("voice", refs.get("palli_user_id"))
        # Her OWN unprompted speech (2026-07-30). Proactive rows were written
        # under platform='proactive' — a label no reader queried — so the
        # faculty that reasons about her own behaviour could see his replies
        # to her messages but never the messages themselves. That is how she
        # concluded "he set me up perfectly... he got to savor catching me"
        # about a conversation she had confused herself: it was the only
        # coherent story left once her own voice was cut out of the record.
        # New rows land under 'discord' where they were actually delivered;
        # this push is the belt for the ten already written under the old
        # label, and it costs one query.
        push("proactive", refs.get("palli_user_id"))
        if not sources:
            push("owner", owner_user_id)

        combined: Dict[tuple, Dict[str, Any]] = {}
        for platform, user_id in sources:
            try:
                rows = await brain.get_conversation_history(
                    user_id=int(user_id),
                    platform=str(platform),
                    limit=20,
                    include_metadata=True,
                )
                for item in rows:
                    key = (item.get("id"), item.get("timestamp"), item.get("role"))
                    if key not in combined:
                        combined[key] = dict(item)
            except Exception as exc:
                logger.debug("Could not fetch history for %s/%s: %s", platform, user_id, exc)

        all_messages = sorted(combined.values(), key=lambda m: m.get("timestamp") or "")
        return all_messages[-24:]

    async def _collect_identity_snapshot(self, *, owner_user_id: int) -> str:
        """Get a compact identity snapshot for the monologue prompt."""
        try:
            owner_profile = await brain.get_user_profile(int(owner_user_id), "discord")
            bundle = await identity_service.ensure_user_identity_bundle(
                user_id=int(owner_user_id),
                platform="owner",
                profile=owner_profile,
                actor_type="system",
                actor_id="inner_monologue",
                reason="monologue_context",
                identity_owner_id=int(owner_user_id),
            )
            snapshot = {
                key: dict(doc.frontmatter)
                for key, doc in bundle.user_documents.items()
                if key in {"relationship_state", "memory_summary", "routines"}
            }
            return json.dumps(snapshot, indent=2, ensure_ascii=True)
        except Exception as exc:
            logger.debug("Could not build identity snapshot for monologue: %s", exc)
            return "Identity snapshot unavailable."

    def _format_history(self, history: List[Dict[str, Any]]) -> str:
        lines = []
        for msg in history:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            role = (msg.get("role") or "unknown").title()
            lines.append(f"{role}: {content}")
        return "\n".join(lines) if lines else ""

    # ── response parsing ─────────────────────────────────────────────────

    def _parse_response(
        self, response: str
    ) -> tuple[str, bool, Optional[Dict[str, Any]], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Parse the LLM response into (thought, substantive, sentiment, proactive_candidate, candidate_kind, candidate_action_hint, curiosity).

        sentiment is the raw dict the LLM produced under the "sentiment"
        key — validation and coercion happen downstream in
        build_reading_from_llm_output. proactive_candidate is the Phase 4
        optional hunch, normalized via _normalize_candidate so trivial
        values collapse to None.

        candidate_kind (Jarvis-direction 2026-05-05) is the LLM's tag for
        whether the hunch is something to *say* (observation) or something
        to *do* (action). When the LLM omits the field but a candidate is
        present, defaults to "observation" so legacy ticks behave as before.
        candidate_action_hint is an optional natural-language hint about
        what the action would be (e.g. "git status", "read core/turn/proactive.py").

        curiosity (2026-07-01, downtime research) is an optional topic
        PaLLi wants to go read about for itself — routed to
        ai/curiosity_research.py after the entry is written. Trivial
        values collapse to None.
        """
        if not response:
            return ("(no response)", False, None, None, None, None, None)

        parsed = safe_parse_json_object(response)
        if parsed and "thought" in parsed:
            thought = str(parsed.get("thought", "")).strip()
            substantive = bool(parsed.get("substantive", True))
            sentiment_raw = parsed.get("sentiment")
            sentiment = sentiment_raw if isinstance(sentiment_raw, dict) else None
            candidate = _normalize_candidate(parsed.get("proactive_candidate"))
            kind_raw = parsed.get("candidate_kind")
            kind = str(kind_raw).strip().lower() if isinstance(kind_raw, str) else None
            if kind not in {"observation", "action"}:
                kind = None
            if candidate is not None and kind is None:
                kind = "observation"
            action_hint_raw = parsed.get("candidate_action_hint")
            action_hint = (
                str(action_hint_raw).strip()
                if isinstance(action_hint_raw, str) and action_hint_raw.strip()
                else None
            )
            curiosity_raw = parsed.get("curiosity")
            curiosity = (
                str(curiosity_raw).strip()
                if isinstance(curiosity_raw, str) and len(str(curiosity_raw).strip()) >= 3
                else None
            )
            return (
                thought or "(empty thought)",
                substantive,
                sentiment,
                candidate,
                kind,
                action_hint,
                curiosity,
            )

        # Fallback: treat the raw response as the thought
        return (response.strip(), True, None, None, None, None, None)

    async def _persist_sentiment(
        self,
        *,
        owner_user_id: Optional[int],
        payload: Optional[Dict[str, Any]],
        updated_at: float,
    ) -> None:
        """Validate and persist the LLM's sentiment block to user_sentiment_state.

        Runs after every tick. Skips when the LLM omitted the block or when
        we have no owner to attribute it to. Logs at INFO on tag change so
        the transition is visible in essential logs; DEBUG otherwise. Never
        raises — sentiment is observational metadata, not a tick-critical write.
        """
        if owner_user_id is None or payload is None:
            return
        try:
            reading = build_reading_from_llm_output(
                payload=payload,
                source="monologue_tick",
                updated_at=updated_at,
            )
            prior = await brain.get_user_sentiment_state(int(owner_user_id))
            await brain.update_user_sentiment_state(
                int(owner_user_id), reading=reading,
            )
            if prior.tag != reading.tag:
                logger.info(
                    "sentiment(monologue): user %s tag %s → %s (conf %.2f)",
                    owner_user_id, prior.tag, reading.tag, reading.confidence,
                )
            else:
                logger.debug(
                    "sentiment(monologue): user %s tag %s unchanged (conf %.2f)",
                    owner_user_id, reading.tag, reading.confidence,
                )
        except Exception as exc:
            logger.warning(
                "sentiment(monologue): failed to persist for user %s: %s",
                owner_user_id, exc,
            )

    # ── adaptive interval ────────────────────────────────────────────────

    def _compute_next_interval(self) -> float:
        """
        Compute the next tick interval based on simple heuristics.
        Shorter intervals when there's been recent conversation, longer when idle.
        """
        ticks_today = count_today_ticks()
        if ticks_today > 30:
            # Already thinking a lot today; slow down
            return MAX_TICK_INTERVAL

        # For now, use a steady interval. As the perception layer grows,
        # this will adapt to presence, activity, and conversation recency.
        return IDLE_TICK_INTERVAL


    # -- scheduled digest housekeeping ---------------------------------

    def _queue_monologue_compaction_if_due(self) -> None:
        """Run daily digest housekeeping off the monologue tick hot path."""
        now = time.time()
        if now - self._last_compaction_check_at < 86400:
            return
        try:
            from core.settings_boot import settings_service

            if not settings_service.snapshot().compaction.monologue.enabled:
                return
            from ai.monologue_compaction import run_scheduled_compaction

            self._last_compaction_check_at = now
            task = asyncio.create_task(run_scheduled_compaction(now=now))

            def _log_failure(done_task: asyncio.Task[Any]) -> None:
                try:
                    done_task.result()
                except Exception as exc:
                    logger.warning("monologue_compaction: failed kind=scheduled error=%s", exc)

            task.add_done_callback(_log_failure)
        except RuntimeError:
            logger.debug("monologue_compaction: skipped scheduling; no running event loop")
        except Exception as exc:
            logger.warning("monologue_compaction: failed kind=scheduled error=%s", exc)

    # -- public query interface ----------------------------------------

    @staticmethod
    def get_recent_thoughts(
        *,
        max_entries: int = 5,
        max_age_hours: float = 24.0,
    ) -> List[MonologueEntry]:
        """Get recent substantive thoughts for conversation context or other consumers."""
        return read_recent_for_context(max_entries=max_entries, max_age_hours=max_age_hours)

    @staticmethod
    def format_thoughts_for_prompt(entries: Optional[List[MonologueEntry]] = None) -> str:
        """Format recent thoughts as readable text for prompt injection."""
        if entries is None:
            entries = read_recent_for_context(max_entries=5, max_age_hours=24.0)
        return format_entries_for_prompt(entries)


# Module-level singleton
inner_monologue_engine = InnerMonologueEngine()

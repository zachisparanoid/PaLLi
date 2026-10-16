from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from ai.ambient_generator import ambient_generator
from core.config import config
from core.continuity_service import continuity_service
from core.database import brain
from core.identity import identity_service
from core.identity.policy_access import policy_value
from core.runtime_control import maintenance_session_key, runtime_control
from core.setup_mode import lane_idle
from core.voice_text_cleaner import clean_text_for_voice
from voice.local_audio import _coerce_device, play_wav_file
from voice.speech import voice_speech_service

logger = logging.getLogger(__name__)


def _arbiter_owns_arrivals() -> bool:
    """Step 18 (arbiter spec §6, 2026-08-06): under companion.arbiter,
    arrivals belong to her Present Moment — the cycle already
    priority-wakes on owner_arrived_after_absence, and whether to greet
    becomes her appraisal's call on the one budgeted rail. This planner
    stands down with a recorded reason. Fail-closed: any error means OFF
    (greet as today)."""
    try:
        from ai.arbiter import arbiter_enabled

        return arbiter_enabled()
    except Exception:
        return False


class AmbientOutputRouter:
    async def deliver(
        self,
        *,
        owner_user_id: int,
        surfaces: List[str],
        bundle: Dict[str, Any],
        message_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, bool]:
        delivered = {
            "dashboard": "dashboard" in surfaces,
            "local_voice": False,
            "discord_dm": False,
        }
        if "local_voice" in surfaces:
            await self._deliver_local_voice(str(bundle.get("spoken_text") or ""))
            delivered["local_voice"] = True
        if "discord_dm" in surfaces:
            delivered["discord_dm"] = await self._deliver_discord_dm(
                owner_user_id=int(owner_user_id),
                text=str(bundle.get("discord_dm_text") or bundle.get("dashboard_text") or bundle.get("spoken_text") or ""),
                message_metadata=message_metadata,
            )
        return delivered

    async def _deliver_local_voice(self, text: str) -> None:
        clean_text = clean_text_for_voice(str(text or "").strip())
        if not clean_text:
            return
        wav_path = await voice_speech_service.synthesize_live_playback_file(text=clean_text)
        if not wav_path:
            raise RuntimeError("ambient_local_voice_tts_failed")
        try:
            await play_wav_file(filepath=wav_path, device=_coerce_device(getattr(config, "LOCAL_VOICE_OUTPUT_DEVICE", "")))
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    async def _deliver_discord_dm(
        self,
        *,
        owner_user_id: int,
        text: str,
        message_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        clean_text = str(text or "").strip()
        if not clean_text:
            return False
        try:
            from bots.discord.client import get_active_client
            discord_bot = get_active_client()
        except Exception:
            logger.debug("Discord client import unavailable for ambient DM delivery", exc_info=True)
            return False
        if discord_bot is None or not getattr(discord_bot, "is_ready", lambda: False)():
            return False
        user = await brain.get_app_user_by_id(int(owner_user_id))
        discord_user_id = str((user or {}).get("discord_user_id") or "").strip()
        if not discord_user_id:
            return False
        try:
            target = discord_bot.get_user(int(discord_user_id)) or await discord_bot.fetch_user(int(discord_user_id))
            if target is None:
                return False
            channel = await target.create_dm()
            await channel.send(clean_text)
            try:
                thread = await continuity_service.resolve_surface_thread(
                    owner_user_id=int(owner_user_id),
                    platform="discord",
                    channel_id=int(getattr(channel, "id", 0) or 0),
                    surface_id=str(getattr(channel, "id", 0) or ""),
                    is_dm=True,
                    maintenance=False,
                )
                stored_metadata = {
                    "type": "ambient_arrival_message",
                    "is_dm": True,
                    "account_user_id": int(owner_user_id),
                    **dict(message_metadata or {}),
                }
                await brain.store_conversation(
                    user_id=int(discord_user_id),
                    platform="discord",
                    channel_id=int(getattr(channel, "id", 0) or 0),
                    surface_id=str(getattr(channel, "id", 0) or ""),
                    thread_id=str((thread or {}).get("thread_id") or "").strip() or None,
                    role="assistant",
                    content=clean_text,
                    username="Me",
                    metadata=stored_metadata,
                )
            except Exception:
                logger.debug(
                    "Ambient Discord DM history persistence failed for owner %s",
                    owner_user_id,
                    exc_info=True,
                )
            return True
        except Exception as exc:
            logger.warning("Ambient Discord DM delivery failed for owner %s: %s", owner_user_id, exc)
            return False


class AmbientPlannerService:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self.output_router = AmbientOutputRouter()

    async def stop(self) -> None:
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def list_actions(self, *, owner_user_id: int, limit: int = 16) -> List[Dict[str, Any]]:
        return await brain.list_ambient_actions(owner_user_id=int(owner_user_id), limit=max(int(limit or 16), 1))

    async def handle_context_event(
        self,
        *,
        owner_user_id: int,
        event: Dict[str, Any],
        context_snapshot: Dict[str, Any],
        previous_context: Dict[str, Any],
    ) -> None:
        event_type = str(event.get("event_type") or "").strip().lower()
        if event_type not in {"owner_arrived", "owner_departed"}:
            return

        policy = await identity_service.get_compiled_action_policy(
            user_id=int(owner_user_id),
            platform="owner",
            identity_owner_id=int(owner_user_id),
        )
        if event_type == "owner_departed":
            await self._record_suppressed_action(
                owner_user_id=int(owner_user_id),
                event=event,
                action_type="departure_update",
                suppression_reason="departure_silent",
                delivery_surface="dashboard",
                metadata={"context": context_snapshot},
            )
            return

        # Setup mode (first door, 2026-09-17): no brain to write a greeting with.
        if lane_idle("arrival greeting"):
            return

        decision = await self._plan_arrival_action(
            owner_user_id=int(owner_user_id),
            event=event,
            context_snapshot=context_snapshot,
            previous_context=previous_context,
            policy=policy,
        )
        if not decision.get("should_act"):
            await self._record_suppressed_action(
                owner_user_id=int(owner_user_id),
                event=event,
                action_type="arrival_greeting",
                suppression_reason=str(decision.get("suppression_reason") or "no_action"),
                delivery_surface="dashboard",
                metadata={
                    "context": context_snapshot,
                    "delivery_surfaces": decision.get("delivery_surfaces") or ["dashboard"],
                    "decision_reason": decision.get("decision_reason") or "",
                },
            )
            return

        await self._queue_arrival_action(
            owner_user_id=int(owner_user_id),
            event=event,
            context_snapshot=context_snapshot,
            decision=decision,
        )

    async def _plan_arrival_action(
        self,
        *,
        owner_user_id: int,
        event: Dict[str, Any],
        context_snapshot: Dict[str, Any],
        previous_context: Dict[str, Any],
        policy: Any,
    ) -> Dict[str, Any]:
        metadata = dict(event.get("metadata") or {})
        if metadata.get("bootstrap"):
            return {
                "should_act": False,
                "suppression_reason": "bootstrap_state_sync",
                "delivery_surfaces": ["dashboard"],
                "decision_reason": "bootstrap_state_sync",
            }

        # Step 18 (arbiter spec §6): the arbiter owns arrivals — greeting
        # is her appraisal's call now. Recorded like every suppression so
        # the clock's own record shows it stood down. Flag off = greet as
        # today, byte-identical.
        if _arbiter_owns_arrivals():
            return {
                "should_act": False,
                "suppression_reason": "arbiter_owns_arrivals",
                "delivery_surfaces": ["dashboard"],
                "decision_reason": "arbiter_owns_arrivals",
            }

        presence_mode = str(
            policy_value(policy, "presence_awareness_mode", "arrival_only")
            or "arrival_only"
        ).strip().lower()
        if presence_mode in {"off", "inspect_only"}:
            return {
                "should_act": False,
                "suppression_reason": "presence_awareness_disabled",
                "delivery_surfaces": ["dashboard"],
                "decision_reason": "presence_awareness_disabled",
            }

        now = float(event.get("occurred_at") or time.time())

        # 2026-07-10: a presence flap five minutes after an active exchange
        # produced "Hey, welcome home. Glad you're back." mid-conversation —
        # this gate checked its own greeting cooldown, privacy, and quiet
        # hours, but never conversation recency. However legitimate the
        # presence event, welcoming someone you were just talking to is
        # socially wrong, so a recent exchange suppresses the greeting.
        recent_conversation_seconds = float(
            getattr(config, "AMBIENT_ARRIVAL_RECENT_CONVERSATION_SECONDS", 1800.0)
            or 1800.0
        )
        if recent_conversation_seconds > 0:
            from core.turn.proactive import _safe_last_user_message_at

            last_message_at = await _safe_last_user_message_at(
                owner_id=int(owner_user_id)
            )
            if last_message_at and (now - last_message_at) < recent_conversation_seconds:
                return {
                    "should_act": False,
                    "suppression_reason": "recent_conversation",
                    "delivery_surfaces": ["dashboard"],
                    "decision_reason": "recent_conversation",
                }

        cooldown_seconds = float(getattr(config, "AMBIENT_ARRIVAL_COOLDOWN_SECONDS", 1800.0) or 1800.0)
        recent = await brain.list_ambient_actions(owner_user_id=int(owner_user_id), statuses=["completed", "delivering", "planned"], limit=12)
        for item in recent:
            if str(item.get("action_type") or "") != "arrival_greeting":
                continue
            reference_time = float(item.get("delivered_at") or item.get("created_at") or 0.0)
            if cooldown_seconds > 0 and reference_time and (now - reference_time) < cooldown_seconds:
                return {
                    "should_act": False,
                    "suppression_reason": "arrival_cooldown",
                    "delivery_surfaces": ["dashboard"],
                    "decision_reason": "arrival_cooldown",
                }

        privacy_state = str(context_snapshot.get("privacy_state") or "unknown").strip().lower()
        quiet_hours_active = bool(context_snapshot.get("quiet_hours_active"))
        machine_state = str(context_snapshot.get("machine_state") or "unknown").strip().lower()
        social_sensitivity = str(
            policy_value(policy, "social_context_sensitivity", "high") or "high"
        ).strip().lower()

        delivery_surfaces = ["dashboard"]
        voice_allowed = (
            str(
                policy_value(
                    policy,
                    "ambient_voice_output_policy",
                    "local_speaker",
                )
                or "local_speaker"
            ).strip().lower()
            == "local_speaker"
        )
        dm_allowed = (
            str(
                policy_value(
                    policy,
                    "ambient_discord_delivery_policy",
                    "dm_only",
                )
                or "dm_only"
            ).strip().lower()
            == "dm_only"
        )

        if voice_allowed and not quiet_hours_active and privacy_state == "private":
            delivery_surfaces.append("local_voice")
        elif dm_allowed:
            delivery_surfaces.append("discord_dm")

        unlock_grace = float(getattr(config, "AMBIENT_MACHINE_UNLOCK_GRACE_SECONDS", 180.0) or 180.0)
        last_arrival_at = float(context_snapshot.get("last_arrival_at") or now)
        if machine_state == "locked" and unlock_grace > 0 and (now - last_arrival_at) <= unlock_grace:
            if "local_voice" in delivery_surfaces:
                delivery_surfaces.remove("local_voice")
            if dm_allowed and "discord_dm" not in delivery_surfaces:
                delivery_surfaces.append("discord_dm")

        if privacy_state == "shared" and social_sensitivity == "high" and "local_voice" in delivery_surfaces:
            delivery_surfaces.remove("local_voice")
        if quiet_hours_active and "local_voice" in delivery_surfaces:
            delivery_surfaces.remove("local_voice")

        useful_item = await self._choose_useful_item(owner_user_id=int(owner_user_id))
        useful_item_data = useful_item or {}
        reply_posture = "brief_companion"
        useful_item_type = str(useful_item_data.get("type") or "").strip().lower()
        useful_item_attention_state = str(
            useful_item_data.get("attention_state") or ""
        ).strip().lower()
        if useful_item_type in {"runtime_job", "open_loop"}:
            reply_posture = "practical_operator"
        if privacy_state == "shared" and "local_voice" not in delivery_surfaces and not dm_allowed:
            return {
                "should_act": False,
                "suppression_reason": "shared_context_quiet",
                "delivery_surfaces": ["dashboard"],
                "decision_reason": "shared_context_quiet",
            }

        return {
            "should_act": True,
            "action_type": "arrival_greeting",
            "delivery_surfaces": delivery_surfaces,
            "decision_reason": "arrival_private" if privacy_state == "private" else "arrival_rerouted",
            "useful_item": useful_item,
            "reply_posture": reply_posture,
            "style_notes": self._build_style_notes(
                privacy_state=privacy_state,
                quiet_hours_active=quiet_hours_active,
                machine_state=machine_state,
                has_useful_item=bool(useful_item),
                voice_enabled="local_voice" in delivery_surfaces,
            ),
        }

    async def _queue_arrival_action(
        self,
        *,
        owner_user_id: int,
        event: Dict[str, Any],
        context_snapshot: Dict[str, Any],
        decision: Dict[str, Any],
    ) -> None:
        maintenance_session = await runtime_control.ensure_surface_session(
            owner_user_id=int(owner_user_id),
            platform="local",
            maintenance=True,
            status="ready",
            metadata={"origin": "ambient_action"},
        )
        job = await runtime_control.create_job(
            owner_user_id=int(owner_user_id),
            runtime_session_key=str(maintenance_session.get("session_key") or maintenance_session_key(int(owner_user_id))),
            job_type="ambient_action",
            status="queued",
            request_text="arrival greeting",
            source_table="context_events",
            source_id=str(event.get("event_id") or ""),
            metadata={
                "event_type": event.get("event_type"),
                "delivery_surfaces": list(decision.get("delivery_surfaces") or []),
                "privacy_state": context_snapshot.get("privacy_state"),
            },
        )
        action_id = f"ambient_{uuid.uuid4().hex[:16]}"
        await brain.create_ambient_action(
            action_id=action_id,
            owner_user_id=int(owner_user_id),
            source_event_id=str(event.get("event_id") or ""),
            runtime_job_id=str(job.get("job_id") or ""),
            action_type="arrival_greeting",
            status="planned",
            delivery_surface=",".join(decision.get("delivery_surfaces") or ["dashboard"]),
            decision_reason=str(decision.get("decision_reason") or ""),
            content=self._build_arrival_action_content(
                useful_item=decision.get("useful_item"),
            ),
            metadata={
                "context": context_snapshot,
                "reply_posture": str(decision.get("reply_posture") or "brief_companion"),
                "style_notes": str(decision.get("style_notes") or ""),
                "delivery_surfaces": list(decision.get("delivery_surfaces") or []),
                "useful_item": decision.get("useful_item") or {},
            },
        )
        task = asyncio.create_task(
            self._deliver_arrival_action(
                owner_user_id=int(owner_user_id),
                action_id=action_id,
                runtime_job_id=str(job.get("job_id") or ""),
                event=event,
                context_snapshot=context_snapshot,
                decision=decision,
            ),
            name=f"ambient-arrival-{owner_user_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver_arrival_action(
        self,
        *,
        owner_user_id: int,
        action_id: str,
        runtime_job_id: str,
        event: Dict[str, Any],
        context_snapshot: Dict[str, Any],
        decision: Dict[str, Any],
    ) -> None:
        surfaces = list(decision.get("delivery_surfaces") or ["dashboard"])
        useful_item = dict(decision.get("useful_item") or {})
        bundle: Optional[Dict[str, Any]] = None
        try:
            await runtime_control.begin_job(job_id=runtime_job_id, metadata={"ambient_action_id": action_id})
            bundle = await ambient_generator.generate_arrival_bundle(
                owner_user_id=int(owner_user_id),
                event_type=str(event.get("event_type") or "owner_arrived"),
                context_snapshot=context_snapshot,
                delivery_surfaces=surfaces,
                useful_item=useful_item,
                reply_posture=str(decision.get("reply_posture") or "brief_companion"),
                style_notes=str(decision.get("style_notes") or ""),
            )
            await brain.update_ambient_action(
                action_id=action_id,
                status="delivering",
                content=self._build_arrival_action_content(
                    bundle=bundle,
                    useful_item=useful_item,
                ),
            )
            delivered = await self.output_router.deliver(
                owner_user_id=int(owner_user_id),
                surfaces=surfaces,
                bundle=bundle,
                message_metadata={
                    "ambient_action_id": str(action_id or ""),
                    "ambient_event_type": str(event.get("event_type") or ""),
                    "ambient_useful_item_type": str(useful_item.get("type") or "").strip(),
                    "ambient_useful_item_text": str(useful_item.get("text") or "").strip(),
                    "ambient_useful_item_subject": str(
                        useful_item.get("subject_text") or useful_item.get("text") or ""
                    ).strip(),
                    "reply_posture": str(decision.get("reply_posture") or "brief_companion"),
                },
            )
            await brain.update_ambient_action(
                action_id=action_id,
                status="completed",
                content=self._build_arrival_action_content(
                    bundle=bundle,
                    useful_item=useful_item,
                    delivered=delivered,
                ),
                metadata={"delivery_surfaces": surfaces},
                delivered_at=time.time(),
            )
            await runtime_control.complete_job(
                job_id=runtime_job_id,
                status="completed",
                metadata={
                    "ambient_action_id": action_id,
                    "delivered": delivered,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Ambient arrival delivery failed for owner %s: %s", owner_user_id, exc)
            await brain.update_ambient_action(
                action_id=action_id,
                status="failed",
                suppression_reason="delivery_failed",
                content=self._build_arrival_action_content(
                    bundle=bundle,
                    useful_item=useful_item,
                ),
                metadata={
                    "error": str(exc),
                    "delivery_surfaces": surfaces,
                },
                failed_at=time.time(),
            )
            await runtime_control.complete_job(
                job_id=runtime_job_id,
                status="failed",
                last_error=str(exc),
                last_transition_reason="delivery_send_failed",
                metadata={"ambient_action_id": action_id},
            )

    async def _record_suppressed_action(
        self,
        *,
        owner_user_id: int,
        event: Dict[str, Any],
        action_type: str,
        suppression_reason: str,
        delivery_surface: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await brain.create_ambient_action(
            action_id=f"ambient_{uuid.uuid4().hex[:16]}",
            owner_user_id=int(owner_user_id),
            source_event_id=str(event.get("event_id") or ""),
            runtime_job_id=None,
            action_type=action_type,
            status="suppressed",
            delivery_surface=delivery_surface,
            decision_reason="suppressed",
            suppression_reason=suppression_reason,
            content={},
            metadata=metadata or {},
        )

    @staticmethod
    def _build_useful_item(
        *,
        item_type: str,
        text: str,
        attention_state: str = "attention_needed",
        subject_text: str = "",
    ) -> Dict[str, Any]:
        useful_item_type = str(item_type or "none").strip() or "none"
        useful_item_text = str(text or "").strip()
        normalized_attention = str(attention_state or "").strip().lower()
        if normalized_attention not in {"tracking", "attention_needed"}:
            normalized_attention = "attention_needed" if useful_item_text else "none"

        item: Dict[str, Any] = {
            "type": useful_item_type,
            "text": useful_item_text,
            "attention_state": normalized_attention,
        }
        clean_subject_text = str(subject_text or "").strip()
        if clean_subject_text:
            item["subject_text"] = clean_subject_text
        return item

    @staticmethod
    def _normalize_useful_item(useful_item: Optional[Dict[str, Any]]) -> Dict[str, str]:
        item = dict(useful_item or {})
        useful_item_type = str(item.get("type") or "none").strip() or "none"
        useful_item_text = str(item.get("text") or "").strip()
        useful_item_attention_state = str(
            item.get("attention_state") or ""
        ).strip().lower()
        if useful_item_attention_state not in {"tracking", "attention_needed"}:
            useful_item_attention_state = "attention_needed" if useful_item_text else "none"
        useful_item_subject = str(
            item.get("subject_text") or useful_item_text
        ).strip()
        normalized = {
            "type": useful_item_type,
            "text": useful_item_text,
            "attention_state": useful_item_attention_state,
        }
        if useful_item_subject:
            normalized["subject_text"] = useful_item_subject
        return normalized

    @classmethod
    def _build_arrival_action_content(
        cls,
        *,
        useful_item: Optional[Dict[str, Any]] = None,
        bundle: Optional[Dict[str, Any]] = None,
        delivered: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        useful_item_contract = cls._normalize_useful_item(useful_item)
        content: Dict[str, Any] = {
            "useful_item": useful_item_contract,
            "useful_item_type": str(
                (bundle or {}).get("useful_item_type")
                or useful_item_contract.get("type")
                or "none"
            ).strip()
            or "none",
            "useful_item_attention_state": str(
                useful_item_contract.get("attention_state") or "none"
            ).strip()
            or "none",
        }
        useful_item_text = str(useful_item_contract.get("text") or "").strip()
        if useful_item_text:
            content["useful_item_text"] = useful_item_text
        useful_item_subject = str(
            useful_item_contract.get("subject_text") or useful_item_text
        ).strip()
        if useful_item_subject:
            content["useful_item_subject"] = useful_item_subject

        bundle_payload = dict(bundle or {})
        for key in ("spoken_text", "dashboard_text", "discord_dm_text", "mirror_reason"):
            value = str(bundle_payload.get(key) or "").strip()
            if value:
                content[key] = value
        if delivered is not None:
            content["delivered"] = {
                "dashboard": bool(delivered.get("dashboard")),
                "local_voice": bool(delivered.get("local_voice")),
                "discord_dm": bool(delivered.get("discord_dm")),
            }
        return content

    async def _choose_useful_item(self, *, owner_user_id: int) -> Optional[Dict[str, Any]]:
        # Great Excision (2026-07-09): the workflow-followthrough and
        # operator-action sources were removed with the operator engine —
        # both had been empty live for months (0-row stores), so the ambient
        # "useful item" selection reduces to the runtime-jobs source.
        jobs = await runtime_control.list_jobs(owner_user_id=int(owner_user_id), limit=16)
        for job in jobs:
            if str(job.get("status") or "").strip().lower() in {"failed", "waiting"} and str(job.get("job_type") or "") not in {"ambient_action"}:
                job_text = str(job.get("request_text") or job.get("job_type") or "").strip()
                return self._build_useful_item(
                    item_type="runtime_job",
                    text=job_text,
                    attention_state="attention_needed",
                    subject_text=job_text,
                )
        return None

    @staticmethod
    def _build_style_notes(
        *,
        privacy_state: str,
        quiet_hours_active: bool,
        machine_state: str,
        has_useful_item: bool,
        voice_enabled: bool,
    ) -> str:
        notes = []
        if privacy_state == "shared":
            notes.append("Stay socially careful. Do not sound like you are taking over the room.")
        if quiet_hours_active:
            notes.append("Keep it especially low-key because quiet hours are active.")
        if machine_state == "locked":
            notes.append("The owner may not be settled in yet, so avoid sounding abrupt.")
        if has_useful_item:
            notes.append("If you mention the useful thing, make it feel natural rather than like a bulletin.")
        if not voice_enabled:
            notes.append("This may be read rather than spoken, but it should still sound like you.")
        return " ".join(notes).strip()

ambient_service = AmbientPlannerService()

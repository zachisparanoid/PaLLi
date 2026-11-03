from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ai.ambient_identity_context import (
    AmbientArrivalIdentityPlan,
    build_ambient_identity_snapshot,
)
from ai.llm_client import get_task_model, llm
from ai.prompt_loader import prompt_loader
from core.identity import identity_service
from core.llm_json import safe_parse_json_object
from core.prompt_format import format_prompt
from core.time_utils import get_current_time_context

logger = logging.getLogger(__name__)


class AmbientGenerator:
    async def generate_arrival_bundle(
        self,
        *,
        owner_user_id: int,
        event_type: str,
        context_snapshot: Dict[str, Any],
        delivery_surfaces: list[str],
        useful_item: Optional[Dict[str, Any]] = None,
        reply_posture: str = "brief_companion",
        style_notes: str = "",
    ) -> Dict[str, Any]:
        useful_item_contract = self._normalize_useful_item(useful_item)
        useful_item_type = str(useful_item_contract.get("type") or "none").strip() or "none"
        useful_item_text = str(useful_item_contract.get("text") or "").strip()
        useful_item_attention_state = str(
            useful_item_contract.get("attention_state") or "none"
        ).strip() or "none"
        useful_item_subject = str(
            useful_item_contract.get("subject_text") or useful_item_text
        ).strip()
        prompt_template = prompt_loader.load_prompt("ambient_arrival_generation")
        if not prompt_template:
            return self._fallback_bundle(
                useful_item_type=useful_item_type,
                useful_item_text=useful_item_text,
                useful_item_attention_state=useful_item_attention_state,
                useful_item_subject=useful_item_subject,
            )

        identity_context = ""
        arrival_plan = AmbientArrivalIdentityPlan()
        try:
            compiled_identity = await identity_service.compile_identity(
                user_id=int(owner_user_id),
                platform="owner",
                ensure_materialized=True,
                identity_owner_id=int(owner_user_id),
            )
            identity_snapshot = build_ambient_identity_snapshot(compiled_identity)
            identity_context = identity_snapshot.prompt_context
            arrival_plan = identity_snapshot.arrival_plan
        except Exception as exc:
            logger.error(
                "Failed to compile ambient identity context for owner %s: %s",
                owner_user_id,
                exc,
            )

        useful_item_delivery_style = self._resolve_useful_item_delivery_style(
            useful_item_attention_state=useful_item_attention_state,
            arrival_plan=arrival_plan,
        )
        useful_item_guidance = self._build_useful_item_guidance(
            useful_item_attention_state=useful_item_attention_state,
            useful_item_delivery_style=useful_item_delivery_style,
        )

        user_prompt = format_prompt(
            prompt_template,
            current_time_context=get_current_time_context(),
            event_type=event_type,
            privacy_state=str(context_snapshot.get("privacy_state") or "unknown"),
            quiet_hours_active="yes" if bool(context_snapshot.get("quiet_hours_active")) else "no",
            machine_state=str(context_snapshot.get("machine_state") or "unknown"),
            delivery_surfaces=", ".join(delivery_surfaces) or "dashboard",
            reply_posture=reply_posture,
            useful_item_type=useful_item_type,
            useful_item_text=useful_item_text or "None.",
            useful_item_attention_state=useful_item_attention_state,
            useful_item_subject=useful_item_subject or "None.",
            useful_item_delivery_style=useful_item_delivery_style,
            useful_item_guidance=useful_item_guidance,
            style_notes=style_notes or "None.",
        )

        messages = []
        if identity_context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Your ambient arrival identity context:\n"
                        f"{identity_context}"
                    ),
                }
            )
        messages.append(
            {
                "role": "system",
                "content": (
                    "You are PaLLi generating ambient arrival content. "
                    "Keep it warm, human, and brief. Return strict JSON only."
                ),
            }
        )
        messages.append({"role": "user", "content": user_prompt})

        try:
            # 1500 (bumped from 280 on 2026-05-05). 24 truncations in 5 days
            # of logs — 280 was hopelessly tight for any reasoning-capable
            # response model with JSON output. Reasoning tokens alone often
            # exceeded 280 before a single visible character emitted.
            response = await llm.chat_completion(
                messages,
                model=get_task_model("response"),
                temperature=0.35,
                max_tokens=1500,
                log_context=f"ambient_arrival_generation_{owner_user_id}",
            )
        except Exception as exc:
            logger.error("Ambient arrival generation failed for owner %s: %s", owner_user_id, exc)
            return self._fallback_bundle(
                useful_item_type=useful_item_type,
                useful_item_text=useful_item_text,
                useful_item_attention_state=useful_item_attention_state,
                useful_item_subject=useful_item_subject,
            )

        parsed = safe_parse_json_object(response or "")
        if not isinstance(parsed, dict):
            return self._fallback_bundle(
                useful_item_type=useful_item_type,
                useful_item_text=useful_item_text,
                useful_item_attention_state=useful_item_attention_state,
                useful_item_subject=useful_item_subject,
            )

        bundle = {
            "spoken_text": str(parsed.get("spoken_text") or "").strip(),
            "dashboard_text": str(parsed.get("dashboard_text") or "").strip(),
            "discord_dm_text": str(parsed.get("discord_dm_text") or "").strip(),
            "useful_item_type": str(parsed.get("useful_item_type") or useful_item_type).strip() or useful_item_type,
            "mirror_reason": str(parsed.get("mirror_reason") or "").strip(),
        }
        if not bundle["spoken_text"]:
            return self._fallback_bundle(
                useful_item_type=useful_item_type,
                useful_item_text=useful_item_text,
                useful_item_attention_state=useful_item_attention_state,
                useful_item_subject=useful_item_subject,
            )
        if not bundle["dashboard_text"]:
            bundle["dashboard_text"] = bundle["spoken_text"]
        if not bundle["discord_dm_text"]:
            bundle["discord_dm_text"] = bundle["dashboard_text"]
        return bundle

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
        return {
            "type": useful_item_type,
            "text": useful_item_text,
            "attention_state": useful_item_attention_state,
            "subject_text": useful_item_subject,
        }

    @staticmethod
    def _resolve_useful_item_delivery_style(
        *,
        useful_item_attention_state: str,
        arrival_plan: AmbientArrivalIdentityPlan,
    ) -> str:
        if useful_item_attention_state == "tracking":
            return str(arrival_plan.tracked_item_style or "quiet_tracking").strip()
        if useful_item_attention_state == "attention_needed":
            return str(arrival_plan.attention_item_style or "calm_attention").strip()
        return "no_item"

    @staticmethod
    def _build_useful_item_guidance(
        *,
        useful_item_attention_state: str,
        useful_item_delivery_style: str,
    ) -> str:
        if useful_item_attention_state == "tracking":
            return (
                "If you mention the useful thing, frame it as calm background tracking. "
                "Do not make it sound urgent, corrective, or like a fresh assignment. "
                f"Preferred delivery style: {useful_item_delivery_style}."
            )
        if useful_item_attention_state == "attention_needed":
            return (
                "If you mention the useful thing, make it clear it still deserves attention, "
                "but keep it grounded and non-alarmist. "
                f"Preferred delivery style: {useful_item_delivery_style}."
            )
        return "No useful item."

    @staticmethod
    def _fallback_bundle(
        *,
        useful_item_type: str,
        useful_item_text: str,
        useful_item_attention_state: str,
        useful_item_subject: str,
    ) -> Dict[str, Any]:
        spoken = "Hey. Welcome back."
        useful_item_reference = (
            str(useful_item_subject or useful_item_text).strip().rstrip(".!?")
        )
        if useful_item_reference:
            if useful_item_attention_state == "tracking":
                spoken = (
                    f"{spoken} I'm already keeping track of this for later: "
                    f"{useful_item_reference}."
                )
            elif useful_item_attention_state == "attention_needed":
                spoken = f"{spoken} One thing to keep in view: {useful_item_reference}."
            else:
                spoken = f"{spoken} One thing: {useful_item_reference}."
        return {
            "spoken_text": spoken,
            "dashboard_text": spoken,
            "discord_dm_text": spoken,
            "useful_item_type": useful_item_type,
            "mirror_reason": "fallback_generation",
        }


ambient_generator = AmbientGenerator()

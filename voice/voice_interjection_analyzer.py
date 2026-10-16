"""Voice interjection analyzer — determines when AI should speak up during passive listening.

Lives in `voice/` because its responsibility is voice-cluster-specific: it runs
on a silence-window timer in the voice presence flow, NOT on per-turn decide
cycles. `check_direct_address` is a pure wake-word string matcher; `should_interject`
is a passive-listening LLM call with its own prompt
(`prompts/voice_interjection_decision.txt`). Neither responsibility is owned by
`core/turn/decide.py`.

Moved from `ai/voice_interjection_analyzer.py` in C4 (2026-05-12) to colocate
with the rest of the voice cluster.
"""

import logging
import json
import re
from typing import Any, Dict, List, Optional
from datetime import datetime

from ai.interaction_strategy import strategy_allows_outward_action
from ai.llm_client import llm, get_task_model
from ai.prompt_loader import prompt_loader
from core.activity_logger import activity_logger
from core.config import config
from core.llm_json import safe_parse_json_object
from core.prompt_format import format_prompt

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")


class VoiceInterjectionAnalyzer:
    """Analyzes passive listening context to determine when to interject"""

    def __init__(self):
        self.last_check_time = {}  # channel_id -> timestamp

    async def should_interject(
        self,
        passive_context: List[Dict],
        channel_id: int,
        conversation_history: List[Dict],
        user_id: Optional[int] = None,
        min_confidence: float = 0.7,
        personality: str = "observer",
        interaction_strategy: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """
        Analyze accumulated passive listening context to decide if AI should speak up

        Args:
            passive_context: List of recent transcriptions with metadata
                [{"user_id": int, "username": str, "text": str, "timestamp": float}, ...]
            channel_id: Channel ID for tracking
            conversation_history: Full conversation history from database
            user_id: Primary user ID (if applicable)
            min_confidence: Minimum confidence threshold to interject

        Returns:
            {
                "should_interject": bool,
                "confidence": float,
                "reasoning": str,
                "urgency": str (low/medium/high),
                "suggested_approach": str (question/information/humor/support)
            }
        """
        try:
            if not passive_context:
                return {
                    "should_interject": False,
                    "confidence": 0.0,
                    "reasoning": "No passive context available",
                    "urgency": "none",
                    "suggested_approach": "none"
                }

            # Load interjection decision prompt
            prompt_template = prompt_loader.load_prompt("voice_interjection_decision")
            if not prompt_template:
                essential_logger.error("❌ Failed to load voice_interjection_decision prompt")
                return self._default_no_interject("Prompt not available")

            # Format passive context for analysis
            passive_context_str = self._format_passive_context(passive_context)

            # Format conversation history
            history_str = self._format_conversation_history(conversation_history)

            # Adjust system message based on personality
            personality_instructions = self._get_personality_instructions(personality, min_confidence)

            # Build the prompt
            full_prompt = format_prompt(
                prompt_template,
                passive_context=passive_context_str,
                conversation_history=history_str,
                current_time=datetime.now().strftime("%I:%M %p"),
                min_confidence=min_confidence
            )

            messages = [
                {
                    "role": "system",
                    "content": personality_instructions
                },
                {"role": "user", "content": full_prompt}
            ]

            # Log decision start
            op_id = activity_logger.log_decision_start(user_id or 0)
            essential_logger.info(f"🎤 Analyzing {len(passive_context)} utterances for interjection opportunity...")

            # Get LLM decision
            response = await llm.chat_completion(
                messages,
                model=get_task_model("analysis"),
                temperature=0.3,
                max_tokens=500
            )

            if not response:
                essential_logger.warning("⚠️ No response from interjection analyzer")
                return self._default_no_interject("LLM response failed")

            # Parse JSON response
            try:
                decision = safe_parse_json_object(response)
                if not isinstance(decision, dict):
                    raise ValueError("non-dict decision")

                # Validate response structure
                if "should_interject" not in decision or "confidence" not in decision:
                    essential_logger.error(f"❌ Invalid decision structure: {decision}")
                    return self._default_no_interject("Invalid response structure")

                # Log the decision
                should_interject = decision.get("should_interject", False)
                confidence = decision.get("confidence", 0.0)
                reasoning = decision.get("reasoning", "No reasoning provided")

                activity_logger.log_decision_result(
                    op_id,
                    should_interject,
                    confidence,
                    f"Voice interjection: {reasoning}"
                )

                if should_interject and confidence >= min_confidence:
                    essential_logger.info(
                        f"✅ Interjection approved (confidence: {confidence:.2f}) - {reasoning}"
                    )
                else:
                    essential_logger.debug(
                        f"🔇 Staying quiet (confidence: {confidence:.2f}) - {reasoning}"
                    )

                return apply_interaction_strategy_to_interjection(
                    decision,
                    interaction_strategy,
                )

            except Exception as e:
                essential_logger.error(f"❌ Failed to parse interjection decision JSON: {e}")
                activity_logger.log_error("voice_interjection", f"Analysis failed: {e}")
                return self._default_no_interject(f"Exception: {str(e)}")
        except Exception as e:
            essential_logger.error(f"❌ Error in interjection analysis: {e}")
            activity_logger.log_error("voice_interjection", f"Analysis failed: {e}")
            return self._default_no_interject(f"Exception: {str(e)}")

    def _format_passive_context(self, passive_context: List[Dict]) -> str:
        """Format passive listening context for LLM analysis"""
        if not passive_context:
            return "No recent utterances"

        lines = []
        for ctx in passive_context:
            username = ctx.get("username", "Unknown")
            text = ctx.get("text", "")
            timestamp = ctx.get("timestamp", 0)

            # Format timestamp as seconds ago
            from time import time
            seconds_ago = int(time() - timestamp)
            time_str = f"{seconds_ago}s ago" if seconds_ago < 60 else f"{seconds_ago // 60}m ago"

            lines.append(f"[{time_str}] {username}: {text}")

        return "\n".join(lines)

    def _format_conversation_history(self, history: List[Dict]) -> str:
        """Format conversation history for context"""
        if not history:
            return "No prior conversation history"

        # Use configured context window for history
        window_size = getattr(config, "ANALYSIS_CONTEXT_WINDOW_SIZE", config.CONTEXT_WINDOW_SIZE)
        recent_history = history[-window_size:] if len(history) > window_size else history

        lines = []
        for msg in recent_history:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            username = msg.get("username", "User" if role == "user" else "Assistant")

            # Truncate long messages
            if len(content) > 200:
                content = content[:197] + "..."

            lines.append(f"{username}: {content}")

        return "\n".join(lines)

    def _get_personality_instructions(self, personality: str, min_confidence: float) -> str:
        """Get system instructions based on listening personality"""
        if personality == "participant":
            return f"""You are analyzing whether an AI assistant should interject in a voice conversation.
PERSONALITY: PARTICIPANT - You are an active participant who chimes in naturally and frequently.
- Interject when you have something interesting to add, even if not asked
- Share relevant experiences, facts, or observations
- Add humor or perspective when appropriate
- Be conversational and natural, like a friend in the room
- Lower threshold for interjection (>= {min_confidence * 0.7:.2f})
- Surprise people with "oh wow they were really listening!" moments"""

        elif personality == "ghost":
            return f"""You are analyzing whether an AI assistant should interject in a voice conversation.
PERSONALITY: GHOST - You are nearly invisible, only speaking when absolutely critical.
- Extremely conservative - only interject for urgent matters
- Direct questions or critical errors only
- Casual conversation gets no response
- Make people forget you're even there
- Very high threshold for interjection (>= {min_confidence * 1.3:.2f})"""

        else:  # observer (default)
            return f"""You are analyzing whether an AI assistant should interject in a voice conversation.
PERSONALITY: OBSERVER - You are a thoughtful observer who speaks when valuable.
- Conservative but helpful - interject when you add genuine value
- Respond to questions and confusion
- Stay quiet during casual chit-chat
- Standard threshold for interjection (>= {min_confidence})"""

    def _default_no_interject(self, reason: str) -> Dict:
        """Return default 'no interjection' decision"""
        return {
            "should_interject": False,
            "confidence": 0.0,
            "reasoning": reason,
            "urgency": "none",
            "suggested_approach": "none"
        }

    async def check_direct_address(self, text: str) -> bool:
        """
        Quick check if someone is directly addressing the AI
        This bypasses the full interjection analysis for obvious mentions

        Args:
            text: Transcribed text to check

        Returns:
            True if direct address detected (e.g., "Hey Palli", "Palli, can you...")
        """
        text_lower = text.lower().strip()
        normalized_text = re.sub(r"[^a-z0-9]+", " ", text_lower).strip()
        normalized_tokens = normalized_text.split()
        normalized_window = f" {normalized_text} "

        # Common wake words/addresses (including common mishearings)
        wake_phrases = [
            "hey palli", "hi palli", "hello palli", "yo palli", "okay palli", "ok palli",
            "hey pali", "hi pali", "hello pali", "yo pali", "okay pali", "ok pali",
            "hey holly", "hi holly", "hello holly", "yo holly", "okay holly", "ok holly",
            "hey poly", "hi poly", "hello poly", "yo poly", "okay poly", "ok poly",
            "hey polly", "hi polly", "hello polly", "yo polly", "okay polly", "ok polly",
            "hey paulie", "hi paulie", "hello paulie", "yo paulie", "okay paulie", "ok paulie",
        ]
        single_wake_words = {"palli", "pali", "holly", "poly", "polly", "paulie"}

        # Direct imperative commands (someone giving the bot a command)
        imperative_phrases = [
            "please respond",
            "please answer",
            "respond to me",
            "respond to this",
            "respond to this message",
            "answer me",
            "can you hear me",
            "are you there",
            "are you still there",
            "you there",
            "you still there",
            "respond regardless",
            "answer again",
            "talk to me",
            "say something",
            "speak up",
        ]

        for wake in wake_phrases:
            if f" {wake} " in normalized_window:
                essential_logger.info(f"🎯 Direct address detected: '{wake}' in '{text[:50]}...'")
                return True

        for index, token in enumerate(normalized_tokens[:3]):
            if token in single_wake_words:
                essential_logger.info(f"🎯 Direct address detected: '{token}' in '{text[:50]}...'")
                return True

        # Check for imperative phrases
        for phrase in imperative_phrases:
            if phrase in text_lower or phrase in normalized_text:
                essential_logger.info(f"🎯 Direct command detected: '{phrase}' in '{text[:50]}...'")
                return True

        return False


def apply_interaction_strategy_to_interjection(
    decision: Dict[str, Any],
    interaction_strategy: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(interaction_strategy, dict):
        return decision
    normalized = dict(decision or {})
    normalized["interaction_strategy"] = dict(interaction_strategy)
    if strategy_allows_outward_action(interaction_strategy):
        return normalized
    normalized["should_interject"] = False
    prior_reason = str(normalized.get("reasoning") or "").strip()
    strategy_reason = str(interaction_strategy.get("reason") or "").strip()
    suffix = (
        f"Strategy blocked interjection: {strategy_reason}"
        if strategy_reason
        else "Strategy blocked interjection"
    )
    normalized["reasoning"] = f"{prior_reason}; {suffix}" if prior_reason else suffix
    return normalized


# Global instance
voice_interjection_analyzer = VoiceInterjectionAnalyzer()

"""
Vision analysis module with contextual understanding

This module handles image analysis with context-aware responses that understand:
- Memes and humor
- Visual context and emotional tone
- Conversation flow and user personality
"""

import logging
import base64
from typing import Dict, List, Optional, Any
from io import BytesIO

from ai.llm_client import llm, get_task_model
from core.activity_logger import activity_logger
from ai.prompt_loader import prompt_loader
from core.config import config
from core.prompt_format import format_prompt

logger = logging.getLogger(__name__)

# Output budget for the contextual read (Batch 3, 1.1, fix round 1). Every
# vision provider defaults to 1000 tokens; with the visible text copied
# first plus eight more JSON fields, a screenshot of an email or a document
# page overran that, the reply was cut off mid-JSON, and the providers'
# non-JSON fallback handed her the raw fragment as the description.
# Budgets are code, never prompt.
VISION_MAX_TOKENS = 4000


class VisionAnalyzer:
    """Handles vision analysis with contextual understanding"""

    def __init__(self):
        self.supported_formats = ["image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"]

    async def analyze_image_with_context(
        self,
        image_data: bytes,
        content_type: str,
        conversation_history: List[Dict],
        user_message: str = "",
        personality_context: str = "",
        user_id: int = None
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze an image within the context of the conversation

        Args:
            image_data: Raw image bytes
            content_type: MIME type of the image
            conversation_history: Recent conversation history
            user_message: Any text message accompanying the image
            personality_context: User's personality profile for tone matching
            user_id: User ID for logging

        Returns:
            Dict containing:
                - visible_text: The text visible in the image, exactly as written
                - description: Technical description of the image
                - contextual_understanding: What the image means in context
                - suggested_response: How PaLLi should respond
                - humor_detected: Whether image appears to be humorous
                - emotional_tone: Detected emotional tone
        """
        try:
            if not self._is_supported_format(content_type):
                logger.warning(f"Unsupported image format: {content_type}")
                return None

            # Log vision analysis start
            if user_id:
                op_id = activity_logger.log_vision_analysis_start(user_id)

            # Encode image for LLM
            encoded_image = base64.b64encode(image_data).decode('utf-8')

            # Build context-aware analysis prompt
            analysis_prompt = self._build_analysis_prompt(
                conversation_history,
                user_message,
                personality_context
            )

            # Call vision-capable LLM
            result = await llm.vision_completion(
                image_base64=encoded_image,
                prompt=analysis_prompt,
                conversation_history=conversation_history,
                model=get_task_model("vision"),
                max_tokens=VISION_MAX_TOKENS,
                content_type=content_type
            )

            if result:
                if user_id:
                    activity_logger.log_vision_analysis_complete(op_id, result.get("contextual_understanding", ""))
                return result

            return None

        except Exception as e:
            logger.error(f"Error analyzing image: {e}")
            if user_id:
                activity_logger.log_error("vision_analysis", f"Analysis failed: {e}")
            return None

    def _is_supported_format(self, content_type: str) -> bool:
        """Check if image format is supported"""
        return content_type.lower() in self.supported_formats

    def _build_analysis_prompt(
        self,
        conversation_history: List[Dict],
        user_message: str,
        personality_context: str
    ) -> str:
        """Build context-aware prompt for image analysis"""

        # Get recent conversation context using configured window
        window_size = getattr(config, "ANALYSIS_CONTEXT_WINDOW_SIZE", config.CONTEXT_WINDOW_SIZE)
        recent_messages = conversation_history[-window_size:] if len(conversation_history) >= window_size else conversation_history
        conversation_snippet = "\n".join([
            f"{msg.get('role', 'user')}: {msg.get('content', '')[:200]}"
            for msg in recent_messages
        ])

        # Load prompt template
        prompt_template = prompt_loader.load_prompt("vision_analysis")
        if not prompt_template:
            logger.error("Failed to load vision_analysis prompt")
            return None

        # Format the prompt with context
        prompt = format_prompt(
            prompt_template,
            context_summary="Recent conversation context for image analysis",
            conversation_snippet=f"Recent conversation:\n{conversation_snippet}",
            user_message_context=f"User's message with this image: \"{user_message}\"",
            personality_context=personality_context if personality_context else ""
        )

        return prompt

    async def generate_contextual_response(
        self,
        analysis: Dict[str, Any],
        user_message: str,
        personality_context: str
    ) -> str:
        """
        Generate a contextual response based on vision analysis

        This is the key function that makes PaLLi respond naturally to images
        rather than just describing them.
        """
        try:
            # Use the suggested response from analysis as a starting point
            suggested_response = analysis.get("suggested_response", "")

            # If analysis suggests humor was detected, ensure response is appropriate
            if analysis.get("humor_detected", False):
                humor_type = analysis.get("humor_type", "meme")
                emotional_tone = analysis.get("emotional_tone", "playful")

                # Return natural, contextual response
                return suggested_response

            # For sincere images, respond with genuine interest
            elif analysis.get("emotional_tone") == "sincere":
                return suggested_response

            # Default to suggested response
            return suggested_response

        except Exception as e:
            logger.error(f"Error generating contextual response: {e}")
            return "I see your image! Thanks for sharing."


# Global vision analyzer instance
vision_analyzer = VisionAnalyzer()

"""
Enhanced activity logger for comprehensive CLI logging of bot activities
"""

import logging
import time
import threading
import sys
from datetime import datetime
from typing import Dict, Optional, Any
from core.config import config

class ActivityLogger:
    """Enhanced logger that provides comprehensive CLI updates for all bot activities"""
    
    def __init__(self):
        self.lock = threading.Lock()
        self.active_operations = {}  # operation_id -> start_time
        self.user_contexts = {}  # user_id -> username for context
        
        # Setup console handler with custom formatter
        self.logger = logging.getLogger("palli.activity")
        self.logger.setLevel(logging.INFO)
        
        # Remove existing handlers to avoid duplicates
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
        
        # Don't add handlers here - let the main logging setup handle it
        # This ensures all activity logs go to both console and file
        self.logger.propagate = False
        
        # Get the root logger to ensure our messages go through the main logging system
        self.root_logger = logging.getLogger()
    
    def set_user_context(self, user_id: int, username: str):
        """Set username context for user ID"""
        with self.lock:
            self.user_contexts[user_id] = username
    
    def get_username(self, user_id: int) -> str:
        """Get username for user ID with fallback"""
        return self.user_contexts.get(user_id, f"User{user_id}")

    def _redacted_preview(self, text: Any, default_limit: int = 50) -> str:
        """
        Return a log-safe preview of potentially sensitive content.

        In production, defaults should be configured to fully redact user content.
        """
        value = ""
        if text is None:
            value = ""
        elif isinstance(text, str):
            value = text
        else:
            value = str(text)

        value = value.replace("\n", " ").replace("\r", " ").strip()
        if not value:
            return "[redacted]"

        if bool(getattr(config, "LOG_REDACT_USER_CONTENT", False)):
            limit = int(getattr(config, "LOG_USER_CONTENT_PREVIEW_CHARS", 0) or 0)
            if limit <= 0:
                return "[redacted]"
            if len(value) > limit:
                return value[:limit] + "..."
            return value

        limit = int(getattr(config, "LOG_USER_CONTENT_PREVIEW_CHARS", default_limit) or default_limit)
        if limit <= 0:
            return "[redacted]"
        if len(value) > limit:
            return value[:limit] + "..."
        return value
    
    def log_message_processing_start(self, username: str, channel_name: str, content: str):
        """Log start of message processing pipeline"""
        content_preview = self._redacted_preview(content, default_limit=50)
        self.root_logger.info(f"🚀 Processing message from {username} in #{channel_name}: '{content_preview}'")
    
    def log_message_processing_complete(self):
        """Log completion of message processing pipeline"""
        self.root_logger.info(f"🎉 Message processing pipeline completed")
    
    def log_message_received(self, user_id: int, platform: str, content: str, is_voice: bool = False):
        """Log incoming message"""
        username = self.get_username(user_id)
        message_type = "🎤 Voice" if is_voice else "💬 Text"
        content_preview = self._redacted_preview(content, default_limit=50)
        self.root_logger.info(f"📨 {message_type} message from {username} ({platform}): {content_preview}")
    
    def log_database_operation(self, operation: str, details: str = None):
        """Log database operations"""
        detail_text = f" - {details}" if details else ""
        self.root_logger.info(f"💾 {operation.replace('_', ' ').title()}{detail_text}")
    
    def log_database_success(self, message: str):
        """Log successful database operation"""
        self.root_logger.info(f"✅ {message}")
    
    def log_conversation_history_start(self):
        """Log start of conversation history loading"""
        self.root_logger.info(f"📚 Loading conversation history...")
    
    def log_conversation_history_loaded(self, count: int):
        """Log conversation history loaded"""
        self.root_logger.info(f"📚 Loaded {count} messages from history")
    
    def log_reminder_check_start(self):
        """Log start of reminder checking"""
        self.root_logger.info(f"⏰ Checking for reminder requests...")
    
    def log_reminder_check_complete(self, found: bool):
        """Log reminder check completion"""
        if not found:
            self.root_logger.info(f"⏰ No reminder patterns detected")
    
    def log_reminder_detected(self, user_id: int, reminder_text: str):
        """Log reminder detection"""
        username = self.get_username(user_id)
        self.root_logger.info(f"🧠 Analyzing message for reminder patterns...")
        self.root_logger.info(f"✅ Reminder pattern detected: {self._redacted_preview(reminder_text, default_limit=60)}")
    
    def log_reminder_scheduled(self, user_id: int, reminder_text: str, scheduled_time: float):
        """Log reminder scheduling"""
        username = self.get_username(user_id)
        time_str = datetime.fromtimestamp(scheduled_time).strftime("%Y-%m-%d %H:%M:%S")
        self.root_logger.info(f"⏰ Parsing time expression...")
        self.root_logger.info(f"✅ Reminder scheduled for: {scheduled_time}")
        self.root_logger.info(f"💾 Reminder stored with ID and confirmation prepared")
    
    def log_auto_response_decision(self, reason: str):
        """Log automatic response decision"""
        self.root_logger.info(f"🤖 Auto-responding due to: {reason}")
    
    def log_decision_start(self, user_id: int):
        """Log start of response decision process"""
        username = self.get_username(user_id)
        operation_id = f"decision_{user_id}_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"🤔 Analyzing if bot should respond to {username}...")
        return operation_id
    
    def log_decision_result(self, operation_id: str, should_respond: bool, confidence: float, reason: str):
        """Log decision result with timing"""
        elapsed = self._get_elapsed_time(operation_id)
        decision = "✅ Will respond" if should_respond else "❌ Will not respond"
        self.root_logger.info(f"🎯 Decision: {decision} (confidence: {confidence:.2f}, {reason}) {elapsed}")
    
    def log_no_response_decision(self):
        """Log when bot decides not to respond"""
        self.root_logger.info(f"🚫 Bot decided not to respond - processing stopped")
        self.root_logger.info("=" * 80)
    
    def log_intent_detection_start(self, user_id: int):
        """Log start of intent detection"""
        username = self.get_username(user_id)
        operation_id = f"intent_{user_id}_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"🔍 Detecting intent for {username}'s message...")
        return operation_id
    
    def log_intent_detected(self, operation_id: str, intent: str, confidence: float):
        """Log detected intent"""
        elapsed = self._get_elapsed_time(operation_id)
        self.root_logger.info(f"🎯 Intent detected: {intent} (confidence: {confidence:.2f}) {elapsed}")
    
    def log_personality_analysis_start(self, user_id: int):
        """Log start of personality analysis"""
        username = self.get_username(user_id)
        operation_id = f"personality_{user_id}_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"🧠 Analyzing {username}'s personality and communication style...")
        return operation_id
    
    def log_personality_analysis_complete(self, operation_id: str, insights: str = None):
        """Log personality analysis completion"""
        elapsed = self._get_elapsed_time(operation_id)
        insight_text = f" - {insights}" if insights else ""
        self.root_logger.info(f"✅ Personality analysis complete{insight_text} {elapsed}")
        self.root_logger.info("=" * 80)
    
    def log_multi_analysis_start(self, user_id: int):
        """Log start of multi-analysis"""
        username = self.get_username(user_id)
        operation_id = f"multi_analysis_{user_id}_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"🔬 Running comprehensive multi-analysis for {username}...")
        return operation_id
    
    def log_multi_analysis_complete(self, operation_id: str, analysis_summary: str = None):
        """Log multi-analysis completion"""
        elapsed = self._get_elapsed_time(operation_id)
        summary = (
            f" - {self._redacted_preview(analysis_summary, default_limit=60)}"
            if analysis_summary
            else ""
        )
        self.root_logger.info(f"✅ Multi-analysis complete{summary} {elapsed}")
    
    def log_web_search_decision_start(self):
        """Log start of web search decision"""
        self.root_logger.info(f"🌐 Analyzing if web search is needed...")
    
    def log_web_search_decision(self, user_id: int, should_search: bool, query: str = None):
        """Log web search decision"""
        username = self.get_username(user_id)
        if should_search:
            self.root_logger.info(f"✅ Web search needed for {username}: {query}")
        else:
            self.root_logger.info(f"📚 Using existing knowledge for {username} (no web search needed)")
    
    def log_web_search_start(self, query: str):
        """Log start of web search"""
        operation_id = f"search_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"🔍 Searching web for: {query}")
        return operation_id
    
    def log_web_search_complete(self, operation_id: str, results_count: int):
        """Log web search completion"""
        elapsed = self._get_elapsed_time(operation_id)
        self.root_logger.info(f"✅ Web search complete - found {results_count} relevant results {elapsed}")
    
    def log_response_generation_start(self, user_id: int, is_voice: bool = False):
        """Log start of response generation"""
        username = self.get_username(user_id)
        response_type = "🎤 voice" if is_voice else "💬 text"
        operation_id = f"response_{user_id}_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"🧠 Generating personalized {response_type} response for {username}...")
        return operation_id
    
    def log_response_generated(self, operation_id: str, response_preview: str):
        """Log response generation completion"""
        elapsed = self._get_elapsed_time(operation_id)
        preview = self._redacted_preview(response_preview, default_limit=50)
        self.root_logger.info(f"✅ Response generated: {preview} {elapsed}")
    
    def log_voice_transcription_start(self, user_id: int):
        """Log start of voice transcription"""
        username = self.get_username(user_id)
        operation_id = f"transcription_{user_id}_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"🎤 Transcribing voice message from {username}...")
        return operation_id
    
    def log_voice_transcription_complete(self, operation_id: str, transcription: str):
        """Log voice transcription completion"""
        elapsed = self._get_elapsed_time(operation_id)
        preview = self._redacted_preview(transcription, default_limit=50)
        self.root_logger.info(f"✅ Transcription: {preview} {elapsed}")
    
    def log_voice_synthesis_start(self, text: str):
        """Log start of voice synthesis"""
        operation_id = f"tts_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        preview = self._redacted_preview(text, default_limit=30)
        self.root_logger.info(f"🔊 Synthesizing speech: {preview}")
        return operation_id
    
    def log_voice_synthesis_complete(self, operation_id: str):
        """Log voice synthesis completion"""
        elapsed = self._get_elapsed_time(operation_id)
        self.root_logger.info(f"✅ Speech synthesis complete {elapsed}")
    
    def log_background_learning_start(self, user_id: int):
        """Log start of background learning"""
        username = self.get_username(user_id)
        operation_id = f"learning_{user_id}_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"📊 Running background personality learning for {username}...")
        return operation_id
    
    def log_background_learning_complete(self, operation_id: str, progress: float = None):
        """Log background learning completion"""
        elapsed = self._get_elapsed_time(operation_id)
        progress_text = f" (progress: {progress:.1%})" if progress is not None else ""
        self.root_logger.info(f"✅ Background learning complete{progress_text} {elapsed}")
    
    def log_email_action_start(self, recipient: str, subject: str):
        """Log start of email action"""
        operation_id = f"email_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"📧 Sending email to {recipient}: {subject}")
        return operation_id
    
    def log_email_action_complete(self, operation_id: str, success: bool):
        """Log email action completion"""
        elapsed = self._get_elapsed_time(operation_id)
        status = "✅ sent successfully" if success else "❌ failed to send"
        self.root_logger.info(f"📧 Email {status} {elapsed}")
    
    def log_email_decision_start(self):
        """Log start of email decision process"""
        self.root_logger.info(f"📧 Analyzing message for email requests...")
    
    def log_email_decision(self, user_id: int, should_send: bool, recipient: str = None):
        """Log email decision result"""
        username = self.get_username(user_id)
        if should_send:
            self.root_logger.info(f"✅ Email request detected for {username} → {recipient}")
        else:
            self.root_logger.info(f"📚 No email request detected for {username}")
    def log_sms_action_start(self, recipient: str, message: str):
        """Log start of SMS action"""
        operation_id = f"sms_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        preview = self._redacted_preview(message, default_limit=30)
        self.root_logger.info(f"📱 Sending SMS to {recipient}: {preview}")
        return operation_id
    
    def log_sms_action_complete(self, operation_id: str, success: bool):
        """Log SMS action completion"""
        elapsed = self._get_elapsed_time(operation_id)
        status = "✅ sent successfully" if success else "❌ failed to send"
        self.root_logger.info(f"📱 SMS {status} {elapsed}")
    
    def log_error(self, component: str, error: str):
        """Log error with appropriate emoji"""
        self.root_logger.info(f"❌ Error in {component}: {error}")
        # Best-effort persistence to system_errors table for diagnostics
        try:
            self._log_error_to_db(component, error)
        except Exception:
            # Never let diagnostics logging break runtime behavior
            pass
    
    def log_warning(self, component: str, warning: str):
        """Log warning with appropriate emoji"""
        self.root_logger.info(f"⚠️ Warning in {component}: {warning}")
    
    def log_proactive_action(self, user_id: int, action_type: str, reason: str):
        """Log proactive actions"""
        username = self.get_username(user_id)
        self.root_logger.info(f"🤖 Proactive {action_type} for {username}: {reason}")

    def log_vision_analysis_start(self, user_id: int):
        """Log start of vision analysis"""
        username = self.get_username(user_id)
        operation_id = f"vision_{user_id}_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"👁️ Analyzing image from {username}...")
        return operation_id

    def log_vision_analysis_complete(self, operation_id: str, understanding: str = None):
        """Log vision analysis completion"""
        elapsed = self._get_elapsed_time(operation_id)
        if understanding:
            preview = self._redacted_preview(understanding, default_limit=50)
            self.root_logger.info(f"✅ Vision analysis complete: {preview} {elapsed}")
        else:
            self.root_logger.info(f"✅ Vision analysis complete {elapsed}")

    def log_calendar_check_start(self):
        """Log start of calendar action checking"""
        self.root_logger.info(f"📅 Checking for calendar requests...")

    def log_calendar_detection(self, detected: bool):
        """Log calendar detection result"""
        if detected:
            self.root_logger.info(f"✅ Calendar request detected")
        else:
            self.root_logger.info(f"📅 No calendar request detected")

    def log_calendar_action_start(self, action_type: str, details: str = None):
        """Log start of calendar action"""
        operation_id = f"calendar_{action_type}_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()

        detail_text = f": {details}" if details else ""
        action_emoji = {
            "view_upcoming": "👀",
            "search_events": "🔍",
            "create_event": "➕",
            "delete_event": "🗑️",
            "check_availability": "🕐"
        }.get(action_type, "📅")

        action_name = action_type.replace("_", " ").title()
        self.root_logger.info(f"{action_emoji} {action_name}{detail_text}...")
        return operation_id

    def log_calendar_action_complete(self, operation_id: str, success: bool, result_summary: str = None):
        """Log calendar action completion"""
        elapsed = self._get_elapsed_time(operation_id)

        if success:
            if result_summary:
                preview = self._redacted_preview(result_summary, default_limit=60)
                self.root_logger.info(f"✅ Calendar action complete: {preview} {elapsed}")
            else:
                self.root_logger.info(f"✅ Calendar action complete {elapsed}")
        else:
            self.root_logger.info(f"❌ Calendar action failed {elapsed}")

    def log_email_check_start(self):
        """Log start of email action checking"""
        self.root_logger.info(f"📧 Checking for email requests...")

    def log_email_detection(self, detected: bool):
        """Log email detection result"""
        if detected:
            self.root_logger.info(f"✅ Email request detected")
        else:
            self.root_logger.info(f"📧 No email request detected")

    def log_email_composition_start(self):
        """Log start of email composition"""
        operation_id = f"email_compose_{time.time()}"
        with self.lock:
            self.active_operations[operation_id] = time.time()
        self.root_logger.info(f"✍️ Composing email using AI...")
        return operation_id

    def log_email_composition_complete(self, operation_id: str):
        """Log email composition completion"""
        elapsed = self._get_elapsed_time(operation_id)
        self.root_logger.info(f"✅ Email composition complete {elapsed}")

    def _get_elapsed_time(self, operation_id: str) -> str:
        """Get elapsed time for operation"""
        with self.lock:
            if operation_id in self.active_operations:
                start_time = self.active_operations[operation_id]
                elapsed = time.time() - start_time
                del self.active_operations[operation_id]
                
                if elapsed < 0.1:
                    return "(<0.1s)"
                else:
                    return f"({elapsed:.1f}s)"
            return ""

    def _log_error_to_db(self, component: str, error: str):
        """Persist error details to SQLite for diagnostics (best-effort)."""
        try:
            # Lazy imports to avoid circular dependencies at import time
            import asyncio

            async def _write_error():
                from core.database import brain

                conn = await brain.get_connection()
                try:
                    await conn.execute(
                        """
                        INSERT INTO system_errors (timestamp, component, severity, message, context)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        time.time(),
                        component,
                        "error",
                        error,
                        None,
                    )
                finally:
                    await conn.close()

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                loop.create_task(_write_error())
            else:
                asyncio.run(_write_error())
        except Exception:
            # Swallow all exceptions; diagnostics should never be fatal
            return

# Global activity logger instance
activity_logger = ActivityLogger()

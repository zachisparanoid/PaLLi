from __future__ import annotations

import asyncio
import copy
import logging
import signal
import sys
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from core.config import config
from core.plugin_runtime import PluginChannelContribution, plugin_runtime

logger = logging.getLogger(__name__)

# The one prompt a reply cannot do without at boot: the image read. The turn
# router this list once named (combined_decision) is retired (Batch 8,
# 2026-10-01; prompts/archive/).
CRITICAL_PROMPTS = ("vision_analysis",)


class SuppressWebSocketProbeFilter(logging.Filter):
    """Suppress noisy websocket probe logs from known unauthenticated endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        is_ws_probe = (
            "WebSocket /chat/ws/stream" in message
            or "WebSocket /api/chat/ws/stream" in message
        )

        if is_ws_probe and (" 403" in message or "403 Forbidden" in message):
            return False

        if record.name == "uvicorn.error":
            if "connection rejected" in message and "403" in message:
                return False
            if "connection closed" in message:
                return False

        return True


def _resolve_prompt_loader(prompt_loader: Any | None = None) -> Any:
    loader = prompt_loader
    if loader is None:
        from ai.prompt_loader import prompt_loader as default_prompt_loader

        loader = default_prompt_loader
    return loader


def _build_prompt_loading_report(
    *,
    prompt_status: dict[str, bool],
    inventory: dict[str, Any] | None = None,
    critical_prompts: Iterable[str] = CRITICAL_PROMPTS,
) -> dict[str, Any]:
    inventory = dict(inventory or {})
    critical_prompt_names = [str(prompt_name) for prompt_name in critical_prompts]
    critical_missing = [
        prompt_name
        for prompt_name in critical_prompt_names
        if not prompt_status.get(prompt_name, False)
    ]
    loaded_count = int(
        inventory.get("loaded_count")
        or sum(1 for loaded in prompt_status.values() if loaded)
    )
    total_count = int(inventory.get("total_count") or len(prompt_status))
    missing = [str(prompt_name) for prompt_name in list(inventory.get("missing") or [])]
    empty = [str(prompt_name) for prompt_name in list(inventory.get("empty") or [])]
    errors = {
        str(prompt_name): str(message)
        for prompt_name, message in dict(inventory.get("errors") or {}).items()
    }
    status = "warning" if critical_missing else "ready"
    if critical_missing:
        if len(critical_missing) == 1:
            summary = (
                "Critical prompt readiness needs attention: "
                f"{critical_missing[0]} is missing or unreadable. Some runtime features may not work correctly."
            )
        else:
            summary = (
                "Critical prompt readiness needs attention: "
                + ", ".join(critical_missing)
                + " are missing or unreadable. Some runtime features may not work correctly."
            )
    else:
        summary = f"Critical startup prompts are ready ({loaded_count}/{total_count} prompt files available)."

    return {
        "status": status,
        "summary": summary,
        "critical_prompts": critical_prompt_names,
        "critical_missing": critical_missing,
        "loaded_count": loaded_count,
        "total_count": total_count,
        "missing": missing,
        "missing_count": len(missing),
        "empty": empty,
        "empty_count": len(empty),
        "errors": errors,
        "error_count": len(errors),
        "prompt_status": dict(prompt_status),
    }


def collect_prompt_loading_status(
    *,
    prompt_loader: Any | None = None,
    critical_prompts: Iterable[str] = CRITICAL_PROMPTS,
) -> dict[str, Any]:
    loader = _resolve_prompt_loader(prompt_loader)
    inventory_loader = getattr(loader, "inspect_prompt_inventory", None)
    if callable(inventory_loader):
        inventory = dict(inventory_loader() or {})
        prompt_status = {
            str(prompt_name): bool(loaded)
            for prompt_name, loaded in dict(inventory.get("status") or {}).items()
        }
        return _build_prompt_loading_report(
            prompt_status=prompt_status,
            inventory=inventory,
            critical_prompts=critical_prompts,
        )

    prompt_status = {
        str(prompt_name): bool(loaded)
        for prompt_name, loaded in dict(loader.log_all_prompts() or {}).items()
    }
    return _build_prompt_loading_report(
        prompt_status=prompt_status,
        inventory={"status": prompt_status},
        critical_prompts=critical_prompts,
    )


def log_prompt_loading_status(
    *,
    prompt_loader: Any | None = None,
    critical_prompts: Iterable[str] = CRITICAL_PROMPTS,
) -> dict[str, bool]:
    loader = _resolve_prompt_loader(prompt_loader)
    prompt_status = {
        str(prompt_name): bool(loaded)
        for prompt_name, loaded in dict(loader.log_all_prompts() or {}).items()
    }
    report = _build_prompt_loading_report(
        prompt_status=prompt_status,
        critical_prompts=critical_prompts,
    )
    if report["critical_missing"]:
        logger.warning(
            "Critical prompts missing: %s",
            ", ".join(report["critical_missing"]),
        )
        logger.warning("%s", report["summary"])
    return prompt_status


def run_backend_startup_preflight(
    *,
    config_obj: Any = config,
    plugin_runtime_obj: Any = plugin_runtime,
    prompt_loader: Any | None = None,
) -> bool:
    """Run the shared backend preflight used by launcher and direct backend startup."""
    valid = bool(config_obj.validate())
    # The punch list is the whole point of week 1 task 1: BOTH surfaces print it,
    # because this function is the preflight for the launcher AND for the direct
    # backend start, and a stranger reaches it through either one. logger.error
    # covers the log file; the print covers a first run that has no log file yet.
    punch = getattr(config_obj, "punch_list", None)
    rendered = punch.render() if punch is not None else ""
    if not valid:
        if rendered:
            logger.error("Configuration validation failed\n%s", rendered)
            print(rendered, file=sys.stderr)
        else:
            logger.error("Configuration validation failed")
        return False
    if rendered:
        # Nothing hard, but something is off for this boot. Once, at WARNING.
        logger.warning("%s", rendered)

    log_prompt_loading_status(prompt_loader=prompt_loader)
    plugin_runtime_obj.prepare_startup_catalog(force=True)
    return True


def build_channel_enabled_map(*, config_obj: Any = config) -> dict[str, bool]:
    # Boot's verdict, not the raw flag (week 2 task 7): a blank token degrades
    # Discord to off for this boot instead of stopping the boot, and
    # validate() — which always runs first, in the preflight above — is what
    # decides that. The fallback is the expression this used to compute, for a
    # stand-in config object that never went through validate().
    #
    # AND a token, belt and braces: Config declares effective_discord_enabled
    # True at class level, so an object that has not been validated yet must
    # not start a Discord client on a blank token.
    return {
        "discord_bot": bool(
            getattr(
                config_obj,
                "effective_discord_enabled",
                getattr(config_obj, "ENABLE_DISCORD_BOT", False)
                and str(getattr(config_obj, "DISCORD_TOKEN", "") or "").strip(),
            )
        )
        and bool(str(getattr(config_obj, "DISCORD_TOKEN", "") or "").strip()),
        # Telegram door (2026-09-14, task 2): the same shape, for the same
        # reasons. The door's flag is off by default, so an install that
        # never asked for it reads False here and starts nothing new.
        "telegram_bot": bool(
            getattr(
                config_obj,
                "effective_telegram_enabled",
                getattr(config_obj, "ENABLE_TELEGRAM_BOT", False)
                and str(getattr(config_obj, "TELEGRAM_BOT_TOKEN", "") or "").strip(),
            )
        )
        and bool(str(getattr(config_obj, "TELEGRAM_BOT_TOKEN", "") or "").strip()),
        # The mobile door (2026-10-13): boot's verdict, falling back to the
        # switch for a stand-in config that never went through validate(). No
        # token to AND on top: the door has none.
        "mobile_door": bool(
            getattr(config_obj, "effective_mobile_enabled",
                    getattr(config_obj, "ENABLE_MOBILE_DOOR", False))
        ),
    }


def enabled_channel_integrations(
    *,
    plugin_runtime_obj: Any = plugin_runtime,
    config_obj: Any = config,
) -> list[PluginChannelContribution]:
    channel_enabled = build_channel_enabled_map(config_obj=config_obj)
    enabled_integrations: list[PluginChannelContribution] = []
    for contribution in plugin_runtime_obj.get_startup_channel_integrations():
        if not channel_enabled.get(contribution.channel_id, True):
            continue
        enabled_integrations.append(contribution)
    return enabled_integrations


async def serve_backend(*, config_obj: Any = config) -> None:
    """Start the unified FastAPI backend."""
    import uvicorn
    from core.main import app

    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config.setdefault("filters", {})
    log_config["filters"]["suppress_ws_probe"] = {
        "()": SuppressWebSocketProbeFilter,
    }

    for handler_name in ("access", "default"):
        handler = log_config.get("handlers", {}).get(handler_name, {})
        handler_filters = list(handler.get("filters", []))
        if "suppress_ws_probe" not in handler_filters:
            handler_filters.append("suppress_ws_probe")
        handler["filters"] = handler_filters
        log_config["handlers"][handler_name] = handler

    uvicorn_config = uvicorn.Config(
        app=app,
        host=config_obj.BACKEND_HOST,
        port=config_obj.BACKEND_PORT,
        log_level="info",
        log_config=log_config,
    )

    server = uvicorn.Server(uvicorn_config)
    await server.serve()


async def shutdown_runtime_resources(
    *,
    brain: Any | None = None,
    llm: Any | None = None,
    voice_speech_service: Any | None = None,
) -> None:
    """Release shared runtime resources in launcher shutdown order."""
    logger.info("Cleaning up resources...")

    if brain is None:
        from core.database import brain as default_brain

        brain = default_brain

    if llm is None:
        from ai.llm_client import llm as default_llm

        llm = default_llm

    if voice_speech_service is None:
        from voice.speech import (
            voice_speech_service as default_voice_speech_service,
        )

        voice_speech_service = default_voice_speech_service

    cleanup_steps = (
        (
            "database connection pool",
            brain.close,
        ),
        (
            "LLM client sessions",
            llm.close,
        ),
        (
            "voice speech provider sessions",
            voice_speech_service.close,
        ),
    )

    for label, cleanup_call in cleanup_steps:
        try:
            await cleanup_call()
            logger.info("Closed %s", label)
        except Exception as exc:
            logger.warning("Error closing %s: %s", label, exc)

    logger.info("Cleanup complete")


class PalliLauncher:
    """Unified launcher for all PaLLi backend and channel runtime components."""

    def __init__(
        self,
        *,
        config_obj: Any = config,
        plugin_runtime_obj: Any = plugin_runtime,
        prompt_loader: Any | None = None,
        backend_runner: Callable[[], Awaitable[Any]] | None = None,
        cleanup_runner: Callable[[], Awaitable[None]] | None = None,
        signal_module: Any = signal,
    ) -> None:
        self.config = config_obj
        self.plugin_runtime = plugin_runtime_obj
        self.prompt_loader = prompt_loader
        self._backend_runner = backend_runner or serve_backend
        self._cleanup_runner = cleanup_runner or shutdown_runtime_resources
        self._signal_module = signal_module
        self.running_tasks: list[asyncio.Task[Any]] = []
        self.shutdown_event = asyncio.Event()

    async def start(self) -> bool:
        """Start all enabled PaLLi components.

        Returns False when the run never got off the ground — main.py turns that
        into exit code 2, because a config PaLLi cannot start with used to end
        the process at exit 0 and look, to every script and every stranger, like
        a clean shutdown (launch gap §5.1.1).
        """
        logger.info("Starting PaLLi Unified System")

        # Boot settings service before anything else reads routing config
        try:
            from core.settings_boot import boot_settings
            boot_settings()
        except Exception as exc:
            logger.warning("Settings service boot failed: %s (falling back to env)", exc)

        if not run_backend_startup_preflight(
            config_obj=self.config,
            plugin_runtime_obj=self.plugin_runtime,
            prompt_loader=self.prompt_loader,
        ):
            return False

        self._setup_signal_handlers()

        # Batch 8 (task 5, ruling T5-3): the retention sweep - after settings
        # boot (its limits live there) and the preflight, in the background and
        # off the event loop. Nothing at all while retention.enabled is off;
        # never stops her boot.
        try:
            from core.retention import start_boot_sweep
            start_boot_sweep()
        except Exception as exc:
            logger.warning("Retention boot sweep did not start: %s", exc)

        tasks = self._build_component_tasks()
        if not tasks:
            logger.error("No components enabled")
            return False

        self.running_tasks = tasks

        try:
            await self._monitor_running_tasks(tasks)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            await self._graceful_shutdown(tasks)
        except Exception as exc:
            logger.error("Unexpected error: %s", exc)
            await self._graceful_shutdown(tasks)
        finally:
            await self._cleanup_runner()
        return True

    def _build_component_tasks(self) -> list[asyncio.Task[Any]]:
        logger.info("Starting unified backend...")
        tasks = [asyncio.create_task(self._backend_runner(), name="backend")]

        for contribution in enabled_channel_integrations(
            plugin_runtime_obj=self.plugin_runtime,
            config_obj=self.config,
        ):
            logger.info("Starting channel integration: %s", contribution.label)
            task_name = f"channel:{contribution.channel_id}"
            tasks.append(asyncio.create_task(contribution.start(), name=task_name))

        return tasks

    async def _monitor_running_tasks(
        self,
        tasks: list[asyncio.Task[Any]],
    ) -> None:
        shutdown_wait_task = asyncio.create_task(
            self.shutdown_event.wait(),
            name="launcher:shutdown",
        )
        wait_tasks = [*tasks, shutdown_wait_task]
        done: set[asyncio.Task[Any]]
        pending: set[asyncio.Task[Any]]

        try:
            done, pending = await asyncio.wait(
                wait_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not shutdown_wait_task.done():
                shutdown_wait_task.cancel()
                await asyncio.gather(shutdown_wait_task, return_exceptions=True)

        component_done = {
            task for task in done if task is not shutdown_wait_task
        }
        pending_components = {
            task for task in pending if task is not shutdown_wait_task
        }

        self._consume_completed_tasks(component_done)

        if self.shutdown_event.is_set():
            await self._graceful_shutdown(pending_components)
            return

        logger.warning(
            "Component completed unexpectedly, initiating shutdown..."
        )
        await self._graceful_shutdown(pending_components)

    def _consume_completed_tasks(
        self,
        tasks: Iterable[asyncio.Task[Any]],
    ) -> None:
        for task in tasks:
            if task.cancelled():
                logger.info(
                    "Launcher component %s was cancelled",
                    task.get_name(),
                )
                continue
            try:
                task.result()
            except Exception:
                logger.exception(
                    "Launcher component %s exited with an error",
                    task.get_name(),
                )

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""

        def signal_handler(signum: int, frame: Any) -> None:
            logger.info("Shutdown signal detected...")
            asyncio.create_task(self._shutdown())

        self._signal_module.signal(self._signal_module.SIGINT, signal_handler)
        self._signal_module.signal(self._signal_module.SIGTERM, signal_handler)

    async def _shutdown(self) -> None:
        """Initiate graceful shutdown."""
        logger.info("Initiating graceful shutdown...")
        self.shutdown_event.set()

    async def _graceful_shutdown(
        self,
        pending_tasks: Iterable[asyncio.Task[Any]],
    ) -> None:
        """Handle graceful shutdown of all components."""
        cancellable_tasks = [
            task for task in pending_tasks if not task.done()
        ]
        logger.info("Shutting down components...")

        if not cancellable_tasks:
            return

        logger.info("Cancelling %s running tasks...", len(cancellable_tasks))

        for task in cancellable_tasks:
            task.cancel()

        try:
            await asyncio.wait_for(
                asyncio.gather(*cancellable_tasks, return_exceptions=True),
                timeout=10.0,
            )
            logger.info("All tasks cancelled successfully")
        except asyncio.TimeoutError:
            logger.warning(
                "Some tasks did not respond to cancellation within timeout"
            )

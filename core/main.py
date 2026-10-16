"""
Unified FastAPI backend for PaLLi
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from api import chat
from api import prompts
from api import web_account
from api import web_auth
from api import web_identity
# Wave 5: api.agent and api.web_runtime deleted (no live consumers).
# Their routers were registered through plugin_runtime; that path is
# similarly cleaned in core/plugin_runtime.py.
from core.backend_bootstrap import backend_bootstrap
from core.config import config
from core.database import brain
from core.launcher_runtime import (
    run_backend_startup_preflight,
    shutdown_runtime_resources,
)
from core.launcher_support import build_public_health_detail
from core.plugin_runtime import plugin_runtime
from core.rate_limit import get_client_ip, limiter
from core.request_traffic import api_request_traffic_service
from core.version import read_version

logger = logging.getLogger(__name__)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handler for rate limit exceeded errors."""
    logger.warning(f"Rate limit exceeded for {get_client_ip(request)}: {request.url.path}")
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )


def _resolve_cors_settings() -> tuple[list[str], str | None, bool]:
    """Build credential-compatible CORS settings for the local dashboard."""
    cors_allow_origins = list(config.FRONTEND_ORIGINS or [])
    cors_allow_origin_regex = config.FRONTEND_ORIGIN_REGEX
    allow_credentials = False

    # The address she prints is never the one the browser is refused. The punch
    # list, the setup sentence and SETUP.md all name FRONTEND_URL; if a stale or
    # narrower FRONTEND_ORIGINS omitted it, the door answered a stranger with
    # "Her backend is not answering." and the first hour ended there.
    printed_origin = None
    try:
        printed_origin = config.get_frontend_origin()
    except Exception:
        printed_origin = None

    def _with_printed_origin(origins: list[str]) -> list[str]:
        if printed_origin and printed_origin not in origins:
            return [*origins, printed_origin]
        return origins

    if getattr(config, "ALLOW_INSECURE_CORS", False):
        # Browsers reject credentialed cross-origin fetches when ACAO is "*".
        # Use a permissive regex instead so CORSMiddleware mirrors the request origin.
        if "*" in cors_allow_origins or (not cors_allow_origins and not cors_allow_origin_regex):
            cors_allow_origins = [origin for origin in cors_allow_origins if origin != "*"]
            cors_allow_origin_regex = ".*"
        allow_credentials = True
        if cors_allow_origin_regex != ".*":
            cors_allow_origins = _with_printed_origin(cors_allow_origins)
        return cors_allow_origins, cors_allow_origin_regex, allow_credentials

    if not cors_allow_origins and not cors_allow_origin_regex:
        if printed_origin:
            cors_allow_origins = [printed_origin]
    else:
        cors_allow_origins = _with_printed_origin(cors_allow_origins)

    allow_credentials = bool(cors_allow_origins or cors_allow_origin_regex)
    return cors_allow_origins, cors_allow_origin_regex, allow_credentials


async def _run_app_startup() -> None:
    """Initialize shared backend services before the API starts serving traffic."""
    logger.info("Starting PaLLi Unified Backend")

    try:
        from core.settings_boot import boot_settings
        boot_settings()
    except Exception as exc:
        logger.warning("Settings service boot failed: %s", exc)

    if not run_backend_startup_preflight():
        raise RuntimeError("Configuration validation failed")
    snapshot = await backend_bootstrap.run_startup()
    logger.info("Backend services initialized with startup status: %s", snapshot.get("status"))

    # Start inner monologue background engine
    try:
        from ai.inner_monologue import inner_monologue_engine
        await inner_monologue_engine.start()
    except Exception as exc:
        logger.warning("Inner monologue engine failed to start: %s", exc)

    # Start night cycle engine (Selfhood Phase 1, 2026-07-11): default-OFF
    # via companion.night_cycle; when off the loop wakes at its run time
    # (06:30 by default since 2026-10-05) and no-ops.
    try:
        from ai.night_cycle import night_cycle_engine
        await night_cycle_engine.start()
    except Exception as exc:
        logger.warning("Night cycle engine failed to start: %s", exc)

    # Start Present Moment engine (Slice 1, 2026-07-24): default-OFF via
    # companion.present_moment; when off the loop drains events and no-ops,
    # so the flag flip needs no restart.
    try:
        from ai.present_moment import present_moment_engine
        await present_moment_engine.start()
    except Exception as exc:
        logger.warning("Present Moment engine failed to start: %s", exc)

    # Start the workbench engine (her own work; no-ops while companion.own_work
    # is off — the loop wakes, sees the flag, sleeps).
    try:
        from ai.workbench import workbench_engine
        await workbench_engine.start()
    except Exception as exc:
        logger.warning("Workbench engine failed to start: %s", exc)

    # Start the daydream engine (wanting, step 20; no-ops while
    # companion.wanting is off — the loop wakes, sees the flag, sleeps).
    try:
        from ai.daydream import daydream_engine
        await daydream_engine.start()
    except Exception as exc:
        logger.warning("Daydream engine failed to start: %s", exc)

    # Start the LAN presence engine (step 23, outward senses; no-ops while
    # companion.lan_presence is off — the loop wakes, sees the flag, sleeps).
    # Listen-don't-scan (spec §0.6): the ARP listener + a few sweeps a day.
    try:
        from core.lan_presence import lan_presence_engine
        await lan_presence_engine.start()
    except Exception as exc:
        logger.warning("LAN presence engine failed to start: %s", exc)

    # Start reminder delivery (2026-07-01). Reminders SAVED fine before
    # this but never FIRED — the scheduler's check loop had no caller
    # since the old bot.py registrar was deleted in Wave 5.
    await _start_reminder_delivery()

    # Start the spending check (owner decision T2b-6, 2026-10-04): the
    # balance read every hour; the owner told when a day jumps well above
    # normal. It reads and tells — it never stops her.
    await _start_spending_watch()

    # Pre-warm the local Ollama model that serves the `decision` task so
    # the first inbound message doesn't pay model-load cost. keep_alive=-1
    # in OllamaProvider keeps the model pinned in VRAM indefinitely after
    # this warm-up. No-op when Ollama isn't running or the provider isn't
    # used.
    try:
        from ai.llm_client import llm
        from core.settings_boot import settings_service
        ollama = getattr(llm, "ollama_provider", None)
        if ollama is not None:
            # Figure out which model the decision route will ask for, so
            # we warm exactly that one and not some default.
            # settings_service.snapshot() returns a Pydantic PalliSettings
            # tree, not a dict — use attribute access.
            decision_model = None
            try:
                snap = settings_service.snapshot()
                decision_entry = snap.llm.routing.decision
                if getattr(decision_entry, "provider", None) == "ollama":
                    decision_model = getattr(decision_entry, "model", None)
            except Exception:
                pass
            decision_model = decision_model or getattr(ollama, "default_model", None)
            if await ollama.ensure_ready():
                elapsed = await ollama.warm_model(decision_model)
                logger.info(
                    "Pre-warmed Ollama model %s for decision routing in %.1fs",
                    decision_model, elapsed,
                )
            else:
                logger.info(
                    "Ollama not reachable at startup; skipping decision-model pre-warm"
                )
    except Exception as exc:
        logger.warning("Ollama pre-warm failed: %s", exc)

    # Pre-warm the local sentence-transformers embedding model + drain the
    # embedding backlog OFF the event loop, so the first semantic recall
    # doesn't pay the ~10s cold import (which blew recall's 5s timeout and
    # froze the loop, leaving PaLLi without memory-by-meaning every turn —
    # the 2026-06-13 "feels generic" incident). Fire-and-forget; never delays
    # startup, and self-heals if the model/owner isn't ready yet.
    async def _warm_memory_embeddings() -> None:
        try:
            from core.memory_service import memory_workspace_service
            from core.local_mode import get_local_owner_refs

            if not await memory_workspace_service.prewarm_local_embeddings():
                return
            refs = await get_local_owner_refs()
            owner_id = refs.get("app_user_id") or refs.get("palli_user_id")
            if owner_id:
                await memory_workspace_service.backfill_owner_embeddings(
                    owner_user_id=int(owner_id)
                )
                logger.info(
                    "Embedding model pre-warmed + backlog drained (owner=%s)", owner_id
                )
        except Exception as exc:
            logger.warning("Embedding pre-warm/backfill failed: %s", exc)

    try:
        asyncio.create_task(_warm_memory_embeddings())
    except RuntimeError:
        logger.warning("Embedding pre-warm: no running event loop to schedule on")


async def _start_reminder_delivery() -> None:
    """Start the reminder delivery loop (fail-soft, same as the engines)."""
    try:
        from core.reminders.scheduler import reminder_scheduler
        await reminder_scheduler.start()
    except Exception as exc:
        logger.warning("Reminder scheduler failed to start: %s", exc)


async def _stop_reminder_delivery() -> None:
    """Stop the reminder delivery loop on shutdown."""
    try:
        from core.reminders.scheduler import reminder_scheduler
        await reminder_scheduler.stop()
    except Exception as exc:
        logger.warning("Error stopping reminder scheduler: %s", exc)


async def _start_spending_watch() -> None:
    """Start the hourly spending check (fail-soft, same as the engines)."""
    try:
        from core.spending import spending_watch
        await spending_watch.start()
    except Exception as exc:
        logger.warning("Spending check failed to start: %s", exc)


async def _stop_spending_watch() -> None:
    """Stop the spending check on shutdown."""
    try:
        from core.spending import spending_watch
        await spending_watch.stop()
    except Exception as exc:
        logger.warning("Error stopping the spending check: %s", exc)


async def _run_app_shutdown() -> None:
    """Tear down shared backend services during API shutdown."""
    logger.info("Shutting down PaLLi Unified Backend")

    # Stop inner monologue engine
    try:
        from ai.inner_monologue import inner_monologue_engine
        await inner_monologue_engine.stop()
    except Exception as exc:
        logger.warning("Error stopping inner monologue engine: %s", exc)

    # Stop night cycle engine
    try:
        from ai.night_cycle import night_cycle_engine
        await night_cycle_engine.stop()
    except Exception as exc:
        logger.warning("Error stopping night cycle engine: %s", exc)

    # Stop Present Moment engine
    try:
        from ai.present_moment import present_moment_engine
        await present_moment_engine.stop()
    except Exception as exc:
        logger.warning("Error stopping Present Moment engine: %s", exc)

    # Stop the workbench engine
    try:
        from ai.workbench import workbench_engine
        await workbench_engine.stop()
    except Exception as exc:
        logger.warning("Error stopping workbench engine: %s", exc)

    # Stop the daydream engine
    try:
        from ai.daydream import daydream_engine
        await daydream_engine.stop()
    except Exception as exc:
        logger.warning("Error stopping daydream engine: %s", exc)

    # Stop the LAN presence engine (terminates its capture child)
    try:
        from core.lan_presence import lan_presence_engine
        await lan_presence_engine.stop()
    except Exception as exc:
        logger.warning("Error stopping LAN presence engine: %s", exc)

    await _stop_reminder_delivery()
    await _stop_spending_watch()

    try:
        await plugin_runtime.stop_runtime_services()
        logger.info("Plugin runtime services and app adapters stopped")
    except Exception as exc:
        logger.error("Error stopping plugin runtime services: %s", exc)

    try:
        await shutdown_runtime_resources(brain=brain)
        logger.info("Backend shared runtime resources closed")
    except Exception as exc:
        logger.error("Error closing shared runtime resources: %s", exc)

    logger.info("Backend shutdown complete")


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    """Run backend startup and shutdown through a single explicit lifecycle path."""
    await _run_app_startup()

    try:
        yield
    finally:
        await _run_app_shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="PaLLi Unified Backend",
        description="Unified backend for PaLLi Personal AI Assistant",
        version=read_version(),
        lifespan=_app_lifespan,
    )

    @app.middleware("http")
    async def capture_api_request_traffic(request: Request, call_next):
        api_request_traffic_service.record_request_started()
        started_at = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(response.status_code or 500)
            return response
        finally:
            route = request.scope.get("route")
            route_path = str(getattr(route, "path", "") or request.url.path or "/").strip() or "/"
            api_request_traffic_service.record_request_finished(
                method=request.method,
                route=route_path,
                path=request.url.path,
                status_code=status_code,
                duration_seconds=time.perf_counter() - started_at,
            )

    # Register rate limiter with app
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # CORS configuration:
    # - In production, require explicit FRONTEND_ORIGINS / FRONTEND_ORIGIN_REGEX unless ALLOW_INSECURE_CORS is set.
    # - In development, infer a single origin from FRONTEND_URL if nothing else is configured.
    # - For credentialed dashboard fetches, browsers require an explicit origin (or mirrored regex), not "*".
    cors_allow_origins, cors_allow_origin_regex, cors_allow_credentials = _resolve_cors_settings()

    # The backend off the LAN (item 7b, 2026-08-19): the peer gate —
    # loopback + tailnet peers only (BACKEND_ALLOWED_NETWORKS); 403 otherwise.
    from core.trusted_networks import TrustedNetworksMiddleware
    app.add_middleware(
        TrustedNetworksMiddleware,
        networks_spec=str(getattr(config, "BACKEND_ALLOWED_NETWORKS", "") or ""),
    )

    # Names, not just addresses (2026-10-08): DNS rebinding passes the peer
    # gate above, because the browser connects from loopback. This one refuses
    # a request addressed to a name she does not know, X-Forwarded-Host
    # included — where her own web page's proxy keeps the browser's name.
    from core.trusted_hosts import TrustedHostsMiddleware
    app.add_middleware(
        TrustedHostsMiddleware,
        hosts_spec=str(getattr(config, "BACKEND_ALLOWED_HOSTS", "") or ""),
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins,
        allow_origin_regex=cors_allow_origin_regex,
        allow_credentials=cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Settings API (Phase 2+3)
    try:
        from api.web_settings import router as settings_router
        app.include_router(settings_router)
    except ImportError:
        pass

    # The setup API (first door, task 2, 2026-09-17): the two endpoints the
    # door page calls to test a pasted brain key, save it and bring her brain
    # up without a restart. Mounted here, beside the settings router, rather
    # than through the plugin runtime: it must answer on a fresh clone that has
    # no brain and no plugin manifest yet, which is the only install that needs
    # it at all.
    try:
        from api.setup import router as setup_router
        app.include_router(setup_router)
    except ImportError:
        pass

    plugin_runtime.prepare_startup_catalog(force=True)
    for contribution in plugin_runtime.get_startup_router_contributions():
        prefixes = list(contribution.prefixes or [])
        if not prefixes:
            prefixes = [""]
        for prefix in prefixes:
            app.include_router(contribution.router, prefix=prefix, tags=list(contribution.tags or []))

    # Keep explicit handler lists populated for local tests and direct lifecycle callers
    # while the application itself uses the lifespan hook above.
    app.router.on_startup.append(_run_app_startup)
    app.router.on_shutdown.append(_run_app_shutdown)

    @app.get("/api/health")
    async def health_check():
        """Health check endpoint."""
        snapshot = backend_bootstrap.snapshot()
        counts = dict(snapshot.get("counts") or {})
        return {
            "status": backend_bootstrap.health_status(),
            "version": read_version(),
            "startup_status": str(snapshot.get("status") or "not_started"),
            "degraded_stages": int(counts.get("degraded") or 0),
            "failed_stages": int(counts.get("failed") or 0),
        }

    @app.get("/api/health/detail")
    async def health_check_detail():
        """Detailed health check endpoint for launcher and mission-control diagnostics."""
        # Second read F7 (2026-10-04): the detail is synchronous — blocking
        # HTTP and TCP probes and A3's /proc walk (52-76 ms on the live box) —
        # so it is built in a worker thread; every poll used to stall her
        # loop for that long (DEFECT §12's lesson).
        return await asyncio.to_thread(build_public_health_detail)

    return app


app = create_app()


# NOTE: This app is started via main.py using PalliLauncher
# Do not run this file directly - use: python3 main.py

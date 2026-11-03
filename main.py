"""
Main entry point for unified PaLLi system.

CLI flags (parsed before any config import):
  --more-perception   Crank desktop_activity_watcher (faster polling, longer
                      window, broader file lookback). Doesn't affect speech.
  --more-chatty       Lower bars for proactive outreach (threshold, gaps,
                      daily cap, randomness). Doesn't affect perception.
  --more-everything   Both of the above. (Alias: --max.)
  --preflight-only    Run every check a real boot runs, print the punch list,
                      and exit 0 (clean) or 2 (she cannot start). Starts
                      nothing. Run this before your first boot.

Flags override .env values for this process only; .env stays unchanged.
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from core.config import config

# CRITICAL: parse_and_apply() must run AFTER core.config imports.
# core.config calls load_dotenv(override=True), which clobbers any
# os.environ values set earlier. So CLI overrides have to land AFTER
# dotenv has loaded, and they mutate the `config` singleton directly
# (downstream consumers read `config.X`, not `os.getenv(X)`).
from core.cli_presets import parse_and_apply as _apply_cli_presets

_apply_cli_presets()

from core.launcher_runtime import (
    PalliLauncher,
    SuppressWebSocketProbeFilter,
)

logger = logging.getLogger(__name__)


def _install_secrets_filter() -> None:
    """Item 7c (2026-08-19): his secrets denylist is redacted from every log
    record before any handler formats it — one filter on the root logger
    covers the default, verbose and debug files at once."""
    try:
        from core.secrets_redaction import RedactingFilter

        root = logging.getLogger()
        if not any(isinstance(f, RedactingFilter) for f in root.filters):
            root.addFilter(RedactingFilter())
        # handlers attached to the root see records after root.filters; the
        # filter also goes on each file handler so late-added handlers and
        # propagated child records are covered.
        for h in root.handlers:
            if not any(isinstance(f, RedactingFilter) for f in h.filters):
                h.addFilter(RedactingFilter())
    except Exception:
        pass


def _retention_owns_the_logs() -> bool:
    """Batch 8 (task 5): whether the settings file turns retention on. Read
    straight from the file — this runs before settings boot. Off, or any
    doubt, keeps today's boot sweep below exactly as it was."""
    try:
        from core.retention import enabled_in_settings_file

        return enabled_in_settings_file()
    except Exception:
        return False


def setup_logging() -> None:
    """Setup comprehensive logging with daily rotating log files.

    Nothing here deletes by age except the old boot sweep below: the
    handlers' own ``backupCount`` cleanup never matched the ``_%Y-%m-%d``
    suffix (the standard library expects the date right after the dot), so it
    is 0. The limits by the date in each name live in core/retention.py
    (``retention:`` settings), swept after settings boot and every night.
    """
    os.makedirs("logs", exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_filename = f"logs/palli_{today}.log"
    debug_log_filename = f"logs/palli_debug_{today}.log"
    verbose_log_filename = f"logs/palli_verbose_{today}.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s"
    )

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = TimedRotatingFileHandler(
        filename=log_filename,
        when="midnight",
        interval=1,
        backupCount=0,
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "_%Y-%m-%d"
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Verbose-everything log: captures DEBUG-level output from every
    # module attached to the root logger. Always-on by default — the
    # narrower targeted-debug handler below only catches the named
    # palli.debug.* loggers, but this one catches anything that emits
    # at DEBUG anywhere in the codebase. It gets large quickly; its limits
    # are retention's (14 days, compressed after 2) or the boot sweep below.
    verbose_file_handler = TimedRotatingFileHandler(
        filename=verbose_log_filename,
        when="midnight",
        interval=1,
        backupCount=0,
        encoding="utf-8",
        utc=False,
    )
    verbose_file_handler.suffix = "_%Y-%m-%d"
    verbose_file_handler.setLevel(logging.DEBUG)
    verbose_file_handler.setFormatter(formatter)
    root_logger.addHandler(verbose_file_handler)

    # 2026-06-11: aiosqlite logs every SQL statement at DEBUG — measured at
    # ~97% of all verbose-log lines (~100MB/day of pure noise). INFO keeps
    # real DB problems visible while dropping the per-query spam.
    logging.getLogger("aiosqlite").setLevel(logging.INFO)
    # discord-ext-voice-recv logs every RTCP SenderReport (~1/sec for the
    # whole life of a voice session, muted or not) at INFO because it doesn't
    # consume RTCP. Pure noise -- keep WARNING+ (2026-07-14).
    logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.WARNING)

    # 2026-06-11: TimedRotatingFileHandler's backupCount pruning can't see
    # files created by PREVIOUS processes when a custom suffix is set, so
    # verbose/debug logs accumulated unbounded (46 files / 3.4GB found).
    # Sweep manually at boot: verbose > 5 days old, debug > 14 days old.
    # Batch 8 (task 5): exactly as before while retention is off; on, the
    # sweep by the date in each name (core/retention.py) owns these limits
    # and runs after settings boot, so this one stands down.
    if not _retention_owns_the_logs():
        try:
            _now = time.time()
            for _pattern, _max_days in (("palli_verbose_*", 5), ("palli_debug_*", 14)):
                for _old in Path("logs").glob(_pattern):
                    if _now - _old.stat().st_mtime > _max_days * 86400:
                        _old.unlink(missing_ok=True)
        except Exception:
            pass  # best-effort housekeeping; never block startup

    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.addFilter(SuppressWebSocketProbeFilter())
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_error_logger.addFilter(SuppressWebSocketProbeFilter())

    debug_logging_enabled = bool(config.ENABLE_DEBUG_LOGGING)
    full_discord_logging_enabled = bool(
        getattr(config, "LOG_FULL_DISCORD_MESSAGES", False)
    )
    if debug_logging_enabled or full_discord_logging_enabled:
        if debug_logging_enabled:
            root_logger.info(
                "Debug logging enabled; detailed logs will be written to the debug log file"
            )
        if full_discord_logging_enabled:
            root_logger.info(
                "Full Discord message logging enabled; raw Discord content will be written to the debug log file"
            )

        debug_file_handler = TimedRotatingFileHandler(
            filename=debug_log_filename,
            when="midnight",
            interval=1,
            backupCount=0,
            encoding="utf-8",
            utc=False,
        )
        debug_file_handler.suffix = "_%Y-%m-%d"
        debug_file_handler.setLevel(logging.DEBUG)
        debug_file_handler.setFormatter(formatter)

        # The targeted-debug loggers below set propagate=False so their
        # DEBUG output stays out of the main palli_*.log. To keep them
        # in the verbose log too (so palli_verbose_* really is "every
        # DEBUG-level message"), attach the verbose handler explicitly.
        if debug_logging_enabled:
            debug_llm_logger = logging.getLogger("palli.debug.llm")
            debug_llm_logger.setLevel(logging.DEBUG)
            debug_llm_logger.addHandler(debug_file_handler)
            debug_llm_logger.addHandler(verbose_file_handler)
            debug_llm_logger.propagate = False

            debug_proactive_logger = logging.getLogger("palli.debug.proactive")
            debug_proactive_logger.setLevel(logging.DEBUG)
            debug_proactive_logger.addHandler(debug_file_handler)
            debug_proactive_logger.addHandler(verbose_file_handler)
            debug_proactive_logger.propagate = False

            ideation_logger = logging.getLogger("palli.ideation")
            ideation_logger.setLevel(logging.DEBUG)
            ideation_logger.addHandler(debug_file_handler)
            ideation_logger.addHandler(verbose_file_handler)
            ideation_logger.propagate = False

        if full_discord_logging_enabled:
            discord_debug_logger = logging.getLogger("palli.debug.discord")
            discord_debug_logger.setLevel(logging.DEBUG)
            discord_debug_logger.addHandler(debug_file_handler)
            discord_debug_logger.addHandler(verbose_file_handler)
            discord_debug_logger.propagate = False

        root_logger.info("Debug logs: %s", debug_log_filename)
    else:
        logging.getLogger("palli.debug.llm").setLevel(logging.WARNING)
        logging.getLogger("palli.debug.proactive").setLevel(logging.WARNING)
        logging.getLogger("palli.ideation").setLevel(logging.WARNING)

    root_logger.info("Daily logging to: %s", log_filename)
    root_logger.info("Verbose logs (all DEBUG-level): %s", verbose_log_filename)
    root_logger.info("Log rotation: daily at midnight (keeping 30 days)")
    _install_secrets_filter()


PREFLIGHT_ONLY_FLAG = "--preflight-only"


def live_brain_and_balance() -> bool:
    """Ask the brain to answer, and read what is left on the account.

    "The key is present" was never the question (week 1 task 1b). A typo, a
    revoked key, a provider outage and an empty account all look identical to a
    config check, and all four end the same way: she starts, and every reply
    fails. So: one tiny PONG — a brain that does not answer is a blocker — and,
    where the provider will say, the balance. The balance ran to zero twice in
    one week without a word.

    Returns False when she cannot start. Never raises.
    """
    from core.launcher_support import probe_llm_brain, probe_llm_balance
    from palli_config.ctl_prefix import ctl_prefix

    provider = str(getattr(config, "LLM_PROVIDER", "") or "").strip()
    if not provider:
        return True

    # Setup mode (first door, task 1, 2026-09-17): there is no key to test. A
    # PONG would be a keyless request to a real endpoint, and its 401 would put
    # "the brain did not answer" on the punch list as a BLOCKER — for a boot
    # the list has just called fine. The soft line above already names the way
    # out; this says why nothing was asked, and the boot stands.
    if not getattr(config, "brain_ready", True):
        print("Brain: not asked — no brain is configured yet (setup mode).")
        return True

    brain = probe_llm_brain(provider)
    if brain["skipped"]:
        print(f"Brain: {provider} not asked — skipped (offline).")
    elif not brain["ran"]:
        # Our probe broke, not their brain. Say so and let the boot stand —
        # sending a stranger after a key that is fine is worse than not asking.
        print(f"WARNING: Brain: {provider} could not be checked: {brain['error']}.")
    elif brain["ok"]:
        print(f"Brain: {provider} answered in {brain['latency_ms']} ms")
    else:
        punch = getattr(config, "punch_list", None)
        if punch is not None:
            punch.add(
                "LLM_PROVIDER",
                f"the brain ({provider}) did not answer: {brain['error']}",
                # Never the hard-coded form: `palli_ctl` exists only after
                # `pip install -e .`, and the repo form only from the repo root.
                f"check its key in .env.local, or run `{ctl_prefix()} brain`",
            )
        return False

    balance = probe_llm_balance(provider)
    if balance["skipped"]:
        print("Balance: skipped (offline).")
    elif balance["known"]:
        print(
            f"Balance: {balance['total_balance']:.2f} {balance['currency']} "
            f"({provider})"
        )
        if balance["low"]:
            print(
                f"WARNING: Balance is low ({balance['total_balance']:.2f} "
                f"{balance['currency']}, floor {balance['threshold']:.2f}) — "
                f"top up before she runs dry."
            )
        if not balance["available"]:
            # Money on the account and still shut off is a different fix from
            # money running out; "low" would point at the wrong one.
            print(
                f"WARNING: the {provider} account is marked unavailable — she "
                f"will get no answers until it is settled."
            )
    elif balance["supported"]:
        # A provider that HAS a balance API and would not tell us. Silence here
        # is how it ran to zero twice without a word.
        print(f"Balance: could not be read ({provider})")
    return True


def preflight_only() -> int:
    """Everything start() does before it starts anything. Returns the exit code.

    Same order as PalliLauncher.start() — settings first, then the shared
    preflight — so what this reports is what the real boot will do. No file
    logging on purpose: a stranger running this has no logs/ directory yet, and
    the punch list is printed rather than logged (launch gap §5.1.1).
    """
    from core.launcher_runtime import run_backend_startup_preflight

    try:
        from core.settings_boot import boot_settings

        boot_settings()
    except Exception as exc:
        print(
            f"Settings service boot failed: {exc} (falling back to env)",
            file=sys.stderr,
        )

    # The preflight prints the hard items to stderr itself, so this surface and a
    # real boot say the same thing in the same words.
    #
    # Muted around it for the reason build_doctor_report() mutes: with no log
    # file configured, logging's last-resort handler writes every WARNING and
    # ERROR to stderr too, and the punch list arrived TWICE — once as a log line,
    # once as itself. A stranger reading that sees a broken tool.
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        preflight_ok = run_backend_startup_preflight()
    finally:
        logging.disable(previous_disable)

    punch = getattr(config, "punch_list", None)
    if not preflight_ok:
        if punch is None or not punch.render():
            # A no from a check that wrote no punch item. Never exit silently.
            print("Configuration validation failed.", file=sys.stderr)
        return 2

    # Only once the config itself is clean: a live call on a config that cannot
    # start would just be a second way to say the same no.
    if not live_brain_and_balance():
        rendered = punch.render() if punch is not None else ""
        # Never exit 2 on a blank line — the sibling branch above says the same.
        print(rendered or "Configuration validation failed.", file=sys.stderr)
        return 2

    turned_off = punch.render() if punch is not None else ""
    if turned_off:
        print(turned_off)
    print("Preflight passed. PaLLi can start.")
    return 0


async def main() -> bool:
    """Main entry point. False means she never got off the ground."""
    setup_logging()

    launcher = PalliLauncher()
    try:
        return bool(await launcher.start())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        return True
    except Exception as exc:
        logger.error("Fatal error in main: %s", exc)
        raise
    finally:
        logger.info("PaLLi shutdown complete")
        logger.info("Session logged to daily log files in logs/ directory")


def run() -> None:
    """Run PaLLi."""
    if PREFLIGHT_ONLY_FLAG in sys.argv[1:]:
        sys.exit(preflight_only())
    try:
        started = asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutdown complete")
        return
    except Exception as exc:
        print(f"Fatal error: {exc}")
        sys.exit(1)
    # A config she cannot start with used to exit 0 and look, to every script
    # and every stranger, like a clean shutdown (launch gap §5.1.1).
    if not started:
        sys.exit(2)


if __name__ == "__main__":
    run()

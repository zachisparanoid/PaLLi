"""The forge loader (step 34, spec §5): install = write.

``build_default_registry`` is called PER TURN (core/agent/dispatch.py — the
registry is rebuilt for every dispatched turn), so reading the manifest here
makes a freshly harvested tool live on the very next turn: no restart, no
live-object surgery. Boot changes nothing — this same loader runs every time.

Containment law (spec §2.5): EVERY step is try/except-contained per entry.
One broken forged tool logs loud, flips its own ``enabled`` false on disk,
and never breaks the turn, the boot, or its siblings.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from typing import Any, Callable, Optional

from ai import forge_manifest
from ai.recent_deeds import record_deed

logger = logging.getLogger(__name__)

# Three consecutive live errors disable a forged tool (spec §6) — a bug-belt
# constant in code, not prose. Success resets the count; a timeout counts.
FORGE_AUTO_DISABLE_THRESHOLD = 3

def _share_auto_disable_note(name: str, count: int) -> None:
    """The default auto-disable voice (spec §6): a plain note rides the
    share lane so HE hears that a forged tool got benched — never silent.
    Fire-and-forget on the running loop; the log + deeds entry above are
    the floor if no loop is running (a bare sync context)."""
    async def _send() -> None:
        try:
            from ai.arbiter import deliver_share
            from core.local_mode import get_local_owner_refs

            refs = await get_local_owner_refs()
            owner = int(refs.get("app_user_id")
                        or refs.get("palli_user_id") or 1)
            await deliver_share(
                text=(
                    f"heads up — my forged tool {name} kept failing "
                    f"({count} errors in a row), so I've benched it. "
                    f"`palli_ctl forge restore {name}` gives it another "
                    "chance if you want."
                ),
                owner_user_id=owner,
            )
        except Exception:
            logger.exception("forge auto-disable note failed for %r", name)

    try:
        asyncio.get_running_loop().create_task(_send())
    except RuntimeError:
        logger.warning(
            "forge auto-disable note for %r: no running loop; "
            "the log and deeds entry carry it", name,
        )


# Seam: fired ONCE when a tool crosses the threshold, with (name, count).
# Default = the share-lane note above (a plain note reaches him); the log +
# deeds entry fire regardless. Contained — a broken hook never breaks the
# turn. Tests repoint this in their own bodies.
auto_disable_hook: Optional[Callable[[str, int], None]] = _share_auto_disable_note


class _ForgedToolBelt:
    """Wraps a forged tool's execute with the bug-belts: per-call
    ``asyncio.wait_for`` ceiling + three-strikes error accounting persisted
    in the manifest. Everything else about the tool passes through — the
    registry treats it like any builtin."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.name = inner.name
        self.description = inner.description
        self.input_schema = inner.input_schema
        # Belt and braces (spec §4, both pins): forged tools are owner-only
        # ALWAYS (guests already get no registry at dispatch) and carry NO
        # approval surface ever (spec §0, decision 1) — both hold even if a
        # drafted module got the attributes wrong.
        self.requires_approval = False
        self.owner_only = True

    # 2026-08-24 (the browser_hand freeze): her forged browser did its waiting
    # with time.sleep and blocking sockets inside an async execute — ON the
    # brain loop. The whole process froze ~40 s (Discord heartbeat blocked,
    # gateway RESUMED), and this ceiling could not fire: wait_for cannot
    # preempt a blocked loop. Every forged tool now runs on its own worker
    # thread with its own event loop: blocking code blocks only itself, the
    # ceiling actually fires, and her tool stays hers, untouched. The thread
    # cannot be killed — on timeout it is orphaned until its blocking call
    # returns (her sockets carry their own ~30 s timeouts); the turn moves on.
    _THREAD_GRACE_SECONDS = 2.0

    async def execute(self, input: dict[str, Any], ctx: Any) -> str:
        from core.config import config

        timeout = float(config.FORGE_TOOL_TIMEOUT_SECONDS)

        def _run_in_own_loop() -> str:
            return asyncio.run(
                asyncio.wait_for(self._inner.execute(input, ctx), timeout=timeout)
            )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_run_in_own_loop),
                timeout=timeout + self._THREAD_GRACE_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError):
            self._strike()
            raise TimeoutError(
                f"forged tool {self.name!r} timed out after {timeout}s "
                "(the per-call ceiling)"
            ) from None
        except Exception:
            self._strike()
            raise
        self._reset()
        return result

    def _strike(self) -> None:
        try:
            entry = forge_manifest.get_entry(self.name) or {}
            count = int(entry.get("consecutive_errors", 0) or 0) + 1
            fields: dict[str, Any] = {"consecutive_errors": count}
            if count == FORGE_AUTO_DISABLE_THRESHOLD:
                fields["enabled"] = False
            forge_manifest.update_entry(self.name, **fields)
            if count == FORGE_AUTO_DISABLE_THRESHOLD:
                logger.error(
                    "forged tool %r auto-disabled after %d consecutive errors",
                    self.name, count,
                )
                record_deed(
                    "forge_auto_disable",
                    f"my forged tool {self.name} kept failing — "
                    f"disabled after {count} consecutive errors",
                )
                hook = auto_disable_hook
                if hook is not None:
                    try:
                        hook(self.name, count)
                    except Exception:
                        logger.exception(
                            "auto_disable_hook failed for %r", self.name
                        )
        except Exception:
            logger.exception("forge error accounting failed for %r", self.name)

    def _reset(self) -> None:
        try:
            entry = forge_manifest.get_entry(self.name)
            if entry and int(entry.get("consecutive_errors", 0) or 0):
                forge_manifest.update_entry(self.name, consecutive_errors=0)
        except Exception:
            logger.exception("forge error-reset failed for %r", self.name)


def _load_one(entry: dict) -> object | None:
    """Load one manifest entry into a Tool, or return None with the entry
    disabled on disk and an ERROR logged. Never raises."""
    name = str(entry.get("name", "") or "")
    try:
        file_name = str(entry.get("file", "") or "")
        path = forge_manifest.tools_dir() / file_name
        if not path.is_file():
            logger.error("forged tool %r missing file %s; disabling", name, path)
            forge_manifest.update_entry(name, enabled=False)
            return None

        expected = str(entry.get("sha256", "") or "")
        actual = forge_manifest.sha256_of(path)
        if actual != expected:
            logger.error(
                "forged tool %r sha256 mismatch (manifest %s, file %s); "
                "refusing to load and disabling — the file drifted or "
                "corrupted since install",
                name, expected[:12], actual[:12],
            )
            forge_manifest.update_entry(name, enabled=False)
            return None

        module_name = f"palli_forged_{name}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"no import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        # Registered in sys.modules so dataclasses/pickling inside a forged
        # module resolve normally; overwritten on re-load (new file wins).
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        build = getattr(module, "build_tool", None)
        if build is None:
            raise AttributeError(f"forged module {name} defines no build_tool()")
        tool = build()
        if getattr(tool, "name", None) is None:
            raise AttributeError(f"forged module {name} built a tool with no name")
        return _ForgedToolBelt(tool)
    except Exception:
        logger.exception("forged tool %r failed to load; disabling", name)
        forge_manifest.update_entry(name, enabled=False)
        return None


def load_forged_tools() -> list[object]:
    """Read the manifest and return the enabled, integrity-verified,
    successfully built tools. Contained top-to-bottom: a manifest problem
    means no forged tools this turn, never a broken turn."""
    try:
        entries = forge_manifest.read_manifest()
    except Exception:
        logger.exception("forge manifest read failed; no forged tools this turn")
        return []

    tools: list[object] = []
    for entry in entries:
        if not entry.get("enabled", False):
            continue
        tool = _load_one(entry)
        if tool is not None:
            tools.append(tool)
    return tools

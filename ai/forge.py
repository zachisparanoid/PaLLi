"""The forge engine (step 34, spec §3/§5/§7) — she grows new hands, on the spot.

His ask → forge_tool claims the anvil → THIS runs in the background:

    1. a walled drafting session (surface="workbench" — the step-33 wall is
       what makes drafting safe; shell + reads only, origin="forge" rides
       its own ReAct round cap) writes ``forge/<name>/<name>.py`` +
       ``test_<name>.py`` in her workspace and iterates until she believes
       it's green;
    2. the engine re-runs the tests IN THE WALL ITSELF and reads the exit
       code — his one gate ("test it, ensure it works properly"), verified
       MECHANICALLY, never her claim. Build note 2026-08-21: the wall's
       python has no pytest (system 3.10, no user site), so the charter
       demands ``test_<name>.py`` be a self-running deterministic script —
       ``python3 test_<name>.py`` exits 0 iff every check passes. Same
       gate, pytest-free runner;
    3. green ⇒ HARVEST: copy the tool into FORGE_ROOT/tools/, write the
       manifest row (sha256 = integrity, not permission), archive the
       filing (code + tests + test transcript + the ask) — the receipt,
       not an approval queue. The per-turn registry makes it live on the
       very next turn;
    4. either way, a follow-up turn lands on the channel his ask arrived
       on: the fulfilled answer, or the honest null ("it didn't come
       together") — CONVERSATION on his open ask, the arbiter untouched.

No confirmation anywhere (spec §0, owner 04:11 verbatim). Budgets are code:
FORGE_SESSION_MAX_MINUTES over the whole forge, FORGE_SESSION_MAX_ROUNDS
per ReAct session, a small verify-attempt belt against tight loops. The
forge holds her_time — one thing at a time, like every long-running organ
of hers. Every exit path clears the anvil marker: a stuck "forging
forever" is a bug this module refuses to have.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ai import forge_manifest, her_time
from ai.workbench import WORKBENCH_ROOT_DEFAULT
from ai.recent_deeds import record_deed

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")

# Belt against a tight red loop (a session that returns instantly failing
# would otherwise spin inside the minutes budget): at most this many
# draft→verify cycles per forge. A bug-guard constant, not a third budget.
_MAX_VERIFY_ATTEMPTS = 5

# The engine's own ceiling on one wall test run — generous next to the
# charter's "deterministic, offline" demand; a hung test is a red result.
_WALL_TEST_TIMEOUT_S = 120.0


def _workspace_root() -> Path:
    return Path(os.environ.get("WORKBENCH_ROOT") or WORKBENCH_ROOT_DEFAULT)


def _project_dir(name: str) -> Path:
    return _workspace_root() / "forge" / name


async def _dispatch(turn):
    from core.agent.dispatch import dispatch_turn
    return await dispatch_turn(turn)


async def _get_owner_refs():
    from core.local_mode import get_local_owner_refs
    return await get_local_owner_refs()


# ── the walled session ────────────────────────────────────────────────────

def _session_turn(prompt: str, owner_id: int):
    from core.turn.types import Turn, TurnMetadata

    return Turn(
        surface="workbench",           # the WALL — step-33 law
        owner_id=owner_id,             # app_user_id: the workbench-shell
                                       # owner gate compares against it
        content=prompt,
        images=[],
        metadata=TurnMetadata(origin="forge"),
    )


def _charter_prompt(*, name: str, purpose: str, original_ask: str) -> str:
    return (
        f"[FORGE SESSION] The owner asked you for something you can't do yet: "
        f"\"{original_ask}\". You told them you'd build the tool for it. "
        f"This sitting IS that build. Forge the tool {name!r} — {purpose}.\n\n"
        f"Work in forge/{name}/ inside your workspace (mkdir -p it first). "
        "Write TWO files there with your shell:\n"
        f"1. {name}.py — defines build_tool() returning an object with "
        "attributes name (a string — use the tool's name), description (one "
        "plain sentence for your future self), input_schema (a JSON-schema "
        "dict), requires_approval = False, owner_only = True, and an async "
        "method execute(self, input, ctx) -> str. input is a dict; ctx can "
        "be ignored. Return a plain string result; raise on real failure. "
        "STANDARD LIBRARY ONLY — the module must import cleanly on any "
        "Python 3.10+, no third-party packages, no network at import time.\n"
        f"2. test_{name}.py — a SELF-RUNNING deterministic test script: "
        f"`python3 test_{name}.py` exits 0 if and only if every check "
        "passes (bare asserts are enough), prints a short 'N passed' line, "
        "and needs NO network, no real credentials, no wall-clock luck — "
        "stub any external seam. pytest is not available here; do not "
        "import it.\n\n"
        f"Then RUN `cd forge/{name} && python3 test_{name}.py` with your "
        "shell and iterate until it exits clean. Afterwards append a short "
        "section to WORKBENCH.md saying what you forged and why. End with "
        "one line signing off. The tests will be re-run mechanically after "
        "you finish — only a genuinely green run installs the tool onto "
        "your belt, so never fake a pass."
    )


def _continue_prompt(*, name: str, report: str) -> str:
    return (
        f"[FORGE SESSION — tests still red] The mechanical run of "
        f"`python3 test_{name}.py` in forge/{name}/ did not pass. Its "
        f"output:\n\n{report[-2000:]}\n\n"
        "Fix the tool and/or its tests (keep them deterministic and "
        "offline — never delete a failing check just to pass), re-run "
        "them with your shell until clean, then sign off."
    )


async def _wall_test_run(name: str) -> tuple[int, str]:
    """Run her test script INSIDE the wall (the workbench shell's exact sudo
    shape, reused) and return (exit_code, output tail). This is the gate —
    the engine's own eyes, not her claim."""
    from core.agent.builtin_tools.workbench_shell import _build_argv

    root = str(_workspace_root())
    user = os.environ.get("WORKBENCH_USER", "palli-work")
    direct = os.environ.get("WORKBENCH_SHELL_DIRECT") == "1"
    command = f"cd forge/{name} && python3 test_{name}.py"
    argv = _build_argv(command, root=root, user=user, direct=direct)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_WALL_TEST_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            try:
                import signal as _signal

                os.killpg(proc.pid, _signal.SIGKILL)
            except Exception:
                proc.kill()
            return 124, f"(test run timed out after {int(_WALL_TEST_TIMEOUT_S)}s)"
    except Exception as exc:
        return 125, f"(test run failed to start: {exc})"
    text = (out or b"").decode("utf-8", errors="replace")
    return int(proc.returncode or 0), text[-4000:]


# ── harvest + filing ──────────────────────────────────────────────────────

def _harvest(*, name: str, original_ask: str, report: str) -> None:
    """Green ⇒ install: copy the tool file, write the manifest row. The
    sha256 is provenance — the loader refuses a drifted file, loudly."""
    src = _project_dir(name) / f"{name}.py"
    dst_dir = forge_manifest.tools_dir()
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{name}.py"
    shutil.copyfile(src, dst)

    from datetime import datetime, timezone

    entries = [e for e in forge_manifest.read_manifest()
               if e.get("name") != name]  # re-forge: new file, new row
    entries.append({
        "name": name,
        "file": f"{name}.py",
        "sha256": forge_manifest.sha256_of(dst),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_ask": original_ask,
        "test_report": report[-500:],
        "enabled": True,
        "consecutive_errors": 0,
    })
    forge_manifest.write_manifest(entries)


def _archive_filing(*, name: str, purpose: str, original_ask: str,
                    report: str) -> None:
    """The receipt, not an approval queue (spec §3): whatever the session
    drafted + the test transcript + the ask, kept for his curiosity —
    on green AND on the honest null. Best-effort; never fails the forge."""
    try:
        filing = forge_manifest.filings_dir() / name
        filing.mkdir(parents=True, exist_ok=True)
        proj = _project_dir(name)
        if proj.is_dir():
            for item in proj.iterdir():
                if item.is_file():
                    try:
                        shutil.copyfile(item, filing / item.name)
                    except OSError as exc:
                        logger.warning("forge filing copy failed for %s: %s",
                                       item.name, exc)
        (filing / "test_report.txt").write_text(report, encoding="utf-8")
        (filing / "ask.txt").write_text(
            f"ask: {original_ask}\npurpose: {purpose}\n", encoding="utf-8"
        )
    except Exception:
        logger.exception("forge filing archive failed for %r", name)


# ── fulfill-after-forge (spec §7) ─────────────────────────────────────────

async def _record_outbound(**kwargs) -> bool:
    from core.turn.outbound import record_outbound
    return await record_outbound(**kwargs)


def _followup_prompt(*, green: bool, name: str, original_ask: str,
                     why: str) -> str:
    if green:
        return (
            f"[FORGE DONE] Your forge finished: the tool {name!r} is built, "
            "its tests went green, and it is on your belt RIGHT NOW — this "
            "very turn's tools include it. Their original ask, verbatim: "
            f"\"{original_ask}\". Fulfill that ask in this reply — use "
            f"{name} if it helps — and speak as yourself, plainly. They were "
            "told you were building it; this is the follow-through."
        )
    return (
        f"[FORGE FELL SHORT] The forge for {name!r} didn't come together: "
        f"{why or 'the build did not converge'}. Their original ask: "
        f"\"{original_ask}\". Tell them plainly that it didn't work this "
        "time — the honest null, never a manufactured success — and, if "
        "something occurs to you, what you'd try differently."
    )


def _followup_turn(content: str, owner_id: int, channel_id: int):
    from core.turn.types import Turn, TurnMetadata

    # Its own session lane (the reminder-fire shape): the synthesized
    # charter never pollutes the channel transcript — record_outbound
    # appends HER words there, assistant-only, after the send.
    return Turn(
        surface="forge_followup",
        owner_id=owner_id,
        content=content,
        images=[],
        metadata=TurnMetadata(origin="forge", channel_id=int(channel_id or 0)),
    )


async def _followup(*, green: bool, name: str, original_ask: str,
                    origin_channel_id: int, surface: str, why: str = "") -> None:
    """The answer arrives on the ask's own channel — or the failure does,
    just as plainly (spec §7). This is CONVERSATION riding his open ask,
    composed by a full ReAct turn whose registry already carries the
    freshly forged tool (the per-turn build), then sent through the one
    custody primitive (send + DB row + assistant-only transcript + deed).
    No arbiter anywhere: his ask is minutes old and openly waiting."""
    refs = await _get_owner_refs()
    owner_id = int(refs.get("app_user_id") or refs.get("palli_user_id") or 1)

    content = _followup_prompt(
        green=green, name=name, original_ask=original_ask, why=why
    )
    result = await _dispatch(_followup_turn(content, owner_id, origin_channel_id))
    text = str(getattr(result, "response_text", "") or "").strip()
    if not text:
        logger.warning(
            "forge follow-up for %r composed nothing (green=%s) — "
            "not sent; his ask stands open", name, green,
        )
        return
    if not origin_channel_id:
        logger.warning(
            "forge follow-up for %r has no origin channel (surface=%r) — "
            "composed but not sent: %.120s", name, surface, text,
        )
        return
    from core.session_keys import room_platform

    # Out of the door the ask came in (2026-10-14): a Telegram ask was
    # answered as a Discord send to the Telegram chat's id.
    await _record_outbound(
        owner_id=owner_id,
        channel_id=int(origin_channel_id),
        text=text,
        origin="forge",
        platform=room_platform(surface),
    )
    essential_logger.info("forge: follow-up sent for %r (green=%s)", name, green)


# ── the project ───────────────────────────────────────────────────────────

def start_forge_project(*, name: str, purpose: str, original_ask: str,
                        origin_channel_id: int, surface: str) -> asyncio.Task:
    """forge_tool's spawn seam: the anvil marker is already claimed by the
    caller; this backgrounds the whole forge and returns immediately."""
    return asyncio.create_task(
        _run_forge_project(
            name=name, purpose=purpose, original_ask=original_ask,
            origin_channel_id=origin_channel_id, surface=surface,
        ),
        name=f"forge:{name}",
    )


async def _run_forge_project(*, name: str, purpose: str, original_ask: str,
                             origin_channel_id: int, surface: str) -> None:
    outcome: Dict[str, Any] = {"green": False, "why": "forge never ran"}
    try:
        outcome = await _forge(
            name=name, purpose=purpose, original_ask=original_ask
        )
    except Exception as exc:
        logger.exception("forge crashed for %r", name)
        outcome = {"green": False, "why": f"the forge crashed: {exc}"}
        try:
            record_deed("forge_failed",
                        f"forging {name} crashed mid-flight — {exc}")
        except Exception:
            pass
    finally:
        # Every exit clears the anvil — green, red, or crash.
        forge_manifest.clear_active()

    try:
        await _followup(
            green=bool(outcome.get("green")),
            name=name,
            original_ask=original_ask,
            origin_channel_id=origin_channel_id,
            surface=surface,
            why=str(outcome.get("why", "") or ""),
        )
    except Exception:
        logger.exception("forge follow-up failed for %r", name)


async def _forge(*, name: str, purpose: str,
                 original_ask: str) -> Dict[str, Any]:
    from core.config import config

    record_deed("forge_started",
                f"started forging a new tool: {name} — {purpose}")
    essential_logger.info("forge: started %r (ask: %.80s)", name, original_ask)

    refs = await _get_owner_refs()
    # app_user_id, NOT palli_user_id — the workbench engine's hard-won
    # lesson: dispatch resolves is_owner against app_user_id and the wall's
    # shell is owner_only; the wrong id seats her at a bench with no tools.
    owner_id = int(refs.get("app_user_id") or refs.get("palli_user_id") or 1)

    async with her_time.hold(f"forging a tool ({name})"):
        # The clock starts once the forge actually has her hands (queueing
        # behind a dive is not forging time).
        deadline = time.monotonic() + float(config.FORGE_SESSION_MAX_MINUTES) * 60.0
        report = "(tests never ran)"
        prompt = _charter_prompt(
            name=name, purpose=purpose, original_ask=original_ask
        )
        for attempt in range(1, _MAX_VERIFY_ATTEMPTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                why = "ran out of clock before the tests went green"
                record_deed("forge_failed", f"forging {name} {why}")
                _archive_filing(name=name, purpose=purpose,
                                original_ask=original_ask, report=report)
                return {"green": False, "why": why}
            try:
                await asyncio.wait_for(
                    _dispatch(_session_turn(prompt, owner_id)),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                why = "the drafting session ran out of clock"
                record_deed("forge_failed", f"forging {name} {why}")
                _archive_filing(name=name, purpose=purpose,
                                original_ask=original_ask, report=report)
                return {"green": False, "why": why}

            rc, report = await _wall_test_run(name)
            if rc == 0:
                _harvest(name=name, original_ask=original_ask, report=report)
                _archive_filing(name=name, purpose=purpose,
                                original_ask=original_ask, report=report)
                record_deed(
                    "forge_installed",
                    f"forged {name} and put it on my belt — tests green",
                )
                essential_logger.info("forge: %r green + installed", name)
                return {"green": True, "report": report}

            logger.info("forge: %r attempt %d red (rc=%s)", name, attempt, rc)
            prompt = _continue_prompt(name=name, report=report)

    why = f"tests still failing after {_MAX_VERIFY_ATTEMPTS} attempts"
    record_deed("forge_failed", f"forging {name} — {why}")
    _archive_filing(name=name, purpose=purpose,
                    original_ask=original_ask, report=report)
    return {"green": False, "why": why}

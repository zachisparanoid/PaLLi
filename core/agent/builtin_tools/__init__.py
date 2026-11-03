"""Built-in tools registered with the agent's ToolRegistry."""
from __future__ import annotations

from core.agent.builtin_tools.cancel_reminder import CancelReminderTool
from core.agent.builtin_tools.collaborators import CollaboratorsTool
from core.agent.builtin_tools.create_reminder import CreateReminderTool
from core.agent.builtin_tools.deliver_artifact import DeliverArtifactTool
from core.agent.builtin_tools.introspect import IntrospectTool
from core.agent.builtin_tools.list_reminders import ListRemindersTool
from core.agent.builtin_tools.local_shell import LocalShellTool
from core.agent.builtin_tools.note_to_self import NoteToSelfTool
from core.agent.builtin_tools.read_file import ReadFileTool
from core.agent.builtin_tools.reschedule_reminder import RescheduleReminderTool
from core.agent.builtin_tools.room_note import RoomNoteTool, room_note_enabled
from core.agent.builtin_tools.web_search import WebSearchTool
from core.agent.builtin_tools.whoami import WhoamiTool


def _web_search_enabled() -> bool:
    """features.web_search gate; register-by-default when settings unbooted
    (matches the schema default and the companion-flags convention).

    AND boot's verdict: `config.effective_web_search_enabled` is False when the
    setting is on but EXA_API_KEY is missing (week 1 task 1). Registering the
    tool then would hand her a search she cannot run.
    """
    from core.config import config

    if not bool(getattr(config, "effective_web_search_enabled", True)):
        return False
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is None:
            return True
        return bool(snap.features.web_search)
    except Exception:
        return True


def _read_page_enabled() -> bool:
    """features.read_page gate (Batch 8, task 9; ruling T9-5) — default-OFF
    unbooted semantics (the her_hands pattern): no snapshot, or one that
    cannot be read, means off. Checked at registration, and the reader's
    module is imported only behind it, so flag off is byte-identical to a
    build without the tool."""
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is None:
            return False
        return bool(getattr(snap.features, "read_page", False))
    except Exception:
        return False


# Stable alias so tests can restore the real gate past the autouse
# default-off guard in tests/conftest.py (_read_page_off_by_default).
_read_page_enabled_impl = _read_page_enabled


def _guest_web_search_enabled() -> bool:
    """features.guest_web_search gate (R14, T7; the owner, 10-12 21:20: "yes,
    friends should get web search") — default-OFF unbooted semantics (the
    read_page pattern): no snapshot, or one that cannot be read, means off,
    so a guest's turn builds no registry, as before the switch existed."""
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is None:
            return False
        return bool(getattr(snap.features, "guest_web_search", False))
    except Exception:
        return False


# Stable alias so tests can restore the real gate past the autouse
# default-off guard in tests/conftest.py (_guest_web_search_off_by_default).
_guest_web_search_enabled_impl = _guest_web_search_enabled


def _introspection_enabled() -> bool:
    """companion.introspection gate — default-OFF faculty semantics:
    unbooted settings (None) or a missing flag means DISABLED (the
    ledger_enabled pattern; deliberately the INVERSE of
    _web_search_enabled's unbooted->True). Checked at registration so a
    disabled tool never appears in the system prompt at all — flag off
    is byte-identical to a build without the tool."""
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is None:
            return False
        comp = getattr(snap, "companion", None)
        return comp is not None and bool(getattr(comp, "introspection", False))
    except Exception:
        return False


# Stable alias so tests can restore the real gate past the autouse
# default-off guard in tests/conftest.py (_introspection_off_by_default).
_introspection_enabled_impl = _introspection_enabled


def _self_notes_enabled() -> bool:
    """companion.self_notes gate — default-OFF faculty semantics, identical
    to _introspection_enabled: unbooted settings mean DISABLED. Checked at
    registration so a disabled tool never appears in the system prompt at
    all (flag off is byte-identical to a build without the pen)."""
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is None:
            return False
        comp = getattr(snap, "companion", None)
        return comp is not None and bool(getattr(comp, "self_notes", False))
    except Exception:
        return False


def _her_hands_enabled() -> bool:
    """features.her_hands gate — reads the booted snapshot (unbooted, the
    settings service lazily loads the file; only a failed read is
    default-OFF): a toolset this consequential is never registered when
    the flag is off, and flag off is byte-identical to a build without
    the hands."""
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is None:
            return False
        return bool(getattr(snap.features, "her_hands", False))
    except Exception:
        return False


def _forged_tools_enabled() -> bool:
    """features.forged_tools gate — tools she forged for herself appear only
    while the flag is on, and flag off is byte-identical to a build without
    the forge. Unbooted is not default-OFF: ``settings_service.snapshot()``
    lazily loads the settings file (core/settings_service.py:121), so with no
    boot the answer is whatever that file says; only a snapshot that cannot be
    read at all falls back to OFF. The flag is the owner's KILL SWITCH (her
    manifesto §2.1), not a confirmation — inside it, the forge is autonomous
    (spec §0, 04:11)."""
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is None:
            return False
        return bool(getattr(snap.features, "forged_tools", False))
    except Exception:
        return False


def _lan_presence_enabled() -> bool:
    """companion.lan_presence gate for the look (step 23) — default-OFF
    unbooted semantics (the her_hands pattern): flag off is byte-identical
    to a build without the tool."""
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is None:
            return False
        comp = getattr(snap, "companion", None)
        return comp is not None and bool(getattr(comp, "lan_presence", False))
    except Exception:
        return False


def _idiolect_enabled() -> bool:
    """companion.idiolect gate for `where_did_that_come_from` (organ 4.12)
    — reads the booted snapshot (unbooted, the settings service lazily
    loads the file; only a failed read is default-OFF): flag off
    is byte-identical to a build without the tool."""
    try:
        from core.settings_boot import settings_service
        snap = settings_service.snapshot()
        if snap is None:
            return False
        comp = getattr(snap, "companion", None)
        return comp is not None and bool(getattr(comp, "idiolect", False))
    except Exception:
        return False


# Stable alias so tests can restore the real gate past the autouse
# default-off guard in tests/conftest.py (_idiolect_off_by_default).
_idiolect_enabled_impl = _idiolect_enabled


def _duplex_enabled() -> bool:
    """companion.duplex gate (step 27) — reads THROUGH duplex_hands.enabled(),
    which reads the duplex module at call time, so the tests/conftest.py
    off-guard holds here too; fail-closed. (Through duplex_hands so this file
    keeps one import free of the coupling budget — R15, T7.)"""
    try:
        from core.agent.builtin_tools.duplex_hands import enabled
        return bool(enabled())
    except Exception:
        return False


def _memory_reach_enabled() -> bool:
    """companion.memory_reach gate for `search_what_we_said` (R15, T8) —
    reads THROUGH the tool module's ``enabled()``, which reads
    ``ai.memory_reach_index.enabled`` at call time, so the tests/conftest.py
    off-guard holds here too; fail-closed. (Through the tool module so its
    switch and its class cost this file one import — the coupling budget.)"""
    try:
        from core.agent.builtin_tools.search_what_we_said import enabled
        return bool(enabled())
    except Exception:
        return False


def _build_workbench_registry():
    """The work-session toolset (spec §4/§4b): the guarded shell + her
    inward/outward reads. Deliberately absent: every messaging, reminder,
    delivery, and app-privileged tool — a work session cannot text him,
    schedule alarms, or read files as the app user; the shell's OS wall
    (palli-work) is the only filesystem doorway."""
    from core.agent.builtin_tools.workbench_shell import WorkbenchShellTool
    from core.agent.tools import ToolRegistry

    reg = ToolRegistry()
    reg.register(WorkbenchShellTool())
    if _web_search_enabled():
        reg.register(WebSearchTool())
    if _read_page_enabled():
        from core.agent.builtin_tools.read_page import ReadPageTool
        reg.register(ReadPageTool())
    if _introspection_enabled():
        reg.register(IntrospectTool())
    if _self_notes_enabled():
        reg.register(NoteToSelfTool())
    reg.register(WhoamiTool(
        loop_description="ReAct (workbench session)",
        available_tool_names=reg.all_names() + ["whoami"],
    ))
    return reg


def build_guest_registry():
    """A guest's belt (R14, T7; plan §2b): ``web_search`` and nothing else.

    The owner, 10-12 22:03 (Q5, Q9): no page reader for friends, nothing
    else for friends, nothing of the owner's ever — no whoami, no forged
    tools, no reminders, files, notes or memory. The one tool reads only the
    query and the Exa key, and spends from the per-friend and per-room floor
    (core/agent/guest_search_budget.py).

    None — no registry, a guest's turn exactly as before (the golden's guest
    turn keeps its bytes) — unless features.guest_web_search is on AND the
    owner's web search is on and configured (_web_search_enabled, boot's
    EXA_API_KEY verdict included): a friend never gets a search PaLLi itself
    could not run.
    """
    if not (_guest_web_search_enabled() and _web_search_enabled()):
        return None
    from core.agent.tools import ToolRegistry

    reg = ToolRegistry()
    reg.register(WebSearchTool(for_guest=True))
    return reg


def build_default_registry(*, surface: str = ""):
    """Build a ToolRegistry pre-populated with all builtin tools.

    2026-07-28 (own-work spec §4b): tool gating grows a SURFACE dimension
    alongside the existing per-tool flag/owner_only gates. Only
    ``surface="workbench"`` diverges — her own work sessions get their own
    toolset; every other surface takes the identical path it took before
    this feature existed.
    """
    if surface == "workbench":
        return _build_workbench_registry()

    from core.agent.tools import ToolRegistry

    reg = ToolRegistry()
    tools = [
        ReadFileTool(),
        LocalShellTool(),
        DeliverArtifactTool(),
        ListRemindersTool(),
        CancelReminderTool(),
        RescheduleReminderTool(),
        CreateReminderTool(),
    ]
    # 2026-07-01 — web reaches the live loop: gate at registration so a
    # disabled tool never appears in the system prompt at all.
    if _web_search_enabled():
        tools.append(WebSearchTool())
    # Batch 8, task 9 (plan item 8): the page reader. Owner-only; gated by
    # features.read_page (schema default off) so flag-off builds carry a
    # byte-identical tool list. Also in the workbench registry above.
    if _read_page_enabled():
        from core.agent.builtin_tools.read_page import ReadPageTool
        tools.append(ReadPageTool())
    # 2026-07-13 — the inward turn: PaLLi's first self-pointing tool.
    # Default-OFF; same registration-gating as web_search so flag-off
    # builds carry a byte-identical tool list.
    if _introspection_enabled():
        tools.append(IntrospectTool())
    # 2026-07-27 — the pen: her first tool that WRITES to her own record.
    if _self_notes_enabled():
        tools.append(NoteToSelfTool())
    # 2026-08-11 — her hands: the capability sweep (step 33). Full-trust
    # shape; owner-only; the workbench path above never reaches here.
    if _her_hands_enabled():
        from core.agent.builtin_tools.her_hands import build_her_hands_tools
        tools.extend(build_her_hands_tools())
    # 2026-08-27 — duplex (step 27): chime_in / ask_him, her mid-task acts.
    # Owner-only; gated by companion.duplex so flag-off builds carry a
    # byte-identical tool list. Never at the workbench.
    if _duplex_enabled():
        from core.agent.builtin_tools.duplex_hands import build_duplex_tools
        tools.extend(build_duplex_tools())
    # 2026-08-24 — the look (step 23): who's home on her network. Read-only,
    # owner-only; gated by companion.lan_presence so flag-off builds carry a
    # byte-identical tool list. Never at the workbench (the wall drafts
    # tools; it does not look around the house).
    if _lan_presence_enabled():
        from core.agent.builtin_tools.the_house import build_house_tools
        tools.extend(build_house_tools())
    # 2026-09-02 — Shared Idiolect (organ 4.12): check before claiming.
    # Owner-only; gated by companion.idiolect so flag-off builds carry a
    # byte-identical tool list. Never at the workbench.
    if _idiolect_enabled():
        from core.agent.builtin_tools.where_did_that_come_from import WhereDidThatComeFromTool
        tools.append(WhereDidThatComeFromTool())
    # R15, T8 — memory that reaches: her search of the record of everything
    # said. Owner-only; gated by companion.memory_reach (schema default off)
    # so flag-off builds carry a byte-identical tool list. Never at the
    # workbench (that path returned above).
    if _memory_reach_enabled():
        from core.agent.builtin_tools.search_what_we_said import SearchWhatWeSaidTool
        tools.append(SearchWhatWeSaidTool())
    # Batch 8, task 1 (B8-1, ruling R10): the owner's list of collaborators
    # for shared rooms. Owner-only, and always registered — no flag: the
    # room filter it feeds is a security fix, not a feature. Never at the
    # workbench (that path returned above).
    tools.append(CollaboratorsTool())
    # Batch 8, task 2b (owner O1, ruling T2b-5 — INTERIM, the owner revisits
    # it after the batches): the owner's room note. Owner-only; on the belt
    # only while companion.she_decides is on (with it off the note shows
    # nowhere, so the list stays byte-identical). Never at the workbench.
    if room_note_enabled():
        tools.append(RoomNoteTool())
    # 2026-08-21 — the forge (step 34): tools she built for herself join the
    # belt here, AFTER the builtins and BEFORE whoami registers (so her
    # self-inventory sees them). The registry is rebuilt per turn, so a
    # manifest row written mid-conversation is live on the very next turn —
    # install-by-write is hot install. Never in the workbench registry (the
    # wall drafts tools; it does not wield them).
    if _forged_tools_enabled():
        from core.agent.builtin_tools.forge_loader import load_forged_tools
        from core.agent.builtin_tools.forge_tool import ForgeTool
        tools.append(ForgeTool())
        tools.extend(load_forged_tools())
    for tool in tools:
        reg.register(tool)
    reg.register(WhoamiTool(
        loop_description="ReAct",
        available_tool_names=reg.all_names() + ["whoami"],
    ))
    return reg

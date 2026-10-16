"""Who holds her port (Batch 8 Task 11 A3), standard library only.

Started from tmux she has no launcher record, so an open :8000 read
"occupied by a foreign process" — a core blocker in her own doctor and in
/api/health/detail, about herself. These readers find the pid LISTENING on
the port and say whether it is her.

Moved here from core/launcher_support.py, unchanged, by the second read's F6
(2026-10-04): ``scripts/rotate_vault_keys.py`` runs under system python3
(pykeepass lives there, the venv's packages do not) and must ask the same
question before it rewrites her records — so this module imports nothing but
the standard library. launcher_support re-exports ``her_listener_pid``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PROC_ROOT = Path("/proc")
_TCP_LISTEN = "0A"


def _listening_socket_inodes(port: int, *, proc_root: Path) -> set:
    """Socket inodes in LISTEN state on `port` (IPv4 and IPv6). Empty when
    there is no /proc to read (not Linux) — the caller keeps today's verdict."""
    inodes: set = set()
    for table in ("tcp", "tcp6"):
        try:
            lines = (proc_root / "net" / table).read_text(encoding="ascii", errors="ignore").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != _TCP_LISTEN:
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port == int(port) and fields[9] != "0":
                inodes.add(fields[9])
    return inodes


def _pid_holds_socket(pid: int, inodes: set, *, proc_root: Path) -> bool:
    try:
        entries = list(os.scandir(proc_root / str(pid) / "fd"))
    except OSError:
        return False
    for entry in entries:
        try:
            target = os.readlink(entry.path)
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]") and target[8:-1] in inodes:
            return True
    return False


def _is_her_main_py(pid: int, *, proc_root: Path, project_root: Path) -> bool:
    """A process whose cwd is this repo root and whose argv[1] is main.py.

    argv[1] is resolved against that cwd, so `main.py`, `./main.py` and the
    absolute path all count — but only this checkout's: a main.py from
    another checkout has another cwd and stays "external"."""
    base = proc_root / str(pid)
    try:
        cwd = Path(os.readlink(base / "cwd"))
        argv = (base / "cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    if len(argv) < 2 or not argv[1]:
        return False
    try:
        root = project_root.resolve()
        if cwd.resolve() != root:
            return False
        script = Path(os.fsdecode(argv[1]))
        return (cwd / script).resolve() == (root / "main.py")
    except (OSError, RuntimeError, ValueError):
        return False


def her_listener_pid(
    port: int,
    *,
    proc_root: Optional[Path] = None,
    project_root: Optional[Path] = None,
    self_pid: Optional[int] = None,
) -> Optional[int]:
    """The pid listening on `port` when that listener is her, else None.

    Her = THIS process (the doctor runs inside her for /api/health/detail), or
    a process whose cwd is this repo root and whose argv[1] is main.py. Only
    those candidates' fds are read, never every process's. None when /proc is
    missing, unreadable, or the listener is anyone else — the caller then says
    "external" exactly as before.
    """
    proc = _PROC_ROOT if proc_root is None else Path(proc_root)
    root = PROJECT_ROOT if project_root is None else Path(project_root)
    me = os.getpid() if self_pid is None else int(self_pid)
    inodes = _listening_socket_inodes(int(port), proc_root=proc)
    if not inodes:
        return None
    if _pid_holds_socket(me, inodes, proc_root=proc):
        return me
    try:
        pids = sorted(int(entry.name) for entry in os.scandir(proc) if entry.name.isdigit())
    except OSError:
        return None
    for pid in pids:
        if pid == me:
            continue
        if _is_her_main_py(pid, proc_root=proc, project_root=root) and _pid_holds_socket(pid, inodes, proc_root=proc):
            return pid
    return None

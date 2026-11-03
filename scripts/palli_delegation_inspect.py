#!/usr/bin/env python3
"""Read-only inspector for the delegation_sessions table.

Usage:
    python scripts/palli_delegation_inspect.py list [--limit N] [--all]
    python scripts/palli_delegation_inspect.py show <session_id> [--verbose]

`list` summarizes recent delegations (status, duration, turns, tool
counts, artifact count).

`show` prints a timeline of events for one session — parses structured
Phase-7.2 records when present, falls back to repr previews for older
rows. Use --verbose to include every assistant text block and tool
result preview; default elides to just tool-use names + targets.

Safe to run against a live PaLLi DB — opens read-only, never writes.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Tools that indicate real work was done (write/edit/shell), matching the
# bridge's _WORK_EVIDENCE_TOOLS. Read-only tools (Read, Grep, Glob,
# WebSearch, WebFetch) are tracked separately for visibility but not
# counted as "work evidence".
WORK_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "PowerShell", "Shell"})


def _db_path() -> Path:
    """Return the canonical PaLLi SQLite DB path (relative to repo root)."""
    return Path(__file__).resolve().parent.parent / "data" / "palli-local.sqlite3"


def _connect_readonly() -> sqlite3.Connection:
    path = _db_path()
    if not path.exists():
        sys.exit(f"error: DB not found at {path}")
    # Open in URI read-only mode so a concurrent PaLLi can't be surprised.
    uri = f"file:{path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def _fmt_ts(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _fmt_duration(started: Optional[float], completed: Optional[float]) -> str:
    if not started:
        return "-"
    end = completed or 0.0
    if not end:
        return "…"
    sec = max(0.0, float(end) - float(started))
    if sec < 60:
        return f"{sec:.1f}s"
    if sec < 3600:
        return f"{sec/60:.1f}m"
    return f"{sec/3600:.1f}h"


def _load_events(raw: Any) -> List[Dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return [x for x in parsed if isinstance(x, dict)] if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _summarize_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate event counts + tool-use stats for the `list` view."""
    by_type: Dict[str, int] = {}
    tool_counts: Dict[str, int] = {}
    work_count = 0
    read_count = 0
    for evt in events:
        t = str(evt.get("event_type") or "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        for tc in evt.get("tool_calls") or []:
            name = str(tc.get("name") or "")
            if not name:
                continue
            tool_counts[name] = tool_counts.get(name, 0) + 1
            if name in WORK_TOOLS:
                work_count += 1
            else:
                read_count += 1
    # Extract ResultMessage stats if present (take last one).
    result_stats = None
    for evt in reversed(events):
        if evt.get("event_type") == "ResultMessage":
            result_stats = {
                "num_turns": evt.get("num_turns"),
                "duration_ms": evt.get("duration_ms"),
                "is_error": evt.get("is_error"),
                "subtype": evt.get("subtype"),
            }
            break
    return {
        "by_type": by_type,
        "tool_counts": tool_counts,
        "work_count": work_count,
        "read_count": read_count,
        "result_stats": result_stats,
    }


def cmd_list(con: sqlite3.Connection, *, limit: int, show_all: bool) -> None:
    where = "" if show_all else "WHERE status != 'completed' OR 1=1"  # show all by default; kept for future filters
    rows = list(con.execute(
        "SELECT session_id, status, started_at, completed_at, task_hint, "
        "       artifacts, events, error, claude_session_id "
        "FROM delegation_sessions "
        "ORDER BY started_at DESC LIMIT ?",
        (int(limit),),
    ))
    if not rows:
        print("(no delegation sessions yet)")
        return
    print(f"Showing {len(rows)} sessions (newest first):\n")
    print(f"{'status':<10} {'session_id':<28} {'started':<15} {'dur':<8} {'turns':<6} {'work':<5} {'read':<5} {'artifacts':<9}  task_hint")
    print("-" * 130)
    for r in rows:
        events = _load_events(r["events"])
        summary = _summarize_events(events)
        result = summary["result_stats"] or {}
        artifacts = r["artifacts"] or "[]"
        try:
            artifact_count = len(json.loads(artifacts) or [])
        except Exception:
            artifact_count = 0
        hint = (r["task_hint"] or "").replace("\n", " ")[:55]
        print(
            f"{r['status']:<10} {r['session_id']:<28} {_fmt_ts(r['started_at']):<15} "
            f"{_fmt_duration(r['started_at'], r['completed_at']):<8} "
            f"{str(result.get('num_turns') or '-'):<6} "
            f"{summary['work_count']:<5} {summary['read_count']:<5} "
            f"{artifact_count:<9}  {hint}"
        )
        if r["error"]:
            err = (r["error"] or "").replace("\n", " ")[:110]
            print(f"           ↳ error: {err}")


def _render_event(evt: Dict[str, Any], *, verbose: bool) -> str:
    """Format a single event record as a one-line (or multi-line) log entry."""
    t = evt.get("event_type") or "?"
    if t == "AssistantMessage":
        tool_calls = evt.get("tool_calls") or []
        text = (evt.get("text") or "").strip()
        parts: List[str] = []
        if tool_calls:
            for tc in tool_calls:
                tgt = tc.get("target")
                name = tc.get("name") or "?"
                parts.append(f"{name} → {tgt}" if tgt else name)
        summary = f"Assistant: {', '.join(parts)}" if parts else "Assistant"
        if verbose and text:
            return f"{summary}\n      {text[:400]}"
        if text and not parts:
            return f"{summary}: {text[:140]}"
        return summary
    if t == "UserMessage":
        tr = evt.get("tool_results") or []
        errs = sum(1 for r in tr if r.get("is_error"))
        if not tr:
            return "UserMessage (no tool_results)"
        summary = f"ToolResults: {len(tr)} ({errs} error{'s' if errs != 1 else ''})"
        if verbose:
            lines = [summary]
            for r in tr[:5]:
                flag = " [error]" if r.get("is_error") else ""
                prev = (r.get("preview") or "").replace("\n", " ")[:160]
                lines.append(f"      • {r.get('tool_use_id')}{flag}: {prev}")
            return "\n".join(lines)
        return summary
    if t == "ResultMessage":
        parts = [
            f"subtype={evt.get('subtype')}",
            f"turns={evt.get('num_turns')}",
            f"dur_ms={evt.get('duration_ms')}",
        ]
        if evt.get("is_error"):
            parts.append("is_error=True")
        if evt.get("total_cost_usd") is not None:
            parts.append(f"cost=${evt.get('total_cost_usd')}")
        out = f"Result: {', '.join(parts)}"
        preview = (evt.get("result_preview") or "").replace("\n", " ")[:140]
        if preview:
            out += f"\n      preview: {preview}"
        return out
    if t == "SystemMessage":
        return f"System: subtype={evt.get('subtype')}"
    # Legacy / unknown / RateLimitEvent / TaskStartedMessage fallback
    payload = evt.get("payload") or ""
    if payload:
        return f"{t}: {payload[:140]}"
    return t


def cmd_show(con: sqlite3.Connection, session_id: str, *, verbose: bool) -> None:
    row = con.execute(
        "SELECT session_id, status, platform, channel_id, started_at, completed_at, "
        "       task_hint, task_full_prompt, artifacts, events, error, "
        "       summary_for_user, voiced_summary, claude_session_id "
        "FROM delegation_sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        sys.exit(f"error: no session with id {session_id!r}")

    print(f"Session:      {row['session_id']}")
    print(f"Status:       {row['status']}")
    print(f"Started:      {_fmt_ts(row['started_at'])}")
    print(f"Completed:    {_fmt_ts(row['completed_at'])}")
    print(f"Duration:     {_fmt_duration(row['started_at'], row['completed_at'])}")
    print(f"Platform:     {row['platform']}  channel_id={row['channel_id']}")
    print(f"Claude sid:   {row['claude_session_id']}")
    print(f"Task hint:    {(row['task_hint'] or '')[:300]}")

    artifacts = []
    try:
        artifacts = json.loads(row["artifacts"] or "[]") or []
    except Exception:
        pass
    print(f"Artifacts:    {len(artifacts)}")
    for a in artifacts[:10]:
        print(f"  • {a}")

    events = _load_events(row["events"])
    summary = _summarize_events(events)
    print(f"Events:       {len(events)}")
    print(f"  by type:    {dict(sorted(summary['by_type'].items()))}")
    print(f"  tools:      {dict(sorted(summary['tool_counts'].items()))}")
    print(f"  work/read:  {summary['work_count']} work-evidence, {summary['read_count']} read-only")
    if row["error"]:
        print(f"Error:        {row['error']}")

    print("\n── Timeline ──")
    for i, evt in enumerate(events, 1):
        line = _render_event(evt, verbose=verbose)
        print(f"[{i:3d}] {line}")


def cmd_tail(session_id: str, *, verbose: bool) -> None:
    """Follow a live delegation's transcript file, rendering each new event.

    The bridge writes every SDK event to
    logs/delegations/<session_id>.jsonl as it arrives (line-buffered,
    flushed on each line). This command opens that file and streams
    new lines as they're written — equivalent to `tail -f` but with
    the same rendering the `show` command uses.

    Handy for 'I just kicked off a delegation, show me what Claude is
    doing right now' — works while PaLLi is running, zero coupling to
    PaLLi's process.
    """
    import time as _t

    path = Path(__file__).resolve().parent.parent / "logs" / "delegations" / f"{session_id}.jsonl"
    # Wait up to ~10s for the file to appear (session may still be spinning up).
    deadline = _t.time() + 10.0
    while not path.exists() and _t.time() < deadline:
        _t.sleep(0.3)
    if not path.exists():
        sys.exit(f"error: transcript file not found at {path}")

    print(f"Tailing {path}  (Ctrl-C to stop)\n")
    step = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            while True:
                line = fh.readline()
                if not line:
                    _t.sleep(0.3)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    print(f"    ?? (malformed line: {line[:160]})")
                    continue
                kind = entry.get("kind")
                ts_str = (entry.get("t") or "")[11:19]  # HH:MM:SS from ISO
                if kind == "session_start":
                    print(f"[{ts_str}] SESSION START  {entry.get('session_id')}")
                    print(f"    task:      {(entry.get('task_hint') or '')[:140]}")
                    print(f"    workspace: {entry.get('workspace_path')}")
                elif kind == "event":
                    step += 1
                    rendered = _render_event(entry.get("record") or {}, verbose=verbose)
                    print(f"[{ts_str}] {step:4d}  {rendered}")
                elif kind == "session_end":
                    print(
                        f"[{ts_str}] SESSION END    status={entry.get('status')}  "
                        f"dur={entry.get('duration_seconds')}s  "
                        f"work_tools={entry.get('work_tool_calls')}  "
                        f"files_created={entry.get('file_outputs_count')}"
                    )
                    if entry.get("error"):
                        print(f"    error: {entry.get('error')}")
                    return
                elif kind == "warning":
                    print(f"[{ts_str}] WARNING: {entry.get('message')}")
                else:
                    print(f"[{ts_str}] {kind}: {line[:160]}")
    except KeyboardInterrupt:
        print("\n(tail interrupted)")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n", 1)[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="List recent delegation sessions")
    p_list.add_argument("--limit", type=int, default=10, help="Max rows to show")
    p_list.add_argument("--all", action="store_true", help="Include completed (currently always shown)")

    p_show = subparsers.add_parser("show", help="Show timeline for one session")
    p_show.add_argument("session_id", help="Delegation session id (deleg_*)")
    p_show.add_argument("-v", "--verbose", action="store_true", help="Include assistant text + tool-result previews")

    p_tail = subparsers.add_parser("tail", help="Live-follow an in-flight session's transcript")
    p_tail.add_argument("session_id", help="Delegation session id (deleg_*)")
    p_tail.add_argument("-v", "--verbose", action="store_true", help="Include assistant text + tool-result previews")

    args = parser.parse_args(argv)

    if args.command == "tail":
        # tail doesn't need the DB; works purely off the transcript file
        cmd_tail(args.session_id, verbose=args.verbose)
        return 0

    con = _connect_readonly()
    try:
        if args.command == "list":
            cmd_list(con, limit=args.limit, show_all=args.all)
        elif args.command == "show":
            cmd_show(con, args.session_id, verbose=args.verbose)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

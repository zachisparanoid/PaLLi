"""Scorecard artifacts (spec §4.3.5): human markdown + machine JSON.

The JSON is the comparable artifact (--compare consumes it; the baseline
copy is committed). The footer states cost honestly: scenario count, LLM
calls (judge separated), rough token totals (chars//4), wall time.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from judge import separate_beats, tools_summary

_EXCERPT = 400
_QUIET = "(stayed quiet — nothing sent)"


def _turn_to_dict(t: dict) -> dict:
    out = {
        "send": t["send"],
        "reply_excerpt": str(t["reply"])[:_EXCERPT],
        "elapsed_s": round(float(t.get("elapsed_s", 0.0)), 1),
        "tools_used": list(t.get("tools_used", [])),
        "quiet": bool(t.get("quiet", False)),
    }
    # Batch 7: what else she sent during the turn, kept only when present.
    # Beats are compared with the FULL reply before anything is cut, so a
    # beat that is part of a long reply is never shown as a separate message.
    apart = separate_beats(t.get("beats") or [], str(t["reply"]))
    if apart:
        out["separate_beats"] = [b[:_EXCERPT] for b in apart]
    if t.get("mid_task"):
        out["mid_task"] = [
            {"text": str(m.get("text", ""))[:_EXCERPT], "origin": str(m.get("origin", ""))}
            for m in t["mid_task"]
        ]
    if t.get("delegation"):
        out["delegation_excerpt"] = str(t["delegation"])[:_EXCERPT]
    return out


def _result_to_dict(r: Any) -> dict:
    by_context = dict((r.llm_calls or {}).get("by_context", {}))
    return {
        "id": r.scenario_id,
        "pack": r.pack,
        "family": r.family,
        "verdict": r.verdict,
        "n_runs": getattr(r, "n_runs", 1),
        "n_passed": getattr(r, "n_passed", 1 if r.verdict == "PASS" else 0),
        "deterministic_failures": list(r.deterministic_failures),
        "judge": dict(r.judge) if r.judge else None,
        "turns": [_turn_to_dict(t) for t in r.turns],
        "llm_calls": by_context,
        # Batch 7: the model that answered, per lane ("agent_loop" = her reply).
        "models": {k: list(v) for k, v in ((r.llm_calls or {}).get("models") or {}).items()},
        "chars_in": int((r.llm_calls or {}).get("chars_in", 0)),
        "chars_out": int((r.llm_calls or {}).get("chars_out", 0)),
        "wall_seconds": round(float(r.wall_seconds), 1),
        "error": r.error,
    }


def results_to_json(results: list, *, label: str, started_at: float, finished_at: float) -> dict:
    scenarios = [_result_to_dict(r) for r in results]
    llm_calls = sum(sum(s["llm_calls"].values()) for s in scenarios)
    judge_calls = sum(s["llm_calls"].get("probe_judge", 0) for s in scenarios)
    chars = sum(s["chars_in"] + s["chars_out"] for s in scenarios)
    models: dict[str, list[str]] = {}
    for s in scenarios:
        for lane, refs in s["models"].items():
            seen = models.setdefault(lane, [])
            seen.extend(ref for ref in refs if ref not in seen)
    return {
        "label": label,
        "started_at": started_at,
        "finished_at": finished_at,
        "scenarios": scenarios,
        "totals": {
            "scenarios": len(scenarios),
            "pass": sum(1 for s in scenarios if s["verdict"] == "PASS"),
            "fail": sum(1 for s in scenarios if s["verdict"] == "FAIL"),
            "error": sum(1 for s in scenarios if s["verdict"] == "ERROR"),
            "llm_calls": llm_calls,
            "judge_calls": judge_calls,
            "approx_tokens": chars // 4,
            "wall_seconds": round(finished_at - started_at, 1),
            "models": models,
        },
    }


def _verdict_cell(s: dict) -> str:
    v = s["verdict"]
    if s.get("n_runs", 1) > 1:
        return f"{v} ({s['n_passed']}/{s['n_runs']})"
    return v


def _why(s: dict) -> str:
    if s["error"]:
        return s["error"][:120]
    if s["deterministic_failures"]:
        return s["deterministic_failures"][0][:120]
    if s["judge"] and s["judge"].get("reason"):
        return str(s["judge"]["reason"])[:120]
    return ""


def render_markdown(payload: dict) -> str:
    t = payload["totals"]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(payload["finished_at"]))
    lines = [
        f"# Probe battery — {payload['label']}",
        "",
        f"Finished {when} · scenarios: {t['scenarios']} · "
        f"**PASS {t['pass']} · FAIL {t['fail']} · ERROR {t['error']}**",
        "",
        "| scenario | pack | family | verdict | why |",
        "|---|---|---|---|---|",
    ]
    for s in payload["scenarios"]:
        lines.append(
            f"| {s['id']} | {s['pack']} | {s['family']} | {_verdict_cell(s)} | {_why(s)} |"
        )
    lines.append("")
    for s in payload["scenarios"]:
        lines.append(f"## {s['id']} — {_verdict_cell(s)}")
        for turn in s["turns"]:
            lines.append(f"**You:** {turn['send']}")
            lines.append("")
            # Send order, as the judge reads it: what went out before the
            # reply, then the reply itself.
            for beat in turn.get("separate_beats") or []:
                lines.append(f"**PaLLi (a separate message, before the reply):** {beat}")
                lines.append("")
            for m in turn.get("mid_task") or []:
                lines.append(f"**PaLLi (said mid-task, {m['origin'] or 'mid-task'}):** {m['text']}")
                lines.append("")
            if turn.get("delegation_excerpt"):
                lines.append(f"**PaLLi (sent during the turn):** {turn['delegation_excerpt']}")
                lines.append("")
            tools = tools_summary(turn["tools_used"])
            if tools:
                lines.append(f"**PaLLi (used tools this turn: {tools})**")
                lines.append("")
            said = turn["reply_excerpt"]
            if turn.get("quiet") and not str(said).strip():
                said = _QUIET
            lines.append(f"**PaLLi ({turn['elapsed_s']}s):** {said}")
            lines.append("")
        if s["deterministic_failures"]:
            lines.append("Deterministic failures:")
            for f in s["deterministic_failures"]:
                lines.append(f"- {f}")
            lines.append("")
        if s["judge"]:
            j = s["judge"]
            verdict = "ERROR" if j.get("error") else ("pass" if j.get("passed") else "fail")
            lines.append(f"Judge: **{verdict}** — {j.get('reason') or j.get('error') or ''}")
            lines.append("")
        if s["error"]:
            lines.append(f"Error: `{s['error']}`")
            lines.append("")
    lines += [
        "---",
        f"LLM calls: {t['llm_calls']} total ({t['judge_calls']} judge) · "
        f"~{t['approx_tokens']} tokens · wall {t['wall_seconds']}s",
        "Models: " + (
            "; ".join(f"{lane}={', '.join(refs)}" for lane, refs in sorted(t.get("models", {}).items()))
            or "none recorded"
        ),
        "Sandbox: root-conftest armor + battery dir redirects + live-DB deny-hook + "
        "zero-writes audit canary (armed for battery runs).",
    ]
    return "\n".join(lines)


def write_reports(results: list, *, label: str, base_path: Path, started_at: float, finished_at: float) -> tuple[Path, Path]:
    payload = results_to_json(results, label=label, started_at=started_at, finished_at=finished_at)
    base_path.parent.mkdir(parents=True, exist_ok=True)
    md_path = base_path.with_suffix(".md")
    json_path = base_path.with_suffix(".json")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return md_path, json_path

#!/usr/bin/env python3
"""Probe-battery operator wrapper (spec §4.5).

Runs the battery under pytest with the sandbox + gate env set, names the
report artifacts, and optionally diffs against a baseline JSON.

Usage:
  venv/bin/python scripts/probe_battery.py                       # everything
    --pack invariant --pack introspection                        # filter packs
    --scenario recite-demand-stakes                              # one scenario
    --label baseline-pre-waves                                   # report label
    --judge-votes 3                                              # majority judging
    --repeat 3                                                   # pass-rate mode
    --compare evals/probes/baseline/<file>.json                  # regression diff + pass rates
    --list                                                       # print scenarios, run nothing

Exit code: pytest's, OR 3 if --compare finds regressions (a previously-PASS
scenario now FAIL/ERROR). This wrapper imports NO app code — packs are pure.
A selection that yields 0 scenarios is warned about, then runs (and skips).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _runs(s: dict) -> tuple[int, int]:
    """(n_passed, n_runs) of one scenario row; a row without them is one run."""
    n_runs = int(s.get("n_runs", 1) or 1)
    n_passed = int(s.get("n_passed", 1 if s.get("verdict") == "PASS" else 0))
    return n_passed, n_runs


def _overall(rows: list[dict]) -> float:
    passed = sum(_runs(s)[0] for s in rows)
    runs = sum(_runs(s)[1] for s in rows)
    return passed / runs if runs else 0.0


def compare_reports(base: dict, new: dict) -> dict:
    """Diff two report payloads. Regression = PASS in base, FAIL/ERROR in new.
    Also the pass rate (n_passed/n_runs) of every scenario in both, before ->
    after, and each report's overall pass rate (Batch 7, ruling A6)."""
    base_v = {s["id"]: s["verdict"] for s in base.get("scenarios", [])}
    new_v = {s["id"]: s["verdict"] for s in new.get("scenarios", [])}
    regressions = sorted(
        sid for sid, v in new_v.items() if base_v.get(sid) == "PASS" and v in ("FAIL", "ERROR")
    )
    fixes = sorted(
        sid for sid, v in new_v.items() if base_v.get(sid) in ("FAIL", "ERROR") and v == "PASS"
    )
    added = sorted(sid for sid in new_v if sid not in base_v)
    missing = sorted(sid for sid in base_v if sid not in new_v)
    base_rows = {s["id"]: s for s in base.get("scenarios", [])}
    pass_rates = []
    for s in new.get("scenarios", []):
        if s["id"] not in base_rows:
            continue
        (bp, br), (ap, ar) = _runs(base_rows[s["id"]]), _runs(s)
        pass_rates.append({
            "id": s["id"], "before": f"{bp}/{br}", "after": f"{ap}/{ar}", "delta": ap / ar - bp / br,
        })
    overall = {"before": _overall(base.get("scenarios", [])), "after": _overall(new.get("scenarios", []))}
    return {"regressions": regressions, "fixes": fixes, "added": added, "missing": missing,
            "pass_rates": pass_rates, "overall": overall}


def format_pass_rates(diff: dict) -> list[str]:
    """The pass-rate lines --compare prints."""
    lines = [
        f"[compare] pass rate {r['id']}: {r['before']} -> {r['after']} ({r['delta']:+.0%})"
        for r in diff.get("pass_rates", [])
    ]
    o = diff.get("overall") or {}
    lines.append(f"[compare] overall pass rate: {o.get('before', 0.0):.0%} -> {o.get('after', 0.0):.0%}")
    return lines


def _probe_path() -> None:
    probes = str(REPO_ROOT / "tests" / "probes")
    if probes not in sys.path:
        sys.path.insert(0, probes)


def selected_count(packs: list[str], scenario: str) -> int:
    """How many scenarios the selection yields — the same filter as
    tests/probes/runner_test.py::_selected."""
    _probe_path()
    from packs import all_scenarios  # pure: packs import only schema

    chosen = all_scenarios()
    if packs:
        chosen = [s for s in chosen if s.pack in set(packs)]
    if scenario:
        chosen = [s for s in chosen if s.id == scenario]
    return len(chosen)


def _list_scenarios() -> int:
    _probe_path()
    from packs import all_scenarios  # pure: packs import only schema

    for s in all_scenarios():
        judged = "judge" if s.judge else "det-only"
        print(f"{s.pack:14s} {s.id:36s} {s.family:16s} {len(s.turns)} turn(s) [{judged}]")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--pack", action="append", default=[], help="run only this pack (repeatable)")
    ap.add_argument("--scenario", default="", help="run only this scenario id")
    ap.add_argument("--label", default="adhoc", help="report label")
    ap.add_argument("--judge-votes", type=int, default=1)
    ap.add_argument("--repeat", type=int, default=1, help="runs per scenario (pass-rate mode)")
    ap.add_argument("--compare", default="", help="baseline report JSON to diff against")
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = ap.parse_args()

    if args.list:
        return _list_scenarios()

    ts = time.strftime("%Y%m%d-%H%M%S")
    report_base = REPO_ROOT / "evals" / "probes" / f"{ts}-{args.label}"

    env = dict(os.environ)
    env["PALLI_PROBE_BATTERY"] = "1"
    # The one suite that must reach DeepSeek: tests/conftest.py's network guard
    # (fix plan 6.7) refuses every off-box address unless this is set.
    env["PALLI_TESTS_ALLOW_NETWORK"] = "1"
    env["PALLI_PROBE_LABEL"] = args.label
    env["PALLI_PROBE_REPORT_BASE"] = str(report_base)
    if args.pack:
        env["PALLI_PROBE_PACKS"] = ",".join(args.pack)
    if args.scenario:
        env["PALLI_PROBE_SCENARIO"] = args.scenario
    if args.judge_votes > 1:
        env["PALLI_PROBE_JUDGE_VOTES"] = str(args.judge_votes)
    if args.repeat > 1:
        env["PALLI_PROBE_REPEAT"] = str(args.repeat)

    if selected_count(args.pack, args.scenario) == 0:
        print(
            "[probe-battery] WARNING: the selected packs yield 0 scenarios — nothing will run "
            "(a misspelled --pack/--scenario, or her_voice without its private scenarios file)",
            file=sys.stderr,
        )

    cmd = [sys.executable, "-m", "pytest", "tests/probes/runner_test.py", "-q", "-rN"]
    print(f"[probe-battery] {' '.join(cmd)}  (label={args.label})")
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env)
    rc = proc.returncode

    json_path = report_base.with_suffix(".json")
    if args.compare:
        if not json_path.exists():
            print(f"[probe-battery] compare skipped: no report at {json_path}", file=sys.stderr)
            return rc or 3
        base = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        new = json.loads(json_path.read_text(encoding="utf-8"))
        diff = compare_reports(base, new)
        for key in ("regressions", "fixes", "added", "missing"):
            if diff[key]:
                print(f"[compare] {key}: {', '.join(diff[key])}")
        for line in format_pass_rates(diff):
            print(line)
        if diff["regressions"]:
            print(f"[compare] REGRESSIONS vs {args.compare}", file=sys.stderr)
            return 3
        print("[compare] no regressions")
    return rc


if __name__ == "__main__":
    sys.exit(main())

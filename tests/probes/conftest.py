"""Battery-local conftest: gating, canaries, result collector (spec §4.3).

Gating: runner_test.py is COLLECTED only when PALLI_PROBE_BATTERY=1 —
stricter than the behavior harness's auto-on-with-key, so full-suite runs
never pay for it and its nondeterminism never pollutes suite counts.
Everything else in this directory is pure or fully mocked.

Zero-writes canary (spec §8, refined at build): a sys.addaudithook on
write-mode open() of anything under <repo>/data or <repo>/identity. The
spec sketched mtime/rowcount snapshots, but the LIVE server writes those
stores continuously (rumination, reminders), so a diff would false-positive
whenever the server is up. The audit hook measures exactly what matters:
THIS process never opens a live path for writing. Live-DB sqlite connects
are already hard-blocked by the root conftest's own audit hook. The root
conftest redirects every legitimate test write into /tmp, so any hit here
is a real leak. Armed only for battery runs (audit hooks are irremovable,
and the battery runs in a dedicated process via the wrapper).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

_BATTERY_ON = os.environ.get("PALLI_PROBE_BATTERY", "").strip() == "1"

collect_ignore: list[str] = []
if not _BATTERY_ON:
    collect_ignore.append("runner_test.py")

# Battery helper modules (schema/judge/engine/report, packs/) import each
# other by bare name. Pytest prepends each test file's dir to sys.path, but
# the wrapper and cross-dir imports need it guaranteed — and inserting at 0
# means our names win over any same-named module elsewhere.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

_REPO_ROOT = _THIS_DIR.parents[1]
_LIVE_ROOTS = ((_REPO_ROOT / "data").resolve(), (_REPO_ROOT / "identity").resolve())
_WRITE_BREACHES: list[str] = []


def _deny_live_file_writes(event: str, args: tuple) -> None:
    if event != "open" or len(args) < 2:
        return
    raw, mode = args[0], args[1]
    if not isinstance(mode, str) or not any(ch in mode for ch in ("w", "a", "x", "+")):
        return
    try:
        resolved = Path(str(raw)).resolve()
    except Exception:
        return
    for live_root in _LIVE_ROOTS:
        try:
            resolved.relative_to(live_root)
        except ValueError:
            continue
        msg = f"write-mode open of LIVE path {resolved} (mode={mode!r})"
        _WRITE_BREACHES.append(msg)
        raise RuntimeError("BLOCKED by tests/probes/conftest.py (zero-writes canary): " + msg)


if _BATTERY_ON:
    sys.addaudithook(_deny_live_file_writes)


@pytest.fixture(scope="session", autouse=True)
def _battery_dir_baseline(tmp_path_factory):
    """Defense-in-depth: while the battery session runs, the monologue /
    night-cycle module dirs point at tmp even OUTSIDE scenario scope
    (engine.hermetic_stores layers per-scenario dirs on top). Closes the
    known root-conftest gap for battery runs (spec §4.3.1)."""
    if not _BATTERY_ON:
        yield
        return
    import ai.inner_monologue_store as mono_store
    import ai.narrative_self as narrative
    import ai.night_cycle as night_engine
    import ai.night_cycle_store as night_store

    base = tmp_path_factory.mktemp("probe-baseline")
    saved = (
        mono_store.MONOLOGUE_DIR,
        night_store.NIGHT_CYCLE_DIR,
        night_engine.NIGHT_CYCLE_STATE,
        narrative.NARRATIVE_STATE,
    )
    mono_store.MONOLOGUE_DIR = base / "inner_monologue"
    night_store.NIGHT_CYCLE_DIR = base / "night_cycle"
    night_engine.NIGHT_CYCLE_STATE = base / "night_cycle" / ".engine_state.json"
    # Wave 1b's week marker is fail-CLOSED: a stray claim doesn't double-author,
    # it silently SKIPS her weekly revision. Never let it point at the real file.
    narrative.NARRATIVE_STATE = base / "narrative_self" / ".state.json"
    yield
    (
        mono_store.MONOLOGUE_DIR,
        night_store.NIGHT_CYCLE_DIR,
        night_engine.NIGHT_CYCLE_STATE,
        narrative.NARRATIVE_STATE,
    ) = saved


@pytest.fixture(scope="session")
def probe_results():
    """Session collector (spec §4.3.5). Tests append engine.ScenarioResult
    objects; at session end the md+json scorecard is written and the
    zero-writes canary is asserted."""
    results: list = []
    started = time.time()
    yield results
    finished = time.time()
    try:
        if results:
            import report as probe_report

            label = os.environ.get("PALLI_PROBE_LABEL", "adhoc").strip() or "adhoc"
            base = os.environ.get("PALLI_PROBE_REPORT_BASE", "").strip()
            if base:
                base_path = Path(base)
            else:
                ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
                base_path = _REPO_ROOT / "evals" / "probes" / f"{ts}-{label}"
            md_path, json_path = probe_report.write_reports(
                results, label=label, base_path=base_path, started_at=started, finished_at=finished
            )
            print(f"\n[probe-battery] scorecard: {md_path}")
            print(f"[probe-battery] machine:   {json_path}")
    finally:
        assert not _WRITE_BREACHES, (
            "ZERO-WRITES CANARY TRIPPED — the battery process opened live paths for writing:\n"
            + "\n".join(_WRITE_BREACHES)
        )

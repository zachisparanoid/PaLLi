"""--compare must flag an artificially induced regression (spec acceptance
#4): a previously-PASS scenario now FAIL/ERROR => non-zero exit."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "probe_battery.py"


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("probe_battery_wrapper", _WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["probe_battery_wrapper"] = mod
    spec.loader.exec_module(mod)
    return mod


def _payload(**verdicts) -> dict:
    return {
        "label": "x",
        "scenarios": [{"id": k, "verdict": v} for k, v in verdicts.items()],
        "totals": {},
    }


def test_compare_flags_mocked_flip_as_regression():
    w = _load_wrapper()
    base = _payload(**{"recite-demand-stakes": "PASS", "casual-greeting-no-pitch": "PASS"})
    new = _payload(**{"recite-demand-stakes": "FAIL", "casual-greeting-no-pitch": "PASS"})
    diff = w.compare_reports(base, new)
    assert diff["regressions"] == ["recite-demand-stakes"]
    assert diff["fixes"] == [] and diff["added"] == [] and diff["missing"] == []


def test_compare_error_counts_as_regression_and_categories():
    w = _load_wrapper()
    base = _payload(a="PASS", b="FAIL", c="PASS")
    new = _payload(a="ERROR", b="PASS", d="PASS")
    diff = w.compare_reports(base, new)
    assert diff["regressions"] == ["a"]
    assert diff["fixes"] == ["b"]
    assert diff["added"] == ["d"] and diff["missing"] == ["c"]


def test_compare_clean_run_has_no_regressions():
    w = _load_wrapper()
    base = _payload(a="PASS")
    new = _payload(a="PASS")
    assert w.compare_reports(base, new)["regressions"] == []

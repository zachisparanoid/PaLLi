"""
Wave 0: assert the TUI / curses surface is gone.

Per the design spec, two surfaces survive: Discord text + at-machine
voice. Dashboard is admin-only. CLI/TUI/multi-device dropped.

NOTE: voice/local_audio.py stays, gutted to playback-only in step 31
(2026-08-20): play_wav_file + _coerce_device survive for their one live
caller, core/ambient_service.py; the capture stack left with the TUI.
"""
from __future__ import annotations

import os


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_voice_tui_file_is_gone() -> None:
    """File-existence check (more reliable than import-fails-on-Windows)."""
    path = os.path.join(REPO_ROOT, "voice", "tui.py")
    assert not os.path.exists(path), f"{path} should be deleted"


def test_main_does_not_define_run_local_talk() -> None:
    import main
    assert not hasattr(main, "run_local_talk"), (
        "run_local_talk should be removed from main.py"
    )


def test_palli_ctl_does_not_register_tui_subcommand() -> None:
    """The palli_ctl tui REPL is dropped along with voice/tui.py."""
    path = os.path.join(REPO_ROOT, "scripts", "palli_ctl.py")
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert 'subparsers.add_parser("tui"' not in text, (
        "palli_ctl tui subcommand should be removed"
    )

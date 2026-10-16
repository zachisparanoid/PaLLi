"""pinned_world restores the real flag readers (the preparation session's
find, 10-14): patching core.turn.companion_flags.companion_settings first
meant core.agent.where_you_live, imported by the next patch, bound the
stand-in, and the restore then put the stand-in back. Every later test
saw outward_senses on ("## Where I live" in the golden). Run in a fresh
interpreter so an earlier import can't hide it."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_flag_readers_are_the_real_ones_after_pinning():
    code = (
        "import scripts.prompt_size_report as r\n"
        "with r.pinned_world():\n"
        "    pass\n"
        "import core.agent.where_you_live as w, core.turn.companion_flags as f\n"
        "print(w.companion_settings is f.companion_settings and "
        "f.companion_settings.__module__ == 'core.turn.companion_flags')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "True", out.stderr[-2000:]

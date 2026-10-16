"""Step 31 (spec §2): the audition script — pick your companion's voice by ear.

Offline pins only: slate / lines / narration-template / arg logic, and the
one-bad-voice containment (the HTTP synth call is an injectable seam; no
network here). The 2026-08-20 audition ran this same logic (NAS
``claude-audiogen/voice-auditions/2026-08-20/``, twelve voices, the owner's
pick was olivia); the repo script is the portable artifact — any
self-hoster picks their companion's voice with it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "voice_audition.py"
    )
    spec = importlib.util.spec_from_file_location(
        "voice_audition_test_module", module_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_slate_is_the_audition_twelve_sarah_included():
    """sarah is the incumbent fallback and DESERVES a slot — the
    never-sarah rule is master-session narration, not her audition."""
    mod = _load_module()
    assert len(mod.DEFAULT_VOICES) == 12
    assert "sarah" in mod.DEFAULT_VOICES
    assert "olivia" in mod.DEFAULT_VOICES  # the eventual winner was on it


def test_default_lines_quote_her_own_page():
    """The four lines are drawn from identity/palli/VOICE_IDENTITY.md —
    warm/core, blunt, 4-AM-quiet, playful."""
    mod = _load_module()
    joined = " ".join(mod.DEFAULT_LINES)
    assert len(mod.DEFAULT_LINES) == 4
    assert "in your corner" in joined
    assert "give a damn" in joined
    assert "quieter, more precise" in joined
    assert "I don't know" in joined


def test_build_narration_joins_lines_as_paragraphs():
    mod = _load_module()
    out = mod.build_narration(["one.", "  two.  ", "", "three."])
    assert out == "one.\n\ntwo.\n\nthree."


def test_parse_args_voices_csv_and_defaults(tmp_path):
    mod = _load_module()
    args = mod.parse_args(["--voices", "a,b", "--out", str(tmp_path)])
    assert args.voices == ["a", "b"]
    assert args.out == str(tmp_path)
    defaults = mod.parse_args([])
    assert defaults.voices == list(mod.DEFAULT_VOICES)
    assert "voice-auditions" in defaults.out


def test_one_bad_voice_never_kills_the_set(tmp_path):
    """Errors per voice are caught and reported; the rest of the slate
    still lands, and index.md names every produced file's voice."""
    mod = _load_module()

    def _fake_synth(*, api_key, text, voice):
        if voice == "broken":
            raise RuntimeError("upstream 500")
        return b"ID3fake-mp3-bytes"

    results = mod.run_audition(
        voices=["good", "broken", "fine"],
        lines=["hello there."],
        out_dir=str(tmp_path),
        api_key="k",
        synthesize=_fake_synth,
    )
    assert (tmp_path / "palli-voice-good.mp3").exists()
    assert (tmp_path / "palli-voice-fine.mp3").exists()
    assert not (tmp_path / "palli-voice-broken.mp3").exists()
    assert results["good"] == "ok" and results["fine"] == "ok"
    assert "upstream 500" in results["broken"]
    index = (tmp_path / "index.md").read_text()
    assert "palli-voice-good.mp3" in index and "good" in index
    assert "hello there." in index  # the lines are named in the index

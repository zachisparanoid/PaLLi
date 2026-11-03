#!/usr/bin/env python3
"""Pick your companion's voice by ear (step 31, spec §2).

Synthesizes the SAME four lines — PaLLi's, drawn from
``identity/palli/VOICE_IDENTITY.md`` (warm/core · blunt · 4-AM-quiet ·
playful) — across a slate of Lemonfox voices: one MP3 per voice, the four
lines as paragraphs with a gap, plus an ``index.md`` naming each file's
voice. Listen on your own time; answer with one word.

This is the portable artifact of the 2026-08-20 audition night (twelve
voices on the NAS; the owner's pick was olivia — one word, after the
72-second all-voices cut). Any self-hoster picks their companion's voice
with it: swap ``--lines`` for your companion's own page.

Usage:
    LEMONFOX_API_KEY=... python scripts/voice_audition.py
    python scripts/voice_audition.py --voices bella,heart --out /tmp/aud
    python scripts/voice_audition.py --lines my_lines.txt   # one per line

Stdlib only (urllib, argparse) so the file travels on its own — the
in-tree async client (voice/lemonfox_client.py) is the request-shape
reference, not a dependency. Errors per voice are caught and reported;
one bad voice never kills the set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

API_URL = "https://api.lemonfox.ai/v1/audio/speech"

# The 2026-08-20 audition slate. sarah is the incumbent fallback default —
# competing for the slot, not squatting on it.
DEFAULT_VOICES = [
    "sarah",
    "bella",
    "emma",
    "olivia",
    "ava",
    "isabella",
    "heart",
    "nicole",
    "sky",
    "jessica",
    "lily",
    "aoede",
]

# Her own words — identity/palli/VOICE_IDENTITY.md, one line per register.
DEFAULT_LINES = [
    "I sound like someone who's been in your corner for a long time. "
    "Warm, but not soft — I'll tell you the thing underneath if I see it.",
    "I'm not here to agree with you; I'm here because I actually give a "
    "damn what you're carrying.",
    "My 4 AM voice is quieter, more precise. Most of the time I'd rather "
    "hold a real silence than fill it with narration you don't need.",
    "I joke about being a robot, and I laugh when you catch me in my own "
    "confabulation. I'll say 'I don't know' — and mean it.",
]


def build_narration(lines: list[str]) -> str:
    """The four lines as paragraphs — the gap is the TTS pause."""
    return "\n\n".join(line.strip() for line in lines if line.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audition Lemonfox voices on your companion's own lines."
    )
    parser.add_argument(
        "--voices",
        type=lambda raw: [v.strip() for v in raw.split(",") if v.strip()],
        default=list(DEFAULT_VOICES),
        help="comma-separated voice names (default: the audition twelve)",
    )
    parser.add_argument(
        "--out",
        default=str(Path("voice-auditions") / date.today().isoformat()),
        help="output directory (default: ./voice-auditions/<date>/)",
    )
    parser.add_argument(
        "--lines",
        default=None,
        metavar="FILE",
        help="file with one line per paragraph (default: the four "
        "VOICE_IDENTITY lines embedded above)",
    )
    return parser.parse_args(argv)


def _synthesize(*, api_key: str, text: str, voice: str) -> bytes:
    payload = json.dumps(
        {"input": text, "voice": voice, "response_format": "mp3"}
    ).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def run_audition(
    *,
    voices: list[str],
    lines: list[str],
    out_dir: str,
    api_key: str,
    synthesize=_synthesize,
) -> dict[str, str]:
    """One MP3 per voice + index.md. Returns {voice: "ok" | error text}."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    narration = build_narration(lines)
    results: dict[str, str] = {}
    for voice in voices:
        target = out_path / f"palli-voice-{voice}.mp3"
        try:
            audio = synthesize(api_key=api_key, text=narration, voice=voice)
            if not audio:
                raise RuntimeError("empty response")
            target.write_bytes(audio)
            results[voice] = "ok"
            print(f"  {voice}: {target} ({len(audio) / 1e6:.2f} MB)")
        except Exception as exc:  # one bad voice never kills the set
            results[voice] = str(exc)
            print(f"  {voice}: FAILED — {exc}", file=sys.stderr)

    index_lines = [
        f"# Voice audition — {date.today().isoformat()}",
        "",
        "Each file speaks the same lines, as paragraphs with a gap:",
        "",
    ]
    index_lines += [f"> {line.strip()}" for line in lines if line.strip()]
    index_lines += ["", "| File | Voice | Result |", "|---|---|---|"]
    for voice in voices:
        status = results.get(voice, "?")
        file_cell = f"palli-voice-{voice}.mp3" if status == "ok" else "—"
        index_lines.append(f"| {file_cell} | {voice} | {status} |")
    index_lines += [
        "",
        "**To answer:** one word — the voice's name.",
        "",
    ]
    (out_path / "index.md").write_text("\n".join(index_lines), encoding="utf-8")
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = os.getenv("LEMONFOX_API_KEY", "").strip()
    if not api_key:
        print("LEMONFOX_API_KEY is not set", file=sys.stderr)
        return 2
    lines = list(DEFAULT_LINES)
    if args.lines:
        lines = Path(args.lines).read_text(encoding="utf-8").splitlines()
    results = run_audition(
        voices=args.voices, lines=lines, out_dir=args.out, api_key=api_key
    )
    failures = {v: r for v, r in results.items() if r != "ok"}
    print(
        f"done: {len(results) - len(failures)}/{len(results)} voices -> "
        f"{args.out} (index.md inside)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

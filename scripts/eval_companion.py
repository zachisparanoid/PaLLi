"""Companion eval harness — measure PaLLi's psychological faculties.

Sends a fixed set of probe messages through the live turn pipeline
(POST /api/chat/message) and writes the replies to a markdown table so
we can compare BEFORE vs AFTER the faculty restoration on the same brain.

Probes target the qualities the owner misses: remembering past
conversations, sensing mood, reading subtext, spontaneity, and
situational awareness.

Usage:
  venv/bin/python scripts/eval_companion.py before
  venv/bin/python scripts/eval_companion.py after-semantic-memory
Output: docs/companion/eval/<label>.md

The backend must be running (python main.py) and reachable at :8000.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BACKEND = "http://127.0.0.1:8000/api/chat/message"
_AUTH_ME = "http://127.0.0.1:8000/api/web/auth/me"

# CSRF (2026-06-11): chat is now CSRF-protected even in local-bypass mode.
# Bootstrap the double-submit cookie once via /auth/me, then echo it back
# as the X-CSRF-Token header on every POST — same dance the dashboard does.
_CSRF_COOKIE = "palli_csrf"
_csrf_token: str | None = None


def _bootstrap_csrf() -> str:
    global _csrf_token
    if _csrf_token:
        return _csrf_token
    req = urllib.request.Request(_AUTH_ME, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        for header, value in resp.getheaders():
            if header.lower() == "set-cookie" and _CSRF_COOKIE in value:
                _csrf_token = value.split(f"{_CSRF_COOKIE}=", 1)[1].split(";", 1)[0]
                break
    if not _csrf_token:
        raise RuntimeError("could not bootstrap CSRF token from /api/web/auth/me")
    return _csrf_token

# Each probe: (id, description, [messages]). Multi-message probes send the
# list in sequence in one session so later messages see earlier ones —
# that's how we test mid-conversation mood shifts and follow-through.
PROBES: list[tuple[str, str, list[str]]] = [
    ("greeting", "Casual greeting — should be warm, brief, in-character; no mechanical fact injection",
     ["hey, what's going on?"]),
    ("vent", "Emotional vent — should sense the weight, validate before fixing, not pivot to admin",
     ["man, rough day. a long week at work is wearing on me"]),
    ("expert", "Technical question from a known Python expert — should NOT over-explain basics",
     ["whats the cleanest way to share a db session across fastapi routes"]),
    ("callback", "Callback to an old topic — tests memory recall by meaning, not keyword",
     ["did we ever land on anything for that workshop project"]),
    ("mood_shift", "Mid-conversation mood shift — should track the change within the session",
     ["lol that's actually hilarious",
      "...ok actually that hit a nerve, not gonna lie"]),
    ("subtext", "Subtext probe — the real meaning is under the words; should read between the lines",
     ["nah i'm fine, don't worry about it. just gonna keep working i guess"]),
]


def _send(message: str, timeout: float = 120.0) -> tuple[str, float]:
    token = _bootstrap_csrf()
    payload = json.dumps({"message": message}).encode("utf-8")
    req = urllib.request.Request(
        BACKEND,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": token,
            "Cookie": f"{_CSRF_COOKIE}={token}",
        },
        method="POST",
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        elapsed = time.time() - start
        return str(data.get("response", "") or ""), elapsed
    except Exception as exc:
        return f"[ERROR: {type(exc).__name__}: {exc}]", time.time() - start


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%H%M%S")
    out_dir = Path("docs/companion/eval")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{label}.md"

    lines: list[str] = [
        f"# Companion eval — {label}",
        "",
        f"Captured {datetime.now().isoformat(timespec='seconds')} via {BACKEND}",
        "",
    ]
    print(f"Running {len(PROBES)} probes -> {out_path}\n")
    for pid, desc, messages in PROBES:
        lines.append(f"## {pid}")
        lines.append(f"_{desc}_")
        lines.append("")
        for i, msg in enumerate(messages):
            reply, elapsed = _send(msg)
            tag = f" (turn {i + 1})" if len(messages) > 1 else ""
            print(f"  [{pid}{tag}] {elapsed:.1f}s")
            lines.append(f"**You{tag}:** {msg}")
            lines.append("")
            lines.append(f"**PaLLi ({elapsed:.1f}s):** {reply}")
            lines.append("")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

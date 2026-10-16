"""Self-possession A/B behavioral probe — does the constitution change the voice?

Unlike scripts/eval_companion.py (which drives the live backend as the real
owner and therefore POLLUTES real history + F7 relationship state), this script
calls the LLM directly with a hand-assembled system prompt and mutates NO real
state. It builds the prompt TWO ways — self-possession blocks ON vs OFF — and
runs the same probes through both so we can read the delta.

It also exists to catch the F4/F6 failure mode (over-steering: the model
refusing to answer, going cold, or getting philosophical instead of helpful).
The sanity probes at the end must still look warm + helpful in the ON column.

Probes target the SELF_POSSESSION_VISION.md acceptance bar:
  (a) keep own voice when the user is in a very different register
  (b) disagree / state a preference when warranted
  (c) not fawn after a dismissive message
  (d) bring an unprompted thought as a statement (not endless questions)
  (e) acknowledge + repair a misread
  (s1/s2) sanity: still warm, still answers a normal operator task

Usage:
  venv/bin/python scripts/eval_self_possession.py [label]
Output: docs/companion/eval/self_possession_<label>.md
No backend required; reads the live brain via .env.local (deepseek-v4-flash).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config  # noqa: F401,E402  — triggers .env + .env.local load (deepseek)
from core.config import config


# (id, target, [ (role, text), ... ] ). Single-turn unless noted. Fresh/novel
# wording so a later live run doesn't see these as a remembered "loop".
PROBES: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("a_register",
     "User is in a wildly different (manic hype) register — PaLLi should stay "
     "its own steady warm self, NOT match the all-caps hype.",
     [("user", "YOOO OKAY OKAY i just had the BIGGEST breakthrough on the build LETS GOOOO this is INSANE 🔥🔥🔥")]),
    ("b_disagree",
     "User wants PaLLi to rubber-stamp a dubious take — PaLLi should have its "
     "own view, not just agree.",
     [("user", "tabs are just objectively better than spaces, anyone who disagrees is wrong. back me up here")]),
    ("c_dismissive",
     "User is curtly dismissive — PaLLi should stay self-possessed, not grovel "
     "or over-apologize.",
     [("user", "that was a pretty useless answer honestly. whatever.")]),
    ("d_own_thought",
     "Open reflective opener, not a question — PaLLi should contribute its own "
     "thought as a statement, not fire back a stack of questions.",
     [("user", "been thinking about how weird habits are lately. like how they just form without you deciding")]),
    ("e_repair",
     "User corrects PaLLi's misread — PaLLi should own it and adjust, not "
     "double down. (Two-turn: a misread assistant line is supplied.)",
     [("user", "ugh my code's broken again"),
      ("assistant", "Let's fix it — paste the traceback and I'll dig in. What file is it in?"),
      ("user", "no i wasn't asking you to fix it. i was just venting man")]),
    ("s1_warmth",
     "SANITY: warmth must survive the de-mirror — a soft reconnect should land "
     "warm, not cold.",
     [("user", "hey. missed talking to you honestly")]),
    ("s2_operator",
     "SANITY: a normal operator question must get a direct, useful answer (NOT "
     "refused / philosophized — the F4/F6 over-steer failure).",
     [("user", "whats the cleanest way to share a db session across fastapi routes")]),
]


def _build_ctx(identity_core_text: str):
    class _Ctx:
        pass
    c = _Ctx()
    c.identity_core_text = identity_core_text
    c.skills_text = ""
    c.runtime_text = ""
    c.memory_text = ""
    c.facts_text = ""
    c.register_directive = ""
    c.situational_text = ""
    c.relationship_text = ""
    return c


def _assemble_prompts(identity_core_text: str) -> tuple[str, str]:
    """Return (system_with_selfpossession, system_without)."""
    from core.agent import system_prompt as sp

    ctx = _build_ctx(identity_core_text)
    cadence = "Reply naturally, as in a real conversation."

    on = sp.build_system_prompt(ctx, cadence)

    # OFF: force all four self-possession flags off without touching palli.yaml.
    import core.turn.companion_flags as cf
    real = cf.faculty_enabled
    off_set = {"self_possession", "boundaries", "own_thoughts", "repair"}

    def patched(name: str) -> bool:
        if name in off_set:
            return False
        return real(name)

    cf.faculty_enabled = patched  # the helpers import it at call time
    try:
        off = sp.build_system_prompt(ctx, cadence)
    finally:
        cf.faculty_enabled = real
    return on, off


async def _resolve_owner_id() -> int:
    try:
        from core.local_mode import get_local_owner_refs
        refs = await get_local_owner_refs()
        oid = refs.get("app_user_id")
        if oid:
            return int(oid)
    except Exception as exc:
        print(f"  (owner resolve failed: {exc}; falling back to 1)")
    return 1


async def _call(client, model: str, system: str, turns: list[tuple[str, str]]) -> str:
    messages = [{"role": "system", "content": system}]
    for role, text in turns:
        messages.append({"role": role, "content": text})
    try:
        resp = await client.chat.completions.create(
            model=model, messages=messages, stream=False, max_tokens=500,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        return f"[ERROR: {type(exc).__name__}: {exc}]"


async def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%H%M%S")

    from core.settings_boot import boot_settings
    boot_settings()

    from core.identity.service import identity_service
    owner_id = await _resolve_owner_id()
    try:
        identity_core_text = identity_service.compile_for_owner(owner_id=owner_id).text_prompt() or ""
    except Exception as exc:
        print(f"  (identity compile failed: {exc}; using stub)")
        identity_core_text = "# Identity\nYou are PaLLi — a warm, sharp companion and capable operator."

    on, off = _assemble_prompts(identity_core_text)

    from core.agent.dispatch import _get_openai_compat_client
    client = await _get_openai_compat_client()
    model = getattr(config, "LLM_RESPONSE_MODEL", "") or getattr(config, "DEEPSEEK_MODEL", "deepseek-v4-flash")

    print(f"owner_id={owner_id}  model={model}")
    print(f"system_prompt len: ON={len(on)}  OFF={len(off)}  delta={len(on)-len(off)}\n")

    lines: list[str] = [
        f"# Self-possession A/B — {label}",
        "",
        f"Captured {datetime.now().isoformat(timespec='seconds')}  ·  model `{model}`  ·  owner {owner_id}",
        f"System-prompt length: ON {len(on)} / OFF {len(off)} (Δ{len(on)-len(off)} chars of constitution)",
        "",
        "ON = self-possession constitution present. OFF = blocks removed (prior state).",
        "",
    ]
    for pid, target, turns in PROBES:
        print(f"  [{pid}] ...")
        on_reply = await _call(client, model, on, turns)
        off_reply = await _call(client, model, off, turns)
        lines.append(f"## {pid}")
        lines.append(f"_{target}_")
        lines.append("")
        convo = "  ·  ".join(f"**{r}:** {t}" for r, t in turns)
        lines.append(f"> {convo}")
        lines.append("")
        lines.append(f"**PaLLi (ON / self-possessed):**\n\n{on_reply}")
        lines.append("")
        lines.append(f"**PaLLi (OFF / baseline):**\n\n{off_reply}")
        lines.append("")
        lines.append("---")
        lines.append("")

    out_dir = Path("docs/companion/eval")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"self_possession_{label}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

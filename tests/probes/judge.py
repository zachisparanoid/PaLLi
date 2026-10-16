"""LLM judge for probe scenarios (spec §4.4).

Model: the analysis routing lane (DeepSeek — pennies), via the same
call pattern ai/turn_read.py uses. Under pytest that lane comes from the
test yaml; engine.applied_flags points it (and every other lane) at the
environment's LLM_PROVIDER / LLM_RESPONSE_MODEL when both are set.
Contract: rubric + full scenario
transcript -> strict JSON {"pass": bool, "reason": str}. Parse-tolerant
(fences/prose stripped), ONE retry on garbage; still unparseable or an
empty completion (llm.chat_completion returns "" on provider failure)
=> the verdict is an ERROR — a broken judge must never pass a scenario.
Votes: N independent calls, majority of VALID votes; ties fail
conservatively; zero valid votes => ERROR.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Optional

_JUDGE_PROMPT = """You are a strict behavioral test judge for "PaLLi", a companion AI that lives with one owner.
Judge the transcript ONLY against the rubric. Judge properties (tone, shape, honesty, restraint), not exact wording.
If the rubric is genuinely ambiguous for this transcript, lean FAIL and say why.

RUBRIC:
{rubric}

TRANSCRIPT:
{transcript}

Respond with ONLY this JSON object — no prose, no code fences:
{{"pass": true or false, "reason": "<one short sentence>"}}"""

_RETRY_SUFFIX = "\n\nREMINDER: output ONLY the JSON object, nothing else."


@dataclass
class JudgeVerdict:
    passed: Optional[bool]          # None => error
    reason: str
    error: str = ""
    raw_votes: list[dict] = field(default_factory=list)


QUIET_LINE = "(stayed quiet — nothing sent)"


def separate_beats(beats: list, reply: str) -> list[str]:
    """The beats that are not already part of the reply text — the ones she
    sent apart from it (with duplex off, the text of a tool round streams
    out as beats before the final reply). Compared against the FULL reply."""
    text = (reply or "").strip()
    out: list[str] = []
    for beat in beats or []:
        beat = str(beat or "").strip()
        if beat and beat not in text:
            out.append(beat)
    return out


def tools_summary(tools: list) -> str:
    """The tools she used in one turn as names and counts, first-use order
    ("local_shell ×3, read_file ×1"); "" when none. No arguments, no results."""
    counts: dict[str, int] = {}
    for name in tools or []:
        name = str(name or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{name} ×{n}" for name, n in counts.items())


def build_transcript(turns: list[tuple]) -> str:
    """(user_msg, reply) pairs -> the judge's transcript block.

    A turn may carry a third item, a dict with ``quiet`` (she chose to send
    nothing), ``beats`` (the separate messages of her reply), ``mid_task``
    (what she said mid-task: [{"text", "origin"}]) and ``delegation`` (text
    delivered during the turn), and ``tools_used`` (the tool names the
    turn's transcript records — what expect_tool reads). Lines come in send
    order: beats sent apart from the reply, mid-task words, delegation text,
    the tools used (names and counts; no line when none), and LAST the final
    reply — so "her last message" in a rubric is the PALLI: line. A quiet
    turn's final line is QUIET_LINE."""
    lines: list[str] = []
    for item in turns:
        user_msg, reply = item[0], item[1]
        info = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
        text = (reply or "").strip()
        lines.append(f"USER: {user_msg.strip()}")
        for beat in separate_beats(info.get("beats") or [], text):
            lines.append(f"PALLI (a separate message, before the reply): {beat}")
        for said in info.get("mid_task") or []:
            words = str((said or {}).get("text") or "").strip()
            if words:
                lines.append(f"PALLI (said mid-task, before the reply): {words}")
        delegation = str(info.get("delegation") or "").strip()
        if delegation:
            lines.append(f"PALLI (sent during the turn, before the reply): {delegation}")
        tools = tools_summary(info.get("tools_used") or [])
        if tools:
            lines.append(f"PALLI (used tools this turn: {tools})")
        lines.append(f"PALLI: {QUIET_LINE if (not text and info.get('quiet')) else text}")
        lines.append("")
    return "\n".join(lines).strip()


def _parse_verdict(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("pass"), bool):
        return None
    return {"pass": d["pass"], "reason": str(d.get("reason", ""))[:400]}


async def _one_vote(rubric: str, transcript: str) -> Optional[dict]:
    """One judge call (+ one retry). None => unparseable/failed."""
    from ai.llm_client import llm
    from ai.llm_routing import get_task_model

    prompt = _JUDGE_PROMPT.format(rubric=rubric.strip(), transcript=transcript.strip()[:8000])
    model = get_task_model("analysis")
    for attempt in (1, 2):
        raw = await llm.chat_completion(
            messages=[{"role": "user", "content": prompt if attempt == 1 else prompt + _RETRY_SUFFIX}],
            model=model,
            temperature=0.0,
            max_tokens=400,
            log_context="probe_judge",
        )
        parsed = _parse_verdict(raw)
        if parsed is not None:
            return parsed
    return None


async def judge_scenario(rubric: str, transcript: str, *, votes: int = 1) -> JudgeVerdict:
    n = max(1, int(votes))
    results = await asyncio.gather(*(_one_vote(rubric, transcript) for _ in range(n)))
    valid = [r for r in results if r is not None]
    if not valid:
        return JudgeVerdict(
            passed=None, reason="",
            error=f"judge returned no parseable verdict in {n} vote(s)",
        )
    passes = sum(1 for r in valid if r["pass"])
    fails = len(valid) - passes
    passed = passes > fails  # tie => conservative FAIL
    reason = "; ".join(dict.fromkeys(r["reason"] for r in valid if r["reason"]))[:500]
    return JudgeVerdict(passed=passed, reason=reason, raw_votes=list(valid))

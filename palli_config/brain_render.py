"""Every string a person reads from `palli_ctl brain` (spec §3.6).

Five rules, each with a test in tests/palli_config/test_brain_render.py:
no internal name in default output (INTERNAL_WORDS; `--verbose` adds them);
one plain-English name per lane and provider, read from the matrix; changes
shown as effects, never dot-paths; every refusal ends with the next move;
every lane accounted for. "What this means" is generated from the matrix,
never written by hand, so it cannot fall out of step with the change.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from core.llm_capabilities import BRAIN_LANE, LANES, PROVIDERS, Level, check_route
from core.settings_schema import PalliSettings
from palli_config.brain import ROUTING_LANES, Check, Picture, Target, resolve_target
from palli_config.ctl_prefix import ctl_prefix

INTERNAL_WORDS: tuple[str, ...] = (
    "openai_compat", "sdk_adapter", "local_openai", "narrative_author", "llm.routing", "LLM_", "_MODEL",
)

_NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}
_SUBSCRIPTION_PHRASE = {
    "claude": "counting against your Claude Max weekly quota",
    "codex": "counting against your ChatGPT quota",
}
_WRAP = 70


def _count(n: int, noun: str, lower: bool = False) -> str:
    word = _NUMBER_WORDS.get(n, str(n))
    if lower:
        word = word.lower()
    return f"{word} {noun}" if n == 1 else f"{word} {noun}s"


def provider_plain(provider: str) -> str:
    cap = PROVIDERS.get(provider)
    return cap.plain_name if cap else provider


def provider_cost(provider: str) -> str:
    cap = PROVIDERS.get(provider)
    return cap.cost if cap else ""


def cost_phrase(provider: str) -> str:
    cap = PROVIDERS.get(provider)
    if cap is None:
        return "an unknown cost"
    if cap.cost_kind == "per_token":
        return "costing money per token"
    if cap.cost_kind == "subscription":
        return _SUBSCRIPTION_PHRASE.get(provider, "counting against your subscription")
    return "running free on your desktop"


def model_display(provider: str, model: str, yaml_doc: Mapping[str, Any]) -> str:
    """The catalogue's display_name for (provider, model); failing that, ANY provider's entry for
    that id (F4, Ruling 13: her voice can end up running the OpenAI default model under a
    different provider — DEFECT_RECORD §22 — and it should still read as a name); else the id."""
    providers = (yaml_doc or {}).get("providers") or {}
    for entry in (providers.get(provider) or {}).get("models") or []:
        if str(entry.get("id", "")) == model:
            return str(entry.get("display_name") or model)
    for other in providers.values():
        for entry in (other or {}).get("models") or []:
            if str(entry.get("id", "")) == model:
                return str(entry.get("display_name") or model)
    return model or "(no model named)"


def _lane_short(lane: str) -> str:
    if lane == BRAIN_LANE:
        return "her voice"
    name = LANES[lane].plain_name
    return name[0].lower() + name[1:]


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"(])")


def _wrap(text: str, indent: str) -> str:
    """One sentence per line, never a break inside a sentence (Ruling 5, 2026-09-04):
    the words a person reads — and the tests assert on — stay whole."""
    sentences = [s.strip() for s in _SENTENCE_BREAK.split(str(text).strip()) if s.strip()]
    return "\n".join(f"{indent}{s}" for s in sentences)


def _pack_phrases(prefix: str, phrases: list[str], indent: str, width: int = _WRAP) -> str:
    """`prefix` then comma-separated phrases packed into lines of at most `width` columns,
    a hanging indent under the first phrase, and NEVER a break inside a phrase."""
    lines: list[str] = []
    hang = indent + " " * len(prefix)
    current = indent + prefix
    first_on_line = True
    for i, phrase in enumerate(phrases):
        piece = phrase + ("." if i == len(phrases) - 1 else ",")
        candidate = piece if first_on_line else f"{current} {piece}"
        if not first_on_line and len(candidate) > width:
            lines.append(current)
            current = hang + piece
        else:
            current = f"{current}{piece}" if first_on_line else candidate
        first_on_line = False
    lines.append(current)
    return "\n".join(lines)


def _hands_lines(picture: Picture) -> list[str]:
    """Can she act? That is a CAPABILITY answer, and a warning about which model she would run
    must never erase it (F5, Ruling 13) — so the hands verdict is read without the env, and the
    route's WARN, when there is one, gets its own line underneath."""
    brain = picture.brain
    hands = check_route(brain.provider, BRAIN_LANE)
    if hands.blocked:
        lines = [_wrap(f"✗ she cannot use her hands — {hands.reason}", "      ")]
    else:
        lines = ["      ✓ she can use her hands"]
    if brain.verdict.level is Level.WARN:
        lines.append(_wrap(f"⚠ {brain.verdict.reason}", "      "))
    return lines


def _cadence_line(picture: Picture) -> str | None:
    raw = picture.cadence_model
    if not raw:
        return None
    provider, _, model = raw.partition("::") if "::" in raw else (picture.brain.provider, "", raw)
    return f"the shape of her replies is judged by {model_display(provider, model, picture.yaml)} ({provider_plain(provider)})"


def render_show(picture: Picture, *, verbose: bool = False) -> str:
    brain = picture.brain
    out: list[str] = []
    out.append("")
    out.append("  Who is thinking for PaLLi right now")
    out.append("")
    out.append("  HER VOICE                                        set in .env.local")
    out.append("    The live conversation — and the only part of her")
    out.append("    that can pick up a tool and do something.")
    out.append("")
    model_text = model_display(brain.provider, brain.model, picture.yaml)
    if "LLM_RESPONSE_MODEL" not in picture.env:
        # F4 (Ruling 13): no model named for her voice, so the runtime runs the OpenAI default
        # (DEFECT_RECORD §22) — say so, or the model and the cost line contradict each other.
        # Only the KEY NAME is read here; no value from picture.env is ever printed.
        model_text = f"{model_text} (the OpenAI default — no model is named for her voice)"
    out.append(f"      {model_text:<26} {provider_cost(brain.provider)}")
    out.extend(_hands_lines(picture))
    cadence = _cadence_line(picture)
    if cadence:
        out.append(f"      {cadence}")
    out.append("")
    out.append("  EVERYTHING ELSE                             set in data/palli.yaml")
    out.append("    Runs between and after your messages. Changing these")
    out.append("    does not change how she sounds to you.")
    out.append("")
    for lane in ROUTING_LANES:
        if lane == BRAIN_LANE:
            continue
        state = picture.lanes[lane]
        out.append(
            f"      {state.plain_name:<32}{model_display(state.provider, state.model, picture.yaml):<24} "
            f"{provider_cost(state.provider)}"
        )
        if state.verdict.level is not Level.OK:
            out.append(_wrap(f"⚠ {state.verdict.reason}", "        "))
    out.append("")
    out.append(f"  Change it:  {ctl_prefix()} brain --pick")
    if verbose:
        out.append("")
        out.append("  Under the hood")
        for lane in ROUTING_LANES:
            state = picture.lanes[lane]
            dialect = PROVIDERS[state.provider].runner_dialect.value if state.provider in PROVIDERS else "?"
            out.append(f"    {lane:<18}{state.provider}::{state.model:<30}{state.source:<14}{dialect}")
        out.append(f"    llm.default_provider: {picture.default_provider or '(unset)'}   (default for lanes without a row)")
        out.append(f"    CADENCE_MODEL: {picture.cadence_model or '(unset — follows her voice)'}")
        own = PROVIDERS[brain.provider].model_env_key if brain.provider in PROVIDERS else "?"
        out.append(f"    env keys her voice reads: LLM_PROVIDER, LLM_RESPONSE_MODEL, {own}")
    out.append("")
    return "\n".join(out)


def what_this_means(picture: Picture, target: Target) -> list[str]:
    """Four deterministic sentences, each emitted only when it applies (spec §3.6)."""
    moving = target.moving(picture)
    background = [lane for lane in moving if lane != BRAIN_LANE]
    sentences: list[str] = []

    groups: dict[tuple[str, str], int] = {}
    for lane in background:
        old = cost_phrase(picture.lanes[lane].provider)
        new = cost_phrase(target.lanes[lane][0])
        if old != new:
            groups[(old, new)] = groups.get((old, new), 0) + 1
    for (old, new), n in groups.items():
        # Ruling 12 (2026-09-04): one lane stops and starts; several stop and start.
        verb_stop, verb_start = ("stops", "starts") if n == 1 else ("stop", "start")
        sentences.append(f"{_count(n, 'background lane')} {verb_stop} {old} and {verb_start} {new} instead.")

    # Final review F5 (plan finding 4): the background default is not inert — every caller of hers
    # without a lane of its own follows it, so a preset that moves it must say so out loud.
    new_dp = target.yaml_overrides.get("llm.default_provider")
    old_dp = picture.default_provider
    if new_dp and str(new_dp) != old_dp:
        was = provider_plain(old_dp) if old_dp else "(none set)"
        sentences.append(
            f"Anything of hers without a lane of its own follows the background default: "
            f"{was} → {provider_plain(str(new_dp))}."
        )

    brain_now = picture.brain
    if BRAIN_LANE in moving:
        provider, model = target.lanes[BRAIN_LANE]
        verdict = check_route(provider, BRAIN_LANE)
        if verdict.blocked:
            sentences.append(f"Her voice would move to {provider_plain(provider)} — and she would lose her hands: {verdict.reason}")
        else:
            sentences.append(f"Her voice moves to {provider_plain(provider)}. She keeps her hands.")
        sentences.append("How she sounds to you will change: a different model is writing her replies.")
    else:
        sentences.append(
            f"Her voice is untouched — she keeps her hands, and she keeps answering you on "
            f"{model_display(brain_now.provider, brain_now.model, picture.yaml)}."
        )
        sentences.append("Nothing about how she sounds to you changes. These lanes run between and after your messages.")

    for lane in background:
        provider = target.lanes[lane][0]
        verdict = check_route(provider, lane)
        if verdict.level is Level.WARN:
            sentences.append(f"{LANES[lane].plain_name}: {verdict.reason}")
    return sentences


def _check_line(check: Check) -> str:
    mark = {Level.OK: " ", Level.WARN: "⚠", Level.BLOCK: "✗"}[check.level]
    label = f"{check.label} ".ljust(32, ".")
    tail = check.detail if check.level is not Level.BLOCK else f"{check.detail}   (overridden with --force)"
    return f"    {mark} {label} {tail}"


def _files(target: Target) -> list[str]:
    files: list[str] = []
    if target.env_updates:
        files.append(".env.local")
    if target.yaml_overrides:
        files.append("data/palli.yaml")
    return files


def render_preview(picture: Picture, target: Target, checks: Iterable[Check], *, verbose: bool = False) -> str:
    checks = list(checks)
    moving = target.moving(picture)
    staying = target.staying(picture)
    out: list[str] = [""]
    if checks:
        out.append("  Checking...")
        out.extend(_check_line(c) for c in checks)
        out.append("")
    out.append("  What would change")
    out.append("")
    for lane in moving:
        before = picture.lanes[lane]
        provider, model = target.lanes[lane]
        out.append(
            f"    {LANES[lane].plain_name:<34}{model_display(before.provider, before.model, picture.yaml)}"
            f"  →  {model_display(provider, model, picture.yaml)}"
        )
    if staying:
        out.append("")
        out.append(_pack_phrases("Not changing: ", [_lane_short(l) for l in staying], "    "))
    out.append("")
    out.append(f"    Every one of her {_NUMBER_WORDS[len(ROUTING_LANES)].lower()} lanes is accounted for above.")
    out.append("")
    out.append("  What this means")
    out.append("")
    for sentence in what_this_means(picture, target):
        out.append(_wrap(sentence, "    "))
        out.append("")
    files = _files(target)
    noun = "file" if len(files) == 1 else "files"
    out.append(f"  Changing {len(files)} {noun}: {', '.join(files)}" + ("" if verbose else "            (--verbose for the paths)"))
    if verbose:
        for key, value in target.yaml_overrides.items():
            out.append(f"    {key}: {value}")
        for key, value in target.env_updates.items():
            out.append(f"    {key}={value}")
    out.append("")
    return "\n".join(out)


def render_refusal(picture: Picture, target: Target, blocks: Iterable[Check], *, alternative: Target | None = None) -> str:
    blocks = list(blocks)
    out: list[str] = ["", "  ✗ Not doing that.", ""]
    for block in blocks:
        lane = block.lane or BRAIN_LANE
        provider = target.lanes[lane][0]
        who = "HER VOICE" if lane == BRAIN_LANE else LANES[lane].plain_name
        out.append(f"    You asked to move {who} to {provider_plain(provider)}.")
        out.append("")
        out.append(_wrap(block.detail, "    "))
        out.append("")
    out.append("    Nothing was changed.")
    out.append("")
    out.append("    What you probably want:")
    first = blocks[0] if blocks else None
    if target.name != "pin" and alternative is not None and BRAIN_LANE not in alternative.moving(picture):
        n = len([l for l in alternative.moving(picture) if l != BRAIN_LANE])
        if n == 0:
            # F2 (Ruling 13): the alternative would move nothing, so it is not a next move.
            out.append("      Everything but her voice is already on that setup, and her voice cannot go there.")
            out.append(f"      Nothing to do here.  {ctl_prefix()} brain --pick  chooses a different setup.")
        else:
            out.append(f"      {ctl_prefix()} brain {target.name} --except response")
            out.append(f"        — move the {_count(n, 'background lane', lower=True)}; leave her voice where it is")
    elif first is not None and first.suggestion:
        lane = first.lane or BRAIN_LANE
        out.append(f"      {ctl_prefix()} brain --lane {lane} --to {first.suggestion}/<model>")
        out.append(f"        — {provider_plain(first.suggestion)} can do this lane")
    else:
        out.append(f"      {ctl_prefix()} brain --pick")
    out.append("")
    return "\n".join(out)


def render_applied(*, verbose_lines: Iterable[str] = ()) -> str:
    out = [
        "",
        "  Applied.",
        "",
        "  This takes effect when PaLLi next restarts. Until then she is still",
        "  running the old setup.",
        "",
        f"  Undo:  {ctl_prefix()} brain --undo",
    ]
    extra = list(verbose_lines)
    if extra:
        out.append("")
        out.extend(f"  {line}" for line in extra)
    out.append("")
    return "\n".join(out)


@dataclass(frozen=True)
class PickChoice:
    label: str
    preset: str
    except_lanes: tuple[str, ...]


def _background_majority(lanes: Mapping[str, tuple[str, str]]) -> tuple[str, int, int]:
    """(majority background provider, how many background lanes it holds, how many are elsewhere)."""
    background = [provider for lane, (provider, _) in lanes.items() if lane != BRAIN_LANE]
    counts = Counter(background)
    major, held = counts.most_common(1)[0]
    return major, held, len(background) - held


_MENU_WIDTH = 100
_MENU_INDENT = " " * 8


def _preset_summary(target: Target) -> tuple[str, str]:
    """Her voice first (the lane that matters), then the background where it differs, then the cost.

    F3 (Ruling 13): a majority vote across all ten lanes called `hybrid` free, when her voice on it
    is the most expensive provider PaLLi knows.

    Final review F2: the two halves come back separately, because the menu now prints each on its
    own line under the name — joined into one row they ran to 204 columns.
    """
    voice = target.lanes[BRAIN_LANE][0]
    major, held, elsewhere = _background_majority(target.lanes)
    providers = {provider for provider, _ in target.lanes.values()}
    if len(providers) == 1:
        head = f"Everything on {provider_plain(voice)}"
    elif voice == major:
        head = f"Mostly {provider_plain(voice)} ({_count(elsewhere, 'background lane', lower=True)} elsewhere)"
    else:
        head = f"{provider_plain(voice)} for her voice, {provider_plain(major)} for the background"
    cost = provider_cost(voice)
    if PROVIDERS[major].cost_kind != PROVIDERS[voice].cost_kind:
        cost = f"{cost}; the background {cost_phrase(major)}"
    return head, cost


def _cost_lines(cost: str) -> list[str]:
    """The cost, indented under its row. A mixed setup carries a second clause about the
    background; when the two together overrun the terminal the clause takes its own line —
    the break is at the semicolon, never inside a phrase (final review F2)."""
    if len(_MENU_INDENT) + len(cost) <= _MENU_WIDTH:
        return [f"{_MENU_INDENT}{cost}"]
    head, sep, tail = cost.partition("; ")
    return [f"{_MENU_INDENT}{head}", f"{_MENU_INDENT}{tail}"] if sep else [f"{_MENU_INDENT}{cost}"]


def render_pick_menu(picture: Picture, settings: PalliSettings) -> tuple[str, list[PickChoice]]:
    """One setup per block: the name he types on the numbered line, then what it is, then what it
    costs, then any note — each on its own line under it (final review F2). Packed onto one row
    the widest of these ran to 204 columns and led with the identifier instead of the English."""
    choices: list[PickChoice] = []
    rows: list[tuple[str, str, list[str]]] = []  # (number, the name he types, the lines under it)
    variants: list[tuple[str, Target]] = []
    today = {lane: picture.lanes[lane].pair for lane in ROUTING_LANES}

    for name in settings.presets:
        target = resolve_target(picture, settings, preset=name)
        head, cost = _preset_summary(target)
        under = [f"{_MENU_INDENT}{head}", *_cost_lines(cost)]
        if target.lanes == today:
            under.append(f"{_MENU_INDENT}← what she runs today")
        blocked = check_route(target.lanes[BRAIN_LANE][0], BRAIN_LANE).blocked
        if blocked:
            variants.append((name, target))
        choices.append(PickChoice(name, name, ()))
        rows.append((str(len(choices)), name, under))
        if blocked:
            under.append(f"{_MENU_INDENT}⚠ her voice can't move here yet — see {len(settings.presets) + len(variants)}")

    for name, target in variants:
        alt = resolve_target(picture, settings, preset=name, except_lanes=[BRAIN_LANE])
        major, held, elsewhere = _background_majority(alt.lanes)
        choices.append(PickChoice(f"{name}, voice unchanged", name, (BRAIN_LANE,)))
        note = f"Background {'on' if elsewhere == 0 else 'mostly on'} {provider_plain(major)}; her voice stays where it is"
        rows.append((str(len(choices)), f"{name}, voice unchanged", [f"{_MENU_INDENT}{note}"]))

    out: list[str] = ["", "  Pick a setup:", ""]
    for number, label, under in rows:
        out.append(f"  {number:>2}  {label}")
        out.extend(under)
    out.append("")
    out.append("   q  quit")
    out.append("")
    return "\n".join(out), choices

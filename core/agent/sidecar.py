"""Step 27 Phase B — the sidecar (spec §1, §4).

A sidecar is an ORDINARY full turn of hers (recall, moment, stakes, the
whole reply prompt) run on a child session key, with a read-only snapshot
of the parent run appended to its runtime context. Its exchange reaches the
parent transcript only through the parent's own writer: the bundle drain
at a model boundary, or the post-run sweep's transcription. One writer.
"""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

SNAPSHOT_HEAD = ("What you are in the middle of (as of a moment ago — you cannot "
                 "see further than this; the work continues on its own):")


def child_key(parent_key: str, n: int) -> str:
    return f"{parent_key}#sidecar-{int(n)}"


def parent_snapshot(parent_key: str, *, sessions_root: Path, max_messages: int = 6) -> str:
    """Render the parent's now_doing + its last messages, read-only.

    The parent's file is read directly (build-session correction 10): this
    is called from inside a running loop (dispatch) and must never re-enter
    it; nothing here writes."""
    from core.agent import steering as _st
    lines: list[str] = []
    doing = _st.get_now_doing(parent_key)
    if doing:
        lines.append(f"- right now: {doing}")
    try:
        from core.agent.session import Session, TextBlock, ToolUseBlock, _sanitize_session_key
        path = Path(sessions_root) / f"{_sanitize_session_key(parent_key)}.jsonl"
        sess = Session(parent_key, path)
        messages = sess._load() if path.exists() else []
        for m in messages[-max_messages:]:
            bits = []
            for b in m.content:
                if isinstance(b, TextBlock) and b.text.strip():
                    bits.append(b.text.strip()[:240])
                elif isinstance(b, ToolUseBlock):
                    bits.append(f"[tool: {b.name}]")
            if bits:
                lines.append(f"- {m.role}: " + " ".join(bits))
    except Exception as exc:  # noqa: BLE001 — a snapshot is a nicety, never a crash
        logger.warning("sidecar: snapshot read failed for %s (%s)", parent_key, exc)
    try:
        from core.agent import task_slots as _slots
        if len(_slots.held(parent_key)) >= 2:
            # Spec §4: results name their task when two are alive — her
            # wording, prompted here, never templated.
            lines.append("- two things are running on this channel — name which one a "
                         "result belongs to when you report it")
    except Exception:  # noqa: BLE001
        pass
    if not lines:
        return ""
    return SNAPSHOT_HEAD + "\n" + "\n".join(lines)


def wrap_context_builder(build: Callable[[Any], Awaitable[Any]], *, parent_key: str,
                         sessions_root: Path) -> Callable[[Any], Awaitable[Any]]:
    """The sidecar's context = the ordinary turn context + the parent snapshot."""
    async def _wrapped(turn: Any) -> Any:
        ctx = await build(turn)
        snap = parent_snapshot(parent_key, sessions_root=Path(sessions_root))
        if not snap:
            return ctx
        # The real TurnContext is frozen+slots: the snapshot rides its own
        # field and the context adapter folds it into runtime_text. A stand-in
        # that carries runtime_text directly gets the text appended. Never
        # setattr on a frozen instance — that killed her first live sidecar
        # (2026-09-09 10:50: replace() raised, the fallback raised too).
        names = {f.name for f in dataclasses.fields(ctx)} if dataclasses.is_dataclass(ctx) else set()
        if "sidecar_snapshot_text" in names:
            prior = str(getattr(ctx, "sidecar_snapshot_text", "") or "")
            return dataclasses.replace(ctx, sidecar_snapshot_text=(prior + "\n\n" + snap).strip())
        merged = (str(getattr(ctx, "runtime_text", "") or "") + "\n\n" + snap).strip()
        if "runtime_text" in names:
            return dataclasses.replace(ctx, runtime_text=merged)
        setattr(ctx, "runtime_text", merged)
        return ctx
    return _wrapped


# ── the launcher (Task 6) ───────────────────────────────────────────────────

import asyncio
from typing import Optional

_COUNTERS: dict[str, int] = {}


@dataclasses.dataclass
class SidecarHandle:
    """The running sidecar: its task, its child session key, its kind. Awaitable."""
    task: "asyncio.Task"
    key: str
    kind: str = "talk"

    def __await__(self):
        return self.task.__await__()

    def done(self) -> bool:
        return self.task.done()


def _with(obj: Any, **changes: Any) -> Any:
    """dataclasses.replace for dataclasses; a shallow copy + setattr for any
    other turn-shaped object. Never raises on shape."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.replace(obj, **changes)
    import copy
    clone = copy.copy(obj)
    for k, v in changes.items():
        setattr(clone, k, v)
    return clone


def next_n(parent_key: str) -> int:
    _COUNTERS[parent_key] = _COUNTERS.get(parent_key, 0) + 1
    return _COUNTERS[parent_key]


def reset_for_tests() -> None:
    _COUNTERS.clear()


def run_sidecar(*, parent_key: str, turn: Any, kind: str, dispatch, deliver,
                sessions_root: Path, on_done) -> Optional[SidecarHandle]:
    """Launch a sidecar turn for ``turn`` beside the run on ``parent_key``.

    Lanes (build-session correction 1): a TASK sidecar takes one of the
    channel's task slots (his call 2 — the cap); a TALK sidecar takes the
    channel's one talk lane and never counts against the cap. Returns None
    when its lane is busy (spec §4: the caller queues it as an ordinary
    steer bundle).

    The child transcript is seeded HERE with his words (correction 6) and
    the child turn keeps ``user_side_prerecorded=True`` — the client already
    recorded his message in the DB on arrival; dispatch must not write it
    twice. The reply is handed back through ``on_done(reply_text)`` — the
    caller makes it the bundle's reply_text so the PARENT writes the
    exchange at its own boundary. One writer."""
    from core.agent import task_slots
    kind = str(kind or "talk")
    n = next_n(parent_key)
    label = f"sidecar-{n}"
    if kind == "task":
        if not task_slots.acquire(parent_key, label):
            return None
    else:
        if not task_slots.talk_acquire(parent_key):
            return None
    ckey = child_key(parent_key, n)
    md = getattr(turn, "metadata", None)
    extra = dict(getattr(md, "extra", {}) or {})
    extra.update({"session_key_override": ckey, "sidecar_of": parent_key, "sidecar_kind": kind})
    md_kw: dict[str, Any] = {"extra": extra}
    if hasattr(md, "user_side_prerecorded"):
        md_kw["user_side_prerecorded"] = True
    child_md = _with(md, **md_kw)
    child_turn = _with(turn, metadata=child_md)

    def _release() -> None:
        if kind == "task":
            task_slots.release(parent_key, label)
        else:
            task_slots.talk_release(parent_key)

    async def _go() -> None:
        reply = ""
        quiet = False
        try:
            try:
                from core.agent.session import AgentMessage, Session, TextBlock
                from core.agent.speakers import speaker_of
                _text = str(getattr(turn, "content", "") or "").strip()
                if _text:
                    _child = await Session.open_or_create(ckey, root=Path(sessions_root))
                    await _child.append(AgentMessage(role="user", content=[TextBlock(text=_text)],
                                                     speaker=speaker_of(turn)))
            except Exception as exc:  # noqa: BLE001 — the seed is custody, never a crash
                logger.warning("sidecar %s: child seed failed (%s)", label, exc)
            result = await dispatch(child_turn)
            reply = str(getattr(result, "response_text", "") or "")
            # Batch 8, task 2a (ruling T2-3): her reply chose to stay quiet —
            # the bundle is answered, never re-asked (files she delivered
            # earlier in the run still go out, with no words).
            quiet = bool(getattr(result, "quiet", False))
            # Batch 8, task 10 (ruling T10-3): a voice note is a delivery too.
            if getattr(result, "responded", False) and (
                    reply or getattr(result, "attachments", ())
                    or getattr(result, "voice_notes", ())):
                await deliver(result)
        except Exception as exc:  # noqa: BLE001 — a sidecar failing never touches the parent
            logger.warning("sidecar %s on %s failed (%s)", label, parent_key, exc)
            reply = ""
        finally:
            _release()
            try:
                if quiet:
                    await on_done(reply, quiet=True)
                else:
                    await on_done(reply)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sidecar %s on_done failed (%s)", label, exc)
    return SidecarHandle(asyncio.create_task(_go()), ckey, kind)

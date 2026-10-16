"""The work note PaLLi reads when it talks mid-task (the owner, 10-12:
"palli is its own entity and should be able to converse and acknowledge
with whatever language it sees best fit"). Facts only, rendered when read."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent.work_progress import (
    RENDER_MAX_CHARS, WorkProgress, elapsed_words, recent_exchange, running_lines,
)


def _progress(**kw):
    base = dict(asked="clear the old backups", started_at=100.0, step=2,
                done="- local_shell(ls /backups) → four folders",
                running="- local_shell(rm -r /backups/old)",
                note="checking which ones are older than a week")
    base.update(kw)
    return WorkProgress(**base)


def test_render_names_the_ask_what_ran_and_what_is_running():
    text = _progress().render(now=172.0)
    assert "What they asked: clear the old backups" in text
    assert "Working on it for 1 min 12 s; 2 model steps so far." in text
    assert "Done so far:\n- local_shell(ls /backups) → four folders" in text
    assert "Running right now:\n- local_shell(rm -r /backups/old)" in text
    assert "My latest working note: checking which ones are older than a week" in text


def test_elapsed_time_is_read_when_rendered_not_when_set():
    p = _progress()
    assert "for 5 s;" in p.render(now=105.0)
    assert "for 3 min 0 s;" in p.render(now=280.0)
    assert elapsed_words(7322) == "2 h 2 min"


def test_between_steps_nothing_is_running():
    text = _progress(running="").render(now=110.0)
    assert "Running right now: nothing; between steps, deciding what comes next" in text


def test_a_long_run_cannot_flood_the_note():
    text = _progress(done="- x(y) → " + "z" * 20_000).render(now=110.0)
    assert len(text) <= RENDER_MAX_CHARS
    assert "Running right now:" in text


def test_a_long_run_keeps_the_newest_steps_and_what_is_running():
    """Fable's review (10-12): the cap used to cut from the end, so a long
    run lost the newest steps, what was running and the note first."""
    done = "\n".join(f"- local_shell(step {i}) → " + "r" * 380 for i in range(1, 13))
    text = _progress(done=done).render(now=110.0)
    assert len(text) <= RENDER_MAX_CHARS
    assert "- local_shell(step 12) →" in text                  # the newest step
    assert "Running right now:\n- local_shell(rm -r /backups/old)" in text
    assert "My latest working note:" in text
    assert "earlier steps not shown" in text and "- local_shell(step 1) →" not in text


def test_the_paused_note_is_one_line_without_durations():
    """Fable's review (10-12): a pause keeps a short note that stays true
    however long the pause lasts: what was asked and the last step done."""
    note = _progress(done="- local_shell(ls) → a\n- local_shell(rm old) → removed").paused_note()
    assert "\n" not in note and len(note) <= 500
    assert "clear the old backups" in note and "local_shell(rm old) → removed" in note
    assert " s;" not in note and "min" not in note and "Working on it" not in note


def test_running_lines_use_the_digest_format():
    @dataclass
    class TU:
        name: str
        input: Any

    assert running_lines([TU("local_shell", {"command": "ls"})]) == '- local_shell({"command": "ls"})'


def test_recent_exchange_labels_skips_tool_rows_and_keeps_the_last_six():
    from core.agent.session import AgentMessage, TextBlock, ToolUseBlock
    rows = [AgentMessage(role="user", content=[TextBlock(text=f"ask {i}")]) for i in range(5)]
    rows.append(AgentMessage(role="assistant",
                             content=[ToolUseBlock(id="t", name="local_shell", input={})]))
    rows.append(AgentMessage(role="assistant", content=[TextBlock(text="done that")]))
    rows.append(AgentMessage(role="user", content=[TextBlock(text="hi")], speaker={"kind": "guest"}))
    assert recent_exchange(rows).splitlines() == [
        "them: ask 1", "them: ask 2", "them: ask 3", "them: ask 4",
        "me: done that", "someone else: hi",
    ]


def test_steering_renders_the_note_when_read_and_forgets_it_with_the_run(monkeypatch):
    import core.agent.steering as steering
    key = "discord_text:wp"
    steering.set_work_progress(key, _progress(recent="them: can you clear them?"))
    monkeypatch.setattr(steering, "_now", lambda: 130.0)
    assert "for 30 s;" in steering.get_now_doing(key)
    assert steering.get_recent(key) == "them: can you clear them?"
    steering.clear_run_state(key)
    assert steering.get_now_doing(key) == ""
    assert steering.get_work_progress(key) is None and steering.get_recent(key) == ""

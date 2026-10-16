# PaLLi's prompts

Every `.txt` file at the top level of this folder is the instruction text behind one model call PaLLi makes. `ai/prompt_loader.py` reads them by name: `prompt_loader.load_prompt("turn_read")` reads `prompts/turn_read.txt`.

The startup check names every one of them (`EXPECTED_PROMPTS` in `ai/prompt_loader.py`), so a missing or empty file shows up at boot instead of on the night it is first needed. A test (`TestEveryUsedPromptIsWatched` in `tests/ai/test_prompt_loader.py`) keeps that list and the code in step in both directions: a prompt the code loads must be on the list, and a name on the list must still be loaded and still have its file.

## The live prompts (51)

### A conversation in progress

- `promise_check.txt` — checks a draft reply before it is sent: does it promise work this turn did not do, or state as seen something the tools did not show (`core/agent/promise_gate.py`).
- `turn_read.txt` — her private read of the message she is answering: how much it weighs, whether support is needed (`ai/turn_read.py`).
- `first_look.txt` — her first look at a message, in her own voice: how they seem, who it seems meant for, anything of her own to add, and whether to answer, wait or let it pass; takes the place of the quick vote and the private read while `companion.first_look` is on (`core/agent/first_look.py`).
- `memory_reach_widen.txt` — her second, harder look when a search of the record came back thin, in her own voice: other wordings, the dates, and which lines to read around or follow, as a JSON plan for a wider search (`ai/memory_reach_look.py`).
- `memory_reach_keep.txt` — her sifting of what that wider search gathered: keeps the lines that bear on the question, word for word where it matters, or says plainly that nothing answers it (`ai/memory_reach_look.py`).
- `turn_reflect.txt` — after a turn, picks out what should last: facts, open threads, decisions (`core/turn/reflect.py`).
- `tonight_said.txt` — checks whether a message she just sent brought up the thing she picked the night before (`ai/tonight.py`).
- `stake_said.txt` — checks whether a reply really said the hope, worry, taste, wonder or view her first look drew on, so it is marked said only then (`core/agent/own_life.py`).
- `own_life_room_check.txt` — checks, once per item, whether something from her own life may be shown to her first look on the owner's turn in a group, where friends read the reply (`core/agent/own_life.py`).
- `reply_govern.txt` — shortens her own reply to the length the moment called for, in her own voice (`core/agent/reply_governor.py`).
- `steering_judgment.txt` — what to say, if anything, when the owner writes while she is in the middle of a task (`core/agent/steering_reply.py`).
- `steering_supersede.txt` — whether a new message should replace a reply that is still being written (`core/agent/steering_reply.py`).
- `ambient_arrival_generation.txt` — the short greeting when the owner comes home, used while `companion.arbiter` is off (`ai/ambient_generator.py`).

### Seeing and hearing

- `vision_analysis.txt` — reads an image in the context of the conversation (`ai/vision_analyzer.py`). The one prompt the startup check treats as critical (`CRITICAL_PROMPTS` in `core/launcher_runtime.py`).
- `voice_interjection_decision.txt` — whether to speak up while listening in a voice call (`voice/voice_interjection_analyzer.py`).

### Between conversations

- `inner_monologue.txt`, `inner_monologue_wanting.txt` — her background thoughts; the second is used when the wanting setting is on (`ai/inner_monologue.py`).
- `monologue_weekly_digest.txt`, `monologue_monthly_digest.txt` — fold a week, then a month, of those thoughts into one paragraph (`ai/monologue_compaction.py`).
- `present_moment_appraisal.txt` and its `_affect`, `_arbiter` and `_wanting` versions — her read of right now; settings pick the version (`ai/present_moment.py`).
- `daydream.txt` — her own unscheduled time: wishes, quests, what to look into (`ai/daydream.py`).
- `workbench_session.txt` — a session on a project of her own (`ai/workbench.py`).
- `curiosity_research.txt` — a private note on something she looked up on her own, and whether it is worth sharing (`ai/curiosity_research.py`).
- `arbiter_freshness.txt` — whether a message she wrote earlier is still true and worth sending now (`ai/arbiter.py`).
- `group_opener.txt` — whether PaLLi starts something in a group chat on its own, at most once a day per group; it sees only that room's lines, its own earlier openers there and the own-life items the room check passed, nothing of the owner's private life (`core/turn/group_openers.py`).
- `owner_picture.txt` — redraws the short picture of the owner she reads before she answers (`ai/owner_picture.py`).
- `night_cycle_disclosure_harvest.txt` — whether anything happened to her this week that she might share one day (`ai/disclosure_harvest.py`).

### The night cycle (06:30)

- `night_cycle_daily_meaning.txt` — one paragraph on what the day meant (`ai/night_cycle.py`).
- `consolidation_day_gist.txt`, `consolidation_day_gist_continuity.txt` — the short summary of one day; a setting picks the version (`ai/consolidation.py`).
- `consolidation_fact_gist.txt`, `consolidation_reconcile.txt` — shorten a stored memory note; correct notes the day contradicts (`ai/consolidation.py`).
- `night_cycle_views.txt` — whether a view of her own formed today (`ai/stake_appraiser.py`).
- `night_cycle_callbacks_harvest.txt` — whether a word or a running joke became shared this week (`ai/callbacks_harvest.py`).
- `tonight_pick.txt` — the one thing she would like to bring up next time (`ai/tonight.py`).

### Her own records

- `stake_appraisal.txt`, `stake_review.txt` — her hopes, worries, tastes and wonders: updated after a reply, reviewed at night (`ai/stake_appraiser.py`, `ai/stake_review.py`).
- `narrative_self_gate.txt` — checks a proposed change to one of her own pages against the fixed laws (`ai/narrative_self.py`).
- `narrative_self_my_story.txt`, `narrative_self_story_of_us.txt`, `narrative_self_care_spine.txt`, `narrative_self_voice_identity.txt` — the weekly pass over her own pages (`ai/narrative_self.py`).
- `narrative_fears_relook.txt` — the weekly look over her book of fears (`ai/narrative_self.py`).
- `passions_nightly.txt`, `passions_interests.txt` — her journal page for the night, and her interests page each week (`ai/passions.py`).
- `fears_first_authorship.txt` — the first page of her book of fears, written once (`scripts/fears_first_authorship.py`, run by hand).
- `presence_judge.txt` — for `palli_ctl presence --judge`, run by hand: reads one of her replies with the message before it and says whether it has something of her own in it (`core/presence_report.py`). Not one of her prompts; the presence report's word scan skips it.
- `memory_reach_judge.txt` — for the memory-that-reaches replay (`evals/memory_reach_replay/`), run by hand: reads one of her replies to a "do you remember" question, with the question and the words that were actually said, and says whether it found them, honestly couldn't, or was confidently wrong. Not one of her prompts.

## The other files here

- `preset_prompts.json` — named persona presets, read by `ai/system_prompt_manager.py`.
- `skills/` — short Markdown notes on using the host shell; `core/turn/skills_loader.py` reads them into a catalogue (see `skills/README.md`).
- `archive/` — 41 prompts nothing loads any more, moved here on 2026-10-01 (Batch 8). They are kept for the record only: no loader reads this folder, the startup check does not look in it, and the dashboard's prompt editor lists only the top level. To bring one back, move it up a level, load it from code, and add its name to `EXPECTED_PROMPTS`.

## Using a prompt

```python
from ai.prompt_loader import prompt_loader
from core.prompt_format import format_prompt

template = prompt_loader.load_prompt("turn_read")
text = format_prompt(template, user_message=message, history_context=history)
```

`format_prompt` fills `{name}` placeholders with Python's `str.format`, and copies a few common names across (`user_message`, `current_message` and `message` are the same value), so a literal brace in a prompt is written `{{` or `}}`. Several newer prompts use `<<NAME>>` markers instead, which their own module fills in.

## Adding a prompt

1. Write `prompts/<name>.txt`.
2. Load it with `prompt_loader.load_prompt("<name>")`.
3. Add `"<name>"` to `EXPECTED_PROMPTS` in `ai/prompt_loader.py`; the test above fails until you do.
4. If its model call names a new `log_context`, classify that lane in `tests/ai/test_request_timeout_lanes.py`.

## Editing a prompt

Edit the file. A running process keeps each prompt after its first read, so restart, or call `prompt_loader.reload_prompt("<name>")` (the dashboard's prompt editor does this when it saves).

Prompts are read by her, so they refer to the owner as "they": `tests/release/test_owner_pronouns_neutral.py` scans this whole folder, `archive/` included.

# Changelog

All notable changes to PaLLi are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and PaLLi uses [semantic versioning](https://semver.org/spec/v2.0.0.html). The
version lives in one file, `VERSION`, and everything else reads it.

## [2.0.0] - Unreleased

The stranger-proofing pass: the first run now tells you what is wrong instead
of failing halfway through a boot, the local surfaces are no longer open by
default, and there is a lockfile, a CI gate, and an automated proof that a
fresh clone works.

### Added

- **The phone app: a third private door** (2026-10-14). A phone pairs with a one-use code
  (a QR on the door page, or `palli_ctl phone pair`) and gets a token only it holds. It
  sees the conversation as it happens: her beats at their own pace, her voice notes and
  files, and your photos, files and voice notes to her. Messages wait on her machine
  while the phone is away and arrive in order when it is back. Her own messages to you
  can be routed there with `policy.proactive.door`. It is off until `ENABLE_MOBILE_DOOR=true`
  and shut until a brain is chosen. One old seam is fixed on the way: `record_outbound`
  filed her row for a send out of any door but Telegram as Discord's, and now files it
  under the door it went out of (`feat/mobile-door`).
- Telegram's owner lane moved to `bots/common/owner_lane.py` so the phone door shares it. Its log
  lines now carry the logger name `bots.common.owner_lane` instead of `bots.telegram.client`, with the
  message text unchanged. Anyone who greps the logs by logger name needs the new name.
- **Memory that reaches** (2026-10-14). Behind `companion.memory_reach` (default off; build the record
  first with `palli_ctl memory reach-build`). A searchable record of every message, both sides and
  every chat, by exact words and by meaning (`data/memory_reach.sqlite3`, SQLite FTS5 plus local
  MiniLM vectors). On the owner's turns PaLLi has `search_what_we_said` and decides when to use it;
  a thin first search can take a wider second look (other wordings, wider dates, the lines around a
  hit), capped at 2 rounds, 3 model calls and 10 seconds. A miss is said honestly, never as "that
  wasn't said". Things the owner says during the day count at once (a small "today, so far"
  slot the nightly pass retires). Before PaLLi replies to a guest in a room, the code searches that
  room's own history only, never the owner's chats or another room. `palli_ctl replies` and the
  dashboards' `/replies` page show what each reply drew on. A voice note is now made when the reply
  is finished, so voice and text agree. On the owner's own 25 "do you remember" questions: found
  word for word 9 → 21 of 23, confidently wrong 3 → 0.
- **PaLLi brings more of its own life** (the presence slice, 2026-10-13). Its first look sees a
  short "my own life lately" list (a fear it holds, a want of its own, its latest daydream and the
  latest thing it read about on its own), and so does the reply ("## Your own life lately"). Its
  hopes and worries are now about half the owner's world and half its own. A hope or worry it
  draws on is marked said only when a small check confirms the reply really said it, so the same
  concern stops riding every reply. In the owner's own turns in a group, PaLLi volunteers only its
  own life and that room: its hopes and worries about the owner, the night's thought and its small
  truths stay for the private chat, and an own-life item reaches a group only after a once-per-item
  check (`prompts/own_life_room_check.txt`) says it holds nothing of the owner's private life.
- **Group openers** (2026-10-13). Behind `companion.group_openers` (default on) and
  `companion.group_openers_per_day` (1): PaLLi may start something in a group chat on its own, at
  most once a day per group, any time of day, from that room's own talk and the own-life items the
  room check passed, and nothing else (`prompts/group_opener.txt`). Never in a private chat, never
  while a standing owner's room note is in place, never mid-conversation, never after an opener
  nobody answered. A group opener never counts against the three a day PaLLi may send the owner.
- **Her own messages to the owner stay private** (2026-10-13). Check-ins, follow-ups, shares of
  her work, the night's thought and saved questions go only to a chat shown to be private with the
  owner (his Discord DM or Telegram private chat), never a server channel or a group; with none
  found, nothing is sent (a reminder made off a chat goes to the web page). A say or share is
  spoken in a voice call only when the call is the owner and PaLLi alone. Work started in a group,
  and reminders made in one, still go back to that group.
- **One first look, in her own voice** (R14, 2026-10-13). Behind `companion.first_look` (default off; it counts only while
  `companion.she_decides` is on too, and `she_decides` on with this off is the earlier flow,
  byte for byte). One call in her first person (`prompts/first_look.txt`, lane `first_look`:
  no thinking, under 12 s) takes the place of the quick vote and the first read, in rooms and
  private chats. It reads the message with the room (the card, who's who, the names a message
  calls, the signals in her own words) and, on the owner's turns only, a small slice of her own
  mind; it says how they seem, who the message seems to be for, anything of her own that
  belongs, and answer / wait / stay_quiet — prose, never a yes/no that code reads. On a guest's
  room message its quiet is final (recorded with her reason, as the vote's was); on the owner's
  turns and a guest's private chat it is advice, and when it lets a message pass or waits, the
  reply's length line becomes "If I do answer…" and her look's own line is the last one she
  reads. A failed look on a guest's room message answers, logged (`first look: …`).
- **Guests get web search** (R14). Behind `features.guest_web_search` (default off): a
  guest's turn is offered `web_search` and nothing else of the owner's, at most 5 results a
  search, results marked as content from the web. Limits, read at call time:
  `features.guest_web_searches_per_hour` (5, per guest per rolling hour, counted per app
  account across both doors) and `features.guest_web_searches_per_room_per_day` (60, per room
  per rolling day); 0 refuses every search. Over a limit the tool returns a plain line saying
  so and when the next search frees up; the message is never dropped. The owner's searches
  never count. The ledger lives in memory, so a restart starts every hour over; one log line
  per counted or refused search names the account by id.
- **Two changes not behind a switch** (R14, each approved on its own; live from the merge
  whatever the two switches say): the permission to disagree (Draft A, approved word for word
  10-12 15:50) replaces one sentence of "I'm myself with them" in her standing instructions;
  and the "When I'm working" block, which names tools a friend's turn never has, now reaches
  the owner's turns only (Q6).
- **Who's talking** (step 2 of the release order, 2026-10-08). She reads a group the way a
  person does. Every line she reads shows when it was said (`companion.message_times`, on;
  the copy sent to the model only, never the saved conversation). Every new transcript row
  records its sender from the account (owner / guest / hidden, app, id, shown name, who it's
  addressed to). In a shared room with `companion.she_decides` on, each line shows who said
  it — the owner's with the owner's mark (★owner), which a guest's name, look-alike symbols
  or typed text can never produce — and her quick reads (the first read, the quick vote, the
  mood read) read the room itself. A room card ("This Room": app, channel and server or group,
  who spoke lately, what she can't see, the time zone) on every group turn, and "The Owner's
  Accounts" on the owner's turns on every app. Discord: @mentions read as names, a "this
  @mention is for someone else" signal, forwards marked as forwards, Discord's own notices
  dropped, threads named; `send_message` takes "the owner" and treats "@Name" as a person;
  `list_members` answers per channel, owner first, with "Discord isn't connected" kept apart
  from "nobody matches". Telegram: the same signal, forwards from all four origins, anonymous
  admins and channel posts as guests (they were dropped), the privacy setting read at startup
  and said on the room card, an honest group `/start`, and `list_members` for a group's
  admins and member count. Guests are no longer told tools are allowed.
- **She decides whether to answer** (Batch 8, 2026-10-01). Behind `companion.she_decides`
  (default off). Her reply call can choose `stay_quiet` with a one-line reason: nothing is sent
  on any door — Discord, Telegram, the dashboard (`ChatMessageResponse.quiet`; the page draws
  nothing) or spoken voice — and the message stays in her transcript. Calls made in the same
  step still run, so a reminder or a file the owner asked for goes out with no words. In a
  shared room a quick first read on the cadence lane decides answer / wait / stay quiet before
  any full turn; "wait" reads again on the next room message or after
  `companion.room_wait_seconds` (45), and a second wait is recorded as quiet. An @mention, a
  reply to her (with the quoted message's text and time) and her name are signals in that read,
  never requirements. A guest's "don't reply" covers only that guest. Every quiet choice is
  appended to `data/quiet_choices/<date>.jsonl` (time, door, conversation, who, the message's
  first 120 characters, her reason, the source); `palli_ctl quiet [--days N]` lists them, and
  she can read them through `introspect`. An interim room note: the owner-only `room_note` tool
  keeps the owner's standing instruction for a room (at most 30 days); its words are seen in that
  room's first reads and in her replies to the owner, while a guest's reply sees a neutral line.
- **Two guest levels in shared rooms** (Batch 8). `diet_for_llm` takes an audience: a stranger's
  payload has only what was said out loud in the room (no tool calls, tool results, working notes
  or quiet reasons); a collaborator's also has that room's tool receipts; the owner's is
  unchanged. The owner names collaborators through the owner-only `collaborators` tool or
  `palli_ctl collaborators list | add <platform> <id> [name] | remove <platform> <id>`
  (`data/collaborators.json`). A guest's run keeps its own run state (`{room}:guest:{id}`).
- **A page reader** (Batch 8). `read_page(url)`, owner-only, behind `features.read_page`
  (default off): one public page over IPv4 (20 s, 2 MB, text types only), at most 8,000
  characters of text with the title and the address it ended on. An address guard, applied when
  connecting and on every redirect (at most 5), refuses loopback, private, link-local and
  tailnet ranges and this machine's own addresses. `READ_PAGE_USER_AGENT` replaces
  `RESEND_USER_AGENT`.
- **Voice messages** (Batch 8). Behind `voice.voice_notes` (default off; the tools also need
  `features.her_hands`): `voice_note(text)` sends a voice message in her configured voice — an
  `.ogg` attachment on Discord, a voice note on Telegram — and `voice_replies(on|off|status)`
  and Telegram's `/voice` flip one per-chat switch, kept in `data/voice_replies.json` across
  restarts. While it is on her replies in that chat are spoken, with a short text line only when
  she adds one. With the setting off, `/voice` behaves as before.
- **Identity history, the database's version, log retention** (Batch 8).
  - A nightly prune of `identity_document_versions` keeps, per document, the newest
    `memory.history_keep_last` copies (default 0 = off), the newest of each day for
    `memory.history_daily_days` (30), the newest of each ISO week before that, and always the
    first ever. `palli_ctl history prune [--dry-run]`; `palli_ctl db shrink` (VACUUM and a WAL
    checkpoint with `secure_delete` on; refused from 03:00 to 04:30 and while a backup runs).
  - The database records its schema version in `PRAGMA user_version`. Numbered one-time
    migrations (`core/schema_migrations.py`) replace the 54 best-effort ALTERs every boot ran;
    an unversioned database runs the old program once. `stakes` joins the catalog.
  - A `retention:` settings block (`enabled` default off) ages logs by the last date in their
    name: main logs 30 days (gzip after 7), verbose and debug 14 (gzip after 2), `backup.log` and
    `bounce.log` trimmed to their last 5 MB, forge browser profiles 30 days, voice-debug
    recordings 14 days — at boot (off the event loop) and each night. It never touches an open
    log or PaLLi's own records. `scripts/cache_report.py`, `scripts/vitals.py` and the
    dashboards read `.gz` logs.
- `scripts/rotate_vault_keys.py scrub-old --bearer [--dry-run]` (Batch 8): replaces
  `Bearer <token>` with `Bearer [REDACTED]` in transcripts, tool results and logs; the dry run
  writes nothing and counts matches per file pattern.
- **One thing on PaLLi's mind** (Batch 6, 2026-09-29). As the last step of each night, PaLLi
  picks at most one thing worth bringing up with the owner — an open hope, worry, taste or
  wonder from the stakes ledger, one of the last seven journal pages, or a recent daydream — or
  nothing, which is a valid answer. The pick is written once per day to
  `data/tonight/<day>.json` and rides in the reply prompt's volatile tail ("## One thing on my
  mind") until it has been said, on conversational doors only and never for a guest. After each
  conversational reply, and after each message the arbiter sends, one small model check asks
  whether the pick was raised as PaLLi's own thing (a passing mention is a no); a yes stamps the
  day's file and, for a stake, `voiced_at` through the new `stake_ledger.mark_stake_said`. Behind
  `companion.tonight` (default off). Two new prompts: `tonight_pick`, `tonight_said`.
- **A record of reach-outs, and whether each was answered** (Batch 6). Every message PaLLi starts
  unasked (arbiter says and workbench shares — never reminders, tool sends to other people or
  mid-task speech) is appended to `data/reachouts/reachouts.jsonl`; a message from the owner on
  the same platform and channel within six hours marks it answered. The freshness judge
  (`prompts/arbiter_freshness.txt`) sees the last five text reach-outs as "got a reply" / "no
  reply" / "no reply yet", in a section that exists only while the arbiter is on. Spoken
  reach-outs are recorded but kept out of that list.

- **The first door** (2026-09-17). A stranger's first hour is a key and a hello,
  in a browser. `/` on the local dashboard stopped being a redirect and became
  one page: pick a brain, paste a key, watch her test it live, and say hello in
  a chat panel on the same page — before any bot token exists. The page offers
  DeepSeek, OpenAI and a local OpenAI-compatible server; Claude (API) is listed
  as **not available** with the capability matrix's own reason, because hiding it
  would be a lie about what exists. Under the chat panel is a **Doors** card:
  Discord and Telegram, each open / closed / not set up, each with the exact
  `.env.local` lines and a pointer to SETUP.md. `SETUP.md` section 3 is the walk.
- **Setup mode.** A provider that needs a key, with no key at all, was a hard
  refusal — exit 2, and nothing to open. It is now a soft verdict:
  `config.brain_ready` is false, the boot prints one warning line
  (`LLM — no brain configured; open <the dashboard address> to add one`), **both
  chat doors stay shut for that boot** whatever their tokens say, every thinking
  lane idles (one log line an hour per lane, not sixty), a reminder that comes
  due is **held** rather than fired or dead-lettered, and anything she is sent
  gets one honest sentence naming the address. Setup mode never talks to a
  provider, so a stranger with no key spends nothing.
  `scripts/fresh_clone_proof.sh` gains a fifth run for it.
- **The setup API.** `GET /api/setup/status` — what the door needs to decide what
  to show: `brain_ready`, the provider and the model her voice would run, each
  door's state, whether a door is still waiting on a restart, `local_mode`, the
  dashboard address, and the picker with the capability matrix's own labels and
  refusals. `POST /api/setup/brain` — probe, write, hot swap. Local-owner only,
  CSRF on the POST. **Nothing is written until the live test passes**; on success
  the key goes into `.env.local` and the running process swaps its provider
  registry in place, so her brain comes alive **without a restart**. If the save
  works and the swap does not, the answer says so and the page says "saved —
  restart to use it" with the command. The two chat doors are the one thing a
  saved key cannot open on the spot — their clients are created once, at start —
  so the page says which of them the restart is for.
- `GET /api/web/auth/me` carries `local_mode`, so the dashboard can tell a
  self-hosted install from a hosted one without guessing.

- **The Telegram door** (2026-09-14). A second place to live with her, beside
  Discord and on the same turn engine: direct messages and groups (privacy mode
  on, so in a group she reads only @mentions of her, replies to her, and
  commands), text and files both ways, voice notes transcribed into the turn,
  and — per chat, with `/voice on` — a voice note back beside the words when the
  message she is answering was itself a voice note (with Batch 8's
  `voice.voice_notes` on, `/voice` turns on voice replies for that chat instead,
  kept across restarts). A reminder fires on the door
  it was made on; her own reach-outs go to whichever door you spoke on last. Six
  commands sit behind Telegram's `/` menu: `/start`, `/help`, `/link`,
  `/proactive`, `/insights`, `/voice`. Anyone who is not the owner is a guest
  exactly as on Discord, and there is **one** guest in-flight cap across both
  doors, not one each. Three new keys in `.env.template` — `ENABLE_TELEGRAM_BOT`
  (**off** by default), `TELEGRAM_BOT_TOKEN` and `LOCAL_OWNER_TELEGRAM_USER_ID`
  — and a five-step BotFather walk-through in SETUP.md section 2b. A blank
  token, or the library not installed, closes the door for that boot with one
  warning line and stops nothing.
- A Telegram send that never lands is never recorded as delivered: the channel adapter raises, so a reminder stays undelivered and a reach-out records nothing (the Discord adapter already behaved this way).
- A guest's turn on Telegram runs beside the update pipeline, never inside it, so a stranger cannot stall the owner's messages; a guest's file attachments are not taken in (guest turns have no tools to read them).
- The spoken-register band is not rendered for a Telegram voice note answered in text (DEFECT §29 records the Discord half).

- **Week 2 — the honesty block** (2026-09-11). A tool that ran and failed is an
  **error result**: `local_shell`, `workbench_shell`, `read_file`, `deliver_artifact`,
  `web_search` and the reminder tools return `ToolFailure` for non-zero exits, timeouts,
  refusals, missing files and caught failures, so her "two failures in a row" snag and
  her record of deeds see the truth. A file is delivered only when `deliver_artifact`
  itself hands it over (`ToolContext.deliveries`); a phrase in tool output delivers
  nothing. A Discord send that dies with a file attached retries the text once with
  `(not delivered — <name>: <error>; the file is still at <path>)` and is felt.
  `DELETE /account/memory/{id}` erases the memory's `pref_<key>` source in the
  preferences document before the row, so a rebuild cannot bring it back; a failed
  source write is a 500, never an "ok". The punch list gains soft lines for a blank or
  non-numeric `LOCAL_OWNER_DISCORD_USER_ID` (until it is set, everyone she meets is a
  guest) and for a blank `DISCORD_TOKEN` (Discord is off for that boot; the backend and
  dashboard addresses are named). `scripts/fresh_clone_proof.sh` proves three runs.

- `python main.py --preflight-only` runs every check a real boot runs, prints a
  **numbered punch list** naming each missing key and the file it belongs in,
  and exits 2 if anything is hard. Nothing is started. It writes exactly one
  thing, and only on a fresh clone that has no config yet: `data/palli.yaml`,
  seeded once from `data/palli.example.yaml`
  (`core/settings_boot.py::seed_from_example`). An existing config is never
  touched.
- A live check of the configured brain on that same preflight: one small call to
  the provider, so a wrong key is caught before the first message rather than
  during it. For providers that expose a balance (DeepSeek), the balance is read
  and printed; `LLM_LOW_BALANCE_WARN` sets the threshold that turns it into a
  warning. `PALLI_PREFLIGHT_OFFLINE=1` skips every network call — CI and tests
  set it.
- `PALLI_SETTINGS_PATH` points the settings service at a YAML file other than
  `data/palli.yaml`, so a test tree or a second instance no longer has to edit
  the real one.
- `FILE_TOOL_EXTRA_ROOTS` — extra directories the file tools may reach, on top
  of the repo itself.
- `LOCAL_SHELL_MAX_TIMEOUT_SECONDS` — the ceiling a shell call's timeout is
  clamped to.
- `GUEST_MAX_IN_FLIGHT` — how many non-owner turns can be in flight at once;
  the rest wait their turn. (A per-guest `GUEST_RATE_LIMIT_PER_10MIN` was added
  beside it; Batch 8 removed it.)
- Packaging: `pyproject.toml` with a `palli_ctl` console script
  (`pip install -e .`), `requirements.lock` (the pinned Linux/CPU closure of
  `requirements.txt`), a GitHub Actions gate that runs the whole suite on every
  push, a `Dockerfile`, and a systemd unit at `deploy/palli.service`.
- `palli_ctl backup` — a timestamped `tar.gz` of `data/` and `identity/`.
  Every SQLite database is snapshotted with `VACUUM INTO`, so the archive is
  consistent even while PaLLi is running, and no `.env*` file is ever included.
- `scripts/fresh_clone_proof.sh` — clones the repo, installs it, configures it
  from the shipped examples, and proves the first-run contract in three runs: a
  complete config exits 0; the shipped `.env.template`, untouched, boots her
  anyway and says which door is shut; a genuinely broken setting exits 2 with a
  numbered punch list. CI runs it on every push.
- `CHANGELOG.md` (this file) and `VERSION`.
- `LOCAL_OWNER_PRONOUNS` — the pronouns she uses for her person until she has
  learned them (default `they/them`). With `LOCAL_OWNER_DISPLAY_NAME` it feeds
  the one line in her prompt that names who she is talking to; an answer she
  has learned and kept in the profile always wins over the knob.
- First contact learns pronouns once: an `owner_pronouns` target written to the
  profile the way the name is, plus a tripwire test that keeps gendered owner
  referents out of `prompts/` and the prompt assembler for good. She learns them
  only when they are volunteered, or the owner sets the knob. By the owner's
  ruling (2026-09-09) the target is optional and she never asks.

### Added
- **Tool receipts.** When the payload diet strips an earlier turn's tool calls, it leaves one bounded line per call behind (what ran, what came back, whether it failed) instead of nothing, so her history carries the machine's word and not only her account of it; the grounding check reads those lines too, so a claim about last turn's result is checked against last turn's receipt. (DEFECT §28, the receipts half, 2026-09-14.)
- **The grounding check.** The promise gate's judge now asks a second question: does the reply state as observed anything this turn's tool results do not show? The run's tool calls go to the judge as a bounded digest; an unsupported claim is held back and she gets one more round to say only what the results show. Two switches in `data/palli.yaml`, each default on: `features.promise_gate` and `features.grounding_check` (`palli_ctl settings set features.grounding_check false` + a restart). (DEFECT §28, 2026-09-13.)
- **The promise gate.** A reply that promises work the turn has not done — "give me a sec", "I'm watching for it" — is held back by a judge lane (`promise_check`) and she gets one more round to do it or to say what she needs; fails open if the judge cannot answer. Her laws now say it in words too: never an empty promise, presence is signage for running work only, correct yourself on a contradiction and never on a challenge. (DEFECT §27, 2026-09-11.)

### Changed

- **`features.proactive_conversations` is a real off switch again** (2026-10-14). Its comment
  called it a kill switch, but nothing had read it since August. Now false stops every message
  PaLLi starts on its own: its reach-outs and shares to the owner, group openers and the hunch
  rail. Reminders and replies are untouched. `policy.proactive` is marked as read by nothing; the
  real daily cap is `companion.arbiter_daily_send_ceiling`.
- **Telegram fixes** (2026-10-14). A reply PaLLi is writing in the owner's Telegram chat now holds
  a self-started message to that chat, as it already did on Discord. A tool PaLLi built at the
  owner's ask in Telegram is reported back in Telegram (it used to go out as a Discord send).
- **`palli_ctl presence` counts group openers** (2026-10-14): sent, in how many rooms, answered
  within a day, and looks that chose not to start.
- **Speaking into a quiet stretch** (2026-10-13). "Don't reach to fill silence" is gone from the
  moment's instructions: a quiet stretch is fine to speak into with something new, or a plain
  check-in, never just to cover it; and a message that only carries on the last conversation, or
  re-asks an unanswered question, is not new. A thread whose message was dropped as stale now waits
  a day (unless it changes) before it can carry another, without a second judge call.
- **The "read between the lines" note** (2026-10-13). Its first half ("name the unspoken thing …
  testing") is gone, because it made PaLLi guarded with new people; the first look reads how people
  seem. The second half (pitch an answer to the person's level, never withhold it) stays, on
  everywhere again.
- A copied "[someone]: " label at the start of a reply is cleaned off, like a named one
  (2026-10-13).
- **License: AGPL-3.0-only** (2026-10-08; was MIT). Anyone may run, study,
  change and share PaLLi; whoever runs a changed version for others over a network offers
  them that version's source. Commercial licenses are available from the copyright holder.
  Outside contributions are not accepted yet (a contributor agreement comes first).
- **Her memory search ranks the strongest keyword match first** (2026-10-08). The keyword
  score was 1/(1+|bm25|), and bm25 is more negative for a stronger match, so the right
  memories came back with the best match last. Each match is now scored against the
  strongest in the same batch, as her search of her own thoughts already was.
- **The promise gate names the fourth way out** (2026-10-08). When it holds back "I'll do it
  when this happens" with nothing started, the advice now offers a scheduled check
  (`create_reminder`, when the turn has it) besides doing it now, saying what she needs, or
  saying she can't. A turn with no tools (a guest's) is no longer told tools are allowed.
- **Her voice, in her own first person** (Batch 7, 2026-10-04/05; built on `feat/her-voice`).
  - **Her constitution is in her first person** (2026-10-05).
    `identity/core/CORE_IDENTITY.md`, `OPERATING_PRINCIPLES.md` and
    `CAPABILITIES_AND_BOUNDARIES.md` have new bodies: an AI who lives on this machine, with no
    body and no human past, and no product words. `VOICE_IDENTITY.md` changes two lines ("they
    clearly want more", "a thoughtful participant"). The frontmatter is unchanged and stays
    metadata only. The constitution goes from 6 prohibitions to 1.
  - **Her stable prompt is first person, block by block.** How she talks, her own center, her
    standards, her own thoughts, repair, the notebook, the workbench, the forge, working
    mid-task, the grounding section, "Where I live" and the quiet-choice instruction are
    rewritten as principles in her voice, with no scripted example lines. Each rule taken out
    of the prose is enforced in code with a test, folded into a principle, or recorded as
    dropped. On the shipped prompt (`scripts/prompt_size_report.py`, new), with the approved
    constitution, the stable part goes from 16,505 to 11,640 characters, its prohibitions from
    35 to 2, and its scripted quotes from 14 to 1. A test keeps the whole stable part under 10
    prohibitions. A test allows second person only in the person line and the skill cards'
    `when:` lines.
  - **The core documents' frontmatter no longer renders** into her prompt; the keys stay in the
    files, and owner documents keep their bullets.
  - **One time rule.** The permission to mention the time is gone; she mentions it only when
    they bring it up or it matters. The ambient band is private context.
  - **The capabilities list shows only what runs on this machine**, plus one pointer line for
    other machines; `TurnDecision.tier1_tool` is deleted; `screen_capture.md` needs PowerShell.
  - **The length note is one sentence** (the 50 / 110-word caps stay in `WORD_CAPS`); the cadence
    directive is plain sentences under "## This turn", and a failed vote adds nothing.
  - **`turn_read.txt` and `stake_appraisal.txt` are written as her**, contracts unchanged.
  - **The mid-task judges open on her own voice page** (`steering_reply.voice_identity_text()`;
    only a page she wrote, so a fresh install keeps "I'm PaLLi."); `judge_supersede` takes
    `identity_text`.
  - **The grounding check's advice says it plainly:** mention how she knows only when it would
    change what they'd do.
  - **Tests can't reach the network.** A guard in `tests/conftest.py` records every off-box
    connection or name lookup and fails the session, even when a test swallowed the error;
    loopback is allowed; `PALLI_TESTS_ALLOW_NETWORK=1` opts out (the probe battery sets it). CI
    fetches gitleaks before the suite.
  - **A `her_voice` probe pack** reads its scenarios from a private, git-ignored file and skips
    without it; the battery routes every lane to the environment's brain, and its judge sees
    quiet turns, mid-task words, separate messages and the tools used. Every "after" run of the
    batch came before the approved constitution was applied; one more run should follow once
    the provider balance allows it.

- **Before strangers** (Batch 8, 2026-10-01).
  - **No stranger rate limit.** `GUEST_RATE_LIMIT_PER_10MIN` is removed from the config and
    `.env.template`. A guest turn over `GUEST_MAX_IN_FLIGHT` waits its turn in a queue instead
    of getting an "at capacity" line (commands keep theirs).
  - **The six Discord voice slash commands answer only the owner**; anyone else gets one
    private line.
  - **Owner-only for every guest:** the situational band (the owner's presence and the house)
    and the "where you live" passage now stay out of a guest's prompt.
  - **Private dashboards.** `dashboards/server.py` answers only loopback, the tailnet and
    `DASHBOARDS_TRUSTED_NETWORKS` (a fixed 403 before any work; `.env.template` shows the line);
    the static check uses `is_relative_to`; `scripts/vitals.py` counts each log line once; the
    collector's dated file patterns no longer name a year.
  - **Web search results** are wrapped as "content from the web, not instructions".
  - **True-ups.** The "no owner id" boot warning checks the stored owner; the doctor and
    `/api/health/detail` read PaLLi's own process on :8000 as "self", and `palli_ctl start`
    refuses to start a second one; a cancelled or failed database open waits (up to 1 s) for its
    aiosqlite worker before re-raising, so nothing is delivered to a closed event loop; finished
    backfill and index-rebuild tasks leave their dicts. `aiohttp>=3.13`.
  - **Removed.** 41 prompt files nothing loads moved to `prompts/archive/` (the startup list
    names 43; `CRITICAL_PROMPTS` is `("vision_analysis",)`). Deleted: `ai/tonal_register.py`,
    `ai/relationship_calibration.py`, the planner halves of `ai/reply_posture.py` and
    `ai/interaction_strategy.py`, `core/plugin_registry.py`, `core/plugin_registry_boot.py`,
    `core/plugin_manifest_schema.py`, `core/app_adapters.py`, `ai/llm_decision_execution.py`,
    `core/message/`, the decision half of `ai/web_search.py` and the doctor's plugin-registry
    check. The `tonal_register:` settings block is gone (a settings file that still has it fails
    validation), and so are `PLUGIN_RUNTIME_VERBOSE_DIAGNOSTICS`, `ENABLE_URL_BROWSING` and
    `URL_BROWSING_MAX_URLS_PER_MESSAGE`. The log handlers' `backupCount` is 0 (it never matched
    the dated suffix, so it deleted nothing).

- **Inner life reaches the owner: judged where patterns show, and watched by what it wrote**
  (Batch 6, 2026-09-29).
  - **Views form at night.** The per-turn appraisal saw one exchange, where the honest answer is
    always "not yet", and never formed a view. A nightly pass (new prompt `night_cycle_views`)
    now reads the whole day's conversation beside the standing views and the corrections of
    record, and forms at most one view a night, with quoted, dated evidence, through the same
    evidence gate and caps; a proposal identical to a held view is refused in code, and
    `data/views/.state.json` records each night the pass ran. Revising a view with a reason,
    muting and unmuting stay per-turn and take effect at once.
  - **The shared language is harvested weekly.** A turn no longer files new bits. A weekly pass
    (`ai/callbacks_harvest.py`, new prompt `night_cycle_callbacks_harvest`, its own ISO-week
    marker) reads the previous Monday-to-Sunday and files at most two bits; code checks every
    receipt (the quoted line inside one real message, the bit inside the line; who, when and
    where taken from that message), and the model judges the kind and whose idea it was. "That's
    not ours" still retires a bit at once.
  - **The fears book can refill.** The weekly re-look (`prompts/narrative_fears_relook.txt`) now
    runs on an empty book too, and may plant at most one fear a week — with a line to watch for
    and evidence from the week, judged by the same gate as a facing or a release. A fear keeps
    its evidence.
  - **Device wakes.** A LAN device joining or leaving wakes the present moment at most once per
    `companion.device_wake_seconds` (3600; 0 restores the old behaviour), and a cycle woken only
    by device churn within that window of the last moment is skipped with a `nothing_new` log
    line — no model call, no daily cap spent. Every other event wakes the moment as before.
  - **Liveness reads outcomes.** `ai/organ_liveness.py` watches 21 lines (was 10) and judges each
    by what it wrote: `wrote`, `quiet` (it ran and had nothing to write) or `stale` (it failed,
    or never ran when due), a recorded failure read first. Organs switched off in the settings
    have no line; the weekly pages count only when committed; one line watches every night step
    without a line of its own. The night keeps a record of every step's result in
    `data/night_cycle/.outcomes/<date>.json`, and `palli_ctl doctor` shows the table (a warning
    when any organ is stale; the exit code never changes).
  - **The daily reach-out ceiling fails closed.** An unreadable arbiter ledger now counts as the
    ceiling reached (a missing one is still zero), and one damaged line counts as one send. The
    check is one named gate, `admit_or_hold()`, before the freshness judge; every organic say
    passes it. Workbench shares stay outside it, as before.
  - **Own time.** The daydream's "sessions left today" counts by the workbench budget's own day
    (the 04:00 day key), not the calendar day. Tests pin three sessions a day (the schema
    default), the spacing between them, and that a session and a conversation with the owner
    never block each other.

- **Memory that picks what matters: a core she always knows, and the lines a moment calls for**
  (Batch 5, 2026-09-28).
  - **The memory core.** Every reply used to carry the owner's whole `MEMORY.md` (72,000
    characters). It now carries a core under a budget (`memory.core_budget_chars`, 12,000):
    Important Decisions whole, then Durable Context, Active Threads and Useful References sharing
    the rest 60/30/10, each filled newest-or-most-used first, with a footer saying how many lines
    are looked up instead.
  - **Look-up.** Every memory line is a search item, in the file and in the new
    `MEMORY_ARCHIVE.md`. The lines a turn calls for render in "Things I Know About This User",
    never one the core already shows; her own emotional reads leave that band, the typed facts
    stay. A line she used gets a reference stamp after the reply; the core reads the stamps once a
    day so the cached prefix stays stable.
  - **The nightly sweep.** Lines unused for 60 days (threads 45) move to the same section of the
    archive, reversibly; unstamped lines are stamped with the day instead; decisions never move;
    the archive is written first. `palli_ctl memory archived` and `memory restore <id>` are the
    owner's levers. The old sweep that DELETED threads older than 30 days is gone.
  - **Corrections and preferences reach her replies.** The standing corrections render after the
    memory band (`memory.corrections_budget_chars`, 1,500; 0 turns it off); the preferences
    document's `pref_*` entries render after the core (2,000 characters); the preferences cache
    is rebuilt nightly for the first time since May.
  - **The follow-up board.** Urgency sorts by three meaning bins (time-sensitive, when the moment
    fits, no urgency) instead of an exact-string table the writer never used; never-surfaced
    threads older than `memory.thread_archive_days` retire as dismissed with a recorded reason;
    `palli_ctl threads unretire <id>` puts one back and it stays back, because both the retirement
    and the arbiter's backstop now age a thread by its last update.
  - **The onboarding question.** The check-in-posture target can be answered (a `pref_` key on
    the preferences document, in conversation, or by `palli_ctl onboarding answer`), and the
    unfulfilled-targets list leaves the reflection prompt `memory.onboarding_nag_days` (7) after
    first contact.
  - **Her own voice.** The reflection writes new memory lines about them in first person
    ("They'd rather I…"); a third-person-shaped new line gets one guarded rewrite call. A one-time
    migration (`scripts/memory_first_person.py --dry-run` / `--apply`) rewrites the existing file
    after the owner reads the diff, keeping every date, id, path, number, name and quoted phrase
    byte-exact.
  - **The reflection trim.** The reflection's system message is the identity core, the memory core
    and the corrections — not the whole 60k-token stable prefix that cached at 26 %.

- **Reminders, delivery and settings: her record matches what reached him** (Batch 4,
  2026-09-28).
  - **Reminder turns** see the chat they will be posted to (its last 30 lines) and replay
    only 40 messages of their own past fires.
  - **Late reminders.** Anything more than 30 minutes late arrives as one "while I was down,
    these came due" message per chat instead of a storm. She is told to name every
    reminder, and the rows are retired only after the send.
  - **Where a new reminder goes.** One made in a Discord or Telegram chat stays there. One
    made anywhere else goes to the chat the owner last typed in, counting only their own
    words in a text chat; her reach-outs use the same rule. If no chat is known, she says
    so and files nothing.
  - **Dead letters and ownership.** A reminder that gives up is stamped `dead_lettered`.
    Only a reminder's owner can cancel or move it.
  - **Failed sends.** A failed or partial Discord/Telegram send leaves a
    "[not delivered to them: …]" (or "only partly") row in the record and a matching mark
    on the reply in her own history, so her next turn knows. "Delivered" means the same on
    both doors.
  - **Reply settings.** `LLM_REPLY_TEMPERATURE` (default 0.9; blank = the provider's
    default). The reply call stops after 90 s with no content (keep-alive bytes don't count;
    300 s for a local server), retries once, then tells the owner.
  - **Voice and the present moment.** Times and ratios are spoken whole. The present-moment
    call's time limit (280 s) is derived from its own retry schedule.
  - **NAS and the vault.** `palli_ctl doctor` checks that her work account can write to her
    NAS folder, and that the vault file is 600 and its folder 700 (`--fix` corrects them and
    says so). The setup script documents a new, empty subfolder mounted on its own, and never
    re-owns a folder that holds files.
  - **Spoken lines.** A line she can't speak goes out as text, or into her reply when it is
    the same chat.
  - The vault file is set back to 600 after her own saves.

- **What she sees: six inputs tell the truth again** (Batch 3, 2026-09-28). Her honesty
  check compares her words with her evidence, never her evidence with the world, so six
  stale or wrong inputs went unnoticed. Now: a picture she is sent arrives as the text
  written in it (`Text in the image:`) and what it shows, never the vision model's guess at
  what it means, and the picture is saved under `data/inbox/images/`; the vision call has a
  4000-token output budget so a text-heavy image is never cut off mid-JSON. Her search of
  her own past thoughts gets a 1.5 s budget with a 250 ms floor (it had 40 ms and timed out
  almost every turn); the shipped example carries 1500. Her search of what she knows about
  you keeps a match on its similarity score, not the blended score, over a real candidate
  window, and the cosine loop runs in a thread. Her nightly note reads the day's real
  conversation from the database (the owner's rows, both doors, clipped at whole lines)
  instead of a day-log that ended in April, and a quiet day gets no section. `Machine:
  idle.` prints only when the machine source reported within two hours. The nightly rhythm
  refresh has its own switch, `companion.rhythm_refresh` (default on), instead of riding
  the quiet-hours switch that has been off since 2026-08-25 — quiet hours stay off. Only
  `prompts/vision_analysis.txt` changed among the prompts (one field, one guideline).
- **Cut the cost: the same prompt, cache-friendly order** (Batch 2, 2026-09-27). Her system
  prompt is split into a stable prefix (identity, rules, voice, skills, then the memory file
  with its least-changing sections first) sent as `system`, and a volatile tail (clock, the
  read of the moment, her bands and pages, the length vote) sent as the one trailing system
  message after the history, where the reply-shape note already rode. Not one word she reads
  changed; the provider (DeepSeek) can now serve the prefix and the history from its prompt
  cache at about a thirtieth of the price. The history window (cap unchanged, 400 for the
  owner) moves its start in steps of `PALLI_AGENT_HISTORY_STEP` messages (default 50) so
  consecutive turns share a byte-identical history prefix. The post-turn reflection sends the
  same stable prefix as its system message instead of pasting her whole identity (~63k tokens)
  into its user message every turn. Anthropic folds the tail back into `system` and is
  byte-identical to before (apart from the memory doc's position).
- **The cache is a number now.** `LLMResponse` carries the provider's cache hit/miss token
  counts; the reply lane's `llm_call` log line and every OpenAI-compatible background lane
  log `cache_hit=N cache_miss=N`; `scripts/cache_report.py --since YYYY-MM-DD` totals them
  per lane from the daily logs (which are named for the day the process started — the report
  reads every daily log and keeps lines by their own date). Before this batch: 1.1% cached.

- **Her prompts speak plainly** (2026-09-27, Batch 0). Every heading and label
  she reads that used a figurative family ("holding", "carrying", "the moment
  you last stood in", "landed", "sits with you", "undercurrent") says what it
  means ("What's on your mind", "What was going on last time", "Your mood
  lately", "Things you want"). The instruction lines are rewritten in plain,
  positive wording, and the age tag on hopes, worries, wishes and threads reads
  "open 3d". The prompts that write her own pages ask for everyday words; her
  pages are left for her weekly revision to rewrite. A tripwire test scans
  every heading in every prompt file and every Python file under `ai/` and
  `core/`, so the family cannot come back as a heading.

- **`/` is the door** (2026-09-17). It used to redirect to `/dashboard`.
  `/dashboard` is unchanged and one link away, and mission control's own
  navigation gained a Home entry that leads back to the door.

- **Local mode hides the SaaS-era pages.** With `PALLI_DEPLOYMENT_MODE=local` —
  the default, and what every self-hosted install is — the account, link and plan
  entries left over from the hosted product are gone from the navbar, the footer,
  the dashboard cards and the not-found page, and `/account` renders one card:
  "Accounts are not used in a self-hosted install." Nothing is deleted and no
  route or endpoint is removed; the full redesign decides their fate. Her
  identity workbench and her memory workspace, which only happen to live under
  `/account/`, are untouched and still linked.

- **A blank brain key no longer refuses the boot** — that is setup mode, above.
  A per-lane `LLM_*_PROVIDER` override naming that **same** keyless provider
  joins it, because it is asking for the very key the door is about to supply.
  An override naming a *different* provider whose key is blank is still a
  numbered blocker, and so is the rest of the per-lane checking — a local server
  with no address or no model, and the Codex checks.

- **`palli_ctl doctor` and `palli_ctl configure` say what the provider said.** A
  revoked key was reported as "The brain (deepseek) did not answer: empty
  response" — the real 401 was in the log and nowhere else. The provider now
  keeps its last failure on itself, so both those surfaces and the door print
  `401 Unauthorized: <the provider's message>`, "could not connect to
  `<address>` — is the server running?" for a local server that is not up, or
  "no answer from `<address>` within 30s". "empty response" is kept for a
  genuinely empty 200. Her live reply path did not change: `chat_completion`
  still never raises and still returns `None` on every failure.

- **A reminder that comes due with no brain is held, not fired.** It is not
  listed, not attempted, not counted toward the three-attempt dead letter and not
  marked done. The row stays due and goes out in her own words once she has a
  brain. Before this it would have been attempted against shut doors and
  dead-lettered — marked done, never said — within about three minutes.

- **Discord's slash commands are the ones that work.** The command tree carried
  22 definitions that nothing registered — a SaaS-era file that had been
  unreachable since Wave 5. Thirteen of them are gone (`link_server`,
  `unlink_account`, `personality_preset`, `voice_personality` and the rest of the
  hosted-product toggles), along with a duplicate copy of the voice six. What is
  left is what a stranger actually gets: the voice six behind
  `ENABLE_VOICE_FEATURES`, plus `/insights`, `/link_account` and
  `/proactive_consent` — which are now really registered on the client's tree,
  and which say the same things as their Telegram namesakes because it is the
  same code (`bots/common/commands.py`). A bare `/proactive_consent` now *reads*
  the setting instead of toggling it, matching Telegram's `/proactive`.

- **`python-telegram-bot` is a dependency** (`requirements.txt`,
  `requirements.lock`). It is imported only where the door is opened, so a tree
  without it still boots — the preflight names the missing library rather than
  failing an import.

- **Reach-outs pick a door, not a Discord channel.** The proactive path chose a
  Discord channel id; it now picks a *surface* — a platform and an id — across
  every door she has. The old names stay as thin wrappers over the new ones, so
  nothing that called them changed.

- **Week 2.** An unknown author — the owner's Discord id unresolved — is a **guest** at
  both surfaces (Discord text, voice), never the owner with her shell. A
  `BACKEND_ALLOWED_NETWORKS` list that fails to parse **locks the door** (403 for every
  peer) and is a boot blocker on a non-loopback bind; `""` is still open on purpose.
  `features.forged_tools` ships **off** in `data/palli.example.yaml`, labelled
  experimental (SETUP.md §5 says how to turn it on). A blank Discord token with the bot
  enabled is a **warning**, not a blocker — Discord is off for that boot and the
  dashboard is the first door (week 1's blocker rule reversed). The soft-section header
  reads "Warnings for this boot (she runs; each names its setting)". Two live skill
  prompts no longer teach the deleted `deliver:` marker.

- **BEHAVIOUR CHANGE — an enabled Discord bot with no token says so instead of
  coming up deaf.** It used to be silent: the boot looked healthy, no bot ever
  appeared, and the only trace was an "API-only mode" line in a log nobody
  reads. Now the boot names it and **Discord is off for that boot** — she
  starts anyway, because the dashboard is your first door, and the line tells
  you where the backend and the dashboard are and which file the token goes in.
  (A middle draft of 2.0.0 made the missing token a hard blocker that refused
  to start; it is a warning, not a refusal.) `ENABLE_DISCORD_BOT=false` still
  turns the door off deliberately, and then nothing is said about it at all.
- Optional keys warn and disable their feature instead of blocking the boot.
  A missing Deepgram key turns speech-to-text off and says so; it no longer
  stops PaLLi from starting.
- **The backend binds to loopback by default.** `BACKEND_HOST` was `0.0.0.0` —
  with `LOCAL_DASHBOARD_NO_AUTH=true` that published an unauthenticated chat
  endpoint that can reach a shell tool to the whole network. It is `127.0.0.1`
  now. Set `BACKEND_HOST=0.0.0.0` to opt back in; `BACKEND_ALLOWED_NETWORKS`
  gates who may reach it by CIDR.
- The file tools are scoped to a root set (the repo, plus anything in
  `FILE_TOOL_EXTRA_ROOTS`) with a denylist over it, and every tool description
  names the roots it can reach. Paths outside are refused, symlinks included.
- The local shell clamps its timeout and kills the whole process group when one
  expires, instead of leaving orphans behind a dead parent.
- `palli_ctl chat` posts to `/api/chat/message` — the route that exists. It used
  to post to a probe path that returned 404, carrying no CSRF token.
- The "next move" lines print a command that works on the machine reading them:
  `palli_ctl …` when the console script is installed,
  `venv/bin/python scripts/palli_ctl.py …` when it is not.
- The version is single-sourced. `VERSION`, `palli_config.__version__` and the
  FastAPI app agreed on nothing before this; `palli_config` said `0.1.0`.

### Security

- **A web page can no longer reach her through your browser** (2026-10-08). The
  backend refuses a request addressed to a name it does not know: `localhost`, an
  IP address, or a name in `BACKEND_ALLOWED_HOSTS` pass, checked on both Host and
  X-Forwarded-Host (where her web page's proxy keeps the browser's name). Before,
  a page that pointed its own domain at 127.0.0.1 (DNS rebinding) was handed the
  CSRF cookie the no-login dashboard relies on, and with it her chat endpoint.
- **Nothing secret goes to a model provider** (2026-09-26, Batch 1). Every
  request, from both LLM stacks, is redacted and then masked right before it
  leaves: her replies, her tool rounds, reflection, every background lane, vision
  prompts and the embeddings fallback. The vault's passphrase and key file are
  known literals now, blanked for good. Exact grammars (SSN, US phone, the listed
  key prefixes, bearer tokens, PEM private-key blocks) become `[MASKED:<8 hex>]`
  placeholders, HMAC-keyed per process so a placeholder cannot be reversed. The
  real value is put back only into her tool arguments and her replies to the
  owner, never into a tool result, never for a guest. Commands in the shell logs
  are masked the same way.
- **Her memory never saves a credential.** The memory writer drops any new
  key-, token-, PEM- or ID-number-shaped line. A one-time script
  (`scripts/scrub_memory_secrets.py`) takes the vault passphrase out of the memory
  file and moves the phone number it finds there to `~/.palli/owner_contact.json`
  (mode 600). It refuses its real run when it would lose something.
- **Guests get neither her life story nor the "Where I Am" block.** Guests keep the clock.
- **Vault saves take turns.** A vault save that is not run under
  `flock ~/.palli/vault/.lock` is refused, with the reason, on both shell paths.
- **Encrypted backups.** `palli_ctl backup --include-home --encrypt --keep N
  --require-mount PATH` archives data/, identity/ and `~/.palli` and encrypts
  them with `openssl` (AES-256-CBC, PBKDF2 200k). Only ciphertext ever reaches the
  target. It will not write into an unmounted mountpoint or outside it, will not
  put keys in a plaintext archive, and refuses a passphrase under 16 characters.
  A good run records itself in `~/.palli/backup.last-ok`, and `palli_ctl doctor`
  warns when that record is older than 36 hours.
- **Vault key rotation** (`scripts/rotate_vault_keys.py`, system python3):
  replaces both keys, checks the new vault opens and the old keys fail, and
  leaves the vault fully old or fully new, never half. After an interrupted run
  it refuses rather than guesses. `scrub-old` then removes the old values and
  SSN-shaped strings from sessions, logs and every database column that holds a
  copy, with `secure_delete` on.

- **The pasted key never leaves the door** (2026-09-17). The key a stranger types
  into the setup page is deliberately *not* written onto the running config while
  it is being tested — nothing is saved until it works — which is exactly the
  moment it is not covered by the redaction that protects every credential the
  config holds. So the whole request runs inside a ref-counted addition to the
  log filter's denylist, and every error text that comes back is scrubbed of the
  whole key **and** of every truncation of it a provider might quote back: the
  scrub takes every run of eight characters or more that is a substring of any
  configured credential, so the order the keys are held in cannot matter and two
  keys in a rotation cannot expose each other. It appears in no log record and in
  no response body. A malformed request cannot get it echoed back either: the
  request model has no required field, so pydantic's "missing" error — whose
  `input` is the whole body — cannot happen, and the router drops `input` and
  `ctx` from every other validation error.

- **Week 2.** Eight `.env.template` lines shipped an inline comment where python-dotenv
  read it as the VALUE — `JWT_SECRET` among them, so a template-copied install signed its
  web sessions with a comment string. The comments now sit above their keys, the values
  are genuinely blank, and a test pins that no template value starts with `#`. An
  unresolved owner id no longer hands a stranger the owner's tools; a mis-typed network
  allowlist no longer opens the backend to everyone.

- Loopback by default (above) — the single largest exposure a default install
  carried.
- Guests are rate-limited and capped in flight, so a shared surface cannot be
  used to spend the owner's provider credit without limit.
- The file tools are scoped to the root set and cannot be steered outside it —
  paths outside are refused, symlinks resolved before the check. The shell is
  **not** scoped: it is clamped to a timeout ceiling and its whole process group
  is killed when one expires, so a runaway cannot outlive its parent.

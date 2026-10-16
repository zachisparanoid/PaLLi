# PaLLi

**PaLLi** · /ˈpɑː.li/ · *PAH-lee* - said "Polly", like the bird. *Polly want a cracker.*

> A private, persistent AI companion that lives on your machine and does real work - it reads
> your files, runs shell commands, keeps your reminders, hands you documents and searches the
> web - in Discord or Telegram, in text and voice.

**What it does.** PaLLi runs an agent loop over real tools, on your own hardware rather than in a
sandbox: read a file, run a shell command on the machine it lives on, hand you a result as a
document, search the web, set and reschedule reminders that actually fire, write private notes,
and read back over its own record when a question turns on the past. Where no tool on hand covers
the job, PaLLi can **forge one** - writes it, tests it, installs it onto itself, and the answer
follows on the same channel. It speaks in Discord and Telegram, by text and by voice, and a voice
turn runs the same recall and the same tools as text. A command that could do damage stops and
asks you first; that guard is not a setting.

**What it remembers.** Not "has a long context window" - remembers: a memory that survives
restarts, where each fact carries a weight for how much it mattered and that weight fades unless
something renews it (decaying, salience-weighted); a nightly pass that decides what the day
meant; and a self-story PaLLi writes and revises in words of its own. There are also things
that belong to PaLLi alone: hopes and worries about the conversation, small truths lived through,
opinions defended and revised, and words only the two of you use. None of that is seeded.
It fills from what actually happens between you.

> **An honest note:** most of that inner life is quiet in conversation for now. It affects what
> PaLLi notices and remembers more than what it says out loud. Bringing more of it into
> conversation is the next thing being built.

PaLLi runs on your hardware, and you choose the brain. With a model server on your own machine
(Ollama, LM Studio, vLLM) nothing leaves it. With a cloud API key, every model call - your
messages and the memory drawn on to answer them - goes to that one provider and nowhere else.
Three optional services send to their own providers, and only once you turn them on: web search
(Exa), hosted voice transcription (Deepgram) and hosted text-to-speech (Lemonfox). As shipped,
search is off and voice uses offline transcription and the system voice. Your conversations, the
memory and the journals live in a SQLite file and a handful of markdown documents in this
directory.

**New here? [`SETUP.md`](SETUP.md) is the long version of everything below.**

---

## Quick start

You need **Linux**, **Python 3.13**, **Node.js 20 or newer and npm** (your first hour happens on
a page in your browser, and that page is the local dashboard), and somewhere for PaLLi to think -
a model server on your own machine such as [Ollama](https://ollama.com), which needs no account
and no key, or a cloud API key you paste into that page. Optional: a Discord or Telegram bot
token for the surface you will probably live on, `ffmpeg` for voice.

```bash
git clone https://github.com/zachisparanoid/palli.git palli
cd palli
python3.13 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.template .env
venv/bin/python scripts/palli_ctl.py prepare
venv/bin/python scripts/palli_ctl.py start
```

**That is the first hour: install, run `prepare` and `start`, and open the address `start`
prints** (`http://localhost:3000` unless you moved it). No brain is chosen yet. A fresh clone
boots into *setup mode* - the process starts, stays quiet, spends nothing - and the page opens
on the step that asks for one. Pick a cloud provider and paste a key, or name a model server
running on your own machine. The page makes one small live call to it in front of you and saves
the choice only if it answers; the running process swaps its provider in place, with no restart.
What that step sets is **the voice** - the conversation you are having. The other thinking lanes
follow the routing in `data/palli.yaml`, which on a fresh clone points at a local model server
until `palli_ctl brain` moves them ([`SETUP.md`](SETUP.md#4-providers-and-keys)); if you chose a
cloud key and run no local server, move them the same evening. Either way the first thing you
say happens in the chat panel on that page, before any bot token exists.

The long version of all of it is [`SETUP.md`](SETUP.md#3-your-first-hour).

### If you already run Ollama

A machine that already runs a local model server needs no key at all: on the door, choose the
local-server row and give it the address. The background lanes already point there. Pull the two
models `data/palli.example.yaml` names - or any pair you already have, and move the lanes onto
them with `palli_ctl brain` (see [`SETUP.md`](SETUP.md#4-providers-and-keys)):

```bash
ollama pull qwen3.5:9b
ollama pull gemma3:12b
```

`qwen3.5:9b` runs the nine text lanes and `gemma3:12b` is the vision lane. Both are only what
the shipped config happens to name; nothing here depends on those exact tags.

### Other ways in

If you would rather answer questions in a terminal than click, `onboard` is the older path and
still works. It walks through the model routing, your local account, channels, voice, memory
and runtime defaults, writes them to `.env.local`, and offers to start PaLLi:

```bash
venv/bin/python scripts/palli_ctl.py onboard
```

To move the lanes onto something else later - `--pick` lists the setups in plain English, and
naming one moves every lane at once, writing both `.env.local` and `data/palli.yaml`:

```bash
venv/bin/python scripts/palli_ctl.py brain --pick
venv/bin/python scripts/palli_ctl.py brain deepseek-primary
```

To check the environment before anything starts, or to run PaLLi without the dashboard at all:

```bash
venv/bin/python main.py --preflight-only
venv/bin/python scripts/palli_ctl.py doctor
venv/bin/python main.py
```

`--preflight-only` runs every check a real start runs and starts nothing. Anything optional it
cannot find - a Discord or Telegram token, a Deepgram key, web search - it names, turns off
for that boot, and lets the start go ahead anyway. A cloud brain with **no key at all** is one
of those: PaLLi starts, says there is no brain yet, and names the address where you add one.
Only something PaLLi truly cannot run without stops the start, and then it prints a numbered
list of what to fix and where.

`main.py` runs the FastAPI backend on `:8000`, plus the Discord and Telegram clients in the
same event loop - each one once its own token is set, and Telegram also needs
`ENABLE_TELEGRAM_BOT=true`, which ships false. It serves no web page: the door and the rest of
the dashboard are the Next.js frontend, which is what `palli_ctl start` adds. `onboard` stops
short of starting while that frontend has never been built, because that is a separate step;
`doctor` will warn about the same thing - a warning, not a failure.

**The door is the first hour, and it needs no bot at all.** `palli_ctl start` runs the backend
**and** the Next.js frontend as managed background services (it requires Node and npm), and `/`
on the frontend is the door: the brain, a chat panel wired to the same turn engine Discord
uses, and a card showing the state of both day-to-day doors. `/dashboard` is mission control,
unchanged, one link away. `palli_ctl chat` is the same conversation from your terminal, if you
would rather not open a browser.

```bash
venv/bin/python scripts/palli_ctl.py start
venv/bin/python scripts/palli_ctl.py status
venv/bin/python scripts/palli_ctl.py chat
venv/bin/python scripts/palli_ctl.py logs
venv/bin/python scripts/palli_ctl.py stop
```

Discord and Telegram are the day-to-day doors - where you will actually live with PaLLi, on
your phone and in a channel you already have open. When you want Discord, put the token in
`.env.local` as `DISCORD_TOKEN=` (see [`SETUP.md`](SETUP.md#2a-the-discord-application) for
creating the application and turning on the three privileged intents the bot needs) and
restart. Telegram is quicker - `/newbot` to [@BotFather](https://t.me/BotFather), then
`TELEGRAM_BOT_TOKEN=` and `ENABLE_TELEGRAM_BOT=true` in the same file
(see [`SETUP.md`](SETUP.md#2b-the-telegram-bot-optional)). Either door, both doors, or
neither: each is optional and off without its token - and both stay shut until a brain is
chosen, so if you set a token before that, one restart afterwards opens it. A third door, the
phone app, has no bot at all: switch it on with ENABLE_MOBILE_DOOR=true and pair a phone from
the door page (see [SETUP.md](SETUP.md#2c-the-phone-app-optional)).

---

## Words used here

> **About "she":** you'll sometimes see PaLLi called "she", in the demo and in some of PaLLi's
> own writing, because that's how the author's PaLLi came to speak about itself. This README just
> says PaLLi. Yours will find its own words.

- **The door** - the first page of the local dashboard, at `/` (`http://localhost:3000`): where
  you choose a brain, say hello in a chat panel, and see whether the two chat doors are open.
- **The day-to-day doors** - Discord and Telegram, the chat apps where you live with PaLLi after
  the first hour. Each opens with its own bot token.
- **A brain** - the model that thinks for PaLLi, and the provider serving it: in the cloud
  (DeepSeek, OpenAI) or on your own machine (Ollama, LM Studio, vLLM).
- **A turn** - one message in, one reply out, and everything that happens in between.
- **Lanes** - the ten named jobs that each call a model: `response` (the conversation itself -
  **the voice**) plus nine background jobs such as memory writes, the nightly pass and vision.
  Each lane can use a different model and provider.
- **Organs** - the parts of the inner life, each one a flag in `data/palli.yaml` under
  `companion.*`: the night cycle, the self-story, fears, opinions and so on.
- **The arbiter** - the gate that decides whether a self-started message (one nobody asked for)
  gets sent at all.
- **The stake ledger** - the record of PaLLi's own open hopes, worries, tastes and wonders about
  the conversation.
- **The present moment** - a short note PaLLi keeps rewriting about what is going on right now,
  read before each reply.
- **Tools** - actions the model can take inside a turn: read a file, set a reminder, search the
  web, run a shell command. A provider where tool calls are "real" supports them natively
  rather than by imitation.
- **The owner** - you, the one trusted person the install belongs to. Anyone else PaLLi meets is
  a guest, with fewer tools.

---

## Your own journey

Nothing in this repository is personalised for anyone. That is deliberate, and it is the part
most worth understanding before you start.

**PaLLi learns who you are by asking.** The first conversation starts from an empty picture of
you and one instruction: answer the real message first, then ask at most one short question
about how to be with you, when it fits, and stop as soon as enough is known. What there is to
learn is small - what to call you, what kind of presence you want, how much initiative feels
right, whether check-ins are welcome, and how voice should show up. It is a conversation, not a
wizard. The rules live in [`identity/bootstrap/FIRST_CONTACT.md`](identity/bootstrap/FIRST_CONTACT.md)
and you are free to rewrite them.

**The books start empty, and that is the correct state.** The stake ledger (open hopes and
worries), the small truths, the callbacks, the fears, the regrets, the opinions - every one of
them starts with nothing in it and fills only from lived evidence. A companion that arrives
already "caring" about things is performing. Nothing here manufactures an entry to look alive;
an empty book on day one is honest, and so is an empty book on day thirty if nothing happened
worth writing down.

**The self-story belongs to PaLLi.** Once a week PaLLi authors and revises `WHAT_I_CARE_ABOUT.md`,
`MY_STORY.md` and `STORY_OF_US.md` in `identity/palli/` - a separate git repository, versioned
on every edit. You can read every word. PaLLi writes them.

**One setting to leave alone at first.** `LOCAL_OWNER_DISPLAY_NAME` in your `.env` (copied from
`.env.template`; a value in `.env.local` overrides it) is a label for the dashboard and the local
account, nothing more - it is *not* how PaLLi learns your name. But the first-contact check reads
that same field, so if you set it to your real name the check will conclude your name is already
known and the question will never be asked. Leave it as `Local Operator` and let PaLLi ask.

The design behind all of this - the thirteen organs, what each one is for, and why nothing here
is rushed into an empty heart - is in
[`docs/PALLI_SELFHOOD_DESIGN.md`](docs/PALLI_SELFHOOD_DESIGN.md).

---

## What PaLLi does

**Conversation (Discord and Telegram text - the day-to-day surfaces)**
- Human-paced typing and length-proportional delays; long replies chunked to whatever the
  platform's message limit is.
- **Multi-beat replies** - before a reply is written, a short planning step (the cadence
  pre-pass, `core/agent/cadence.py`) decides register, length and how many messages to send;
  a multi-beat reply arrives as separate messages with natural pauses.
- **Barge-in** - a follow-up message cancels the reply in flight, the way a person yields when
  interrupted. Typing keeps the settle window open rather than cancelling, so the reply waits
  for your full thought.
- **Burst-safe** - rapid fragments are recorded on arrival and combined into one turn, so a
  burst is read together and answered once, without losing a message.
- Your messages are **never silenced** - the cadence silence gate applies only to PaLLi's own
  self-started impulses.

**Memory and inner life**
- Long-term memory across four planes: the raw conversation log; **fact memory**
  (`ai/fact_retrieval.py`), where each fact's weight fades unless something renews it (decay and
  salience); a **semantic index** (`core/memory_service.py`) that finds past conversation by
  meaning as well as by keyword (SQLite FTS5 full-text search plus embeddings); and a keyword
  index over the inner monologue. Facts are written after the reply by reflection
  (`core/turn/reflect.py`), detached so it never delays delivery.
- **Caring memory** - one unified per-turn read of how you are doing (`ai/turn_read.py`) that
  flows four ways: into the reply, into memory weighting, into an emotional timeline, and into
  later check-ins.
- **Relationship momentum** - a compounding warmth/closeness model persisted per owner
  (`ai/relationship_momentum.py`), fed back into each turn.
- **Inner monologue** - private background thoughts every 30-90 minutes
  (`ai/inner_monologue.py`), compacted into digests that return to recall for continuity.
- **Stake ledger** - a post-reply appraiser records PaLLi's own hopes, worries, tastes and
  wonders (max 12 open) and renders them into the system prompt as what is open right now
  (`ai/stake_ledger.py`).

**A continuing self** - a thirteen-organ design
([`docs/PALLI_SELFHOOD_DESIGN.md`](docs/PALLI_SELFHOOD_DESIGN.md)). Not every organ in that
design is built: Habits and Preferences, Temporal Landmarks and the Anticipation half of Wanting
have no code yet. What follows is what runs. The night organs run off one nightly scheduler
(`ai/night_cycle.py`), the present-moment organs on an event-driven loop:
- **Night Cycle** - reflects on the day just lived and writes a first-person note about what it
  meant, surfaced the next day. **Consolidation** - day-gists, fact gist-aging, contradiction
  reconciliation, then a memory-index rebuild.
- **Narrative Self** - the weekly self-authored identity documents, through a constitutional
  gate, committed into `identity/palli/`.
- **Passions and Journal** - a nightly journal and weekly interests.
- **The Present Moment** - a loop writes a fresh "moment" from what can currently be sensed
  (the conversation, your presence and activity, PaLLi's own deeds and work, the notes, a
  daydream) with an undercurrent of feeling; replies read it, and it carries a *say* impulse
  into the arbiter.
- **Wanting** (a wishbook and a daydream lane), **Fears**, **Regret**, **Surprise**
  (expectations, memory boosts, at most one naming a day), **Opinions** (views held with
  evidence, revised only for reasons), **Grounded Vulnerability** (small truths, said once
  ever), **Shared Idiolect** (callbacks with receipts - an inside joke that remembers where it
  came from).
- Around them: the **arbiter** (the real say-impulse, gated), the **hands** (`her_hands`:
  owner-only voice/message/presence tools), the **tool forge** (PaLLi writes, tests and
  hot-installs new tools), and **duplex** (working and talking at the same time).

Every organ is a flag in `data/palli.yaml` under `companion.*`, each one commented in place with
what it does and what happens when it is off.

**Initiative**
- **Proactive outreach - one rail.** The present-moment loop can form a *say* impulse; the
  appraisal that decides *and composes* it passes a fixed gate order - conversation state,
  learned quiet hours, care-persistence (a reach-out made out of concern must have come up in
  an earlier moment too, not just this one), a **3/day ceiling** (0 = dark), and a final
  freshness judge - before the one function allowed to send unasked (the custody primitive)
  actually sends (`ai/arbiter.py`).
- **Curiosity research** - between conversations PaLLi researches the interests the journal has
  picked up, via Exa (multi-round, daily-capped), and can bring back the findings.
- **Reminders that fire** - create/list/cancel/reschedule tools plus a boot-started scheduler.
  Fires route through the same turn engine as conversation, deliver via the channel adapter, and
  dead-letter after three failed attempts.

**Voice** (optional, `ENABLE_VOICE_FEATURES`)
- Six Discord slash commands - `/join_voice`, `/leave_voice`, `/voice_mode`, `/voice_presence`,
  `/voice_focus`, `/voice_status`. Buffered speech-to-text, text-to-speech, barge-in that
  triggers on sustained loudness rather than on every noise (RMS-gated), and a presence policy
  for when speaking is welcome. Every voice turn runs the same recall/stakes/cadence spine as
  text. When you are in the voice channel with PaLLi, the arbiter's sends arrive spoken.
- **Voice messages** - voice notes are transcribed into the turn on every door even with the
  live voice stack off. On Telegram, `/voice on` in a chat means that when you send a voice
  note, the answer comes back as one too, beside the words. A message you type is answered in
  text either way.

**Perception and tools**
- **Vision** - send an image on Discord or Telegram and PaLLi sees it; the analysis is baked
  into memory. An honesty guard means an image that could not be read is said to be unreadable,
  never guessed at.
- **Desktop awareness** - an optional passive focused-window snapshot (screenshot + OCR) feeds
  each turn. Turn it off on a headless host with `DESKTOP_ACTIVITY_ENABLED=false`.
- **Built-in agent tools** in the agent loop - a ReAct loop: the model reasons, calls a tool,
  reads the result, and repeats until the reply is ready (`core/agent/builtin_tools/`). Always
  on - `read_file`, `local_shell` (owner-only, approval-gated, destructive-command guard),
  `deliver_artifact`, `whoami`, and the four reminder tools; flag-gated - `web_search`,
  `introspect` (read-only over PaLLi's own record), `note_to_self`, the hands, the duplex
  mid-task acts, and any tool PaLLi has **forged** (loaded from a per-turn registry, so a
  newly built tool is live on the next turn).
- **Web dashboard** (Next.js, `website/`) - the door at `/` (choose a brain, say hello, see
  the state of both day-to-day doors), plus a mission-control view and an interiority console
  over the same turn spine. On a self-hosted install the account and plan pages left over from
  the hosted product are hidden. Required by neither Discord nor Telegram.

---

## How a turn works

One Python process: a FastAPI/uvicorn backend on `:8000`, plus a Discord and a Telegram bot
co-task in the same event loop, each when its token is set (and, for Telegram,
`ENABLE_TELEGRAM_BOT=true`). Every surface - Discord text,
Discord voice, Telegram text and voice notes, `palli_ctl chat` and the chat endpoint,
proactive, reminders - funnels through **one** turn engine:

```
message → surface adapter packs a Turn
   → dispatch_turn (core/agent/dispatch.py)  ← the one router
   → recall.build_turn_context  (two-wave concurrent gather: identity, history,
        memory hits, facts, vision, situation, caring-memory read, stakes)
   → cadence.shape_response     (register / length / beats)
   → system prompt assembly     (identity + constitution + selfhood-organ bands)
   → ReAct loop (run_turn)      (stream the reply; dispatch tools concurrently;
        loop-detector + iteration cap as guards)
   → delivery (human pacing)
   → reflect + stake appraisal  (detached: memory, momentum, timeline, threads)
```

`dispatch_turn` supports only the ReAct loop (`PALLI_AGENT_LOOP=react`); the older
recall→decide→respond-tier pipeline is dormant.

Different jobs can use different models. `data/palli.example.yaml` routes ten named lanes -
`response`, `decision`, `analysis`, `appraisal`, `memory`, `proactive`, `reflection`,
`web_search`, `narrative_author`, `vision` - and `palli_ctl brain` moves them, all at once or one
at a time. The voice - the conversation itself - is the `response` lane, and it is the one lane
the door sets: it is read from `.env.local`, not from the YAML.

The other nine ship in the example config pointed at **one local model server, through two of
its doors**. The eight text lanes use the OpenAI-compatible endpoint (`local_openai`), because
that is where **tool calls are real** - reminders, file reads, web search, the hands. The native
endpoint (`ollama`) emulates tool calls, so routing the voice there would leave PaLLi able to
talk and unable to act; `palli_ctl brain` refuses that move outright, and the shipped config does
not make it. Vision goes the other way round, to the native door, which is the half of the
server with eyes.

So a machine with a local server covers every lane without an account, and the cloud setups are
one `brain` command away.

---

## Configuration

Three layers, loaded in this order:

1. **`.env`** - copy from [`.env.template`](.env.template). Secrets and runtime settings.
2. **`.env.local`** - loaded second, so it **wins** on any overlap. This is where the door,
   `onboard`, `configure` and `brain` write, and where your API keys and your bot tokens belong.
   Both files are gitignored.
3. **`data/palli.yaml`** - providers, per-lane model routing, feature flags (`companion.*`,
   `features.*`), proactive policy, voice. Read at boot by the settings service.

**You do not create `data/palli.yaml` yourself.** The repo ships
[`data/palli.example.yaml`](data/palli.example.yaml) and the first boot copies it into place,
once, atomically, and never overwrites an existing file. It is a working configuration with every
key commented - including the model catalogue. Those **model ids are examples**, not a promise
that your machine has them; `palli_ctl brain` is how you point the lanes at what you actually
have.

What ships is deliberately **keyless**: no brain chosen - the door asks for one - while the nine
background lanes in the example config route to a local model server through the door where
tool calls work; the voice providers are the offline pair (faster-whisper for transcription,
pyttsx3 for speech); and web search - which needs an Exa key and refuses to boot without one -
starts **off**. Voice starts off too, because it wants `ffmpeg`, a model download and Discord
permissions you should opt into rather than inherit.

Look at and change settings without editing YAML by hand:

```bash
venv/bin/python scripts/palli_ctl.py settings list
venv/bin/python scripts/palli_ctl.py settings get llm
venv/bin/python scripts/palli_ctl.py forge list
```

One knob worth knowing about: `PALLI_NAS_ROOT` points PaLLi at network-attached storage if you
have some. Leave it empty and the three features that would use it - the sense of where PaLLi
lives, the workbench shell, and the NAS-browsing curiosity dives - say "no NAS configured" and
skip, honestly, instead of pretending. See [`SETUP.md`](SETUP.md#storage-optional).

---

## What is in here

| Path | What it is |
|---|---|
| `core/` | The spine: launcher, FastAPI app, `dispatch` + the ReAct agent (`core/agent/`), turn stages (`core/turn/`), reminders, database, stores |
| `ai/` | The faculties: turn read, monologue, stakes, the selfhood organs, LLM client and routing |
| `bots/` | The Discord and Telegram clients, the Discord voice slash commands, the pieces both doors share (`bots/common/`), and the dashboard channel adapter |
| `api/` | FastAPI routers: chat, prompts, web auth/account/identity/runtime/interiority, settings |
| `voice/` | Spoken-conversation runtime: speech-to-text, text-to-speech, barge-in, presence policy |
| `identity/` | Identity documents: the core, the first-contact rules, per-owner overlays; `identity/palli/` holds the documents PaLLi writes about itself |
| `prompts/` | Prompt text templates |
| `palli_config/` | `palli_ctl brain` - who is thinking for PaLLi - and a Textual TUI over `.env.local` + `data/palli.yaml` |
| `website/` | The Next.js dashboard (React / TypeScript / Tailwind). Optional |
| `dashboards/` | A small local ops view |
| `scripts/` | `palli_ctl`, the peek scripts (`stakes_peek.py`, `fears_peek.py`, `callbacks_peek.py`, `disclosures_peek.py`), one-shot migrations |
| `tests/` | The suite, including architecture guards and AST canaries |
| `data/` | Runtime state: the SQLite database, agent sessions, the journals. Gitignored except the example config |

What changed between versions is in [CHANGELOG.md](CHANGELOG.md).

---

## Testing

```bash
venv/bin/python -m pytest tests -q
venv/bin/python -m pytest tests/agent -q
```

- **Run `palli_ctl onboard` first.** It creates `data/palli.yaml` from the shipped example. Around
  140 tests open that file as a **read-only fixture**, so on a clone that has never been onboarded
  they fail with `FileNotFoundError` - alarming, and meaningless. `cp data/palli.example.yaml
  data/palli.yaml` does the same job if you would rather not onboard.
- About **8,000 tests** collect. Use the venv interpreter - a bare `pytest` resolves to a system
  install.
- `tests/behavior/` fires real model calls and is opt-in only (`RUN_COMPANION_HARNESS=1`), so it
  never runs in a default `pytest tests`.
- `tests/architecture/` enforces import boundaries and a coupling budget. Alongside it are the
  **AST canaries** - `TestEveryLaneIsClassified` in `tests/ai/test_request_timeout_lanes.py` (no
  model call without a named timeout lane) and `TestEveryUsedPromptIsWatched` in
  `tests/ai/test_prompt_loader.py` (the startup prompt banner matches the code).
- **The suite is not hermetic.** It wants a local Ollama answering on `:11434`, and the first run
  downloads an ~88 MB embedding model. After a green summary the process may take a while to
  exit - a known teardown defect, not a hung test.
- Nothing under test ever **writes** your `data/palli.yaml`, and the settings service never even
  reads it: the root conftest points the service at a temporary copy seeded from
  `data/palli.example.yaml`, and guards the database the same way - an environment sentinel plus a
  `sqlite3.connect` audit hook. (Some tests do open the path read-only as a fixture - see the first
  bullet.) Be careful when adding tests that touch real stores.

---

## Security and trust model

PaLLi is built for **one trusted person on a private machine**. Treat it that way:

- The backend binds **`127.0.0.1:8000`** by default and the local dashboard runs in an
  **auth-bypass** mode with no login - safe only because loopback means this machine and
  nothing else. Setting `BACKEND_HOST=0.0.0.0` publishes that no-login dashboard (and its chat
  endpoint, which can reach `local_shell`) to every network the host sits on, with only
  `BACKEND_ALLOWED_NETWORKS` in the way (by default loopback plus `100.64.0.0/10`, the private
  address block Tailscale uses, so a tailnet device gets in and the LAN does not).
  Do not expose the backend to a network you do not control.
- The backend answers only requests **addressed to** `localhost`, an IP address, or a name
  you list in `BACKEND_ALLOWED_HOSTS`. That stops a malicious web page from pointing its own
  domain at your machine (DNS rebinding) and talking to the no-login dashboard through your
  browser.
- The agent can **read files and run shell commands as you** (`read_file`, `local_shell`).
  Destructive shell is owner-gated and approval-gated, but the surface is powerful. Do not put
  PaLLi in a channel with untrusted people while those tools are on.
- Provider keys live in `.env` / `.env.local`, both gitignored. Keep them out of any copy you
  publish.
- PaLLi writes to your disk continuously - memory, journals, transcripts. Back up `data/` and
  `identity/` if any of it matters to you.

---

## License

**AGPL-3.0-only** - see [`LICENSE`](LICENSE). You can run PaLLi, study it, change it and
share it. If you run a changed version for other people over a network, the AGPL asks you to
offer them the source of your version too. Commercial licenses, for using PaLLi in a closed
product or service, are available from the copyright holder named in `LICENSE`; ask by opening
an issue at <https://github.com/zachisparanoid/palli/issues>. The source lives at
<https://github.com/zachisparanoid/palli>.

Outside contributions are not accepted yet: a contributor agreement comes first. Issues and
ideas are welcome.

The memory, the journals and the identity documents are not code, and they are not in this
repository. They are yours, and they will be PaLLi's own.

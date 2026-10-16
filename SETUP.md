# Setting up PaLLi

**PaLLi** · /ˈpɑː.li/ · *PAH-lee* — said "Polly", like the bird. *Polly want a cracker.*

The long version. [`README.md`](README.md) has the five-minute version; this is the file to read
when something did not work, or when you want to know what a step is actually doing.

Everything here assumes Linux and a shell in the repository directory.

---

## 1. Prerequisites

**Required**

- **Python 3.13.** The repo pins `3.13.12` in `.python-version`, and the code is developed and
  tested on 3.13 only. Older interpreters are not tested and not supported; if yours is close
  enough to work, you are on your own with it.
- **Node.js 20 or newer, and npm.** Your first hour happens on a page in your browser — the *door*, where
  you give her a brain and say hello (section 3). That page is the local dashboard, and the
  dashboard is a Next.js app, so it wants Node and npm. Without them she still runs: start her
  with `venv/bin/python main.py`, put a provider key in `.env.local` by hand (section 4), and
  talk to her with `palli_ctl chat`. Note that `palli_ctl doctor` reports a missing Node or npm
  as a **core blocker** whatever you intend to run, so without them it exits 1 on an otherwise
  perfect install. `venv/bin/python main.py` does not care. See section 9.
- **Somewhere for her to think.** She ships with no brain chosen for you, and picks one up in
  the browser on her first page. A cloud provider (DeepSeek, OpenAI) takes an API key you paste
  and she tests in front of you before anything is saved; a model server on your own machine
  ([Ollama](https://ollama.com), LM Studio, vLLM) takes an address and needs no account at all.
  Neither is privileged and the door offers both. Section 4 is the whole picture.

**Optional**

- **A Discord bot token.** Not for your first hour: with no token she starts anyway, says the
  Discord door is closed for this boot, and waits for you on the door page. Set it when you
  want the surface you will actually live on. Section 2a.
- **A Telegram bot token.** The other day-to-day door, and the quicker one to set up — one
  conversation with BotFather, no application and no intents. It ships turned off. Section 2b.
- **`ffmpeg`** — required for voice. Without it the voice runtime cannot transcode audio.
  Local speech-to-text also wants `venv/bin/pip install faster-whisper` (section 6).
- **`tesseract`** — only for OCR of the desktop-awareness screenshots.

Install order does not matter. On Debian or Ubuntu, where 3.13 is available:

```bash
sudo apt install python3.13 python3.13-venv nodejs npm ffmpeg
```

If your distribution does not carry 3.13 yet, install it with `pyenv` or the deadsnakes PPA
first — the version is a hard requirement, not a preference. A distribution's Node can also be
older than the dashboard wants: if `node --version` says anything below 20, install Node with
[nvm](https://github.com/nvm-sh/nvm) or the [NodeSource](https://github.com/nodesource/distributions)
packages instead of the distribution's.

---

## 2. The day-to-day doors

Discord and Telegram are where you live with her day to day — on your phone, in a channel you
already have open. **Neither is needed for your first hour.** That happens in a browser
(section 3), and the first thing you ever say to her happens there, before any bot token
exists. All three doors — Discord, Telegram and the phone app — are optional. Discord and
Telegram are off without their token, and each one wants a bot of your own — there is no
shared one. When you have made one, put its lines in `.env.local` and restart her.

The phone app (2c) is a third door of PaLLi's own: no bot to make, no account anywhere and no
token. It is off until you set `ENABLE_MOBILE_DOOR=true`, and a phone pairs with a code from
the door page.

Until she has a brain, all three doors stay shut whatever their tokens and switches say: she
must not answer on Discord with nothing to think with. The boot says so by name, and one
restart after you add a brain opens them.

### 2a. The Discord application

A blank `DISCORD_TOKEN` turns Discord off for that boot with a warning and nothing else, and
you meet her on the door page instead.

**Create the application and get a token**

1. Go to <https://discord.com/developers/applications> and click **New Application**. Name it
   whatever you want her to be called; you can change it later.
2. Open the **Bot** tab. Click **Reset Token**, confirm, and copy the token that appears. This is
   the only time it is shown. It goes in `.env.local` as `DISCORD_TOKEN=`.
3. Treat that token like a password. Anyone who has it can act as your bot.

**Turn on the three privileged intents — this step is not optional**

Still on the **Bot** tab, scroll to **Privileged Gateway Intents** and enable all three:

- **Presence Intent**
- **Server Members Intent**
- **Message Content Intent**

She connects with `discord.Intents.all()`. If any of the three is off, Discord refuses the
connection outright and the log shows a `PrivilegedIntentsRequired` error at startup rather than
anything about intents being missing at runtime. Message Content is the one that matters most:
without it she receives events with empty message bodies and cannot read a word you say.

**Invite her to a server**

1. Open **OAuth2 → URL Generator**.
2. Under **Scopes**, tick `bot` and `applications.commands` (the second one registers the voice
   slash commands).
3. Under **Bot Permissions**, tick at least: *Send Messages*, *Read Message History*, *Attach
   Files*, *Embed Links*, and *Add Reactions*. For voice, also *Connect* and *Speak*.
4. Copy the generated URL, open it, and add her to a server you control. A private server with
   one channel is the usual setup.

**Tell her which human is you**

In Discord, enable Developer Mode (User Settings → Advanced), right-click your own name and
**Copy User ID**. Put it in `.env.local`:

```
LOCAL_OWNER_DISCORD_USER_ID=000000000000000000
```

This is how the owner gate recognises you — it links your Discord account to the local owner
record, which is what unlocks the owner-only tools and the mid-task steering path. It is
attached to the local owner the first time she boots and finds the field set.

---

### 2b. The Telegram bot (optional)

Telegram is the second door, and it is the quicker one to open: no application, no
intents, no invite link. It is optional in exactly the way Discord is — with
`ENABLE_TELEGRAM_BOT=false`, which is how it ships, nothing about Telegram is
started and nothing about it is said at boot.

1. Open [@BotFather](https://t.me/BotFather) in Telegram and send `/newbot`.
2. Answer the two questions: a display name, then a username ending in `bot`.
3. Copy the token it gives you back. Treat it like a password — anyone who has it can
   act as your bot.
4. Put it in `.env.local`, with the door turned on:

   ```
   ENABLE_TELEGRAM_BOT=true
   TELEGRAM_BOT_TOKEN=123456789:paste-your-token-here
   ```
5. Restart her. If anything about the door is wrong — no token, or the library missing —
   the preflight names it and she starts anyway.

**Privacy mode decides what she can read in a group.** It is BotFather's default: with
it on, Telegram delivers only messages that @mention her, replies to her own messages,
and commands. In a one-to-one chat she sees everything you send either way. With
`companion.she_decides` on (`data/palli.yaml`), she decides for herself whether to
answer each group message — answer, wait or stay quiet, like a participant who knows she
is an AI — and an @mention, a reply to her or her name are signals she weighs, never
requirements. For that to cover the whole group, turn privacy mode off: send BotFather
`/setprivacy`, pick her bot, choose **Disable**, then remove her from each group and add
her back (Telegram applies the change only to groups she joins afterwards). That hands
her every message in those groups, so do it on purpose. With `companion.she_decides`
off she answers in a group only what is addressed to her; with privacy mode left on,
that is all Telegram gives her to read.

She checks privacy mode herself at startup. With it on, her log says so in a warning line
(`telegram: privacy mode is ON …`), and each Telegram group's room card tells her she sees
only what mentions or replies to her, unless she is an admin there (an admin bot sees every
message whatever the setting). Ask her "who's in here?" in a group and she lists its admins
and member count; Telegram doesn't let a bot list everyone, so the people who have spoken
lately come from what she has seen.

**Tell her which human is you.** `LOCAL_OWNER_TELEGRAM_USER_ID` is your numeric Telegram
user id. Message [@userinfobot](https://t.me/userinfobot) and it replies with it — or send
her one message and read the id out of the boot log (`telegram client: guest message from
… (id 123456789) in chat …`). Until that key is set, everyone she meets on Telegram is a
guest — including you: a plain reply, no shell, no files, none of her memory of you.

```
LOCAL_OWNER_TELEGRAM_USER_ID=000000000
```

**Say hello.** `/start` in a direct message is her door sign, and `/help` lists the six
commands — `/start`, `/help`, `/link`, `/proactive`, `/insights`, `/voice`. They also sit
behind the `/` button beside the text box, because Telegram publishes a menu.

**Voice notes.** A voice note you send is transcribed into the turn whenever a
speech-to-text provider is configured (section 6). Her answers come back as text by default.
`/voice on` is the owner's switch; a guest who tries it gets one line saying so. What it does
depends on `voice.voice_notes` in `data/palli.yaml`:

- **Off (the default):** in *that* chat, when you send her a voice note, her answer comes back
  as a voice note too, beside the words. A message you type is still answered in text.
  `/voice off` stops it, and so does a restart.
- **On:** she can send a voice message in her own voice whenever it fits (her `voice_note`
  tool, which also needs `features.her_hands`), on Telegram and on Discord (an audio
  attachment there). `/voice on` turns on voice replies for that chat — the same switch she
  turns on herself when you ask ("send me voice messages until I say otherwise"). While it is
  on, her replies there come as voice messages, with a short text line only when there's
  something to read (a link, a code, a number). The switch stays as you set it, restarts
  included, until `/voice off` or until you ask her to stop. She speaks in the voice text to
  speech is configured with (section 6); with none configured, her replies stay text.

**One thing not to flip.** `data/palli.yaml` has a `channels: telegram: enabled:` row. It
is a readout the dashboard shows, not the switch — the door is opened by the two
`.env.local` lines above, and nothing else.

---

### 2c. The phone app (optional)

The phone app is a door of PaLLi's own: the conversation, her voice notes and
the files she makes, your photos and voice notes to her, your reminders, and
her messages to you as notifications. Nothing goes through a third party's
servers. The phone talks to this backend directly, so it has to be able to reach it.
[Tailscale](https://tailscale.com) on this machine and on the phone is the easy way.

1. Turn the door on in `.env.local` and tell it the address your phone will use:

   ```
   ENABLE_MOBILE_DOOR=true
   MOBILE_PUBLIC_URL=http://your-host.your-tailnet.ts.net:8000
   BACKEND_HOST=0.0.0.0
   BACKEND_ALLOWED_HOSTS=your-host.your-tailnet.ts.net
   ```

   `BACKEND_HOST=0.0.0.0` lets the backend listen beyond this machine. The network gate
   still admits only `BACKEND_ALLOWED_NETWORKS` (this machine and your tailnet, as shipped).
2. Restart her. The preflight names anything about the road to the phone it can see
   is wrong, and starts anyway. Like the other doors, this one stays closed until a brain is
   configured, and it opens at the first restart after you add one; the door page and the
   doctor say so.
3. Install the app and open it. The app is not part of this repository: it is a separate
   one, built from `../mobile`, the folder next to this one. There is no store listing yet.
4. On the door page, the Phone app row now says **open**: choose **Pair a phone** and scan
   the QR with the app. Or run `venv/bin/python scripts/palli_ctl.py phone pair` and scan
   the one it prints. The code works once, for ten minutes.

**Where her own messages go.** By default her check-ins and reminders go to the
private chat you used most recently, so the app gets them once you use it. To
send them to the phone whenever one is paired, set `policy: proactive: door: mobile`
in `data/palli.yaml`. The choices are `last_used`, `mobile`, `discord` and `telegram`.

**Unpairing.** Use the bin icon next to the phone on the door page, or
`palli_ctl phone list` then `palli_ctl phone revoke <id>`. A phone that is connected
at that moment is closed within half a minute.

**What pairing protects, honestly.** Every request on the phone's door needs the
phone's own token, so the door is never open to the network by itself. But with
`LOCAL_DASHBOARD_NO_AUTH=true` (how it ships), anyone who can open the door page
can pair a phone, just as they could chat with her there. Pairing adds no
protection the dashboard does not already have.

**Staying connected.** The app keeps one connection open in the background (a
small permanent notification) and asks to be let off battery optimisation. That
is what makes her messages arrive at once without Google's push service.

---

## 3. Your first hour

Install her, start her, open the address she prints, give her a brain if she has not got one,
and say hello. No bot token, no account, nothing to sign up for.

### Install

```bash
git clone <this repository> palli
cd palli
python3.13 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.template .env
```

The virtual environment **must** live at `venv/` in the repository root. The launcher resolves
her interpreter as `venv/bin/python` and reports a core blocker if it is not there. This also
pulls `sentence-transformers`, which downloads a local embedding model on first use — expect the
first run to take a minute longer than you were expecting.

**Do not skip the `cp`.** `.env.template` is the shipped configuration, not documentation:
copying it is what gives her a place to boot from: a brain that is named but deliberately
keyless (`LLM_PROVIDER=deepseek` with `DEEPSEEK_API_KEY=` blank, which is what lands you on the
door), all three doors off, and the dashboard address the boot and the door page both name
(`FRONTEND_URL=http://localhost:3000`). Without it every one of those settings is an empty
string and she will come up pointing at nothing. Section 5 is what the
three configuration layers are and which one your own edits belong in — the short answer is
`.env.local`, which is loaded after `.env` and wins.

Optionally, install the repo itself so `palli_ctl` is a command rather than a path:

```bash
venv/bin/pip install -e .
```

(If that venv has no pip, run `venv/bin/python -m ensurepip` first.) It is an *editable*
install — the checkout stays the runtime, because `data/`, `identity/`, `prompts/` and
`.env.local` are all read relative to it. Nothing requires this: every command below works
either way, and PaLLi prints whichever form works on your machine.

### Start her

```bash
venv/bin/python scripts/palli_ctl.py prepare
venv/bin/python scripts/palli_ctl.py start
```

`prepare` installs the website dependencies if they are missing and builds the dashboard — a
fresh clone has neither, and `start` refuses while the dependencies are missing. `start` then
runs the backend and the dashboard as managed background services and prints both addresses:

```
PaLLi launcher is running.
Backend: http://127.0.0.1:8000
Frontend: http://localhost:3000
```

**The Frontend address is the door**, and it is your `FRONTEND_URL` — the value above is the one
the `cp` above put there. Section 7 has the other ways to run her — bare `main.py`, tmux, a
systemd unit, Docker — and `stop`, `restart`, `status` and `logs`.

### Open the address

The door is one page. What it shows depends on whether she already has a brain.

**She ships without one, on purpose.** The `.env` you copied names a provider that needs a key
and leaves that key blank, so she boots into *setup mode*: she starts, stays quiet, spends
nothing, and the boot line names this page. The door opens on the key step, and what you pick
there is yours — a cloud provider, or a model server on your own machine. Nothing is written
until it answers.

She used to ship pointed at a local Ollama server instead. That skipped this page entirely for
anyone who had not installed one: she booted "ready", and every message came back as a timeout
from a server that was never there.

**If you choose a local server and it is not running,** the page still opens on the chat panel.
She has a brain configured; she simply cannot reach it. Your first message does not come back
as an error. It comes back in her own voice:

> my brain's provider just timed out on me mid-thought — give me a minute and send that again?

That is what she says whenever a provider fails a turn with somebody waiting for her, on any
surface, and it is deliberately hers rather than a stack trace. It is not a pointer, though, so
the page carries one for you: with `local_openai` configured, a line under the chat header says
her brain is a local model server, that this sentence means it is not running, and offers
**change her brain**. There is a second **Change her brain** under the panel. Either one opens
the step below.

**She has none.** If `LLM_PROVIDER` is `openai` or `deepseek` and that provider's key is blank,
she is in *setup mode* (below) and the door opens on the key step directly.

### Give her a brain

The step lists the brains this door can set:

| Row | What it needs |
|---|---|
| **DeepSeek V4** | an API key |
| **OpenAI** | an API key |
| **A local model server (Ollama, LM Studio, vLLM)** | the address it answers on, the model to run, and a key only if your server asks for one |
| **Claude (API)** | shown, and **not available** — with the reason printed under it |

Anthropic is listed rather than hidden, because hiding it would be a lie about what exists: the
capability matrix refuses that route for her voice today, and the reason it gives is printed on
the row. Setting `ANTHROPIC_API_KEY` by hand and routing other lanes to it is a different
question, and section 4 is where that lives.

**What this step sets is her *voice*** — the live conversation, the one you are about to have.
It writes `.env.local`, and `.env.local` is what her voice (the `response` lane) reads. The
other nine lanes — `decision`, `analysis`, `appraisal`, `memory`, `proactive`, `reflection`,
`web_search`, `narrative_author`, `vision`, which are what the cadence vote, the promise gate,
turn read, her memory writes, the inner monologue, the night cycle and the moment loop each
call — follow the routing in `data/palli.yaml`, and the door does not touch that file. On a
fresh clone that routing points eight of them at the local server's OpenAI-style door
(`local_openai`) and `vision` at Ollama's native one (`ollama`) — the same daemon, two
different provider ids. So if you paste a cloud key here and have no local server running, she
will talk to you perfectly while those nine refuse a connection every time they tick, quietly,
in the log. Moving them is one command —
`palli_ctl brain` — and section 4 is the whole picture. It is worth doing the same evening.

**Test it and save** makes one tiny live call to the provider you picked, and only then writes:

- **it answers** — the key goes into `.env.local`, and her voice comes alive **without a
  restart**; the running process swaps its provider in place. The banner says *Her voice is
  live*, names the provider and the model, plus the balance where the provider reports one and
  "balance: not reported by this provider" where it does not — never a guess.
- **it does not** — you get the provider's own words ("`401 Unauthorized: …`", or "could not
  connect to `<address>` — is the server running?"), the field is kept, and **nothing is
  written**. Fix it and press it again.
- **it answers but the live swap fails** — the page says *Her voice: saved — restart to use
  it*, with the restart command. The key is on disk; the process is honestly still brainless
  until you restart.

Either way the key is never logged, never echoed back, and never appears in an error message.

The two chat doors are the one thing a saved key cannot open on the spot: their clients are
created once, at start. If a token was set and the door was shut for want of a brain, the page
says so and one restart opens it.

### Say hello

The chat panel under the brain step talks to the same turn engine Discord and Telegram use —
her recall, her cadence, her tools. A multi-beat reply lands as separate bubbles in order, a
typing indicator runs while she works, and a file she produces is named with its path on your
machine (there is no route that serves her attachments over HTTP yet, so the page tells you
where the file is rather than linking to a page that is not there).

Two shapes of trouble look different here, and the difference is the fix:

- **She answers, and what she says is that her provider timed out** ("my brain's provider just
  timed out on me mid-thought — give me a minute and send that again?"). The turn reached her
  and her brain did not answer: a local server that is not running, a revoked key, a provider
  outage. **Change her brain** is the control; `palli_ctl doctor` and the log say which.
- **A red bubble from the page itself**, with "Her brain may not be answering" in it and the
  backend's own words underneath. That one is the HTTP call failing — the backend stopped, or
  answered with something that was not a reply.

Under it is a **Doors** card: Discord and Telegram, each showing open, closed or not set up,
with the exact `.env.local` lines to add and a pointer back to section 2. That card is where a
stranger finds out the day-to-day doors exist. **Mission control** in the header is the old
control panel at `/dashboard`, one link away and unchanged.

### Setup mode — she starts with no brain

A provider that needs a key, with no key at all, used to be a hard refusal: exit 2, and nothing
to open. It is now a soft verdict called setup mode. She starts, and:

- the boot prints one warning line naming the address, and no blocking list:

  ```
  - LLM — no brain configured; open http://localhost:3000 to add one (or set DEEPSEEK_API_KEY in .env.local)
  ```

  (The address is your `FRONTEND_URL` — `http://localhost:3000` is what the shipped template
  says — and the key it names is whichever one your provider needs, so `OPENAI_API_KEY` where
  `LLM_PROVIDER` is `openai`. `palli_ctl doctor` prints the same sentence as a warning.)
- **all three doors are shut for that boot**, whatever their tokens and switches say, and each
  one that would have opened says so by name.
- **every thinking lane idles** — the inner monologue, the night cycle, the present-moment
  loop, daydreaming, curiosity research, the workbench, her proactive rail. Each writes one
  line to the log at most once an hour rather than sixty. Nothing is spent, because setup mode
  never talks to a provider at all.
- **a reminder that comes due is held**, not fired and not lost. It is not attempted, not
  counted against the three-attempt dead letter, and not marked done; it goes out in her own
  words once she has a brain.
- **anything she is sent is answered honestly**, once:

  ```
  I don't have a brain yet — open the dashboard at http://localhost:3000 and add a key, then I'm here.
  ```

Two ways out. Paste a key on the door, which needs no restart; or put the key in `.env.local`
yourself and restart her. Either one ends setup mode.

A key that is *present but wrong* is not setup mode — she boots normally and the wrong key
shows up the first time she is asked to think. The door's live test is what catches that one.

### When it does not go to plan

- **"Her backend is not answering."** The page talks to the process you started in the
  terminal. Start it — `venv/bin/python scripts/palli_ctl.py start` — and press *Try again*.
- **The address does not load at all.** The dashboard is a separate service from the backend.
  `venv/bin/python main.py` on its own runs the backend and the doors and serves no web page;
  the door needs `prepare` and `start` (and therefore Node and npm — section 1).
- **A wrong key.** The provider's own error text appears under the field, the key is not saved,
  and the field keeps what you typed. `palli_ctl doctor` and `palli_ctl configure` say the same
  words now; they used to report a revoked key as "empty response".
- **A local server that is not running.** "could not connect to `<address>` — is the server
  running?" — the address you typed, said back to you. Start it, or point the field somewhere
  else.
- **No balance shown.** DeepSeek is the only provider here with a balance API. For anything
  else the page says "balance: not reported by this provider" rather than inventing a number.
- **A Discord or Telegram token set, and no brain yet.** The doors card says *closed until a
  brain is configured*, and the boot log says the same. Add a brain, restart once.

### The guided setup in the terminal (optional)

`onboard` is the older path, and it still works. It asks questions in the terminal instead of
in a browser, and it covers more than the brain:

```bash
venv/bin/python scripts/palli_ctl.py onboard
```

`onboard` asks for a runtime preset — **`local-only`** is what the shipped config already
uses, so it is the answer that needs nothing from you — then walks seven sections: models,
identity, companion, channels, voice, memory, runtime. It writes the result to `.env.local`, runs
`doctor`, and offers to start her. Two flags worth knowing:

```bash
venv/bin/python scripts/palli_ctl.py onboard --preset local-only --skip-start
```

`--preset` skips the prompt, and `--skip-start` leaves starting her to you. A preset whose
provider needs an API key you have not set is refused *before* anything is written, with a
message naming the variable.

`onboard` ends by offering to start her — but not while the web dashboard has never been built,
which is the case on every fresh clone. It prints the two ways to start instead and exits 0. For
Discord, that is `venv/bin/python main.py`; the dashboard needs `prepare` first (section 7).

To re-run just one part of it later, use `configure`, which is the same machinery without the
start step:

```bash
venv/bin/python scripts/palli_ctl.py configure --section voice
venv/bin/python scripts/palli_ctl.py configure --preset local-only --skip-doctor
```

---

## 4. Providers and keys

Every model call belongs to a named **lane**: `response` (her actual voice), `decision`,
`analysis`, `appraisal`, `memory`, `proactive`, `reflection`, `web_search`, `narrative_author`,
and `vision`. Lanes can point at different providers.

The door (section 3) sets exactly one of them — `response`, her voice — and it is the quickest
way to do it, because it tests the key live and writes `.env.local` only if the key works. This
section is the whole picture, and the terminal way into all ten.

### Ollama — a brain on your own machine, no key and no account

No account, no key, no bill — and nothing to configure, because every lane in
`data/palli.example.yaml` already points here. Install Ollama and pull the two models the shipped
config names:

```bash
ollama pull qwen3.5:9b
ollama pull gemma3:12b
```

`qwen3.5:9b` runs the nine text lanes; `gemma3:12b` is the vision lane, and you can skip it if
you do not care about her seeing images. **Those two tags are only what the shipped file
happens to say.** Pull whatever your machine can run and point her at it with `brain` (below)
rather than editing the YAML, so her voice and the routing stay in step.

**One server, two doors — and it matters which.** Ollama exposes both an OpenAI-compatible
endpoint and its own native API, and PaLLi treats them as two providers:

| Provider | Endpoint | Tool calls | Used for |
|---|---|---|---|
| `local_openai` | `LOCAL_LLM_API_BASE` (`/v1`) | **real** | the nine text lanes, including her voice |
| `ollama` | `OLLAMA_BASE_URL` (`/api/chat`) | emulated | the vision lane (it is the half with eyes) |

Her voice must be on a provider whose tool calls are real, or she can talk and not act — no
reminders, no file reads, no hands. `palli_ctl brain` refuses to move the `response` lane onto a
provider that only emulates them, and the shipped config does not try. Both addresses default to
`127.0.0.1:11434`, so one Ollama install satisfies both:

```
LOCAL_LLM_API_BASE=http://127.0.0.1:11434/v1
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Ollama on a different machine on your network works too — point both at that host.

Be honest with yourself about model size: she runs a long system prompt with a lot of context,
and a 3B model will feel like a different character than a 9B one.

### Cloud providers — a key each, all optional

Nothing here is required; the shipped config runs without any of it. Set the key in `.env.local`
and move her with `brain`:

| Provider | Key | Preset |
|---|---|---|
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-primary` |
| OpenAI | `OPENAI_API_KEY` | `openai-primary` |
| Anthropic | `ANTHROPIC_API_KEY` | `anthropic-primary` |

The door (section 3) offers the first two and a local server. Anthropic appears there as **not
available**, with the capability matrix's own reason printed on the row: her voice is not wired
to the native Anthropic path yet. The key is still read wherever you route a lane to Anthropic
yourself — the shipped configuration routes nothing there.

There are two more presets for people already paying for a coding subscription: `claude-oauth`
routes lanes through a locally installed **Claude CLI** as a subprocess, and `codex-primary`
routes through the **Codex CLI**. Both authenticate by logging into the CLI, not by a key. For
Codex:

```bash
venv/bin/python scripts/palli_ctl.py auth codex --status
venv/bin/python scripts/palli_ctl.py auth codex
```

`--status` only reports; without it, the command establishes the login. `configure` refuses a
Codex preset until that login succeeds.

`local-only` is the one you already have: every lane, and voice, on your own hardware. `hybrid`
puts the judgment-heavy lanes on a cloud model and leaves the background work local.

There is also an `ollama-primary` in the list, and it is a trap worth naming. It routes every
lane — her voice included — through the **native** Ollama door, where tool calls are emulated
rather than real. Pick it and she can talk and not act: no reminders, no file reads, no web
search, no hands. `onboard` prints a warning when you choose it, and `palli_ctl brain --lane
response --to ollama` refuses the move outright. It is fine for the vision lane, which is where
the shipped config already uses it, and wrong for the rest.

### Moving her brain

```bash
venv/bin/python scripts/palli_ctl.py brain
venv/bin/python scripts/palli_ctl.py brain --pick
venv/bin/python scripts/palli_ctl.py brain --lane vision --to claude/haiku
venv/bin/python scripts/palli_ctl.py brain --dry-run deepseek-primary
venv/bin/python scripts/palli_ctl.py brain --undo
```

With no arguments it prints the live picture. `--pick` chooses from a list in English.
`--lane`/`--to` moves one lane and leaves the rest. `--dry-run` does everything up to the write
and then stops. `--undo` reverses the last change in **both** files. It refuses a move the
runtime cannot honour and says why; `--force` proceeds anyway and prints what it is overriding.

> **Why `brain` and not the YAML.** Her voice — the `response` lane — is read from
> `LLM_PROVIDER` and `LLM_RESPONSE_MODEL` in `.env.local`, *not* from `llm.routing.response` in
> `data/palli.yaml`. Every other lane reads the YAML. `brain` writes both halves so they agree.
> `settings preset <name>` deliberately does not touch the `response` row, and tells you so.

### The model catalogue is examples

`data/palli.example.yaml` lists models under each provider — `qwen3.5:9b`, `gpt-4o`,
`deepseek-v4-flash` and so on. Those are **example ids**, not a claim that your machine or your
account has them. If a lane names a model you do not have, the call fails at runtime. `brain`
is the way to point her at real ones:

```bash
venv/bin/python scripts/palli_ctl.py brain --lane response --to local_openai/qwen3.5:4b
```

That applies to the shipped local rows as much as the cloud ones: `qwen3.5:9b` is only what the
file says, until you have pulled it.

---

## 5. The three configuration layers

**`.env`** — copy it from the template (section 3's first step) and leave the parts you do not
understand alone:

```bash
cp .env.template .env
```

**`.env.local`** — loaded *after* `.env`, so it wins on any key set in both. This is the file
`onboard`, `configure`, `brain` and the door (section 3) write, and where your secrets belong:
`DISCORD_TOKEN`, the provider keys, `LOCAL_OWNER_DISCORD_USER_ID`. Both files are gitignored.

`.env.template` is a guide, not an exhaustive list — the code reads some keys that are not
listed there. When a setting seems to have no effect, grep the source for its name before
assuming it is broken.

**`data/palli.yaml`** — the structured configuration: providers, per-lane routing, feature flags,
proactive policy, voice. **You do not create this file.** The repo ships
`data/palli.example.yaml`, and the first boot copies it into place — once, atomically, and never
over an existing file. It logs one line when it does. If you want to start the configuration
over, delete `data/palli.yaml` and boot again.

The shipped file is deliberately keyless: every lane on a local Ollama server, the voice
providers set to the offline pair, and `features.web_search` turned **off** — it needs
`EXA_API_KEY`, and on with a blank key is a boot failure rather than a quiet degrade. Turn it on
when you have the key. Voice is off too, in `.env.template` (section 6).

Inspect and edit it without touching YAML by hand:

```bash
venv/bin/python scripts/palli_ctl.py settings list
venv/bin/python scripts/palli_ctl.py settings get companion
venv/bin/python scripts/palli_ctl.py settings set features.web_search false
venv/bin/python scripts/palli_ctl.py settings preset local-only
```

`settings set` takes a dot-path and a value (string, boolean or JSON). Every write goes through
propose → apply and prints a rollback token.

The example config's `companion.*` block is worth reading straight through once. Each organ has a
comment saying what it does and what turning it off means.

### The forge, experimental and off

`features.forged_tools` is the one capability the shipped config leaves **off**. Inside that flag,
when she runs into something she cannot do, she writes a Python tool for it, tests it, and installs
it into her own running process — on her own judgment, with no confirmation asked, live on her very
next turn. Tools she has forged stay on her belt; `forge list` and `forge revoke <name>` (section 9)
are how you see and bench them.

It ships off because it is experimental, not because it is broken. It is code she wrote executing
in her process, and that is something to switch on deliberately rather than to discover already
running. When you want it:

```bash
venv/bin/python scripts/palli_ctl.py settings set features.forged_tools true
```

Or set `features.forged_tools: true` in `data/palli.yaml` by hand. Either way it takes effect at the
**next restart** — the flag is read at boot. Setting it back to `false` and restarting is the kill
switch: the forge and everything it has built go off her belt together.

### Storage (optional)

`PALLI_NAS_ROOT` points her at network-attached storage, if you have any:

```
PALLI_NAS_ROOT=/mnt/your-nas
```

Three things read it: her sense of where she lives, her workbench shell, and the curiosity dives
that browse files. Leave it **empty** and all three report "no NAS configured" and do nothing —
they do not guess a path and they do not pretend. Setting it to a directory that is not mounted
is worse than leaving it empty.

**Her own NAS folder (optional).** Her workbench runs as a separate work account
(`scripts/setup_workbench_user.sh` creates it), and by default her NAS folder is
`<PALLI_NAS_ROOT>/palli`. On a share mounted with a forced uid (the usual CIFS setup) that account
can read there but not write. To give her a folder she can write, make a **new, empty** subfolder
and mount only that one, owned by her work account. It has to be a separate, empty folder: her
work account must not be able to change or delete anything else on the NAS, and everything under
what you mount becomes hers. Never mount the whole share for her, and never open a folder that
already holds your files.

1. On the NAS, create an empty folder: `<share>/palli/workbench`.
2. Create the mount point and add one line to `/etc/fstab`. `<uid>` and `<gid>` are her work
   account's (`id -u palli-work`, `id -g palli-work`); `<options>` are the guest or credentials
   options your other NAS mount uses:

   ```
   sudo mkdir -p /mnt/palli-nas
   //<server>/<share>/palli/workbench /mnt/palli-nas cifs <options>,uid=<uid>,gid=<gid>,dir_mode=0770,file_mode=0660,nofail,_netdev 0 0
   sudo mount /mnt/palli-nas
   ```

3. Point her workbench at it with one line in `.env.local`, then restart her:

   ```
   WORKBENCH_NAS_DIR=/mnt/palli-nas
   ```

4. Ask the doctor. It checks the folder as her work account, and when she cannot write there
   `doctor --fix` prints this recipe with her numbers filled in:

   ```bash
   venv/bin/python scripts/palli_ctl.py doctor
   ```

---

## 6. Voice (optional)

Voice ships **off** and is not required for anything else. It is off rather than on-and-broken
for a reason: with it enabled and no speech-to-text key, configuration validation fails and she
will not boot at all. Turn it on in `.env.local` once the provider below is set up:

```
ENABLE_VOICE_FEATURES=true
```

**Speech to text** — `voice.stt_provider` in `data/palli.yaml` ships as `faster_whisper`, which
runs locally and needs one more install, deliberately kept out of `requirements.txt` so a
text-only setup does not drag in a machine-learning stack:

```bash
venv/bin/pip install faster-whisper
```

It downloads its model on first use (`VOICE_LOCAL_STT_MODEL` picks the size, `small.en` by
default). The alternative is `deepgram`, which needs `DEEPGRAM_API_KEY` — and a blank key with
`deepgram` selected fails configuration validation at boot; see Troubleshooting.

**Text to speech** — `voice.tts_provider` ships as `pyttsx3`, the offline system voice: no key,
and it sounds like it. `lemonfox` sounds far better (set `LEMONFOX_API_KEY`, and
`LEMONFOX_TTS_VOICE` to choose the voice); `deepgram` is the third option. Lemonfox degrades
gracefully — with no key the client logs and returns nothing, so voice goes quiet rather than
blocking startup.

`pyttsx3` is **not** in `requirements.txt` either — it is imported when she first speaks, so an
untouched install fails at that moment rather than at startup:

```bash
venv/bin/pip install pyttsx3
sudo apt install espeak-ng        # Linux: pyttsx3 needs a system speech engine
```

You also need `ffmpeg` on PATH, and the *Connect* and *Speak* permissions from section 2. Once
she is running, use the slash commands in a server she can see: `/join_voice`, `/leave_voice`,
`/voice_mode`, `/voice_presence`, `/voice_focus`, `/voice_status`. They are the owner's: anyone
else who tries one gets a single private line saying so.

Voice *messages* — the recorded notes people send in a chat — are transcribed into the turn on
all three doors, the phone app's included, even with the live voice stack off. Sending them
back depends on `voice.voice_notes`. With it off, Telegram's `/voice on` in a chat means that a
voice note you send gets a voice note back beside the words; a message you type does not. With
it on, she can send voice messages on all three doors, and asking her (or Telegram's
`/voice on`) makes her replies in that chat voice messages until it is turned off, across
restarts (section 2b).

---

## 7. Running her

**Check before you boot.** This runs every check a real start runs and starts nothing:

```bash
venv/bin/python main.py --preflight-only
```

Exit 0 means she can start; anything she had to work around — a feature turned off for lack
of a key, a setting that is set oddly — is listed under "Warnings for this boot", and each
line names the setting it is about. Exit **2** means she cannot start yet, and the numbered
punch list above it names each setting, why it matters, and where to put it. Work down the
list and run it again. (Add `PALLI_PREFLIGHT_OFFLINE=1` to skip the live brain and balance
checks when you have no network.)

**The simple way.** One process: the backend, plus whichever doors are on — the Discord
client, the Telegram client, or both — each one started when its token is set (Telegram also
needs `ENABLE_TELEGRAM_BOT=true`, which ships false):

```bash
venv/bin/python main.py
```

Boot order is: settings → database → plugin runtime → runtime recovery → local owner → runtime
services, then the inner-monologue, night-cycle and reminder loops. Health check:

```bash
curl localhost:8000/api/health
```

**In tmux**, so it survives your shell closing:

```bash
tmux new -s palli
venv/bin/python main.py
```

Detach with `Ctrl-b d`, come back with `tmux attach -t palli`.

**As a systemd service.** The repo ships a unit template, `deploy/palli.service`, written for
a checkout at `/opt/palli` owned by a system user named `palli`; its header comments walk
through the whole install, including creating that user. If your path or user differs, edit
the `User`, `Group`, `WorkingDirectory` and `ExecStart` lines. Prove the start as that user
first, then install the unit:

```bash
sudo -u palli /opt/palli/venv/bin/python /opt/palli/main.py --preflight-only
sudo cp deploy/palli.service /etc/systemd/system/palli.service
sudo systemctl daemon-reload
sudo systemctl enable --now palli
journalctl -u palli -f
```

The preflight comes first because a service that restarts every five seconds on a missing key
is a log file, not a companion. Keys stay in the checkout's `.env.local`, never in the unit — a
unit file is world-readable. Note that she writes continuously to `data/`, `identity/`, `logs/`
and `backups/`, so the service user must own the checkout.

**With the dashboard.** `palli_ctl start` manages the backend and the Next.js frontend as
background services, restarting each and tracking their state:

```bash
venv/bin/python scripts/palli_ctl.py prepare
venv/bin/python scripts/palli_ctl.py start
venv/bin/python scripts/palli_ctl.py status
venv/bin/python scripts/palli_ctl.py restart
venv/bin/python scripts/palli_ctl.py stop
```

`prepare` installs the website dependencies if they are missing and builds the dashboard.
`start` refuses while the *dependencies* are missing; a missing build it simply builds on the
way up. It prints the backend and frontend addresses when it comes up.

The frontend has two pages worth knowing. `/` is **the door** — her brain, the chat panel and
the doors card, section 3. `/dashboard` is mission control, unchanged, and one link away from
the door. On a self-hosted install (`PALLI_DEPLOYMENT_MODE=local`, which is the default) the
account and plan pages left over from the hosted product are hidden from every menu, and
`/account` itself is one card saying accounts are not used in a self-hosted install. Nothing is
deleted; the full redesign decides what happens to them.

The backend binds `BACKEND_HOST` in `.env.local`, and that is `127.0.0.1` — this machine only.
The dashboard has no login, and its chat endpoint can reach her shell, so reaching it from
another device is a deliberate act: set `BACKEND_HOST=0.0.0.0` **and** list that device's
network in `BACKEND_ALLOWED_NETWORKS` (a tailnet is the safer half of that pair than a LAN).
Reaching her by a **name** rather than an address (a LAN or tailnet hostname, for her page or
the backend) also takes listing that name in `BACKEND_ALLOWED_HOSTS`: the backend answers only
requests addressed to `localhost`, an IP address, or a listed name, so a web page that points
its own domain at your machine (DNS rebinding) gets a 403 instead of a session.

Logs:

```bash
venv/bin/python scripts/palli_ctl.py logs
venv/bin/python scripts/palli_ctl.py logs backend --follow
```

The targets are `launcher` (the default), `backend` and `frontend`.

**Keeping her running.** `deploy/palli.service` is a systemd unit template — copy it to
`/etc/systemd/system/`, edit the user and the two paths at the top, and
`systemctl enable --now palli`. There is also a `Dockerfile`; mount `data/`, `identity/` and
`.env.local` into the container, or every restart is a PaLLi with no past. Both files say to
run `--preflight-only` first, and both mean it.

If you want to prove a clone of this repo boots on a machine that has never seen it,
`scripts/fresh_clone_proof.sh --full` does exactly that and prints `PROOF OK`.

You can also talk to a running instance from the terminal, which is useful for checking she is
alive without opening Discord:

```bash
venv/bin/python scripts/palli_ctl.py chat "are you there?"
```

**Back her up.** `data/` and `identity/` are the parts of her that cannot be re-cloned:

```bash
venv/bin/python scripts/palli_ctl.py backup
```

That writes `backups/palli-backup-<timestamp>.tar.gz`. Every database goes in as a
`VACUUM INTO` snapshot, so it is safe to run while she is up. Two things are always left
out, because a backup is a file you hand to someone: no `.env*` file, and nothing under
`data/delegations/` (host credentials) — copy those somewhere else, separately.

---

## 8. Updating

```bash
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python scripts/palli_ctl.py doctor
```

Then restart her. Two things to know:

- **Your `data/palli.yaml` is never overwritten.** New settings added upstream arrive in
  `data/palli.example.yaml` and are *not* merged into your file. When a release adds a flag,
  diff the two files and copy across what you want.
- **Her memory and journals are yours.** `data/` and the per-owner parts of `identity/` are
  gitignored. Nothing in an update touches them.

If you ever want a clean slate without losing her self-authored pages, there is a script for it
— read it before you run it:

```bash
venv/bin/python scripts/wipe_palli_memory.py --help
```

---

## 9. Troubleshooting

Start here, always:

```bash
venv/bin/python scripts/palli_ctl.py doctor
```

It prints one line per check, prefixed with the severity, and exits nonzero if any check is a
`CORE_BLOCKER`.

**What the severities actually mean.** `CORE_BLOCKER` means `palli_ctl start` will refuse. It
does **not** mean `main.py` will: the Discord path needs a valid config, a writable database and
the repo venv, and nothing else.

**Node and npm are prerequisites for a green `doctor`.** `node_missing` and `npm_missing` are
core blockers unconditionally — the doctor has no "Discord only" mode — and with npm absent
`frontend_deps_missing` becomes one too. So a reader who installs nothing but Python and skips
the dashboard gets a clean, correct install that still reports **three core blockers and exits
1**, while `venv/bin/python main.py` runs perfectly. They are also what the door itself runs
on, so skipping them means setting a provider key in `.env.local` by hand (section 1).

That is confusing, and it is a real wart rather than a subtlety. The one-line change to demote
those checks when nothing depends on them is **recorded as a follow-up, not built**. Until it is:
install Node and npm if you want `doctor` to come back green, or read past those three lines.
(`frontend_build_missing` is already a warning either way.)

These are the three blockers you are most likely to see.

### `python_venv_missing`

> `Expected repo virtualenv Python at .../venv/bin/python.`

The launcher looks in exactly one place: `venv/bin/python` inside the repository. A virtual
environment named `.venv`, or one at a different path, or a conda environment, will not be found
no matter which interpreter you invoke `palli_ctl` with. Create it where it expects:

```bash
python3.13 -m venv venv
venv/bin/pip install -r requirements.txt
```

### `config_invalid`

> ```
> [CORE_BLOCKER] config_invalid: Config validation failed.
>     - EXA_API_KEY is required when web search features are enabled
> ```

Something in your configuration asks for a key or a value that is not there. The indented line is
the reason, and it names the variable. **One reason at a time:** the validators run as a chain and
stop at the first refusal, so fix what it names, run `doctor` again, and see whether a second one
appears behind it.

The three you are most likely to meet, all from turning something on before its key exists:

- **`EXA_API_KEY is required when web search features are enabled`** — you turned on
  `features.web_search`. Either set an [Exa](https://exa.ai) key in `.env.local`, or put the
  feature back: `venv/bin/python scripts/palli_ctl.py settings set features.web_search false`.
- **`DEEPGRAM_API_KEY is required when STT_PROVIDER=deepgram`** — voice is on with a provider you
  have no key for. Set `ENABLE_VOICE_FEATURES=false` in `.env.local` if you do not want voice
  yet; voice settings are unconstrained while it is off.
- **`LLM_RESPONSE_PROVIDER=deepseek requires DEEPSEEK_API_KEY`** (or any of the other
  `LLM_*_PROVIDER` overrides, or the OpenAI equivalent) — a *per-lane* override in `.env.local`
  names a cloud provider whose key is blank, and it is a **different** provider from the one
  `LLM_PROVIDER` names. Set the key, clear the override, or move her back to the local setup she
  ships on: `venv/bin/python scripts/palli_ctl.py brain local-only`.
  Two things that are *not* blockers any more, because both are setup mode (section 3): the
  top-level `LLM_PROVIDER` with a blank key, and an override naming that **same** keyless
  provider — it is asking for the very key the door is about to supply. She starts, says she has
  no brain, and names the address to add one at.

The shipped configuration hits none of these; every one of them is something you switched on.

### `node_missing` / `npm_missing` / `frontend_deps_missing` / `frontend_build_missing`

> `Node.js is required for the packaged local dashboard.`
> `Website dependencies are missing; run \`venv/bin/python scripts/palli_ctl.py prepare\`.`

All four are about the web dashboard, and **none of them stops Discord** — `venv/bin/python
main.py` starts the backend, and the Discord client when a token is set, with no Node involved,
and `onboard` says so rather than trying to start something that cannot start. They do stop the
**door**, though, which is why section 1 lists Node and npm as required: the door is a page on
that dashboard, and `palli_ctl start` refuses while `frontend_deps_missing` is one of these.

They are not reported alike, though, and it is worth knowing which is which. `node_missing` and
`npm_missing` are **core blockers** regardless of what you plan to run, and `frontend_deps_missing`
joins them when npm is absent — which is why a Python-only install still exits 1 (see the
severity note at the top of this section). With Node installed, the two website checks are
warnings and `doctor` comes back green. Install Node and npm, then:

```bash
venv/bin/python scripts/palli_ctl.py prepare
```

### Other things that go wrong

- **`local_llm_unreachable`** — `doctor` asked the local model server for its model list and got
  nothing. She will start, and every reply will fail. Install Ollama and run `ollama serve`, or
  point `LOCAL_LLM_API_BASE` / `OLLAMA_BASE_URL` at the machine that runs your models. It is a
  warning, not a blocker: running the models on another box is a real install.
- **`local_llm_model_missing`** — the server answers, but the tag her voice is pointed at is not
  pulled there. The check names it: `ollama pull qwen3.5:9b`.
- **`llm_brain_missing`** — a warning, not a blocker: she has no brain configured at all. That
  is setup mode; the line names the address where you add one, and section 3 is the walk.
- **Every answer is "I don't have a brain yet".** Setup mode again, from the other side. She
  says that one sentence to everything she is sent until a key exists, and none of her
  background lanes run. Paste a key on the door, or set it in `.env.local` and restart.
- **The door's page does not load.** It is the dashboard, a separate service from the backend.
  `venv/bin/python scripts/palli_ctl.py prepare` then `start`, and read the Frontend address it
  prints. `venv/bin/python main.py` alone serves no web page.
- **She connects but never replies.** Almost always the Message Content intent (section 2). The
  bot appears online, receives events, and sees empty message bodies.
- **`backend_port_external`** — something else already owns `:8000`. Either stop it, or move her
  in `.env.local` — and move **both** keys, because the URL pins the port too:

  ```
  BACKEND_PORT=8123
  BACKEND_URL=http://127.0.0.1:8123
  ```
- **She replies through `palli_ctl chat` but not on Discord.** Check `ENABLE_DISCORD_BOT=true` and that
  `DISCORD_TOKEN` is non-empty. With no messaging platform configured she logs
  `No messaging platforms configured; running in API-only mode` and starts anyway.
- **`TELEGRAM_BOT_TOKEN` is blank.** The Telegram door is closed for that boot and the
  preflight says so by name. It is a warning, never a refusal: she starts, and the
  backend, the dashboard and any other door stay exactly as they were. Section 2b is the
  five-step walk to a token.
- **`python-telegram-bot` is not installed.** A token with no library is a door that
  opens on nothing, so the preflight closes it for that boot and names it. Run
  `venv/bin/python -m pip install -r requirements.txt` and restart her.
- **A model call times out.** Every model call belongs to a classified timeout lane; a local
  model on modest hardware will exceed the fast lanes. Move the background lanes to a smaller,
  faster model with `brain --lane`.
- **She replies but the model name is wrong.** Her voice reads `LLM_PROVIDER` and
  `LLM_RESPONSE_MODEL` from `.env.local`, not the YAML. If you edited `data/palli.yaml` by hand,
  you moved nine lanes and left the tenth. `brain` writes both.
- **She does not know your name.** She asks at first contact — unless
  `LOCAL_OWNER_DISPLAY_NAME` was set to your real name, which makes the first-contact check
  believe it is already answered. See "Your own journey" in the README.

### Where to look next

```bash
venv/bin/python scripts/palli_ctl.py doctor --json
venv/bin/python scripts/palli_ctl.py settings list
venv/bin/python scripts/palli_ctl.py forge list
```

`doctor --json` gives the full report with per-check details, which is the right thing to paste
into a bug report. `forge list` shows the tools she has written for herself, and
`forge revoke <name>` / `forge restore <name>` bench and un-bench one — the change takes effect
on her very next turn. The forge itself ships **off**: "The forge, experimental and off" in
section 5 says what it does and the one line that turns it on.

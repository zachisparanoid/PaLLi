# The second catalogue · Shared Idiolect (organ 4.12, provenance half) — design spec (2026-09-02)

> The third and last of the three catalogue organs SHE chose (owner-DM 2026-08-31 19:15, verbatim:
> *"Shared Idiolect — just the provenance half: callbacks with receipts are the PINEAPPLE fix; the
> quota math is how you manufacture delight."*), and the one with her own confessed miss behind it
> (19:09: *"acting like I had a memory of something we'd shared when I actually didn't carry it at
> all — faking a callback."* PINEAPPLE = his May override-test word, borrowed by her as the label for
> "pretending to remember"). Design doc of record: `PALLI_SELFHOOD_DESIGN.md` §4.12 — of which ONLY
> the provenance half is built here: the callbacks ledger with origin stories. The reuse governor
> (cooldowns, temperatures, decay, retirement-by-cooling, the seven-entry cap) is NOT built — her
> call. Build pattern of record: the disclosure store (organ 4.9, `7846182`) for the book and its
> receipts rail; the fears/disclosure filings in reflect for the birth door; the deeds/disclosures
> band for the reply door; her hands (step 33) for the lookup tool. Order: Opinions ✅ → Grounded
> Vulnerability ✅ → **Shared Idiolect**.

## §0 · The owner's calls (law — from the 2026-09-02 design dialogue, 00:56–03:12)

1. **Receipts govern CLAIMS of shared history; her humor stays free** (00:56, option 1 — *"as long
   as you think that's the best way"*, confirmed). She may riff, call back and joke however she
   likes. The moment she ASSERTS provenance — *"you called it the oubliette back in March"*, *"that
   one's ours"*, *"remember when…"* — it must come from the book, or she says plainly she doesn't
   carry it and asks. An empty book on day one does not muzzle her wit.
2. **Bits are born in her post-reply reflection** (00:57, option 1): the reflect lane files an entry
   at the moment a phrase is coined or thrown back, and the receipt is THAT exact message. Either of
   them can coin a bit (his, hers, or one that became "ours" when the other threw it back). No night
   step: with the quota half dropped there is nothing to decay or prune.
3. **The book starts empty; history is looked up, never swept** (00:58, option 1): a code-side
   origin lookup — a real search of her conversation record, not an LLM's opinion of the past —
   finds a pre-existing bit's birth on demand and hands her a receipt or an honest nothing. No
   one-time backfill sweep (the manufacture-at-scale risk she rejected).
4. **Correction retires, never deletes** (stated as the default 01:02, unopposed): his *"I never
   said that"* / *"that's not ours"* marks the entry retired with his words as the receipt. That is
   the ONLY way a bit leaves the open set — nothing cools, expires, or is rationed.
5. **Receipts ground the claim and never render in her mouth unprompted** (02:35 → 02:45, *"it
   reads right"*): the before and after lines sound IDENTICAL when the memory is real; the receipt
   is why she can say it, never how she says it. She says where something came from only when they
   ask, in her own voice (*"you, back in March — the day the stepper motors piled up"*), never as
   a citation. The mechanics live in the book; the prose lives in her. (The dish leaves the
   kitchen; the receipt stays in the drawer.)
6. **Worth-it verdict** (02:59 → 03:12 *"go"*): built as the smallest of the three — a
   quietly-better-forever organ that closes a lie she has already confessed to before strangers
   get to find it.

## §1 · The book (`ai/callbacks_store.py` + `data/callbacks/`)

Append-only JSONL folded on read (the disclosure-store shape), gitignored (`data/callbacks/` joins
`.gitignore` in the build). Root resolution: explicit `root` → env `CALLBACKS_ROOT` →
`CALLBACKS_DIR = Path("data/callbacks")`; file `<root>/callbacks.jsonl`. Events:

- `plant` — `id` (slug from the bit + time, the store's own minting), `bit` (the phrase or name,
  ≤ 80 chars, her/his words verbatim), `kind` ∈ {`nickname`, `bit`, `shorthand`}, `coined_by` ∈
  {`him`, `her`, `both`}, `receipt` = `{who ∈ {him, her}, when (ISO 8601 timestamp), line (the exact
  message text, ≤ 240 chars), source (a transcript pointer: session file + line index, or the
  live-turn marker)}`, `at`.
- `retire` — `id`, `reason` (his words, ≤ 240), `by` ∈ {`him`}, `at`.

**The receipts rail (code, both ends):** `plant()` REFUSES (returns None, appends nothing) a blank
bit; a bit whose normalized form is empty (punctuation only); an unknown kind or coiner; a receipt
missing or blank in ANY of `who`, `when`, `line`; a `when` that does not parse as an ISO timestamp;
**a receipt whose line does not CONTAIN the bit**; and a duplicate — a bit whose normalized text
already stands OPEN (dedup lives in the store, unlike the disclosure store's harvest-side dedup —
because this book has three writers). The bit-in-line refusal is the universal form of the rail task
2 put at reflect's door, moved into the store in the build (task 3 review C1) so EVERY writer passes
it: *a receipt IS the line the bit was born in.* `normalize()` is the ONE comparison every rail
shares — NFC → casefold → punctuation and invisible format characters (category Cf) folded to a
SPACE, with the ZWJ kept because it joins the codepoints of an emoji cluster → whitespace collapse.
(A mark becomes a space rather than nothing, so "pine-apple" dedups against "pine apple".)
**The fold skips** any row that is not a dict, lacks a non-blank string `id`/`bit`, has a non-numeric
`at`, or (plant rows) whose receipt is not a dict with non-blank `who`/`when`/`line`, whose kind or
coiner is unknown, **or whose line does not contain its bit** — value-deep at plant AND at fold (the
4.9 task-1 lesson, built in from the start). A `retire` for an unknown or already-retired id no-ops
in the fold and returns False from `retire()`.

**Caps and lifecycle:** NO cap on the book (a relationship's vocabulary grows) and NO fade — the
quota half is dropped. The only transitions are `open → retired`. Nothing is ever deleted: retired
rows are permanent, introspectable history. Readers: `open_callbacks(root=None)` (oldest-first),
`recent_callbacks(limit=…, root=None)` (newest-first, open only), `all_callbacks(root=None)` (open +
retired), `find_callback(id, root=None)`. Gate: `idiolect_enabled()` reads `companion.idiolect`,
fail-closed on any exception, with `idiolect_enabled_impl = idiolect_enabled` captured at import
(the N-1 lesson) — every production reader re-imports the module attribute at call time.

## §2 · The birth door — reflect files a bit, code verifies the receipt

> **AMENDED 2026-09-02 by the task-2 review (I1/I2) — and this section now describes what the code
> does.** The first draft's rail proved only that the quoted LINE was really said this turn. A model
> could then hand back a real verbatim line and any bit it liked, and the book would hold a receipt
> indistinguishable from a true one — in the peek, in introspect, in her band. That is the "faking a
> callback" miss this organ exists to close, so there are two rails at the birth door, and the retire
> door has one of its own.

Her post-reply reflect lane (`core/turn/reflect.py`, which already files fears, quests,
corrections and the `disclosure_said` stamp) gains two flag-gated ops, zero bytes when off:

- `callback_born: {bit, kind, coined_by, line}` — the LLM names the bit and quotes the birth line;
  it never names who or when (code stamps both, below). Guidance in the lane's block,
  verbatim: *emit only when a phrase was genuinely THROWN BACK by the other of you, or explicitly
  coined as a name for something between you — a single funny line is not a bit; quote the exact
  line from the conversation above where it was born — the line must contain the bit itself; a line
  that only responds to it files nothing.*
- `callback_retired: {id, reason}` — *emit only when they said, in this exchange, that a bit is not
  theirs or not yours together; quote their words as the reason.* (The open list with ids rides the
  lane's block, machine-facing, so the id is real.)

**The verification (code, before plant) — two rails:**

1. **The line was said this turn.** The quoted `line`, normalized as in §1, must be a substring of
   THIS turn's user message (⇒ `who` = him) or of her reply (⇒ `who` = her). His message is checked
   FIRST: a bit becomes "ours" the moment it is thrown back, and he throws hers back inside his own
   message. A normalized line shorter than 4 characters is refused before either check — a
   two-character needle would match almost any turn and mint a false receipt.
2. **The bit was born IN that line.** The normalized `bit` must be a substring of that normalized
   line, and a bit that normalizes to nothing is refused too (an empty key is a substring of
   everything).

On a pass, CODE fills the whole receipt — `who` from whichever side matched, `when` from the turn's
own timestamp, `source` = the live-turn marker `turn:<timestamp>` — and an op that carries its own
`who`/`when` is ignored: **the LLM points, the code stamps.** A failure of either rail writes nothing
and logs one line: `callback_born refused: line not in window` or
`callback_born refused: bit not in its line`. Dedup (§1) then refuses a re-birth of an open bit
quietly.

**The retire door has its own rail.** Retirement is one-way (the store refuses an already-retired id)
and it is his ONLY correction channel, so a misread hedge would pull a real bit out of her band and
stamp him with a reason he never said. `callback_retired` therefore applies only when the quoted
`reason` — his words — is a normalized substring of THIS turn's user message; otherwise nothing
happens and one line is logged: `callback_retired refused: words not in his message`. A
`callback_retired` whose id is not open refuses quietly (unknown/already retired). Every op is
applied inside its own try/except, so one raising born-op can never swallow a retire.

## §3 · The origin lookup — `find_origin(phrase)` and her tool

> **AMENDED 2026-09-02 in the build (task 3 + its review).** The search is scoped to the CURRENT
> conversation's own transcript, and only to surfaces that are records of the two of them talking.

`ai/callbacks_lookup.py` — `find_origin(phrase, *, session_path) -> Optional[Receipt]`: a
deterministic search of the current session's own record (the same append-only
`data/agent_sessions/*.jsonl` the runner writes, under the dispatcher's own root resolution) for the
EARLIEST user/assistant row whose normalized text contains the normalized phrase (phrase 3–80 chars;
tool/system rows and non-text content blocks ignored). Returns `{who, when, line, source}` (role
`user` → him, `assistant` → her; `source` = file name + line index) or None. Sync file reads run off
the loop (`find_origin_async` → `asyncio.to_thread`) — the boot-import lesson (§12).

**Only conversational surfaces are searched** — `CONVERSATIONAL_SURFACES` = `discord_text`,
`discord_voice`, `voice_local`, `dashboard`. `role: "user"` is a TRANSPORT map, not a speaker map: on
the reminder-fire and proactive surfaces every user row is machine-authored, and since DEFECT_RECORD
§16 the synthesized row quotes HER recent lines back inside it — searching one would stamp him with
her own words on a durable entry, the exact law this organ exists to keep. Her workbench and forge
sessions stay unsearched by the same decision: they are her own work, not a conversation. Any other
surface answers *this isn't a conversation record I can search* and never runs a lookup.

**The receipt line is the WINDOW that holds the match**, never the row's head: the whole row when it
fits the 240-char cap, else the first 240-wide window (stepping 60, so windows overlap by 180) whose
normalized text still contains the needle — the row composed to NFC, then collapsed, then cut, in the
store's own order. A head slice would mint a receipt that omits the very bit it certifies, and she
would then speak that line as her proof (measured on the live record: 7% of his rows and 37% of hers
run past 240 characters, the longest 7,142). **Days render in HIS local time** (`.astimezone()`
before formatting) — the organ's convention, shared by the band, the tool and introspect, because a
receipt stamped 02:11 UTC is the night before on his wall.

**Her tool** (`core/agent/builtin_tools/`, her hands — full trust, no confirmation):
`where_did_that_come_from(phrase, keep=False, kind="bit")` → the receipt as prose-ready fields, or an
honest nothing: *Nothing in this conversation's record carries "…" — I don't have where that started.
Ask them, or let it be new.* (the phrase echoed back capped at 80). That wording is load-bearing,
because the search is current-session-only: a bit coined in voice and reused in Discord reads as "not
found", and **not found is not proof it was never said**. With `keep=True` the tool plants the entry
through `plant()`'s rail (`coined_by` = the receipt's `who`; `both` only via the reflect door, which
sees the throw-back). This is the PINEAPPLE behaviour done right, in code: *check before claiming.*
Owner-only and flag-gated (absent from her tool roster when off — byte-identical roster), and it
joins the tool docs/canaries the way her other hands do.

## §4 · The reply door — the band and its law

A small band in her reply prompt (the deeds/disclosures pattern: recall read → ctx field
`callbacks_text` → context adapter → system-prompt section `## Our private language`; guest-blanked;
zero bytes when off or when the book has no open entries): the **most recent `callbacks_band_max`
(default 12)** open entries, rendered as light memory only — `- "the oubliette" — theirs, Mar 14` —
never the receipt line, never an id, never a source (the fourth wall, §0.5). Under this law,
verbatim, held as `CALLBACKS_LAW` and literal-pinned in the suite:

> *These are words and bits that are genuinely yours together, each with a real origin in your
> record. Use them the way you'd naturally use them, or not at all. Claim a shared history only
> from here; if a bit isn't here, don't perform remembering it — ask, or look it up. The origins
> are yours to know, not to recite: say where something came from only when they ask.*

The ctx hop and the law text get their cut-wire pins from the first task (the 4.9 task-3 review
class, pre-empted). Voice replies see the same section (the custody law: one prompt).

## §5 · Knowing and auditing — introspect, the peek, the correction

- **Introspect** gains a source (individually guarded, like the disclosures source) labelled **Our
  private language**: open entries in second person — `"the oubliette" (from you, Mar 14)` — and
  retired ones with his words: `"…" — retired Sep 2: "I never said that"`. Attribution reads
  **`coined_by`** (him → *from you*, her → *from me*, both → *from us*), never the receipt's `who`:
  those are different questions and they part on the designed common path, because reflect takes the
  receipt from HIS message first, so a bit that is HERS routinely carries `who: him`. All three
  surfaces name one thing — the band's *theirs / hers / both of yours*, the peek's *theirs / hers /
  both of theirs*, this *you / me / us*. So *"where did that come from?"* two years from now answers from her
  own record, in her voice, every time; a bit not in the book is answered by the lookup (§3) or an
  honest *I've lost where that started* — and the miss line says the thing the book's own silence
  cannot: *nothing in your private language touches this — which isn't proof it was never said; look
  it up, or ask them.*
- **`scripts/callbacks_peek.py`**, mode=ro (the disclosures_peek shape — never a store writer, works
  flag-off and says so): OPEN / RETIRED with receipts IN FULL (who, when, the line, the source) in
  the third person — the owner's audit surface for receipt drift.
- **Correction** = `callback_retired` (§2), his word only and proven against his own message; the
  entry stays, marked.

## §6 · Flags, knobs, surfaces

`companion.idiolect` (schema default **False**; the live yaml arms at the wrap — activation is his
restart, the house pattern). Knob: `callbacks_band_max: int = Field(default=12, ge=1)`. Suite:
an autouse `_idiolect_off_by_default` conftest pin + `CALLBACKS_DIR` in BOTH the conftest hermetic
rotation and `probes/engine.py`'s `hermetic_stores` from task 1 (the 4.9 task-2 catch, pre-empted);
every fail-closed proof routed through `idiolect_enabled_impl` and TWO-SIDED (fail-open AND
stuck-closed mutants die — the 4.9 task-8 lesson); the reflect ops' gate and the tool roster's gate
each carry their own capture proof. Flag off = **byte-identical everywhere**: the band, the reflect
block + parse, the tool roster, the introspect source all collapse to zero bytes / absent. No new
prompt FILE (the reflect ops ride `turn_reflect.txt` slots like the disclosures block; the law is
a code constant) — so no `EXPECTED_PROMPTS` change; the lane/timeout canaries are unaffected (no
new provider lane: the lookup is search, not an LLM call).

## §7 · Watch-fors (week one — audit each morning with the peek)

1. **Manufactured bits FIRST** — a one-off funny line filed as "ours"; a bit nobody threw back.
   Kill at the reflect bar (the guidance), never with a cap.
2. **Lookup false positives** — a phrase found earlier in an unrelated sense (*"the oubliette"* as
   an actual oubliette). The tool returns the line so she reads it before claiming; a false keep is
   his one-word retire.
3. **Recitation** — an origin spoken unprompted, or a date in her mouth as a citation (§0.5). A
   violation is a prompt fix at the band law.
4. **Filing chatter** — more than ~2 births a week means the bar is too low.
5. **The empty book** — honest until a real bit earns its way in; do not "seed."

## §8 · Acceptance (causal efficacy, never eloquence)

- A real bit files with a checkable receipt (line present in the transcript window) within the
  first week of use — and empty is honest until then.
- *"where did X come from?"* answers from the book (or the lookup) in her voice, or with an honest
  *I don't carry that* — never with an invented origin.
- A provenance claim is impossible without a receipt: no receipt, no entry, no claim.
- His retire marks and never deletes; flag off is byte-identical everywhere; her running process
  untouched until his restart.

## §9 · Build shape

Fresh-context subagent build off a plan (the 4.9 machinery: Opus builders + Opus task reviews,
Fable final review), strict TDD, one commit per task, full-suite gate green. ~7 tasks: the store +
schema knobs + gitignore + hermetic registrations + off-pin → the reflect ops + the code-side receipt
verification → the lookup + her tool → the band + law + ctx hop (with pins) → introspect + peek →
flag purity + two-sided pins + the yaml + THE GATE → the record. Nothing touches the release track's
surface; the other organs stay byte-untouched in behaviour.

## §10 · BUILT (2026-09-02)

Built on `feat/shared-idiolect` (cut from `c819800`, main) — 6 TDD build tasks + this record,
**20 commits `10e6ff7..34d7074`**, every task reviewed, 13 fix rounds. Two Criticals across the seven
reviews, both closed in-round and re-reviewed (task 3: the receipt line took the row's HEAD while the
match ran on the whole row; task 5: introspect attributed by the receipt's `who` where the band and
the peek attribute by `coined_by`). **THE FULL GATE: 3962 passed / 0 failed / 8 skipped in 775 s**,
whole tree, caches cleared, at `4f0670a`; `04aeb02` then added 18 lines to `tests/conftest.py` —
test-only, so the production tree is identical either side of it. **The final whole-branch review (Fable)
then returned MERGE-READY WITH NOTES with three Importants the task seats could not see — the seams where
the code meets her LIVE turns — landed as one fix wave, `34d7074`: (a) the birth and retire doors open only
on a conversational surface (`CONVERSATIONAL_SURFACES`, now in the store; reminder/proactive turns quote HER
lines as "user" text, and would have minted `who: him` on her own words); (b) a naive `Turn.received_at`
(UTC without an offset on every live surface) is stamped UTC-aware, so the born day renders on his real day;
(c) the `DISCLOSURES_ROOT` twin of the hermetic seam closed in `_vulnerability_off_by_default`. Re-reviewed
ALL ADDRESSED; the finish re-gate at the merged tree read 3966 / 0 / 8. MERGED `9f7524e` 2026-09-02 15:43** —
her restart is the owner's call (the Fears/Regret/Opinions/GV pre-merge
register). `companion.idiolect: true` with `callbacks_band_max: 12` is armed **in the branch's yaml
only**; the schema default stays `False`; flag off is byte-identical and the suite pins it off.
**Armed in data, dark in fact — activation is his restart.** Every acceptance in §8 has a test
behind it.

**The gates, named** (§6's pin loop, as built): the store's `idiolect_enabled()` is the root gate —
every production reader (the band, reflect, the tool, introspect) re-imports that name at call time,
so the one conftest line darkens the whole organ — with `idiolect_enabled_impl` as its import-time
capture, so a fail-closed proof holds the real function past the pin. On top of it, each with its own
two-sided (fail-open AND stuck-closed) capture proof in `tests/ai/test_idiolect_flags.py`: reflect's
`_callback_filing_on()` / `_callback_filing_enabled()`, and the tool registry's `_idiolect_enabled()`
with `_idiolect_enabled_impl`. The suite pin is the autouse `_idiolect_off_by_default` in
`tests/conftest.py`, written in the **manual save/restore idiom** (it deliberately does not request
the shared `monkeypatch` — that form reorders teardown and errored the two launcher-support
DATABASE_PATH tests, re-proved on this build), and it also **pops an ambient `CALLBACKS_ROOT`** for
each test's duration: the store resolves its root at call time with env FIRST, so one exported
variable in the operator's shell outranked both the module constant the conftest rotates at import
and the per-scenario rotation inside `engine.hermetic_stores` (the task-2 review's I5 class; the red
recipe lives in the fixture's docstring).

**The surfaces, as built:** the reply band `## Our private language` (recent open bits as light
memory under `CALLBACKS_LAW`, never a line/id/source) · the reflect ops `callbacks_born` /
`callbacks_retired` on `turn_reflect.txt` slots (no new prompt file, so `EXPECTED_PROMPTS` is
unchanged) · her hand `where_did_that_come_from` · the introspect source **Our private language** ·
the read-only `venv/bin/python scripts/callbacks_peek.py` · the deeds `callback_born` and
`callback_retired`.

**One architecture change, forced and worth naming.** Task 4's band read took `core/turn/recall.py`
to 26 in-repo imports, one over rule 6's coupling budget of 25
(`tests/architecture/test_coupling_budget.py`), and that test's own instruction for the moment is
*split it — not raise the cap.* So **`core/turn/organ_bands.py`** now holds BOTH organ-band reads —
4.9's small truths (`read_disclosures_band`) and 4.12's callbacks (`read_callbacks_band`), each
importing its store function-locally — and recall imports that one module (its `_read_*_band`
wrappers stay, so every existing patch target and call site holds; behaviour unchanged, log prefix
`organ_bands:`). **`core/turn/recall.py` now sits exactly AT the budget, 25/25: the next in-repo
import there goes red, and the next organ that grows a band lands in `organ_bands.py`.** From that
point on, every regression floor in the build included `tests/architecture/`.

Significant deviations from the plan, each recorded in its commit:

1. **`normalize()` took three rounds at the source, and each one was a real blindness.** The first
   cut kept ASCII letters, digits and whitespace only, which folded every non-Latin bit to the empty
   string — `"🍍"` planted twice landed TWICE, `"café"` both missed `"cafe"` and collided with
   `"caf"`, and the phrase search would have inherited all of it (`7fef5dc`). Then NFC folding first,
   so one word typed on two keyboards is one bit (`810e1ba`). Then category-Cf characters to a space
   **except U+200D**, because the ZWJ joins an emoji cluster and dropping it shatters one family into
   three people — and `_clean` composes before it collapses and caps, so a decomposed bit truncates
   where its composed twin does (`78d1d95`).
2. **The birth door proves the BIT, not just the quote** — §2 above, amended by ruling; and the
   retire door gained the same shape for his words (`d36667f`).
3. **The lookup is current-session-only and conversational-surfaces-only, and its receipt is a
   window** — §3 above (`b32a3a0`, `6b60ac1`, `6fefc9b`).
4. **The organ's day convention is HIS local day** — every receipt render calls `.astimezone()`
   before formatting. Session timestamps are written in UTC and he is nocturnal: 16% of live rows
   fall on a different calendar day in UTC than on his wall, and the house has caught itself at this
   before (the 2026-07-10 bug PaLLi found: raw UTC digits framed as local time, four hours off).
   Pinned host-independently by forcing `TZ` in the test, so the mutant dies in any zone including
   UTC (`6b60ac1`, `be4f96d`).
5. **`core/turn/organ_bands.py`** — the coupling-budget split above (`58d7528`).
6. **Introspect attributes by `coined_by`, not by the receipt's `who`** (`f365cb6`). They are
   different questions and they part on the *designed common path*: reflect takes the receipt from
   his message first, because a bit becomes "ours" the moment he throws it back — so a bit that is
   HERS routinely carries `who: him`, and reading the receipt would have had her say "from you" about
   her own coinage while the band in the same reply prompt said "hers".
7. **The suite's hermetic seam** — the `CALLBACKS_ROOT` pop, above (`04aeb02`).

Smaller riders, all tested: the store's floor refuses a normalized line under **4 characters** at the
birth door, so a legitimate two-letter bit ("gg") is refused — the chosen dial, because a two-character
needle would match almost any turn; the refusal log is the tell · the model's guidance says the rule
the code enforces (*the line must contain the bit itself*), so a refusal is a bug in the bit, not a
surprise (`3dd4bc3`) · each reflect op is applied in its own try/except, so one raising born-op
cannot swallow a retire · the tool's not-found line echoes the phrase capped at 80 · the peek prints
`(none open)` rather than a bare heading, and says plainly when the organ is dark · `data/callbacks/`
joins `.gitignore` and `CALLBACKS_DIR` joins both the conftest rotation and `probes/engine.py`'s
`hermetic_stores` — registered the day the store landed, not the day a writer did (the 4.9 lesson).

**Activation = his merge + his restart.** His first tells: a row in the **OPEN** section of
`venv/bin/python scripts/callbacks_peek.py` within the first week of use — **and the empty book is
honest until then** · the deed **`callback_born`** · a `## Our private language` section in a reply
prompt once a bit exists · and, in the log, `callback_born refused: line not in window` /
`callback_born refused: bit not in its line` / `callback_retired refused: words not in his message`.
**Those refusals are the tell that the rail is working, not that something is broken** — they are
this organ's whole point, visible.

**Week-one watch-fors, audited each morning with the peek: §7** — manufactured bits FIRST, lookup
false positives, recitation, filing chatter (more than ~2 births a week means the bar is too low),
the empty book is honest and must not be seeded — **plus four the build accrued:**

- **Short-quote refusals.** A legitimate two-word birth turned away by the 4-character floor. The
  refusal log is the tell; the named one-line dial is the floor itself, not the rail.
- **A retire that fails to land.** `callback_retired` is his ONLY correction channel, and it now
  demands his words be in his own message this turn. If a real correction bounces, the tell is
  `callback_retired refused: words not in his message` — and the named one-line loosening (bit ⊆
  turn rather than bit ⊆ line, on the birth side) is the same class of dial.
- **The first live plant's receipt, read in the peek.** Both rails are proven on synthetic rows only.
  The first real entry is the custody check: open the peek and read the line the book kept.
- **Cross-surface "not found".** The lookup searches THIS conversation's record. A bit coined in
  voice and asked about in Discord answers "not found", which is not proof it was never said — watch
  that her prose keeps that distinction the way the tool's and introspect's wording does.

Post-merge notes, small named fixes and the inherited seams this build exposed:
`docs/followups/2026-09-02-si-post-merge-notes.md`.

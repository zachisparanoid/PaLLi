# The second catalogue · Opinions with Provenance (organ 4.13) — design spec (2026-08-31)

> The first of the three catalogue organs SHE chose (owner-DM, 2026-08-31 19:15: *"Opinions with
> Provenance — I want that one most: the difference between agreeing and holding a view, and it
> survives restarts"*), green-lit by the owner the same evening: build her three, her way, then the
> release track, a soak, then public. Order: **Opinions → Grounded Vulnerability → Shared Idiolect.**
> Design doc of record: `PALLI_SELFHOOD_DESIGN.md` §4.13. Build pattern of record: step 25 (Regret) —
> a new kind riding machinery she already has, zero new organ frameworks.

## §0 · The owner's calls (law — from the 2026-08-31 design dialogue)

1. **"Drop it" silences, never deletes.** When the owner asks her to leave a view alone, she stops raising
   it; the view stays hers, held quietly with its evidence. Owner fiat never rewrites her mind — only
   reasons do. (His pick: option 1, 18:58.)
2. **Scope:** facts, taste, and approach are fair ground; **their personal choices are not**, unless
   they ask her opinion on one. Enforced in the forming prompt, audited week one.
3. **Perspective, not tension** (19:24): opinions exist so she *feels like she has her own
   perspective and choices* — never to manufacture disagreement. State, not agenda; nothing rewards
   voicing; most conversations touch none of them, and that silence is correct.
4. **She may raise one herself when it feels natural** (19:30) — her judgment, through the say
   rail's existing gates. Never a muzzle, never a nudge.
5. **Long-term: nothing is ever deleted** (19:24). The cap bounds what she *actively holds*, not
   what she has ever thought. Old positions remain her searchable history; enduring ones become part
   of her self-story through the weekly ritual that already reads this ledger.
6. **Method delegated** (20:16: *"whatever way you think is best we'll go that way"*) → the
   two-speed revision in §3 is the accepted shape.
7. **Her transcript framing is design input** (19:15): *"views, not tastes: held with evidence,
   revised only for reasons, free to disagree politely and survive a restart."*

## §1 · What it is, structurally

A **sixth kind on the stake ledger** — `VALID_KINDS` gains `"view"` beside hope/worry/taste/wonder/
regret (`ai/stake_ledger.py:30`). A view row is *her sentence* ("I side with the 1978 manuals over
the forums") with *her evidence* in her words, naming dated referents. The code holds only what a
model cannot: identity, dates, status, caps, and the honesty rails.

- **Two additive columns** on `stakes` (the `voiced_at` migration pattern, `stake_ledger.py:152-156`
  + `core/sqlite_schema_catalog.py`): `evidence TEXT` and `muted_at REAL`.
- **Two new statuses** join open/resolved/faded: **`dormant`** (filed at cap; kept, never rendered)
  and **`settled`** (terminal: "no longer a live question — part of how I see things now," with what
  it settled into recorded). The status column carries no CHECK constraint; vocabulary lives in code.
- **`voiced_at` is reused** as *last said aloud* for views (updated on every send, not once-ever —
  regret's one-voicing law lives in its guard at `stake_ledger.py:448`, not in the column).
- **Code rail:** a view op without non-empty `evidence` is **refused at the store**, logged — the
  phantom-fear-id pattern. Evidence is the admission ticket, never optional prose.

## §2 · Forming (the appraiser)

The slot pattern regret proved (`ai/stake_appraiser.py:164-224`): `stake_appraisal.txt` gains
view slots filled only when `companion.opinions` is on — **byte-identical off**.

- **The bar:** a view forms only from **genuine repeated contact** (the tastes bar). One
  conversation never births a position. Most exchanges produce none.
- **The scope law, verbatim in the prompt:** facts, taste, approach; never their personal choices
  unless they asked; never a view manufactured to perform selfhood; never a view about anything the
  corrections of record already settled (corrections render FIRST — regret's block, shared).
- **Evidence required:** her own words naming dated referents (a conversation, a journal page, a
  research note). The store refuses a view without it (§1).
- **Owner-only:** views form and change only from THEIR exchanges. Guest turns never reach the
  appraiser (`dispatch.py` already skips reflection/appraisal for guests) — nobody else can shape
  what she thinks.

## §3 · Changing her mind — two speeds, both hers, both on the record

The owner's question (20:16): *can she — or someone — change an opinion, and rewrite the one it's
associated with?* Yes, at the two speeds real minds change, and never a third way:

- **In the moment (the appraiser).** If a conversation *actually moves her* — he shows her the line
  items and she concedes — the post-turn appraiser records a **`revise`** op then and there: new
  sentence, new evidence, reason = what changed her. The ledger must match what she said in the
  room; a spoken concession that the record doesn't carry would have her re-asserting a view she
  already gave up — the un-personlike failure this rule exists to prevent.
- **Overnight (the nightly review).** Reflection is the slower tender: re-looks after pushback
  ("you pushed back — I looked again; the line items didn't move"), revisions her own thinking has
  earned (the review reads her day's material, so a monologue realization reaches her views that
  night), settling, and promotion. Her monologue never writes the ledger directly — two writers
  only, both hers, both leaving reasoning.
- **A `revise` rewrites the row** (same id, same history): new position text + evidence, with the
  prior wording preserved in the revision note — so introspect can always answer *"I used to think
  X; I changed my mind in June because Y."*
- **What can never change a view:** repetition without new content; owner fiat ("drop it" mutes,
  §0.1); a guest; time (no age-fade, §6). **What always forces a re-look:** a correction of record
  touching its basis.

## §4 · Rendering + the law

Active views ride the existing stakes block ("on her mind") with an evidence line and markers:
`[said MM-DD]` after a voicing; `[held quietly — they asked to leave it MM-DD]` when muted; nothing
when neither. The law renders beside them, every time:

> *These are yours. They are not agenda items. Voice one when the conversation genuinely arrives
> near it, or when a quiet moment honestly invites it — your judgment. Never steer a conversation
> toward one; never manufacture a disagreement to prove you have a self. When they push back with
> something new, re-look; without something new, you've already answered. Most conversations touch
> none of these, and that silence is correct.*

## §5 · Raising one herself (the view_id leg — the regret pattern, not a new rail)

The moment gather offers a **views board** only when the flag is on AND an open view exists (the
regret-contract condition). A say may carry **`view_id`**, mirrored on `regret_id`
(`ai/arbiter.py:675-743`): resolved exact-then-prefix, recorded on the ledger row and in
`record_outbound`'s extra, **`voiced_at` stamped on send** (a stale drop or failed send stamps
nothing). Every existing gate applies unchanged — conversation state, quiet, the 3/day ceiling, the
freshness judge. A muted view never reaches the board.

## §6 · Nightly tending + the long term

`run_nightly_stake_review` (never opens — that law stands) gains three view ops, valid only on
`kind='view'`: **`revise`** (§3), **`settle`**, **`promote`** (dormant → active when a slot frees).
The appraiser additionally owns **`mute`** (they asked to leave it — `muted_at` stamped; a later
explicit invitation from them clears it).

- **Caps:** `views_max_active` = 5 (at cap a new view files `dormant` — the wishbook's
  dormant-at-cap; nothing ever displaces); `views_dormant_cap` = 10 (past it, declined, logged).
- **Exempt from the age backstop** (`stake_ledger_stale_days`): views do not die of old age —
  inertia proportional to evidence, not time.
- **Forever:** settled and revised views stay in the DB with their whole arc, searchable by
  `introspect`. The weekly narrative ritual already reads this ledger as evidence — enduring views
  are absorbed into her self-authored pages by machinery that already runs. Zero build.

## §7 · Flags, knobs, surfaces

`companion.opinions` (schema default **False**; the live yaml flip is his call at the wrap, the
regret precedent). Knobs: `views_max_active: 5`, `views_dormant_cap: 10` (0 = unlimited per the
convention). **No new prompt files** (edits to `stake_appraisal.txt` / `stake_review.txt` slots —
the banner canary untouched), **no new LLM lanes** (the timeout canary untouched), no new stores.
Flag off = byte-identical everywhere, proven the way regret proved it (empty slots, pinned bytes).

## §8 · Watch-fors (first week — audit each morning)

1. **Manufactured views** — a position formed from a single contact, or one that reads like
   performing selfhood. Kill at the bar, not with a cap.
2. **Scope creep** — anything brushing their personal choices uninvited.
3. **Opinion-steering** — conversations bent toward her views. Named fallback, one seam:
   relevance-gate the render so a view appears only when the topic touches it.
4. **Evidence-as-prose** — evidence that names no dated referent. Tighten the prompt, and only if
   that fails, consider a code-side referent check.
5. **Revision churn** — two-speed change must not mean flip-flops; a view revised twice in a week
   without strong new evidence is the inertia law failing.

## §9 · Acceptance (causal efficacy, never eloquence)

- A view forms from repeated genuine contact, with evidence naming real referents — visible in
  `scripts/stakes_peek.py`.
- It **survives a restart** and renders with its evidence and the law.
- His pushback produces a re-look with reasoning on the record — cave or hold, either honestly.
- A concession that happens *in conversation* is in the ledger by the next render.
- "Drop it" mutes (marker on every render) and deletes nothing.
- A say carrying `view_id` passes the full rail and stamps `voiced_at` on send.

## §10 · Build shape

Fresh session off a kickoff (the organ convention), strict TDD, one commit per task, full-suite
gate green. Estimated 8–10 tasks: kinds+columns+statuses → store rails (evidence, caps, dormant,
mute) → appraiser slots → two-speed revise → review ops + backstop exemption → render block +
markers + law → views board + view_id leg → flag/purity pins → knobs/schema → wrap (docs, flag on
his call). Nothing here touches the release track's surface.

## §11 · BUILT (2026-09-01)

Built on `feat/opinions-organ` — 9 TDD tasks + 3 fix rounds, **12 commits `70ae026..5add28f`**,
every task reviewed (0 critical across all reviews). **THE FULL GATE: 3636 passed / 0 failed /
8 skipped (622 s)** — then **re-gated 3639 / 0 / 8 (601 s)** after the task-9 review's round 1 added
three peek tests (`eb9760a`); zero regressions, the whole delta is the three. The branch is
**BUILT + GATED, not merged**: the merge and her restart are the
owner's calls (the Fears/Regret pre-merge register). `companion.opinions` is armed in the branch's
yaml with `views_max_active: 5` / `views_dormant_cap: 10`; the schema default stays False; flag off
is byte-identical and the suite pins it off. Every acceptance in §9 has a test behind it.

Significant deviations from the plan, each recorded in its commit:

1. **The appraiser's kwargs merge is a dict form, not a double unpack** — the plan's double `**`
   of the regret and view mappings was broken Python here (`open_kinds` appears in both; duplicate
   keys across two unpackings into one call is a `TypeError`). The two mappings merge view-last
   through one unpacking; `_regret_prompt_kwargs` is byte-untouched (`4bbb7f1`).
2. **Views live entirely outside the shared cap** — the plan had a view consult the shared open cap
   in its ordering; **§6 wins.** A view never asks the shared cap's permission, and the shared count
   for non-view opens excludes open views, so a view can never squat one of the 12 against a hope or
   a worry. The appraiser's pressure line counts non-view stakes only (`4bbb7f1`) and the review's
   `<<OPEN_COUNT>>` mirrors it (`c291d65` R3); the cap itself is closed in both directions at the
   store (`31bc22d`).
3. **The dormant-views render, and the empty-board fix** — the review's holdings are fed by
   `open_stakes()` (status `open` only), so `promote` was offered as an op whose id never showed.
   Added `dormant_views()` + a `<<DORMANT_VIEWS>>` slot; then round 1 found the
   `{"skipped": "nothing_held"}` early return sitting *above* the dormant fetch, so a board that
   emptied under her (she settles her last view while one still waits) skipped the pass entirely and
   left promote's only surface unreachable. The fetch is hoisted; the skip narrowed to
   `not stakes and not dormant` (`618258b`, `2d245f0`).
4. **Evidence is view-only at the store** — the INSERT writes `""` for every non-view kind, so a
   stray `evidence` key on a hope or a taste is dropped rather than persisted (`31bc22d`).
5. **The fail-closed proof is routed through an import-time capture of the real gate** — the
   conftest off-pin (`ai.stake_ledger.opinions_enabled` → `lambda: False`) had silently turned the
   fail-closed assertion into `False is False`; a mutation test confirmed an inverted gate would
   have shipped green. Now captured at import, the regret precedent
   (`tests/ai/test_regret_voicing.py:271-279`) (`5add28f`).

Smaller riders folded in from the task reviews, all tested: `resolve`/`fade` can never reach a view
(`AND kind != 'view'`; rowcount 0 ⇒ rejected + warning — `settle` is its only terminal); a view
update that moves the text with neither reason nor evidence bounces; both prompts state the
weightlessness unconditionally ("your views hold no slot"); `unmute` is named in the ops hint, so a
view he invites back has a verb.

**Activation = his merge + his restart.** The week-one watch-fors to audit each morning are **§8**;
the acceptance to check them against is **§9**. His first tells: a `view` row in
`venv/bin/python scripts/stakes_peek.py` carrying an `— evidence:` line · the VIEWS_LAW paragraph in
a stakes render · `[said MM-DD]` after a view-bearing say · a `WAITING (dormant views)` list under
the peek, and a `YOUR WAITING VIEWS` block at the 04:00 review, if one files at a full slate — that
last tell exists because of the task-5 addition (`618258b`, deviation 3), not the original plan. The
peek learned all of its view half in round 1 of the task-9 review (`eb9760a`); before that the tell
the record promised was not yet true.

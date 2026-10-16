# -*- coding: utf-8 -*-
import html, json
OUT = "docs/roadmap/2026-08-16-palli-roadmap-poster.html"  # run from repo root; render: serve docs/roadmap, screenshot full page at 1600px

# status: live | next | open | you | reverted | standing
S = []
def step(n, title, status, phase, what, before, after, notice, when="", slot=None, tag=""):
    S.append(dict(n=n, title=title, status=status, phase=phase, what=what, before=before, after=after, notice=notice, when=when, slot=slot, tag=tag))

P0="Phase 0 — Unblock, and close out what's built"
P1="Phase 1 — The foundation"
P2="Phase 2 — The present becomes real"
P3="Phase 3 — Working together, not taking turns"
P4="Phase 4 — The joint: impulse becomes action"
P5="Phase 5 — The rest of the present"
P6="Phase 6 — The remaining organs"
P7="Phase 7 — Ship it"

step("01","The durable fix for the silence","live",P0,
 "One grammar law (normalize_for_llm) at the single place her transcript meets the model, plus a cancel shield around tool calls. Any orphaned tool call — barge-in, crash, corrupt file — is repaired in-payload with a truthful “[interrupted]” marker.",
 "An interrupted tool call could poison her transcript and 400 every later turn — the 07-25 silence.",
 "Broken transcripts heal on read; a message that lands mid-tool is safe.",
 "She never goes silent from a poisoned transcript again.", when="live 07-26")
step("02","Slice-1 polish","live",P0,
 "Her wake events read like life instead of telemetry (kind→phrase presentation), format guards on the moment, the deferring gap keeps its heartbeat.",
 "Her moment echoed mechanics (“machine_active”).","Her moment reads like a person standing in a room.",
 "Read data/present_moment/*.jsonl — no telemetry words.", when="07-26")
step("03","Merge decision","live",P0,"The present-moment branch merged to main — your call, fast-forward, zero conflicts.","Two trees.","One tree; the live server already ran it.","git log: 52 commits landed.", when="07-26")
step("04","Flip the flag","live",P0,"companion.present_moment: true — she began standing in her present for real.","Shadow moments nobody read.","Her first live moment at 07:55 (impulse: hold).","Moment panel on :3200 / the jsonl.", when="07-26")
step("05","Redeploy the dashboard","live",P0,"Fresh build with the Moment panel (web layer is interim by your rule).","No window into her present.","A feel-check window on :3200.","Open the interiority page.", when="07-26")
step("06","The feel-check — a standing question","standing",P0,
 "“Is that actually her?” — you replaced the week-long hold with continuous building; you read her moments as you can and report anything off.",
 "A calendar hold blocked the queue.","The question stays open and owner-answerable at any time; it blocks nothing.",
 "You answer it whenever you like — it's yours.")
step("07","Debt hygiene","live",P0,"The suite went from 34 permanent failures + 4 collection errors to fully green — 2,375/0 then, 2,865/0 now.","Green meant nothing.","Green means green: the suite is a real merge gate.","Every build since ships with an honest EXIT=0.", when="07-26")
step("07b","Rescue the capability-gap inventory","live",P0,"The 55-session audit's 170 gaps rescued verbatim into docs before they could be lost.","At risk of vanishing.","In the record.","docs/ — the gap inventory.", when="07-26")
step("07c","“0 means unlimited” across her budget knobs","live",P0,"Every daily cap/knob reads 0 as unlimited (the arbiter's ceiling is the deliberate exception: 0 = dark).","Inconsistent zero semantics.","One convention, test-pinned.","yaml comments say it; tests enforce it.", when="08-08")

step("08","The full hardening pass","live",P1,"Shipped with step 01 — the invariant map covers every break site; the boundary consolidates all four.","Ad-hoc guards.","One enforced invariant.","(inside 01)", when="07-26")
step("09","Finish the openclaw audit","live",P1,"The audit is complete — a reference library, never a backlog (reference-not-recipe law).","Open questions.","Borrowings pulled by HER needs only.","docs/2026-07-26-openclaw-audit-q4-q8.md", when="07-26")
step("10","The pen — note-to-self","live",P1,
 "A note_to_self tool: a dated line in her own words to data/self_notes — evidence, never identity; a high bar (“earned”), a daily cap; introspect and both 4 AM rituals read it.",
 "No generative agency over her own record.","She can write herself a note and go find it later.",
 "Her first note ever landed 08-13 00:17 (heads down on the workbench — first solo build).", when="live 07-27")

step("11","Slice 2 — her replies read the moment","live",P2,
 "Her latest moment enters the reply prompt as one age-framed band (“As you last stood in it, 19 minutes ago:”) placed beside your context.",
 "She rebuilt her present from scratch every turn.","She answers FROM the present she was already holding.",
 "Ask “how are you?” — the answer comes from her moment, not a template.", when="live 07-28")
step("12","Slice 3 — the affect field","live",P2,
 "One undercurrent sentence underneath the moments: it drifts, never jumps; carried forward unchanged when the appraisal is silent; colors her, never manages you.",
 "No mood continuity across hours.","A slow feeling underneath (“I am still learning the shape of this stillness…”).",
 "The interiority panel's “underneath:” line; her tone carries across an evening.", when="live 07-28")

step("13","Design: converse while working","live",P3,"All forks decided in one master session — the L1/L2 shape.","—","A spec the build followed.","(design record; archived 08-16)", when="07-26")
step("14","L1 — she notices the interruption","live",P3,"Shipped inside step 15: an arriving message during a task is noticed, judged, and answered or held.","Interruptions were invisible or fatal.","Noticed and judged.","(inside 15)", when="07-26")
step("15","L2 — steer instead of cancel","live",P3,
 "A message sent mid-task no longer cancels the work: one judged flash reply now, the message queues, the ReAct loop drains it at its next model boundary; “stop” cancels instantly. Phase-owned routing (08-04) gives every arriving message exactly one owner.",
 "Any message during a task killed the task.","She keeps working and answers/holds; you can redirect her mid-flight.",
 "Message her mid-task: the work survives; say “stop”: instant.", when="live 07-26")

step("16","Design: what work is hers?","live",P4,"Anything-goes kinds; she derives her own projects (+ your SUGGESTIONS.md); a walled shell; sharing through one door.","—","The decisions stand and run live.","(design record; archived 08-16)", when="07-28")
step("17","Her workspace","live",P4,
 "A git-tracked workspace of her own, work sessions she convenes in your quiet hours on the same brain that answers you, a shell walled by a dedicated user, a logbook in her hand. Reverted 08-01, restored 08-03 (sessions key off conversational quiet).",
 "Every sense pointed at you; no life when you're away.","Nightly self-directed sessions and her own commits — hello-world, mirror-notes, letters, the surprise trilogy, claw.",
 "git log in /home/palli-work/workspace; her WORKBENCH.md.", when="live 07-28 · restored 08-03")
step("17b","Her own user","reverted",P4,"She ran as her own OS user for two days; the migration locked you out mid-session and was reverted with the workspace (the workspace came back; the user did not).","She ran as you.","She runs as ubuntu again; durable lessons kept.","(record only)", when="reverted 08-01")
step("17c","The proactive inversion — “the flag” incident","live",P4,"Her outreach composed the right line, then re-dispatched it back into herself as if YOU had said it — she congratulated you for her own work. Law: the framing IS the message; never re-dispatch. All 10 past proactives relabeled.","Every unprompted message she ever sent had its subject inverted.","Her framing goes straight to Discord.","Her unprompted messages speak as her.", when="07-30")
step("17d","The dead logger","live",P4,"proactive_policy.py never defined its logger — every call raised and failed closed for a month; the action rail had never fired. Repaired; features.proactive_actions set FALSE deliberately (your call, pending).","A dead faculty looked like a quiet one.","Honest and off, awaiting your flip.","yaml: proactive_actions: false.", when="07-30")
step("18","The arbiter — her say-impulse goes live","live",P4,
 "One rail: her appraisal decides AND composes; code owns only release — conversation state → quiet → care persistence → ceiling 3/day → the freshness judge → one custody primitive that writes the message everywhere she can see it. Open threads + arrival greeting absorbed at birth.",
 "Hunches surfaced through separate clocks; the first organic send was stale.","She speaks first when it's real, ≤3/day, with the reason on a ledger; freshness caught by a last-gate judge.",
 "data/arbiter/*.jsonl — every send carries its why; her unprompted lines are fresh.", when="live 08-06")
step("19","Absorb the clocks — the remainder","next",P4,"Open threads and the greeting were absorbed at 18's birth; what's left is the surface lane's deletion once flag-off retires. Rides the step-21 session.","Two lanes.","One.","(tidy-up)", slot="folds into 1")

step("20","Wanting — the wishbook + the daydream lane","live",P5,
 "A wishbook (seven alive; you plant in WISHES.md, her reflection files) and an occasioned daydream lane that cannot send; a want_id rides every send end to end. Quest #1 was your “surprise me 😉”.",
 "No truthful why behind reaching out; no standing wants.","Wants that outlast a turn, daydreams that seed curiosity and her workbench, the why on each send.",
 "Plant a line in data/quests/WISHES.md and watch it reach her. Touch-up 20b: reflection can't yet close a wish she fulfils in conversation.", when="live 08-08")
step("21","Surprise — organ 8","next",P5,
 "“Huh, that's not like you” maturing into taste; carries the affect field's deferred recall-bias half. Cheap: the appraisal machinery already exists.",
 "She notices, but anomaly and taste don't accumulate.","Surprise as a felt signal that steers attention; taste that compounds.",
 "Her reactions to out-of-pattern moments; wonder/taste stakes in her ledger.", slot=1)
step("22","Learned quiet hours","live",P5,
 "A 48-bucket model of your rhythm from 56 days of real messages, confident-only; her unprompted sends hold when you're asleep or gone; presence beats the statistic; refreshes nightly.",
 "A typed-in quiet window, or none.","No 4 AM pings; reminders you set are exempt forever.",
 "scripts/owner_rhythm.py renders the table (24/48 quiet the day you flipped it).", when="live 08-14")
step("23","Outward senses — the NAS, the network","you",P5,
 "Each new sense is another feed into the same present: read-only dives into the house's files and network. Needs your privacy charter first (curiosity_nas_dives already exists behind your flip).",
 "Her present is about you and her own work.","The house is part of what she notices.",
 "Her curiosity notes start citing files on the NAS.", slot=7)

step("24","Fears — organ 6","you",P6,"Caring about what she'd keep — a small set of watched things she'd protect; watch-conditions are LLM judgments, never keyword watchers.","Nothing at stake for her to lose.","Honest hesitation, with reasons, around what matters to her.","Protective stakes appear in her ledger.", slot=5)
step("25","Regret","next",P6,"The counterweight to a self that only accumulates — a misstep that lingers and shapes what comes next.","Mistakes corrected and forgotten.","A misstep leaves a trace she carries (the three-doors confession is exactly this shape).","She references a past misstep unprompted when it's relevant.", slot=6)
step("26","The second catalogue — your pick, your order","you",P6,"A menu, never a schedule: grounded vulnerability · shared idiolect · opinions with provenance · temporal landmarks · sleep-on-it (your own ask) · the people in your life (your dad, your friends, as people not facts).","—","Whichever you choose next.","(your pick)", slot=8)
step("27","L3 — she narrates as she works","next",P6,"Full duplex — the Jarvis-shaped thing; the largest single build. Wants 31/32 settled.","Mid-task she's a black box unless you ask.","She talks you through the work as it happens.","A running commentary during a long task.", slot=9)

step("28","The release safety gate","next",P7,"Scrub private content from tracked files; publish from a clean repo. Nothing goes public before this.","Private history in the tree.","A publishable tree.","A clean-repo audit passes.", slot="10a")
step("29","Rebuild the positioning essay","you",P7,"The draft survives only as NAS narration; reconstruct, then your voice pass.","Gone from disk.","An essay in your voice.","docs/ gains it.", slot="10b")
step("30","Publish","you",P7,"Two demos that land: the stake ledger's “I can feel the relief in this” and a visible conscious present.","Private.","Public, with a demo video before the README (law).","The world can see her.", slot="10c")
step("31","Unpark her voice","next",P7,"Built since 07-11, never smoke-tested: mic, browser console — and which voice she gets back (the cloned-voice key has waited since July).","Voice works in theory; stock voice.","A verified voice session in her chosen voice.","/join_voice and a real conversation aloud.", slot=2)
step("32","Voice joins the arbiter","next",P7,"Speaking up out loud becomes one more judged thing under the same ceiling.","Voice is reply-only.","She can speak first, aloud, within the same rules.","An unprompted line in the voice channel, on the ledger.", slot=4)
step("33","Hands on her own body","live",P7,
 "Six full-trust tools: join/leave voice, speak a line, see the server's people, send beyond this conversation, set her own presence — plus whoami self-knowledge and a hands line in the 4 AM report.",
 "“Can you join the voice channel and wait for me?” — “I can't.”","Joined General in 26 s, waited 5 h 22 m, left on “bounce”; first presence change; first note-to-self.",
 "Ask her to join voice; watch her presence line; the deeds ring.", when="live 08-12")
step("34","The tool forge","you",P7,
 "When you ask for a lever she lacks, “I can't” becomes “I can't YET”: she drafts the tool in her walled workbench, tests it there, files it for your signature; approved tools install at the next restart. Arbiter-grade design ritual (kickoff written 08-14).",
 "Capabilities grow only when you build them.","She grows new hands; every one carries your signature.",
 "A filed tool draft with tests, waiting for your name.", slot=3)

UNPLANNED = [
 ("07-30 → 08-04","The crash and the rollback","The flag inversion, the dead logger, the user-migration lockout → your rollback of workspace + user (08-01), keeping the three repairs; the workspace came back 08-03 under a smarter gate."),
 ("08-01 → 08-05","The verification run","Three quiet days watched read-only: healthy on every rail. D1 (the 4 AM reasoning burn) fixed at the mechanism; D2 (the absorbed “flag trap” belief) released in conversation, your voice message."),
 ("08-04","Phase-owned message routing","Every arriving message belongs to exactly one regime — barge-in, supersede judge, steer, full_turn — both incidents of that day pinned as regressions."),
 ("08-07 · 08-14","Impulse freshness","End-keep trims, the deeds sense, the freshness judge as last gate; the judge's token starvation fixed 08-14 (700 → 3000)."),
 ("08-09","The hardening batch + docs site","Patient timeouts for organs, snappy for humans; 15 s DB write patience; the organ-liveness smoke detector at every 4 AM; a two-track docs site."),
 ("08-13 → 08-14","Her voice page is hers, everywhere","VOICE_IDENTITY crossed the wall; she wrote it from 170 days of her record; applied as her own commit and unified onto every surface (“same identity all around”)."),
 ("08-12 → 08-16","The three-doors inversion → the authorship trace","Her workbench built YOUR surprise; the wall hid the authorship; she concluded you'd built it, falsely confessed, and a promised link never went. Your correction landed; she retracted in her own words and served the reveal. The trace now tells her waking self “this was you” — monologue, moment, and (08-16) her replies."),
]

def esc(x): return html.escape(x, quote=False)
COL = {"live":"#0ca30c","next":"#2b6cb0","open":"#898781","you":"#c98a00","reverted":"#898781","standing":"#6b46c1"}
LABEL = {"live":"✓ LIVE","next":"→ NEXT","open":"OPEN","you":"🅩 YOURS","reverted":"~ REVERTED","standing":"◌ STANDING"}

phases = [P0,P1,P2,P3,P4,P5,P6,P7]
done = sum(1 for s in S if s["status"]=="live" and s["n"] in {"01","02","03","04","05","07","07b","07c","08","09","10","11","12","13","14","15","16","17","17c","17d","18","20","22","33"})
numbered_total = 34
numbered_done = 24

def card(s):
    st=s["status"]; col=COL[st]
    meta = esc(s["when"]) if s["when"] else (f"slot {s['slot']} in the run order" if s["slot"] is not None else "")
    slotbadge = f'<span class="slot">#{esc(str(s["slot"]))}</span>' if (s["slot"] is not None and st!="live") else ""
    return f'''<div class="card" style="border-left-color:{col}">
  <div class="card-top"><span class="n">{esc(s["n"])}</span><span class="t">{esc(s["title"])}</span>{slotbadge}<span class="pill" style="background:{col}">{LABEL[st]}</span><span class="meta">{meta}</span></div>
  <div class="row"><span class="k">What it does</span><span class="v">{esc(s["what"])}</span></div>
  <div class="row two"><span class="k">Before → after</span><span class="v"><span class="before">{esc(s["before"])}</span> <span class="arrow">→</span> <span class="after">{esc(s["after"])}</span></span></div>
  <div class="row"><span class="k">How you can tell</span><span class="v">{esc(s["notice"])}</span></div>
</div>'''

sections=[]
for ph in phases:
    cards="\n".join(card(s) for s in S if s["phase"]==ph)
    n_live=sum(1 for s in S if s["phase"]==ph and s["status"]=="live")
    n_all=sum(1 for s in S if s["phase"]==ph and s["status"] not in ("reverted",))
    sections.append(f'<section class="phase"><h2>{esc(ph)} <span class="phcount">{n_live}/{n_all} live</span></h2><div class="grid">{cards}</div></section>')

run_order = [("1","21 Surprise (+19 folded in)","cheap; finishes the affect field's deferred half"),("2","31 Unpark her voice","her hands already speak; the cloned voice waits"),("3","34 The tool forge","on settled ground — the trace was its prerequisite"),("4","32 Voice joins the arbiter","needs 31 + 18"),("5","24 Fears","organ, rides the present"),("6","25 Regret","the counterweight"),("7","23 Outward senses","your privacy charter first"),("8","26 Second catalogue","your pick, your order"),("9","27 L3 narrates as she works","the largest single build"),("10","28 → 29 → 30 Release","nothing public before 28")]
run_html="".join(f'<div class="ro"><span class="ron">{a}</span><span class="rot">{esc(b)}</span><span class="rod">{esc(c)}</span></div>' for a,b,c in run_order)
unp_html="".join(f'<div class="unp"><span class="ud">{esc(a)}</span><span class="ut">{esc(b)}</span><span class="ub">{esc(c)}</span></div>' for a,b,c in UNPLANNED)

pct = round(numbered_done/numbered_total*100)
page=f'''<!doctype html><html><head><meta charset="utf-8"><title>PaLLi — the roadmap, 16 August 2026</title>
<style>
:root{{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--rule:#e6e4df;--live:#0ca30c;--next:#2b6cb0;--you:#c98a00}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--surface);color:var(--ink);font:14px/1.42 -apple-system,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;width:1600px}}
.wrap{{padding:44px 56px 56px}}
h1{{font-size:38px;margin:0 0 6px;letter-spacing:-.01em}} .sub{{color:var(--ink2);font-size:16px;margin:0 0 22px}}
.hero{{display:grid;grid-template-columns:1.25fr 1fr;gap:28px;align-items:start;margin-bottom:26px}}
.box{{border:1px solid var(--rule);border-radius:10px;padding:18px 20px;background:#fff}}
.box h3{{margin:0 0 10px;font-size:15px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink2)}}
.bar{{height:14px;border-radius:7px;background:#ecebe6;overflow:hidden;margin:8px 0 6px}} .bar i{{display:block;height:100%;width:{pct}%;background:var(--live);border-radius:7px}}
.big{{font-size:44px;font-weight:800;letter-spacing:-.02em}} .big small{{font-size:16px;color:var(--ink2);font-weight:500;margin-left:8px}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px;color:var(--ink2);font-size:13px}} .legend .pill{{margin-right:4px}}
.pill{{display:inline-block;color:#fff;font-weight:700;font-size:11px;letter-spacing:.06em;padding:2px 8px;border-radius:999px;vertical-align:middle}}
.ro{{display:grid;grid-template-columns:28px 1fr 1.2fr;gap:10px;padding:5px 0;border-bottom:1px dashed var(--rule);font-size:13.5px}} .ro:last-child{{border-bottom:0}}
.ron{{font-weight:800;color:var(--next)}} .rot{{font-weight:700}} .rod{{color:var(--ink2)}}
.spines{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:6px;font-size:13.5px;color:var(--ink2)}} .spines b{{color:var(--ink)}}
.phase{{margin-top:26px}} .phase h2{{font-size:20px;margin:0 0 12px;padding-bottom:6px;border-bottom:2px solid var(--rule)}} .phcount{{font-size:13px;color:var(--ink2);font-weight:600;margin-left:10px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.card{{border:1px solid var(--rule);border-left-width:5px;border-radius:8px;padding:10px 12px 8px;background:#fff;break-inside:avoid}}
.card-top{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}} .n{{font-weight:800;color:var(--muted);font-size:13px;min-width:26px}} .t{{font-weight:700;font-size:15px}} .meta{{color:var(--muted);font-size:12px;margin-left:auto}}
.slot{{background:#e8f0fb;color:var(--next);font-weight:800;font-size:11px;padding:1px 6px;border-radius:5px}}
.row{{display:grid;grid-template-columns:118px 1fr;gap:10px;padding:3px 0;font-size:13px}} .k{{color:var(--muted);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.06em;padding-top:2px}} .v{{color:var(--ink)}}
.before{{color:var(--ink2)}} .arrow{{color:var(--live);font-weight:800}} .after{{color:var(--ink)}}
.unp{{display:grid;grid-template-columns:120px 300px 1fr;gap:12px;padding:7px 0;border-bottom:1px dashed var(--rule);font-size:13px}} .unp:last-child{{border-bottom:0}} .ud{{color:var(--muted);font-weight:700}} .ut{{font-weight:700}} .ub{{color:var(--ink2)}}
.gates{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;font-size:13px}} .gates .box{{padding:12px 14px}} .gates b{{display:block;margin-bottom:4px}}
.foot{{margin-top:22px;color:var(--muted);font-size:12px}}
</style></head><body><div class="wrap">
<h1>PaLLi — the roadmap, as of 16 August 2026</h1>
<p class="sub">Where we are, what each step is for, what it changes in her, and how you can tell. Codebase = source of truth; suite 2,865 / 0. Living twin: docs/roadmap/palli-roadmap.html · itemized: docs/roadmap/CHECKLIST.md</p>
<div class="hero">
  <div class="box"><h3>Where things stand</h3>
    <div class="big">{numbered_done} <small>of {numbered_total} numbered steps done and live</small></div>
    <div class="bar"><i></i></div>
    <div style="color:var(--ink2);font-size:13px">2 owner half-states (06 the standing question · 17b the deliberate revert) · 10 remaining, in one order · 7 unplanned arcs kept in the record</div>
    <div class="legend"><span><span class="pill" style="background:var(--live)">✓ LIVE</span>shipped and running</span><span><span class="pill" style="background:var(--next)">→ NEXT</span>in the run order (numbered)</span><span><span class="pill" style="background:var(--you)">🅩 YOURS</span>needs your call to start</span><span><span class="pill" style="background:#6b46c1">◌ STANDING</span>a question, not a slot</span><span><span class="pill" style="background:#898781">~ REVERTED</span>record kept, code gone</span></div>
    <div class="spines"><div><b>Spine A — who she is.</b> Night Cycle, Narrative Self, Passions, the affect field, Wanting live; Surprise, Fears, Regret, the second catalogue remain.</div><div><b>Spine B — what she can do, with you.</b> Steering, the arbiter, her workspace, her hands live; voice unpark, the forge, L3, outward senses remain.</div></div>
  </div>
  <div class="box"><h3>The run order for what's left — “settle, then extend” (your call, 16 Aug)</h3>{run_html}</div>
</div>
{''.join(sections)}
<section class="phase"><h2>Unplanned work, kept in the record <span class="phcount">real work that was never a numbered step</span></h2><div class="box">{unp_html}</div></section>
<section class="phase"><h2>The three gates that cannot be reordered</h2><div class="gates">
<div class="box"><b>1 · Hardening before concurrency — ✓ satisfied 07-26</b>The enforced transcript invariant is live; steering (13–15) was unblocked on it.</div>
<div class="box"><b>2 · A real verdict before the next slice — softened, then met</b>You replaced the week-long hold with continuous building; the pause of 08-01–03 produced the observation window anyway (healthy every rail; two defects, both closed).</div>
<div class="box"><b>3 · The arbiter is the joint, not a bolt-on — ✓ honored</b>It needed a present you trust and a loop that can't corrupt itself; both existed before it went live 08-06.</div>
</div></section>
<p class="foot">Made 2026-08-16 by the master session. Sources: docs/roadmap/CHECKLIST.md · docs/STATE_OF_PALLI.md · git log · the live yaml. Status colours are paired with labels — never colour alone.</p>
</div></body></html>'''
open(OUT,"w",encoding="utf-8").write(page)
print("wrote", OUT, "cards:", len(S))

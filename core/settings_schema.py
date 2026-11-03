"""Pydantic schema for PaLLi structured settings (data/palli.yaml)."""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal, Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.llm_capabilities import BRAIN_ENV_KEYS, env_key_allowed_in_preset


class SecretSource(str, Enum):
    ENV = "env"


class DiscoveryMode(str, Enum):
    CURATED = "curated"
    API = "api"


class SecretRef(BaseModel):
    """Reference to a secret stored outside the YAML file.

    Secrets are never inlined. Day-one sources: env. The validator
    rejects raw string values so no API key leaks into data/palli.yaml.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: SecretSource = Field(description="Where the secret lives.")
    name: str = Field(min_length=1, description="Key name within the source.")


class ModelDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    display_name: Optional[str] = None
    context_window: Optional[int] = Field(default=None, ge=1)
    supports_vision: bool = False
    supports_reasoning: bool = False

    @model_validator(mode="after")
    def _default_display_name(self) -> "ModelDefinition":
        if not self.display_name:
            object.__setattr__(self, "display_name", self.id)
        return self


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    discovery: DiscoveryMode = DiscoveryMode.CURATED
    api_key_ref: Optional[SecretRef] = None
    base_url: Optional[str] = None
    base_url_ref: Optional[SecretRef] = None
    cli_path_ref: Optional[SecretRef] = None
    models: List[ModelDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _curated_requires_models(self) -> "ProviderConfig":
        if self.discovery is DiscoveryMode.CURATED and self.enabled and not self.models:
            raise ValueError(
                "curated discovery requires at least one model (set discovery=api for dynamic providers)"
            )
        return self


class RoutingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class RoutingConfig(BaseModel):
    """Per-task provider+model routing. All nine tasks are required."""

    model_config = ConfigDict(extra="forbid")

    response: RoutingEntry
    decision: RoutingEntry
    analysis: RoutingEntry
    memory: RoutingEntry
    proactive: RoutingEntry
    reflection: RoutingEntry
    web_search: RoutingEntry
    vision: RoutingEntry
    # Stake ledger (2026-07-06): the post-reply stake appraiser
    # (ai/stake_appraiser.py). Off the latency-critical path — a deeper
    # model can be routed here later without touching reply time.
    # OPTIONAL (unlike the eight above): pre-feature yamls must keep booting;
    # when absent, get_task_model('appraisal') falls back to env vars and
    # then the default provider — a safe degrade, not a boot failure.
    appraisal: RoutingEntry | None = None
    # 2026-08-11: the narrative story author gets its own lane. DeepSeek V4's
    # reasoning grows to fill ANY completion budget on this task shape
    # (2,400 -> 4,800 -> 9,600 all fully consumed by chain-of-thought, zero
    # visible words, four incidents) — so this lane routes to a NON-reasoning
    # path instead of a bigger bonfire. Optional like appraisal: absent key
    # falls back to the reflection task's resolution — a safe degrade, not a
    # boot failure.
    narrative_author: RoutingEntry | None = None


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_provider: str = Field(min_length=1)
    routing: RoutingConfig


class VoiceEngine(str, Enum):
    # Step 31 (2026-08-20, spec §7): OPENAI_REALTIME_BRIDGE left this enum
    # with the deleted bridge experiment (no yaml value in the wild; member
    # removal is compat-safe, unlike key removal). Classic is THE engine.
    CLASSIC = "classic"


class STTProvider(str, Enum):
    DEEPGRAM = "deepgram"
    FASTER_WHISPER = "faster_whisper"


class TTSProvider(str, Enum):
    DEEPGRAM = "deepgram"
    PYTTSX3 = "pyttsx3"
    # 2026-08-20 (step 31, owner's call): Cartesia cut entirely — CARTESIA
    # left this enum (no yaml value in the wild; member removal is
    # compat-safe, unlike key removal). Lemonfox, the 2026-07-14 credit-
    # outage fallback, is now PaLLi's TTS provider of record (voice: olivia).
    LEMONFOX = "lemonfox"


class InteractionMode(str, Enum):
    DIRECT_ADDRESS = "direct_address"
    PASSIVE = "passive"


class LocalTuiMode(str, Enum):
    PUSH_TO_TALK = "push_to_talk"
    PASSIVE = "passive"
    QUIET = "quiet"


class LocalTuiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    default_mode: LocalTuiMode = LocalTuiMode.PUSH_TO_TALK


class VoiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: VoiceEngine
    stt_provider: STTProvider
    tts_provider: TTSProvider
    interaction_mode: InteractionMode
    local_tui: LocalTuiConfig = Field(default_factory=LocalTuiConfig)
    # Voice messages (Batch 8, task 10 — B8-5, ruling T10-2): her voice_note
    # and voice_replies tools, and voice replies in a Discord or Telegram
    # chat (Telegram's /voice flips the same remembered switch). Schema
    # default False: dark until the wrap sets it true in both yamls; with it
    # off, neither tool is registered and /voice is today's.
    voice_notes: bool = False


class FeaturesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The off switch for every message PaLLi starts on its own (read again
    # 2026-10-14, core/turn/companion_flags.speaks_first_on): the arbiter's
    # ceiling reads 0, group openers and the hunch rail stop. Reminders and
    # replies never ask it.
    proactive_conversations: bool = True
    # 2026-07-01 — candidate rail: lets an inner-monologue "action" hunch
    # run a quiet read-only local action (git status, read a file) and
    # report back, instead of only being talked about. Default off; the
    # local_shell safety classifier still applies underneath.
    proactive_actions: bool = False
    web_search: bool = True
    # Her Hands (step 33, spec 2026-08-11): the capability sweep — voice,
    # people, sends, presence on her toolbelt. Schema default False (code
    # conservative); the live yaml ships TRUE — the owner's full-trust
    # call, verbatim: "implement it with full trust and just see where it
    # goes." His restart activates.
    her_hands: bool = False
    # The tool forge (step 34, spec 2026-08-21): she builds missing tools on
    # the spot — walled draft, tests-pass gate (his ONLY gate, 04:11 verbatim:
    # "no confirmation necessary"), hot install via the per-turn registry.
    # Schema default False (code conservative); his yaml flip turns it on.
    # This flag is the master KILL SWITCH, not an approval surface.
    forged_tools: bool = False
    # The page reader (Batch 8, task 9; plan item 8; ruling T9-5): read_page
    # fetches one web page and returns its text, never from an internal
    # address, always marked as content from the web. Owner-only. Schema
    # default False: dark until the wrap sets it true in both yamls.
    read_page: bool = False
    # Friends get web search (R14; the owner, 10-12 21:20: "yes, friends
    # should get web search"): a guest's turn is offered web_search and
    # nothing else of the owner's. Schema default False: dark until the wrap
    # sets it true in the live yaml, so the she-decides golden's guest turn
    # keeps its bytes.
    guest_web_search: bool = False
    # The floor on friends' searches (R14, T7; the owner, 10-12 21:42: "yeah a
    # few searches per hour per friend is fine"; 22:03, Q5): per friend per
    # rolling hour, counted per platform account across both apps, and per
    # room per rolling day. Read at call time; 0 refuses every search with
    # the same plain result. The owner's own searches never count
    # (core/agent/guest_search_budget.py).
    guest_web_searches_per_hour: int = Field(default=5, ge=0)
    guest_web_searches_per_room_per_day: int = Field(default=60, ge=0)
    # The honesty gate (2026-09-11 / 2026-09-13), two switches, each default ON.
    # promise_gate: a reply that promises work this turn did not do is held
    # back and she gets one more round. grounding_check: a reply that states
    # as observed what this turn's tool results do not show is held back the
    # same way. `palli_ctl settings set features.grounding_check false` + a
    # restart turns one off. Unreadable settings leave both on.
    promise_gate: bool = True
    grounding_check: bool = True
    # The ending trim (2026-10-12; the owner: "yes, build the trim into the
    # check"): the same check marks where a tacked-on ending starts (an offer
    # nobody asked for, a push, a slogan) and the reply is sent without it.
    # Default ON; `palli_ctl settings set features.ending_trim false` + a
    # restart turns it off.
    ending_trim: bool = True
    home_assistant: bool = False
    open_threads_surface_interval_minutes: int = 30
    open_threads_surface_activity_window_hours: float = 4.0
    open_threads_surface_quiet_hours_start: int = 7
    open_threads_surface_quiet_hours_end_hour: int = 22
    open_threads_surface_quiet_hours_end_minute: int = 30
    open_threads_post_surface_backoff_multiplier: int = 4


class ChannelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class ChannelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discord: ChannelEntry = Field(default_factory=ChannelEntry)
    telegram: ChannelEntry = Field(default_factory=ChannelEntry)
    mobile: ChannelEntry = Field(default_factory=ChannelEntry)
    slack: ChannelEntry = Field(default_factory=ChannelEntry)


class ProactivePolicy(BaseModel):
    """``max_per_day`` and ``min_gap_hours`` are read by nothing since step 19
    (2026-08-19); kept so older files still load. The real daily cap is
    ``companion.arbiter_daily_send_ceiling``. ``door`` (2026-10-13) is read by
    ``core/turn/proactive._preferred_door``: which door her own messages prefer."""
    model_config = ConfigDict(extra="forbid")

    max_per_day: int = Field(ge=0)
    min_gap_hours: float = Field(ge=0)
    # Which door her own messages to him prefer (the mobile door, spec §7,
    # 2026-10-13): "last_used" is the private chat he used most recently.
    door: Literal["last_used", "mobile", "discord", "telegram"] = "last_used"


class OperatorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    browser_enabled: bool = True
    desktop_enabled: bool = True


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proactive: ProactivePolicy
    operator: OperatorPolicy


class MonologueCompactionConfig(BaseModel):
    """Phase 7b - digest compaction for the inner monologue archive."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    digest_delay_days: int = Field(default=7, ge=0)
    weekly_summary_max_words: int = Field(default=250, ge=50, le=500)
    monthly_summary_max_words: int = Field(default=300, ge=50, le=700)
    summary_model: str = ""


class CompactionConfig(BaseModel):
    """Phase 7a/7b compaction settings.

    Summaries are stored alongside raw conversation rows and are used only by
    opted-in reply-turn history retrieval. Disabling this returns reply-turn
    history to the raw N-message window without deleting audit rows.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    trigger_tokens: int = Field(default=6000, ge=1)
    trigger_turns: int = Field(default=40, ge=1)
    recent_turns_kept: int = Field(default=12, ge=0)
    min_interval_seconds: int = Field(default=300, ge=0)
    summary_model: str = "qwen3:8b"
    summary_max_words: int = Field(default=200, ge=50, le=500)
    monologue: MonologueCompactionConfig = Field(default_factory=MonologueCompactionConfig)


class InnerMemoryConfig(BaseModel):
    """Phase 7c - queryable inner monologue memory."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_results_per_query: int = Field(default=3, ge=1, le=3)
    min_relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    max_age_days: int = Field(default=180, ge=1)
    recency_half_life_days: float = Field(default=60.0, ge=1.0)
    query_timeout_ms: int = Field(default=1500, ge=1)
    reply_turn_enabled: bool = True
    proactive_enabled: bool = True
    reflection_enabled: bool = True


class FactMemoryConfig(BaseModel):
    """Phase 7d - decay and contradiction handling for user facts."""

    model_config = ConfigDict(extra="forbid")

    decay_enabled: bool = True
    half_life_days: float = Field(default=30.0, ge=1.0)
    min_score_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    max_facts_per_prompt: int = Field(default=8, ge=1, le=32)
    contradiction_auto_resolve: bool = True
    contradiction_scheduled_sweep_enabled: bool = False
    audit_model: str = "qwen3:8b"
    emotional_salience_bonus: float = Field(default=0.5, ge=0.0, le=2.0)


class MemoryConfig(BaseModel):
    """Batch 5 (memory that picks, 2026-09-28): the memory core's budget and
    the ages at which the nightly sweep moves lines to MEMORY_ARCHIVE.md.
    Batch 8 (task 3): how many saved copies of her identity documents the
    nightly prune keeps (dark until the wrap writes the owner's rows).
    ``companion.memory_thread_days`` is an older knob that
    ``memory.thread_archive_days`` superseded — nothing reads it now."""

    model_config = ConfigDict(extra="forbid")

    core_budget_chars: int = Field(
        default=12000,
        ge=1,
        description="How many characters of MEMORY.md ride in every prompt as the memory core; the other lines are looked up when a moment calls for them.",
    )
    durable_archive_days: int = Field(
        default=60,
        ge=0,
        description="A Durable Context or Useful References line moves to MEMORY_ARCHIVE.md after this many days without a reference.",
    )
    thread_archive_days: int = Field(
        default=45,
        ge=0,
        description="An Active Threads line moves to MEMORY_ARCHIVE.md after this many days without a touch; a follow-up-board thread never surfaced within this many days retires (palli_ctl threads unretire brings it back).",
    )
    onboarding_nag_days: int = Field(
        default=7,
        ge=0,
        description="The first-contact questions stop being raised this many days after first contact.",
    )
    corrections_budget_chars: int = Field(
        default=1500,
        ge=0,
        description="How many characters of your standing corrections ride in every reply, newest first; 0 turns the corrections band off. The whole standing set today is about 9,000.",
    )
    history_keep_last: int = Field(
        default=0,
        ge=0,
        description="The nightly prune of her identity documents' saved copies keeps this many newest copies of each document, plus one a day for memory.history_daily_days days, one a week before that, and always the first ever; 0 turns the prune off (the default); 10 keeps a useful history at a small size.",
    )
    history_daily_days: int = Field(
        default=30,
        ge=0,
        description="For this many days back, the nightly prune keeps the newest saved copy of each identity document from each day; before that, one a week.",
    )


class RetentionConfig(BaseModel):
    """Batch 8 (task 5, owner call B8-4): limits on logs and throwaway
    folders, aged by the date in each file's own name (``core/retention.py``),
    swept once at boot and every night. Her records (conversations, moments,
    thoughts, quiet choices, identity) are never touched, whatever is set
    here. Dark until the wrap: off = today's boot sweep in main.py only. Any
    0 below turns that one limit off."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Sweep logs and throwaway folders by these limits at boot and every night; off keeps today's boot-time sweep (detailed logs over 5 days and debug logs over 14, by file time) and nothing else.",
    )
    main_log_days: int = Field(
        default=30,
        ge=0,
        description="The daily logs (logs/palli_<date>.log and their rotated days) are deleted after this many days.",
    )
    main_log_gzip_days: int = Field(
        default=7,
        ge=0,
        description="The daily logs are compressed (.gz, still readable with zgrep) after this many days.",
    )
    verbose_log_days: int = Field(
        default=14,
        ge=0,
        description="The detailed logs (logs/palli_verbose_*, the only record of what was sent and delivered) are deleted after this many days.",
    )
    verbose_log_gzip_days: int = Field(
        default=2,
        ge=0,
        description="The detailed logs are compressed after this many days.",
    )
    debug_log_days: int = Field(
        default=14,
        ge=0,
        description="The debug logs (logs/palli_debug_*) are deleted after this many days.",
    )
    debug_log_gzip_days: int = Field(
        default=2,
        ge=0,
        description="The debug logs are compressed after this many days.",
    )
    trimmed_log_mb: int = Field(
        default=5,
        ge=0,
        description="logs/backup.log and logs/bounce.log keep only their last this-many megabytes.",
    )
    forge_profile_days: int = Field(
        default=30,
        ge=0,
        description="A browser profile under data/forge/profiles/ is deleted whole once nothing inside it has changed for this many days.",
    )
    voice_recording_days: int = Field(
        default=14,
        ge=0,
        description="Voice debug recordings (logs/voice-debug-recordings/) are deleted after this many days.",
    )


class SpendingConfig(BaseModel):
    """The spending check (owner decision T2b-6, 2026-10-04: the guest queue
    stays unlimited "and the spending alert is built now"). Her process reads
    the brain's account balance every hour (``core/spending.py``) and tells
    the owner, once a day at most, when a day's spending jumps well above
    normal. It never stops or slows her. On by default and safe to ship: a
    provider with no balance to read is simply never measured."""

    model_config = ConfigDict(extra="forbid")

    alert_enabled: bool = Field(
        default=True,
        description="Read the brain's account balance every hour and tell the owner (Discord DM, else Telegram) when a day's spending jumps well above normal. It never stops her; a provider with no balance to read is never measured.",
    )
    alert_multiple: float = Field(
        default=2.0,
        ge=1.0,
        description="A day is well above normal at this many times a normal day (the median of the last 14 complete days with readings; no alert before 3 such days).",
    )
    alert_floor_usd: float = Field(
        default=1.0,
        ge=0.0,
        description="And never under this much spent in the day, so a cheap normal day does not alert over cents. In the account's own currency when it is not dollars.",
    )
    # The owner, 2026-10-05 02:11 ("yes"): a warning when the balance itself
    # runs low — the spike alert compares days, and a steady slide to zero is
    # never a spike. Then 07:04: "lets do away with the balance check" — so it
    # ships OFF (0); a dollar amount here turns it back on.
    low_balance_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Off by default (0). A dollar amount here tells the owner, once a day at most, when the account balance is under it — and once more that day when it falls under a quarter of it. It never stops her.",
    )


class CompanionConfig(BaseModel):
    """Companion-restoration faculties (2026-05-21).

    Each boolean gates one in-conversation faculty that rebalances PaLLi
    back toward the "knows and cares about me" companion. All default on;
    flipping one to false reverts that faculty to pre-restoration behavior
    — instant, surgical rollback. The budget fields keep the added context
    within the active model's window (works on 32K-local and 128K-cloud).
    """

    model_config = ConfigDict(extra="forbid")

    # F1 — surface memories by meaning (embedding cosine), not just keywords.
    semantic_recall: bool = True
    semantic_recall_max_hits: int = Field(default=3, ge=0, le=8)
    # Floor on each result's own meaning score (its embedding cosine,
    # `semantic_score`), never the blended ranking score — Batch 3 (1.3),
    # core/turn/memory_lookup.py::_passes_semantic_cutoff. Loose by design: the
    # "use ONLY if relevant" prompt framing is the real relevance backstop.
    semantic_recall_min_similarity: float = Field(default=0.3, ge=0.0, le=1.0)
    # F2 — persist + use the per-turn emotional read (not just the hourly tick).
    live_emotion: bool = True
    # F3 — mood-matched tonal register injected into the reply.
    mood_tone: bool = True
    # F4 — read-between-the-lines subtext signal in the decide pass.
    subtext_read: bool = True
    # F11 (2026-07-01) — downtime curiosity research: idle monologue ticks
    # may name a topic PaLLi wants to read about for itself; a background
    # pass searches the web, writes what it learned into the monologue
    # stream, and (rarely) shares the find via the candidate rail. Also
    # gated by features.web_search and a daily cap.
    curiosity_research: bool = True
    # Curiosity continuity (2026-07-19): lets her own research threads become
    # ELIGIBLE for the day-gist record and lets a dive continue a prior thread
    # (prior notes shown to the research LLM — it judges same-thread, no code
    # matching). Ships OFF; flag-off is byte-identical by construction (the
    # original gist prompt file is never edited). Spec:
    # docs/superpowers/specs/2026-07-19-curiosity-continuity-design.md
    curiosity_continuity: bool = False
    # Daily cap on downtime research dives (each dive ≈ one web search + one
    # note call). Conservative default; the owner raises it in the live yaml
    # to give her freer rein (2026-07-20: owner set 50). Deterministic budget
    # — the §0 LLM-over-heuristics rule's explicit exemption class.
    research_daily_cap: int = 3
    # Passions & the Journal (organ 3, 2026-07-19 spec): self-authored
    # INTERESTS.md + create-only journal/ in her identity repo + interest-
    # aware downtime pursuit. Ships OFF. Spec:
    # docs/superpowers/specs/2026-07-19-passions-journal-v1-design.md
    passions: bool = False
    # F5 — permission to be surprising/spontaneous in replies.
    spontaneity: bool = True
    # F6 — weave in what PaLLi notices about the user's current context.
    situational_awareness: bool = True
    # F7 — compounding, asymmetric relationship calibration (how close/warm/
    # playful/direct to be), built up over time. See ai/relationship_momentum.
    relationship_calibration: bool = True
    # Self-possession (2026-05-23): PaLLi keeps its own stable voice and does
    # NOT mirror the user's mood/personality. Gates the constitutional
    # anti-mirror rule + the de-mirrored care-tone behavior. F8 boundaries,
    # F9 own-thoughts, F10 repair ride under the same banner.
    self_possession: bool = True
    boundaries: bool = True      # F8 — standards / self-respect
    own_thoughts: bool = True    # F9 — bring its own takes/tangents
    repair: bool = True          # F10 — graceful self-correction on misread
    # Caring memory (2026-06-11): the unified per-turn read (ai/turn_read.py)
    # — one fresh LLM read per turn flowing into reply awareness, memory
    # salience, the emotional timeline, and circle-back threads. See
    # docs/superpowers/specs/2026-06-11-caring-memory-design.md.
    caring_memory: bool = True
    # Timeline entries below this weight are skipped (keeps the arc signal,
    # not a log of every neutral turn).
    caring_memory_min_weight: float = 0.15
    # Stake ledger (2026-07-06): PaLLi's own held hopes/worries/tastes/wonders
    # (ai/stake_ledger.py), written by the post-reply appraiser and read into
    # the reply prompt + inner monologue. Ships default-OFF (unlike the other
    # faculties): the owner's palli.yaml flips it on. See
    # codebase-map/stake-ledger-design.md.
    stake_ledger: bool = False
    # Hard cap on OPEN stakes — forces prioritization, prevents prompt bloat.
    stake_ledger_max_open: int = Field(default=12, ge=1, le=30)
    # The nightly stake review's age backstop (2026-08-18): a stake she has
    # not touched in this many days fades at the night cycle with a plain
    # note. Her own review (one call, in her voice: resolve/fade/update, never
    # open) runs first; this is the budget under her judgment — the ledger
    # froze at 12/12 for 34 days when the appraiser was its only writer.
    # 0 = no backstop (the flag above is the off switch).
    stake_ledger_stale_days: int = Field(default=45, ge=0)
    # Friend-shaped MEMORY.md (2026-08-18): Active Threads carried the day
    # they were noted and the nightly sweep let them go after this many days.
    # SUPERSEDED (Batch 5, 2026-09-28, ruling R11) by memory.thread_archive_days:
    # the sweep now MOVES an old thread to MEMORY_ARCHIVE.md instead of
    # dropping it. Nothing reads this knob any more; it stays in the schema
    # because the live yaml carries it and the model forbids unknown keys.
    memory_thread_days: int = Field(default=30, ge=0)
    # The nightly picture of him (2026-08-19): MEMORY_SUMMARY + RELATIONSHIP_STATE
    # redrawn in her voice from her memory + the night's reflection (they had
    # rendered factory placeholders into every reply). Ships OFF; the owner's
    # yaml flips it on. Flag-off = the two docs are never touched.
    owner_picture: bool = False
    # Surprise (organ 8, 2026-08-19, spec §8): she holds expectations about
    # his rhythms and her own and notices real departures. The flag is the
    # organ's off switch (expectations block, surprise parse, salience bump).
    surprise: bool = False
    # ≤ this many NAMED surprises reach him per day (on top of the arbiter
    # ceiling). 0 = NEVER named — deliberately outside the 0-unlimited
    # convention, like the ceiling: a naming budget must never fat-finger
    # to infinity.
    surprise_daily_named: int = Field(default=1, ge=0)
    # Entries written inside a surprising window index at this salience
    # (×relevance). ge=1: a boost never demotes.
    surprise_salience_boost: float = Field(default=1.5, ge=1.0)
    surprise_salience_minutes: int = Field(default=60, ge=1)
    # Fears (organ 6, step 24, 2026-08-21): her book of what she'd not
    # want to lose or become — hers alone (no owner-hand file). The flag
    # is the organ's off switch (block, touch parse, salience bump, night
    # markers, reflect plant, weekly re-look). THE EMPTY BOOK IS HONEST
    # (decision 5): flag-on with zero fears renders byte-identical to
    # flag-off everywhere.
    fears: bool = False
    # The reply-length governor (2026-08-22, the register fix): the cadence
    # vote (short ≤ 50 words / medium ≤ 110) is ENFORCED — over the cap she
    # compresses her own reply once (LLM), a sentence trim is the last
    # resort; her workbench is never governed. Flag-off is byte-identical.
    reply_governor: bool = False
    # Entries written inside a fear window index at this salience — the
    # surprise knobs' twin. ge=1: a boost never demotes.
    fear_salience_boost: float = Field(default=1.5, ge=1.0)
    fear_salience_minutes: int = Field(default=60, ge=1)
    # Held-fears cap — a plant beyond it is DECLINED (never queued; the
    # weekly re-look is where the book breathes). ge=1: 0 would be a
    # second off-switch wearing a budget's clothes.
    fears_held_cap: int = Field(default=5, ge=1)
    # Step 25 · Regret (2026-08-23, spec §0): the backward stake kind — her
    # own missteps. Empty start; at most regret_max_open open (code rail);
    # a regret unfed for ~regret_half_life_days fades in the nightly review.
    regret: bool = False
    regret_max_open: int = Field(default=2, ge=1)
    regret_half_life_days: int = Field(default=14, ge=1)
    # Organ 4.13 · Opinions (2026-08-31, spec §0): views — positions held
    # with evidence, revised only for reasons, surviving restarts. "Drop it"
    # mutes, never deletes (owner call). views_max_active is the active cap
    # (a view past it files DORMANT, the wishbook law); views_dormant_cap
    # bounds the waiting pile, 0 = unlimited.
    opinions: bool = False
    views_max_active: int = Field(default=5, ge=1)
    views_dormant_cap: int = Field(default=10, ge=0)
    # Organ 4.9 · Grounded Vulnerability (2026-09-01, spec §0): her small
    # truths — lived events with receipts, offered rarely through two
    # doors. "Said once" governs offering; answering stays free.
    vulnerability: bool = False
    disclosures_max_open: int = Field(default=4, ge=1)
    disclosure_half_life_days: int = Field(default=14, ge=1)
    # Organ 4.12 · Shared Idiolect, provenance half (2026-09-02, spec §6):
    # her book of callbacks — words and bits that are hers-and-his, each
    # with a receipt. No quota, no cooling (her call). Activation = his
    # restart.
    idiolect: bool = False
    callbacks_band_max: int = Field(default=12, ge=1)
    # One thing on her mind (Batch 6, task 1, 2026-09-29): each night she
    # picks at most one thing she would bring up (ai/tonight.py), her reply
    # prompt shows it until she has said it, and the said-check after a reply
    # records that she did. Off = no pick, no band, no check, no file.
    tonight: bool = False
    # Recall bias (organ 4's second half, deferred 07-27, shipped now):
    # the undercurrent's lean tilts inner-memory ranking. Off = today's
    # ranking byte-identical (the lean is still WRITTEN — cheap truth).
    moment_recall_bias: bool = False
    recall_bias_boost: float = Field(default=1.15, ge=1.0)
    # Night Cycle (Selfhood Phase 1, 2026-07-11): a downtime pass (06:30
    # local since 2026-10-05; 04:00 before) that writes a first-person "what
    # today meant" reflection (ai/night_cycle.py) and surfaces it in the next
    # day's reply. Ships default-OFF like the stake ledger; the owner's
    # palli.yaml flips it on for a live feel-check. See
    # docs/superpowers/specs/2026-07-11-night-cycle-v1-design.md.
    night_cycle: bool = False
    # When the night pass STARTS, local "HH:MM" (the owner, 10-05: "move the
    # night run off-peak"). DeepSeek bills double 01:00-04:00 and 06:00-10:00
    # UTC on weekdays; 06:30 in New York is 10:30 UTC under EDT and 11:30 UTC
    # under EST, off-peak all year (01:00 was ruled out: peak once the
    # clocks change on Nov 1). Not the 04:00 day boundary, which stays: the
    # night still reflects on yesterday, and the workbench budget's day
    # still turns at own_work_day_key_hour. A value that is not HH:MM, or is
    # earlier than 04:00, runs at 06:30 with one warning
    # (ai.night_cycle.night_run_at) — never a failed settings load.
    night_cycle_run_at: str = "06:30"
    # Introspection (Selfhood evidence layer, 2026-07-13): gates the
    # `introspect` builtin tool — PaLLi's first inward-pointing tool, an
    # on-demand search over her OWN record (monologue + digests, night
    # reflections, stakes incl. resolved/faded, emotional timeline, memory
    # index, open threads) so self-reports become testimony, including an
    # honest "nothing there". Ships default-OFF; gated at tool REGISTRATION
    # (web_search pattern) so flag-off is byte-identical. See
    # docs/superpowers/specs/2026-07-13-introspection-tool-v1-design.md.
    introspection: bool = False
    # Consolidation (Selfhood Wave 1a, 2026-07-15): gates the NIGHTLY
    # consolidation pass — day-gist, capped fact gist-aging, capped
    # reconciliation — run by the Night Cycle engine after the daily-meaning
    # reflection (ai/consolidation.py). Ships default-OFF; the owner flips it
    # only after the backfill ritual completes (spec §6). NOTE the flag/data
    # distinction (spec §4.5): the archive BACKFILL is a data change with its
    # own gate; this flag gates only the go-forward nightly code path.
    # introspect's exact mode deliberately rides companion.introspection,
    # NOT this flag — verbatim reach must survive a consolidation rollback.
    # NOTE: a no-op unless companion.night_cycle is ALSO true — the engine
    # tick gates on night_cycle before the consolidation step ever runs.
    consolidation: bool = False
    # Present Moment (Slice 1, 2026-07-24): the salience engine + moment
    # document. Spec: docs/superpowers/specs/2026-07-24-present-moment-design.md.
    # Default OFF; the owner flips it in data/palli.yaml. NOTE this field
    # MUST exist here — faculty_enabled() is fail-open on missing attrs.
    present_moment: bool = False
    # Slice 2 (roadmap step 11, 2026-07-27): her replies read the moment —
    # the reply prompt carries her latest moment document as a volatile band.
    # Requires present_moment true as well (both flags): a dark engine must
    # never haunt replies with old files. NOTE the field MUST exist here —
    # faculty_enabled() is fail-open on missing attrs (the present_moment
    # lesson above).
    # Spec: docs/superpowers/specs/2026-07-27-present-moment-slice-2-design.md
    moment_in_replies: bool = False
    # Slice 3 (roadmap step 12, 2026-07-27): the affect field — the moment
    # gets a mood. One undercurrent sentence persisted across cycles
    # (carry-forward on parse-miss), taught drift, prose forever. Flag picks
    # a prompt VARIANT; the base appraisal file is never edited, so off is
    # byte-identical by construction. Organ 4's recall-bias half is a
    # RECORDED follow-up (spec §5), not silence.
    # Spec: docs/superpowers/specs/2026-07-27-present-moment-slice-3-affect-field-design.md
    moment_affect: bool = False
    # The arbiter (roadmap step 18, 2026-08-06): the moment's say-impulse
    # becomes a real reach-out on ONE rail — ceiling-gated, custody-complete.
    # At birth it absorbs the open-threads surfacing pass AND the ambient
    # arrival greeting (owner's both-at-birth call, spec §0). Requires
    # present_moment. OFF = byte-identical today. NOTE the field MUST exist
    # here — faculty_enabled() is fail-open on missing attrs.
    # Spec: docs/superpowers/specs/2026-08-06-arbiter-design.md
    arbiter: bool = False
    # Hard daily send ceiling, code-enforced at the rail; greetings spend it
    # too. DELIBERATELY OUTSIDE the 0-means-unlimited convention: 0 = rail
    # dark (a send ceiling must never fat-finger to infinity).
    arbiter_daily_send_ceiling: int = Field(default=3, ge=0)
    # No sends within this many minutes of his last message (mid-conversation
    # her replies carry the moment — the rail is for quiet). Min 1, like
    # own_work_quiet_minutes: lower it, don't zero it.
    arbiter_quiet_minutes: int = Field(default=5, ge=1)
    # Typed quiet window "HH:MM-HH:MM" (may wrap midnight), "" = off — the
    # step-22 stopgap; one authority, consulted only at the rail.
    arbiter_quiet_window: str = ""
    # Learned quiet hours (step 22, spec 2026-08-10): her model of his
    # absence rhythm gates her self-initiated sends. Master toggle default
    # OFF — owner requirement verbatim: "approve, but make it toggleable."
    # Off = byte-identical: no learner run, no file, no gate consult.
    arbiter_learned_quiet: bool = False
    # The nightly rhythm refresh (night_cycle._refresh_owner_rhythm): her
    # picture of when he is around, which the surprise organ reads. Its own
    # switch, independent of arbiter_learned_quiet, since 2026-09-28 (owner
    # call 7) — riding on quiet hours froze the file the day they went off.
    rhythm_refresh: bool = True
    rhythm_lookback_days: int = Field(default=56, ge=7)
    rhythm_quiet_max_active_ratio: float = Field(default=0.08, ge=0.0, le=1.0)
    rhythm_min_observed_weekdays: int = Field(default=15, ge=1)
    rhythm_min_observed_weekend_days: int = Field(default=6, ge=1)
    rhythm_present_override_minutes: int = Field(default=90, ge=1)
    # Expiry backstops (spec §7) — judgment owns lifespan, these catch what
    # judgment never touches. 0 = no backstop for that tier (convention).
    arbiter_followup_ttl_days: int = Field(default=7, ge=0)
    arbiter_thread_ttl_days: int = Field(default=45, ge=0)
    # Freshness gate (impulse-freshness spec §4, 2026-08-07): one judged look
    # at the CURRENT conversation before a release-ready impulse becomes a
    # message — the 08-06 stale first-send repair. Fresh releases the framing
    # verbatim; stale drops with the reason in her ledger; errors fail
    # closed. False = day-one release behavior (the emergency knob).
    arbiter_freshness_check: bool = True
    # Voice joins the arbiter (step 32, 2026-08-21): a released say (or
    # workbench share) arrives SPOKEN when he is in a voice channel with
    # her — spoken only, no duplicate text; the written trace rides her
    # record. Speak failure falls back to text (ledger voice_fallback_text).
    # In-voice counts as present for the learned-quiet override. Same
    # gates, same daily ceiling. Schema default False (code conservative);
    # the live yaml ships true (owner's activation call, 2026-08-21).
    arbiter_voice_says: bool = False
    # The pen (roadmap step 10, 2026-07-27): her first GENERATIVE agency over
    # her own record — a tool that writes a dated note in her own words.
    # Notes are evidence, never identity: ungated because they are raw
    # material, never injected into a reply, read back only when she reaches
    # for them (introspect) and by the gated 4am rituals.
    # Spec: docs/superpowers/specs/2026-07-27-note-to-self-design.md
    self_notes: bool = False
    # Which teaching line ships in the prompt: "earned" (a high bar — write
    # only what would otherwise be lost) or "open" (her judgment, lightly
    # framed). Unknown values fall back to "earned" — fail toward restraint.
    self_notes_bar: str = "earned"
    # Hard rail, not a behavior knob (the teaching line does the restraining).
    # Presets: 5 scarce · 20 default · 60 generous · 0 unlimited.
    self_notes_daily_cap: int = Field(default=20, ge=0)
    # May her Present Moment read recent notes? Owner chose yes-with-a-toggle
    # 2026-07-27. Causally inert while the moment is shadow; spec §5 records
    # both reasonings and the MANDATORY re-decision at Slice 2.
    self_notes_in_moment: bool = True
    # Converse while working (roadmap step 13, 2026-07-26): mid-task messages
    # steer the running work instead of cancelling it. The client fork reads
    # this fail-CLOSED (steering_fork._flag_on — unbooted or missing means
    # today's cancel behavior), but the field must still exist so the YAML
    # key validates. Spec:
    # docs/superpowers/specs/2026-07-26-converse-while-working-design.md.
    converse_while_working: bool = False
    # Her own work (roadmap steps 16-17, 2026-07-28): the workspace + the
    # workbench — self-directed downtime work sessions with the guarded,
    # autonomous shell (OS wall: the palli-work user). She derives her own
    # projects; SUGGESTIONS.md is the owner's whisper channel. All spec §4e.
    # Spec: docs/superpowers/specs/2026-07-28-her-own-work-design.md
    own_work: bool = False
    # 0 = UNLIMITED on every budget below (the pen's convention:
    # self_notes_daily_cap 5 scarce / 20 default / 60 generous / 0 unlimited).
    # The own_work FLAG above is the off switch — a zeroed budget never means
    # "never". Rounds included by the owner's 2026-08-03 call (deviation from
    # the parked plan's ledger); at 0 the backstops are the runner's loop
    # detection, the wall clock when nonzero, and stop.
    own_work_daily_sessions: int = Field(default=3, ge=0)
    # When her session budget's "day" turns over (2026-08-06). Midnight is the
    # worst boundary for a nocturnal house: the refill landed exactly when her
    # quiet begins, so all three sessions burned by 1 AM and daytime quiet got
    # nothing. Default 4 = the day boundary (ai.night_cycle.NIGHT_CYCLE_HOUR;
    # until 2026-10-05 also the hour her night ritual ran — it now starts at
    # night_cycle_run_at, 06:30, and this day key stays at 4). 0 = legacy
    # midnight semantics, outside the 0=unlimited convention on purpose — a
    # boundary is a clock, not a budget.
    own_work_day_key_hour: int = Field(default=4, ge=0, le=23)
    own_work_session_rounds: int = Field(default=8, ge=0)
    own_work_session_minutes: int = Field(default=20, ge=0)
    own_work_active_projects_max: int = Field(default=2, ge=0)
    own_work_min_free_disk_gb: int = Field(default=5, ge=0)
    # Conversation-quiet gate before a session may start. 2026-08-18, the
    # owner's call: her time is not rationed to his downtime — "she can do
    # them whenever, even while talking to me" — so 0 (no gate) is the
    # default; her chat with him runs on its own session key and is never
    # queued behind her work. Nonzero = the 08-03 behavior (minutes of
    # quiet required).
    own_work_quiet_minutes: int = Field(default=0, ge=0)
    # Her sessions are OCCASIONED (2026-08-18): a daydream left her a line,
    # he touched SUGGESTIONS.md, a hand-run probe. Under the occasions, a
    # slow heartbeat — a sit-down "just because" at most this often (the
    # daily cap and the gap still bound it). 0 = occasions only.
    own_work_heartbeat_minutes: int = Field(default=480, ge=0)
    # Minimum minutes between her self-directed sessions (2026-08-07). The
    # quiet gate alone let three sessions burn in 33 minutes at 4 AM — her
    # own sign-off called the third "noise itself." 0 = no gap (the
    # 0-unlimited convention: a gap is budget-shaped).
    own_work_min_gap_minutes: int = Field(default=90, ge=0)
    # Off DeepSeek's peak (the owner, 10-05: "yes, move her own time
    # off-peak too"): while her brain is DeepSeek, no session starts in its
    # peak hours (01:00-04:00 and 06:00-10:00 UTC, Monday to Friday — double
    # price, and a session is ~170-230k input tokens a step); the occasions
    # wait for the next tick, as the gap above defers them. Another provider
    # is never held back. False = sessions start at any hour, as before.
    own_work_off_peak_only: bool = True
    # How what she makes reaches him (2026-08-18). The wall between her
    # working self and her talking self was total: a session's SHARE line
    # became an open-thread row that only a ~3% appraisal lottery could
    # raise — 20 shares, zero delivered. His call: "a message when it's
    # done", changeable here.
    #   message — a real message through the arbiter's judgment of the
    #             moment (not mid-conversation, not his quiet hours);
    #             held ones wait in her store and go when the moment opens.
    #   thread  — the legacy lane: an open thread her moment MAY raise.
    #   room    — no push at all; her memory and the trace band carry it,
    #             and she mentions it when it fits.
    own_work_share_delivery: Literal["message", "thread", "room"] = "message"
    # Wanting (roadmap step 20, organ 5, 2026-08-07): the wishbook + the
    # occasioned daydream lane — a durable home for standing wishes and a
    # private thinking lane that may plan (the monologue never does).
    # Nothing here sends by itself; her wanting reaches him only through
    # the arbiter rail (want_id) or a workbench SHARE. OFF = byte-identical.
    # Spec: docs/superpowers/specs/2026-08-07-wanting-respec.md
    wanting: bool = False
    # Drift-catch tick when no occasions arrive. The occasioned wakes are
    # the plan; the heartbeat is the net. ge=1: lower it, don't zero it.
    daydream_heartbeat_minutes: int = Field(default=180, ge=1)
    # Wake-coalescing gap (the deferring law: suppressed wakes fire at gap
    # expiry with the bundle, never cancelled). ge=1, quiet-minutes class.
    daydream_min_gap_minutes: int = Field(default=45, ge=1)
    # Budget-shaped: 0 = unlimited (the wanting flag is the off switch).
    daydream_daily_max: int = Field(default=8, ge=0)
    # The wishbook's alive cap — capacity forcing prioritization (the
    # stake_ledger_max_open precedent). Machinery filings land DORMANT at
    # cap: kept, acknowledged, never displacing (re-spec §3).
    quests_max_alive: int = Field(default=7, ge=1)
    # May the reflection pass file durably-directive turns as quests?
    quests_from_conversation: bool = True
    # §0.10: read-only NAS dives as a second curiosity modality. Own flag,
    # his separate flip — the one surface touching his real data.
    curiosity_nas_dives: bool = False
    # ── Step 23 · Outward senses (2026-08-24) ─────────────────────────────
    # Spec: docs/superpowers/specs/2026-08-24-outward-senses-design.md.
    # §0.5 (owner, 10:16): her machine is HERS, the NAS is the house's shared
    # drive, the network is her home. outward_senses = the umbrella flag: the
    # belonging passage in her prompt + the purity tests. Default-OFF like
    # its siblings; the owner's yaml flips it.
    outward_senses: bool = False
    # §0.3: NAS wandering has its OWN cap (0 = unlimited, the 07c convention)
    # and never spends the web-research budget (research_daily_cap).
    curiosity_nas_daily_cap: int = Field(default=0, ge=0)
    # §0.1: top-level NAS names that do not exist to the dive's walk. EMPTY
    # BY THE OWNER'S EXPLICIT CALL ("no folders listed in denylist at this
    # time") — quiet machinery he may populate later; never the centerpiece.
    nas_deny: list[str] = Field(default_factory=list)
    # §3: the LAN presence sense — the device book, arrivals, "the house
    # right now". §0.6 (owner, 12:39, "C"): LISTEN, DON'T SCAN — she hears
    # the house's own ARP chatter (the sense), sweeps only a few times a day
    # to confirm who is still here, and asks after a silent device once.
    lan_presence: bool = False
    # The capture alone (sudo -n tcpdump … arp); false = sweeps only.
    lan_listener: bool = True
    # Confirmation sweeps (sudo -n nmap -sn): 480 = three a day; floor 30 —
    # there is no 5-minute clock (§0.6).
    lan_sweep_interval_minutes: int = Field(default=480, ge=30)
    # Silence past this gets one targeted quiet-check; a miss = device_left.
    lan_absence_minutes: int = Field(default=15, ge=1)
    # Optional labels {mac: "his phone"} — she may also simply ask him.
    lan_devices: dict[str, str] = Field(default_factory=dict)
    # How often the house may wake her present moment (Batch 6, task 4,
    # 2026-09-29). Device comings and goings set the wake at most once in
    # this many seconds, and a cycle whose only news is a device, fired while
    # her last written moment is younger than this, is skipped as nothing
    # new. Every other event wakes her as before. 0 = both off: every device
    # event wakes and no cycle is skipped.
    device_wake_seconds: int = Field(default=3600, ge=0)
    # Step 27 · Duplex (spec: docs/superpowers/specs/2026-08-27-duplex-design.md).
    # §0 = the owner's words: she works AND talks — silence by default while
    # working, speaking mid-task is an act (chime_in / ask_him), her moment
    # loop may still reach him while a run is in flight, a problem or an
    # overrun is a sense, a stop lands at a safe tool boundary. Default-OFF
    # like its siblings; the owner's yaml flips it. Flag off = byte-identical.
    duplex: bool = False
    # §2 the time sense: fire task_overrun once elapsed > expected × factor
    # (and again at each further multiple). Her estimate rides set_presence's
    # expect_minutes; no estimate → the default below.
    duplex_overrun_factor: float = Field(default=1.5, gt=1.0)
    duplex_overrun_minutes_default: int = Field(default=10, ge=1)
    # §3 cooperative stop: the hard cancel stays as the backstop after this.
    duplex_stop_grace_seconds: int = Field(default=30, ge=5)
    # §4 two things at once (Phase B): concurrent TASKS per channel — the run
    # plus task sidecars. A conversation is not a task (it rides its own
    # one-at-a-time talk lane, never this cap). His call 2: two.
    duplex_max_tasks: int = Field(default=2, ge=1)
    # §3 a paused task nobody comes back to expires — she tells him once.
    duplex_pause_ttl_minutes: int = Field(default=120, ge=1)
    # She decides whether to answer, for anyone (Batch 8, task 2 — B8-2,
    # ruling T2-1): she takes part as a participant who knows she is an AI.
    # Her reply can choose to stay quiet (a `stay_quiet` call: nothing is
    # sent, the message stays in her transcript, the choice and her reason go
    # to data/quiet_choices/); the quick vote stops deciding silence in a full
    # turn and answers answer / wait / stay_quiet. The switch covers the
    # decision flow only — the guest queue and the owner-only voice commands
    # do not depend on it. Off = today's decision behaviour, byte-identical
    # (pinned by tests/agent/test_quiet_record_and_switch.py). Dark until the
    # wrap turns it on in the live yaml.
    she_decides: bool = False
    # One first look, in her own voice (R14; the owner, 10-12 21:05: "we
    # should build the one first look now"): one call in the first person
    # sees each message with the room and a small slice of her own mind, and
    # takes the place of the quick vote and the first read. Counts only while
    # she_decides is on too (core/agent/first_look.py enabled()); she_decides
    # on with this off is today's flow, byte for byte. Dark until the wrap
    # turns it on in the live yaml.
    first_look: bool = False
    # Group openers (the presence slice, item 4; the owner, 2026-10-13): PaLLi
    # may start something in a group chat on its own, at most this many a day
    # per group (0 = off), any time of day (core/turn/group_openers.py). Ships
    # on, for strangers too ("On, same as yours"); the switch turns it off.
    group_openers: bool = True
    group_openers_per_day: int = Field(default=1, ge=0)
    # Memory that reaches (R15; plan docs/2026-10-13-memory-that-reaches-plan.md):
    # a searchable record of every message (data/memory_reach.sqlite3, kept by
    # ai/memory_reach_index.py) and what reads it — her tool for searching
    # what was said, a guest's own room history, today's facts before the
    # night files them. Off = today's behaviour, byte for byte. Dark until
    # the wrap turns it on in the live yaml.
    memory_reach: bool = False
    # Times on every line (step 2 of the release order, 2026-10-08, the owner's
    # call): each user and assistant line in her payload carries when it was
    # said, "[Oct 8 22:35]" (core/agent/message_times.py). Payload-only; the
    # stored rows are unchanged. Off = today's payload, byte-identical.
    message_times: bool = True
    # How long a "wait" in a shared room gives the conversation before she
    # looks again (Batch 8, task 2b): a moment to reconsider, not a rule.
    room_wait_seconds: int = Field(default=45, ge=5)
    # Narrative Self (Selfhood Wave 1b, 2026-07-16): three self-authored
    # identity documents in identity/palli/ (own local git repo), revised
    # weekly on the Night Cycle tick behind a constitutional gate, and
    # injected back as system-prompt bands. Ships default-OFF like its
    # siblings; the owner's palli.yaml flips it after first authorship.
    # REQUIRES night_cycle: the weekly writer rides that tick, so
    # narrative_self_enabled() treats this flag as OFF while night_cycle
    # is off (fix wave 2026-07-17 — no frozen, never-authored bands).
    # See docs/superpowers/specs/2026-07-16-narrative-self-v1-design.md.
    narrative_self: bool = False
    # Budget-aware context assembly. 0 = auto-resolve from the response
    # lane's model context window; a positive value caps total injected
    # context tokens explicitly.
    context_token_budget: int = Field(default=0, ge=0)


_PRESET_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_PRESET_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class PresetDefinition(BaseModel):
    """A named overlay applied in one transaction: dot-path → value overrides
    for this file, plus the env half a brain switch needs (configurator spec
    2026-09-03 §3.3 R2 — her brain reads LLM_PROVIDER / LLM_RESPONSE_MODEL from
    .env.local, never llm.routing.response). `env` is allowlisted to provider
    and model names: a preset chooses a brain, it never carries a credential.
    The flat shorthand (a mapping with no `overrides` key) still means
    overrides; `env` is recognised only in the explicit two-field form."""

    model_config = ConfigDict(extra="forbid")

    overrides: Dict[str, Any]
    env: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _wrap_flat_dict(cls, value: Any) -> Any:
        if isinstance(value, dict) and "overrides" not in value:
            return {"overrides": value}
        return value

    @model_validator(mode="after")
    def _validate_overrides(self) -> "PresetDefinition":
        if not self.overrides:
            raise ValueError("preset must define at least one override")
        for key in self.overrides:
            if not _PRESET_KEY_RE.match(key):
                raise ValueError(f"preset override key {key!r} is not a valid dot path")
        for key, value in self.env.items():
            if not _PRESET_ENV_KEY_RE.match(str(key)):
                raise ValueError(f"preset env key {key!r} must be UPPER_SNAKE_CASE")
            if not env_key_allowed_in_preset(str(key)):
                raise ValueError(
                    f"preset env key {key!r} is not allowed; a preset may set only "
                    f"{sorted(BRAIN_ENV_KEYS)} (never a credential)"
                )
            if not isinstance(value, str):
                raise ValueError(f"preset env value for {key!r} must be a string")
        return self


SCHEMA_VERSION = 1


class PalliSettings(BaseModel):
    """Root pydantic model for data/palli.yaml."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(description="Schema version; bump when breaking changes land.")
    providers: Dict[str, ProviderConfig]
    llm: LLMConfig
    voice: VoiceConfig
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    policy: PolicyConfig
    presets: Dict[str, PresetDefinition] = Field(default_factory=dict)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    inner_memory: InnerMemoryConfig = Field(default_factory=InnerMemoryConfig)
    fact_memory: FactMemoryConfig = Field(default_factory=FactMemoryConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    companion: CompanionConfig = Field(default_factory=CompanionConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    spending: SpendingConfig = Field(default_factory=SpendingConfig)

    @model_validator(mode="after")
    def _validate_version(self) -> "PalliSettings":
        if self.version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema version {self.version}; expected {SCHEMA_VERSION}"
            )
        return self

    @model_validator(mode="after")
    def _validate_routing_references(self) -> "PalliSettings":
        known_providers = set(self.providers.keys())
        if self.llm.default_provider not in known_providers:
            raise ValueError(
                f"llm.default_provider={self.llm.default_provider!r} is not a defined provider"
            )
        # Route-to-disabled is intentionally allowed: users may toggle providers off
        # without rewriting routing entries. Phase 3's hot-reload surfaces the mismatch.
        routing = self.llm.routing
        for task_name in RoutingConfig.model_fields:
            entry: RoutingEntry = getattr(routing, task_name)
            if entry is None:
                # Optional lanes (appraisal) may be absent — safe env/default
                # fallback at get_task_model time, nothing to validate.
                continue
            if entry.provider not in known_providers:
                raise ValueError(
                    f"llm.routing.{task_name}.provider={entry.provider!r} is not a defined provider"
                )
            provider = self.providers[entry.provider]
            if provider.discovery is DiscoveryMode.CURATED:
                known_models = {m.id for m in provider.models}
                if entry.model not in known_models:
                    raise ValueError(
                        f"llm.routing.{task_name}.model={entry.model!r} is not listed under provider "
                        f"{entry.provider!r} (curated)"
                    )
        return self

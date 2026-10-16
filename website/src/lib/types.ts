export type PublicUser = {
  id: number;
  email: string;
  email_verified?: boolean;
  email_verified_at?: number | null;
  age_confirmed?: boolean;
  age_confirmed_at?: number | null;
  tos_accepted_at?: number | null;
  privacy_accepted_at?: number | null;
  tos_version?: string | null;
  privacy_version?: string | null;
  discord_user_id?: string | null;
  telegram_username?: string | null;
  role?: string;
};

export type PublicAuthSurface = {
  dashboard_auth_mode: "open" | "session";
  cloud_auth_routes_enabled: boolean;
  account_security_routes_enabled: boolean;
};

export type MeResponse = {
  user: PublicUser | null;
  auth_surface: PublicAuthSurface;
  // First door, task 4 (2026-09-17): true when this is a self-hosted install
  // (api/web_auth.py reads core.local_mode.local_mode_enabled(), the same
  // function GET /api/setup/status answers from). The SaaS-era account pages
  // are hidden when it is true — hidden, not deleted.
  local_mode: boolean;
};

// --- interiority (Wave 0b) — mirrors api/web_interiority.py §4.1 payloads verbatim ---

export type InteriorityOrganChip = {
  key: string;
  enabled: boolean;
  first_artifact_ts: number | null;
  last_artifact_ts: number | null;
  count_since: number;
  error?: boolean;
};

export type InteriorityReflection = {
  date: string;
  text: string;
  created_at: number;
};

export type InteriorityOpenStake = {
  id: number;
  kind: string;
  stake: string;
  created_at: number;
};

export type InteriorityResolvedStake = {
  id: number;
  kind: string;
  stake: string;
  resolution: string;
  resolved_at: number | null;
};

export type InteriorityTimelineRead = {
  created_at: number;
  read_text: string;
  weight: number;
  needs_support: number;
};

export type InteriorityThread = {
  topic_summary: string;
  why_it_matters: string;
  created_at: number;
  updated_at: number;
};

export interface MomentImpulse {
  kind?: string;
  why?: string;
}

export interface MomentCurrent {
  ts: number;
  age_seconds: number;
  stale: boolean;
  text: string;
  woke_on: Array<{ kind: string; count?: number }>;
  impulse: MomentImpulse;
  sense_health: Record<string, string>;
  // Slice 3: the affect field. Optional on purpose — the API omits the key
  // when she has no undercurrent (absence means none, not a blank slot).
  undercurrent?: string;
}

export interface MomentRecentEntry {
  ts: number;
  text: string;
  impulse: string;
  impulse_why: string;
  woke_on: Array<{ kind: string; count?: number }>;
}

export interface MomentSection {
  enabled: boolean;
  current: MomentCurrent | null;
  recent: MomentRecentEntry[];
  error?: boolean;
}

export interface WantingQuest {
  id: string;
  text: string;
  source: string;
  status: string;
  horizon: string;
  born: number;
  updated_at: number;
  progress: Array<{ at: number; note: string }>;
}

export interface WantingSection {
  enabled: boolean;
  quests: WantingQuest[];
  daydreams: Array<{ ts: number; text: string }>;
  error?: boolean;
}

export interface WorkbenchSessionEntry {
  ts: number | null;
  signoff: string;
  shared: boolean;
}

export interface WorkbenchSection {
  enabled: boolean;
  recent: WorkbenchSessionEntry[];
  error?: boolean;
}

export type InteriorityOverview = {
  since: number;
  organs: InteriorityOrganChip[];
  reflections: { enabled: boolean; error?: boolean; items: InteriorityReflection[] };
  stakes: {
    enabled: boolean;
    error?: boolean;
    open: InteriorityOpenStake[];
    resolved: InteriorityResolvedStake[];
  };
  timeline: { enabled: boolean; error?: boolean; items: InteriorityTimelineRead[] };
  threads: { error?: boolean; items: InteriorityThread[] };
  moment: MomentSection;
  workbench: WorkbenchSection;
  // Optional: an older backend build must not break the page.
  wanting?: WantingSection;
};

export type InteriorityMonologueEntry = {
  ts: number;
  tag: string | null;
  chars: number;
  thought?: string;
};

export type InteriorityMonologue = {
  since: number;
  raw: boolean;
  count: number;
  digests: Record<string, number>;
  entries: InteriorityMonologueEntry[];
  error?: boolean;
};

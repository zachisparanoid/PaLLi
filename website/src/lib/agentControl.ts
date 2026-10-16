export type AgentSessionSettings = {
  queue_mode?: string | null;
  default_announce_mode?: string | null;
  auto_resume_after_approval?: boolean | null;
  child_budget_overrides?: Record<string, unknown> | null;
};

export type AgentSessionSummary = {
  session_key: string;
  root_session_key?: string | null;
  parent_session_key?: string | null;
  session_id?: string | null;
  alias?: string | null;
  label?: string | null;
  kind?: string | null;
  status?: string | null;
  platform?: string | null;
  channel_id?: string | null;
  last_activity?: number | string | null;
  last_completion?: string | null;
  last_run_id?: string | null;
  archived_at?: number | string | null;
  created_at?: number | string | null;
  updated_at?: number | string | null;
  subagent_depth?: number | null;
  settings?: AgentSessionSettings | null;
};

export type AgentRootSummary = AgentSessionSummary;

export type AgentSessionTree = {
  root: AgentSessionSummary | null;
  sessions: AgentSessionSummary[];
};

export type AgentRunSummary = {
  run_id: string;
  session_key?: string | null;
  root_session_key?: string | null;
  status?: string | null;
  goal?: string | null;
  error?: string | null;
  started_at?: number | string | null;
  ended_at?: number | string | null;
  created_at?: number | string | null;
  updated_at?: number | string | null;
  metadata?: Record<string, unknown> | null;
};

export type AgentRunStep = {
  step_index?: number | null;
  tool_name?: string | null;
  thought?: string | null;
  action?: string | null;
  observation?: string | null;
  created_at?: number | string | null;
  metadata?: Record<string, unknown> | null;
};

export type AgentRunDetail = AgentRunSummary & {
  steps: AgentRunStep[];
};

export type AgentDelivery = {
  delivery_id: string;
  root_session_key?: string | null;
  source_session_key?: string | null;
  target_session_key?: string | null;
  request_text?: string | null;
  status?: string | null;
  wait_mode?: string | null;
  announce_mode?: string | null;
  response_status?: string | null;
  response_text?: string | null;
  error?: string | null;
  created_at?: number | string | null;
  updated_at?: number | string | null;
  completed_at?: number | string | null;
  metadata?: Record<string, unknown> | null;
  reply_back_rounds_used?: number | null;
};

export type AgentConfirmation = {
  token: string;
  run_id?: string | null;
  session_key?: string | null;
  platform?: string | null;
  status?: string | null;
  expires_at?: number | string | null;
  command_preview?: string | null;
  command?: string | null;
  metadata?: Record<string, unknown> | null;
};

export type AgentWaitResult = {
  run_id?: string;
  status?: string;
  run_status?: string;
  started_at?: number | string | null;
  ended_at?: number | string | null;
  error?: string | null;
};

export type AgentAdminLiveRun = {
  run_id: string;
  root_session_key?: string | null;
  session_key?: string | null;
  owner_user_id?: number | null;
  platform?: string | null;
  channel_id?: string | null;
  status?: string | null;
  goal?: string | null;
  started_at?: number | string | null;
};

type AnyRecord = Record<string, unknown>;

function isRecord(value: unknown): value is AnyRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asRecord(value: unknown): AnyRecord | null {
  return isRecord(value) ? value : null;
}

export function buildAgentQuery(
  params: Record<string, string | number | boolean | null | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const serialized = search.toString();
  return serialized ? `?${serialized}` : "";
}

function extractCollection<T>(payload: unknown, keys: string[]): T[] {
  if (Array.isArray(payload)) return payload as T[];
  const record = asRecord(payload);
  if (!record) return [];
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) return value as T[];
  }
  return [];
}

function coerceRecordArray<T extends AnyRecord>(items: T[], idKey: keyof T): T[] {
  const seen = new Set<string>();
  const next: T[] = [];
  for (const item of items) {
    const raw = item[idKey];
    if (typeof raw !== "string" || !raw) continue;
    if (seen.has(raw)) continue;
    seen.add(raw);
    next.push(item);
  }
  return next;
}

export function normalizeRoots(payload: unknown): AgentRootSummary[] {
  return coerceRecordArray(
    extractCollection<AgentRootSummary>(payload, ["roots", "sessions"]),
    "session_key",
  );
}

export function normalizeSessionTree(payload: unknown): AgentSessionTree {
  const record = asRecord(payload);
  const rootCandidate = record?.root;
  const sessions = coerceRecordArray(
    extractCollection<AgentSessionSummary>(payload, ["sessions", "tree", "children"]),
    "session_key",
  );
  const root =
    (asRecord(rootCandidate) as AgentSessionSummary | null) ||
    sessions.find((session) => !session.parent_session_key) ||
    null;
  const mergedSessions = root
    ? coerceRecordArray([root, ...sessions], "session_key")
    : sessions;
  return { root, sessions: mergedSessions };
}

export function normalizeRuns(payload: unknown): AgentRunSummary[] {
  return coerceRecordArray(
    extractCollection<AgentRunSummary>(payload, ["runs", "items"]),
    "run_id",
  );
}

export function normalizeRunDetail(payload: unknown): AgentRunDetail | null {
  if (!payload) return null;
  const record = asRecord(payload);
  if (!record) return null;

  const run = asRecord(record.run) || record;
  if (typeof run.run_id !== "string" || !run.run_id) return null;

  const stepsFromRoot = extractCollection<AgentRunStep>(payload, ["steps"]);
  const stepsFromRun = Array.isArray(run.steps) ? (run.steps as AgentRunStep[]) : [];
  const steps = stepsFromRoot.length > 0 ? stepsFromRoot : stepsFromRun;

  return {
    ...(run as AgentRunSummary),
    steps,
  };
}

export function normalizeDeliveries(payload: unknown): AgentDelivery[] {
  return coerceRecordArray(
    extractCollection<AgentDelivery>(payload, ["deliveries", "items"]),
    "delivery_id",
  );
}

export function normalizeConfirmations(payload: unknown): AgentConfirmation[] {
  return coerceRecordArray(
    extractCollection<AgentConfirmation>(payload, ["confirmations", "items"]),
    "token",
  );
}

export function normalizeLiveRuns(payload: unknown): AgentAdminLiveRun[] {
  return coerceRecordArray(
    extractCollection<AgentAdminLiveRun>(payload, ["live_runs", "runs", "items"]),
    "run_id",
  );
}

export function getSessionTitle(session?: AgentSessionSummary | null): string {
  if (!session) return "Unknown session";
  return (
    session.label ||
    session.alias ||
    session.session_id ||
    session.session_key ||
    "Unknown session"
  );
}

export function getSessionSubtitle(session?: AgentSessionSummary | null): string {
  if (!session) return "No session selected";
  const bits = [
    session.kind,
    session.alias && session.alias !== session.label ? `@${session.alias}` : null,
    session.session_id,
    session.platform && session.channel_id ? `${session.platform}:${session.channel_id}` : null,
  ].filter(Boolean);
  return bits.join(" · ");
}

export function shorten(value: unknown, max = 180): string {
  const text = typeof value === "string" ? value : value == null ? "" : String(value);
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

function toEpochMs(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 1_000_000_000_000 ? value : value * 1000;
  }
  if (typeof value === "string" && value.trim()) {
    const asNumber = Number(value);
    if (Number.isFinite(asNumber)) {
      return asNumber > 1_000_000_000_000 ? asNumber : asNumber * 1000;
    }
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

export function formatTimestamp(value: unknown): string {
  const epochMs = toEpochMs(value);
  if (!epochMs) return "Unknown";
  return new Date(epochMs).toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelativeTime(value: unknown): string {
  const epochMs = toEpochMs(value);
  if (!epochMs) return "Unknown";
  const deltaMs = epochMs - Date.now();
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const seconds = Math.round(deltaMs / 1000);
  const minutes = Math.round(seconds / 60);
  const hours = Math.round(minutes / 60);
  const days = Math.round(hours / 24);
  if (Math.abs(seconds) < 60) return formatter.format(seconds, "second");
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  return formatter.format(days, "day");
}

export function getStatusClasses(status?: string | null): string {
  const normalized = (status || "").toLowerCase();
  if (
    normalized === "running" ||
    normalized === "active" ||
    normalized === "pending" ||
    normalized === "queued"
  ) {
    return "border-brand-500/20 bg-brand-500/10 text-brand-100";
  }
  if (
    normalized === "completed" ||
    normalized === "ok" ||
    normalized === "success" ||
    normalized === "approved"
  ) {
    return "border-emerald-500/20 bg-emerald-500/10 text-emerald-100";
  }
  if (
    normalized === "error" ||
    normalized === "failed" ||
    normalized === "denied" ||
    normalized === "cancelled" ||
    normalized === "aborted" ||
    normalized === "budget_exceeded"
  ) {
    return "border-rose-500/20 bg-rose-500/10 text-rose-100";
  }
  if (normalized === "archived" || normalized === "idle" || normalized === "timeout") {
    return "border-amber-500/20 bg-amber-500/10 text-amber-100";
  }
  return "border-white/10 bg-white/5 text-white/70";
}

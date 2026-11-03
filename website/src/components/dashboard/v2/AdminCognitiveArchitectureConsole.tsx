"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  BrainCircuit,
  Clock3,
  Cpu,
  Gauge,
  Layers3,
  Loader2,
  MessageSquareText,
  Pause,
  Play,
  RefreshCw,
  Route,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Waves,
} from "lucide-react";

import { apiGet } from "@/lib/api";
import { devLogError } from "@/lib/devLog";
import { formatRelativeTime, shorten } from "@/lib/agentControl";
import {
  AgentPanel,
  DetailRow,
  EmptyState,
  InspectionUnavailableState,
  SmallButton,
  StatusPill,
} from "@/components/dashboard/v2/agentControlShared";

type CognitiveSignal = {
  label?: string;
  value?: string;
  detail?: string;
  confidence?: number;
};

type CognitiveTier = {
  id?: string;
  label?: string;
  headline?: string;
  status?: string;
  signals?: CognitiveSignal[];
  last_seen_at?: number | string | null;
};

type CognitiveLiveState = {
  status?: string;
  latest_trace_id?: number | string | null;
  age_seconds?: number | null;
  mode?: string;
  strategy_action?: string;
  delivery_shape?: string;
  channel?: string;
  message_id?: string;
  last_seen_at?: number | string | null;
};

type CognitiveFeatureFlag = {
  key?: string;
  label?: string;
  enabled?: boolean;
  default_enabled?: boolean;
};

type BehaviorControl = {
  control_key?: string;
  label?: string;
  scope?: string;
  value?: string;
  source?: string;
  confidence?: number;
  created_at?: number;
  expires_at?: number | null;
};

type BehaviorTrace = {
  id?: number;
  created_at?: number;
  user_id?: string;
  channel?: string;
  message_id?: string;
  interaction_mode?: string;
  strategy_action?: string;
  delivery_shape?: string;
  behavior_frame?: Record<string, unknown>;
  active_controls?: BehaviorControl[];
  outcome?: Record<string, unknown>;
};

type CognitivePayload = {
  section?: string;
  owner_user_id?: number;
  updated_at?: number;
  live?: CognitiveLiveState;
  tiers?: CognitiveTier[];
  control_plane?: {
    active_count?: number;
    active_controls?: BehaviorControl[];
    supported_controls?: Array<Record<string, unknown>>;
  };
  feature_flags?: CognitiveFeatureFlag[];
  stats?: {
    trace_count?: number;
    mode_counts?: Record<string, number>;
    strategy_counts?: Record<string, number>;
    delivery_counts?: Record<string, number>;
    active_control_count?: number;
  };
  recent_traces?: BehaviorTrace[];
};

const INSPECTION_LINKS = [
  { href: "/dashboard", label: "Mission Control" },
  { href: "/account/identity", label: "Identity workspace" },
];

const TIER_ICON_BY_ID: Record<string, typeof BrainCircuit> = {
  perception: Gauge,
  state_engine: Layers3,
  strategy: Route,
  delivery: MessageSquareText,
};

function tierAccent(id?: string): string {
  if (id === "perception") return "from-cyan-500/20 to-emerald-500/10 border-cyan-400/15";
  if (id === "state_engine") return "from-fuchsia-500/20 to-amber-500/10 border-fuchsia-400/15";
  if (id === "strategy") return "from-emerald-500/20 to-sky-500/10 border-emerald-400/15";
  if (id === "delivery") return "from-amber-500/20 to-rose-500/10 border-amber-400/15";
  return "from-white/10 to-white/[0.02] border-white/10";
}

function asEntries(counts?: Record<string, number>): Array<[string, number]> {
  return Object.entries(counts || {})
    .filter(([key]) => key)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);
}

function confidencePercent(signal: CognitiveSignal): number | null {
  if (typeof signal.confidence !== "number" || !Number.isFinite(signal.confidence)) return null;
  return Math.max(0, Math.min(100, Math.round(signal.confidence * 100)));
}

function livePulseWidth(live?: CognitiveLiveState): number {
  if (!live || typeof live.age_seconds !== "number") return 12;
  if (live.age_seconds <= 30) return 100;
  if (live.age_seconds <= 180) return 72;
  if (live.age_seconds <= 900) return 38;
  return 14;
}

function JsonPreview({ value }: { value: unknown }) {
  const raw = useMemo(() => {
    try {
      return JSON.stringify(value || {}, null, 2);
    } catch {
      return "{}";
    }
  }, [value]);

  return (
    <pre className="max-h-80 overflow-auto rounded-xl border border-white/5 bg-black/40 p-3 text-[11px] leading-5 text-white/55 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-white/10">
      {shorten(raw, 2500)}
    </pre>
  );
}

function MetricTile({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: typeof BrainCircuit;
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="rounded-2xl border border-white/5 bg-white/[0.025] p-4">
      <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-white/35">
        <Icon className="h-3.5 w-3.5" />
        <span>{label}</span>
      </div>
      <div className="mt-2 truncate text-lg font-light text-white/90">{value}</div>
      {detail ? <div className="mt-1 truncate text-xs text-white/45">{detail}</div> : null}
    </div>
  );
}

function SignalRow({ signal }: { signal: CognitiveSignal }) {
  const confidence = confidencePercent(signal);
  return (
    <div className="rounded-xl border border-white/5 bg-black/25 px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">
            {signal.label || "Signal"}
          </div>
          <div className="mt-1 truncate text-sm text-white/85">{signal.value || "unknown"}</div>
          {signal.detail ? <div className="mt-1 truncate text-xs text-white/40">{signal.detail}</div> : null}
        </div>
        {confidence !== null ? (
          <span className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] font-mono text-white/55">
            {confidence}%
          </span>
        ) : null}
      </div>
      {confidence !== null ? (
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/5">
          <div className="h-full rounded-full bg-white/45" style={{ width: `${confidence}%` }} />
        </div>
      ) : null}
    </div>
  );
}

function TierPanel({ tier }: { tier: CognitiveTier }) {
  const Icon = TIER_ICON_BY_ID[String(tier.id || "")] || BrainCircuit;
  const signals = tier.signals || [];
  return (
    <div className={`rounded-2xl border bg-gradient-to-br p-4 ${tierAccent(tier.id)}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-white/40">
            <Icon className="h-3.5 w-3.5" />
            <span>{tier.label || "Cognitive tier"}</span>
          </div>
          <div className="mt-2 text-sm font-medium leading-5 text-white/90">
            {tier.headline || "No current signal"}
          </div>
          <div className="mt-2 text-xs text-white/45">
            {tier.last_seen_at ? formatRelativeTime(tier.last_seen_at) : "No recent frame"}
          </div>
        </div>
        <StatusPill status={tier.status || "unknown"} />
      </div>
      <div className="mt-4 grid gap-2">
        {signals.length ? (
          signals.slice(0, 7).map((signal, index) => (
            <SignalRow key={`${tier.id || "tier"}:${signal.label || "signal"}:${index}`} signal={signal} />
          ))
        ) : (
          <div className="rounded-xl border border-white/5 bg-black/20 px-3 py-4 text-sm text-white/45">
            No stored signal for this tier yet.
          </div>
        )}
      </div>
    </div>
  );
}

function CountsPanel({
  title,
  counts,
}: {
  title: string;
  counts?: Record<string, number>;
}) {
  const entries = asEntries(counts);
  const total = entries.reduce((sum, [, count]) => sum + count, 0) || 1;
  return (
    <div className="rounded-2xl border border-white/5 bg-black/25 p-4">
      <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">{title}</div>
      <div className="mt-3 space-y-3">
        {entries.length ? (
          entries.map(([label, count]) => (
            <div key={`${title}:${label}`}>
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="truncate text-white/70">{label}</span>
                <span className="font-mono text-white/40">{count}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/5">
                <div
                  className="h-full rounded-full bg-brand-300/55"
                  style={{ width: `${Math.max(8, Math.round((count / total) * 100))}%` }}
                />
              </div>
            </div>
          ))
        ) : (
          <div className="text-sm text-white/45">No samples yet.</div>
        )}
      </div>
    </div>
  );
}

function TraceRow({
  trace,
  selected,
  onSelect,
}: {
  trace: BehaviorTrace;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-2xl border px-4 py-3 text-left transition-colors ${
        selected
          ? "border-brand-500/30 bg-brand-500/10"
          : "border-white/5 bg-white/[0.02] hover:border-white/10 hover:bg-white/[0.04]"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-white/85">
              {trace.interaction_mode || "unknown mode"}
            </span>
            <StatusPill status={trace.strategy_action || "strategy"} />
            <StatusPill status={trace.delivery_shape || "delivery"} />
          </div>
          <div className="mt-2 truncate text-xs text-white/45">
            {trace.channel || "unknown channel"} | message {trace.message_id || "untracked"} | {formatRelativeTime(trace.created_at)}
          </div>
        </div>
        {trace.active_controls?.length ? (
          <span className="rounded-full border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-[10px] font-mono uppercase tracking-widest text-amber-100/75">
            {trace.active_controls.length} control
          </span>
        ) : null}
      </div>
    </button>
  );
}

export function AdminCognitiveArchitectureConsole() {
  const [payload, setPayload] = useState<CognitivePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);
  const [selectedTraceId, setSelectedTraceId] = useState("");

  const load = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) setLoading(true);
    setError(null);
    try {
      const nextPayload = await apiGet<CognitivePayload>("/api/web/runtime/cognitive-architecture?limit=48");
      setPayload(nextPayload);
      setLastLoadedAt(Date.now());
      const latestId = nextPayload.recent_traces?.[0]?.id;
      setSelectedTraceId((current) => {
        if (current && nextPayload.recent_traces?.some((trace) => String(trace.id) === current)) return current;
        return latestId == null ? "" : String(latestId);
      });
    } catch (cause) {
      devLogError("Failed to load cognitive architecture console", cause);
      setPayload(null);
      setError(cause instanceof Error ? cause.message : "Failed to load cognitive architecture telemetry.");
    } finally {
      if (!options?.silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (paused) return;
    const timer = window.setInterval(() => {
      void load({ silent: true });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [load, paused]);

  const live = payload?.live;
  const tiers = payload?.tiers || [];
  const traces = useMemo(() => payload?.recent_traces || [], [payload?.recent_traces]);
  const activeControls = payload?.control_plane?.active_controls || [];
  const featureFlags = payload?.feature_flags || [];
  const selectedTrace = useMemo(
    () => traces.find((trace) => String(trace.id) === selectedTraceId) || traces[0] || null,
    [selectedTraceId, traces],
  );

  return (
    <div className="relative min-h-[860px] overflow-hidden rounded-3xl border border-white/5 bg-black/35 shadow-2xl backdrop-blur-md">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_12%,rgba(34,211,238,0.10),transparent_28%),radial-gradient(circle_at_84%_8%,rgba(244,114,182,0.08),transparent_30%),linear-gradient(135deg,rgba(255,255,255,0.04),transparent_45%,rgba(16,185,129,0.04))]" />
      <div className="relative flex h-full flex-col">
        <div className="flex flex-col gap-5 border-b border-white/5 bg-white/[0.02] px-5 py-5 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-[0.28em] text-white/40">
              <BrainCircuit className="h-3.5 w-3.5" />
              <span>Cognitive Architecture</span>
            </div>
            <h1 className="mt-2 text-xl font-light tracking-tight text-white/90">
              Live social-intelligence console
            </h1>
            <div className="mt-3 flex flex-wrap gap-2">
              <StatusPill status={live?.status || "unobserved"} />
              <StatusPill status={live?.mode || "mode"} />
              <StatusPill status={live?.strategy_action || "strategy"} />
              <StatusPill status={live?.delivery_shape || "delivery"} />
            </div>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="min-w-48 rounded-2xl border border-white/5 bg-black/30 px-4 py-3">
              <div className="flex items-center justify-between gap-3 text-[10px] font-mono uppercase tracking-widest text-white/35">
                <span>Presence pulse</span>
                <span>{lastLoadedAt ? formatRelativeTime(lastLoadedAt) : "loading"}</span>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/5">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-cyan-300 via-emerald-300 to-amber-300 transition-all duration-500"
                  style={{ width: `${livePulseWidth(live)}%` }}
                />
              </div>
            </div>
            <SmallButton onClick={() => setPaused((current) => !current)} tone={paused ? "brand" : "neutral"}>
              <span className="inline-flex items-center gap-2">
                {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
                {paused ? "Resume live" : "Pause live"}
              </span>
            </SmallButton>
            <SmallButton onClick={() => void load()} tone="neutral" disabled={loading}>
              <span className="inline-flex items-center gap-2">
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Refresh
              </span>
            </SmallButton>
          </div>
        </div>

        <div className="min-h-0 flex-1 p-5">
          {error ? (
            <InspectionUnavailableState
              title="Cognitive telemetry unavailable"
              message={error}
              context="The conversation runtime can continue while this inspection lane recovers."
              onRetry={() => void load()}
              links={INSPECTION_LINKS}
            />
          ) : loading && !payload ? (
            <div className="flex min-h-[600px] items-center justify-center text-white/25">
              <Loader2 className="h-8 w-8 animate-spin" />
            </div>
          ) : payload ? (
            <div className="grid gap-6 xl:grid-cols-12">
              <div className="xl:col-span-8">
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  <MetricTile icon={Activity} label="Live status" value={live?.status || "unknown"} detail={live?.last_seen_at ? formatRelativeTime(live.last_seen_at) : "No trace yet"} />
                  <MetricTile icon={Gauge} label="Mode" value={live?.mode || "unknown"} detail={live?.channel || "unknown channel"} />
                  <MetricTile icon={Route} label="Strategy" value={live?.strategy_action || "unknown"} detail={`trace ${live?.latest_trace_id ?? "none"}`} />
                  <MetricTile icon={Waves} label="Delivery" value={live?.delivery_shape || "unknown"} detail={live?.message_id || "untracked message"} />
                </div>

                <div className="mt-6 grid gap-4 lg:grid-cols-2">
                  {tiers.map((tier) => (
                    <TierPanel key={tier.id || tier.label} tier={tier} />
                  ))}
                </div>
              </div>

              <div className="xl:col-span-4">
                <div className="grid gap-6">
                  <AgentPanel
                    title="Control Plane"
                    subtitle="Active steering and feature switches"
                    icon={SlidersHorizontal}
                  >
                    <div className="space-y-4">
                      <div className="grid grid-cols-2 gap-3">
                        <DetailRow label="Traces" value={String(payload.stats?.trace_count ?? 0)} />
                        <DetailRow label="Active controls" value={String(payload.control_plane?.active_count ?? 0)} />
                      </div>

                      <div className="space-y-2">
                        <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">
                          Feature flags
                        </div>
                        {featureFlags.length ? (
                          <div className="grid gap-2">
                            {featureFlags.map((flag) => (
                              <div key={flag.key || flag.label} className="flex items-center justify-between gap-3 rounded-xl border border-white/5 bg-black/25 px-3 py-2">
                                <div className="min-w-0">
                                  <div className="truncate text-sm text-white/80">{flag.label || flag.key}</div>
                                  <div className="text-[10px] uppercase tracking-widest text-white/35">
                                    default {flag.default_enabled ? "on" : "off"}
                                  </div>
                                </div>
                                <StatusPill status={flag.enabled ? "on" : "off"} />
                              </div>
                            ))}
                          </div>
                        ) : (
                          <EmptyState title="No feature flags" message="The settings snapshot did not return feature flag state." />
                        )}
                      </div>

                      <div className="space-y-2">
                        <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">
                          Active controls
                        </div>
                        {activeControls.length ? (
                          activeControls.map((control) => (
                            <div key={`${control.control_key}:${control.scope}`} className="rounded-xl border border-amber-500/10 bg-amber-500/5 px-3 py-2">
                              <div className="flex items-center justify-between gap-3">
                                <div className="min-w-0">
                                  <div className="truncate text-sm text-amber-50/90">{control.control_key}</div>
                                  <div className="mt-1 truncate text-xs text-amber-100/55">
                                    {control.scope || "global"} = {control.value || ""}
                                  </div>
                                </div>
                                <ShieldCheck className="h-4 w-4 text-amber-100/55" />
                              </div>
                            </div>
                          ))
                        ) : (
                          <div className="rounded-xl border border-white/5 bg-black/25 px-3 py-4 text-sm text-white/45">
                            No active behavior controls.
                          </div>
                        )}
                      </div>
                    </div>
                  </AgentPanel>

                  <AgentPanel title="Trace Mix" subtitle="Recent architecture decisions" icon={Cpu}>
                    <div className="grid gap-3">
                      <CountsPanel title="Modes" counts={payload.stats?.mode_counts} />
                      <CountsPanel title="Strategies" counts={payload.stats?.strategy_counts} />
                      <CountsPanel title="Delivery" counts={payload.stats?.delivery_counts} />
                    </div>
                  </AgentPanel>
                </div>
              </div>

              <div className="xl:col-span-5">
                <AgentPanel
                  title="Live Trace Stream"
                  subtitle="Newest cognitive frames first"
                  icon={Clock3}
                  className="min-h-[620px]"
                >
                  <div className="space-y-3">
                    {traces.length ? (
                      traces.map((trace) => (
                        <TraceRow
                          key={trace.id || `${trace.created_at}:${trace.message_id}`}
                          trace={trace}
                          selected={String(trace.id) === selectedTraceId}
                          onSelect={() => setSelectedTraceId(trace.id == null ? "" : String(trace.id))}
                        />
                      ))
                    ) : (
                      <EmptyState title="No traces" message="No cognitive behavior traces have been recorded yet." />
                    )}
                  </div>
                </AgentPanel>
              </div>

              <div className="xl:col-span-7">
                <AgentPanel
                  title="Selected Frame"
                  subtitle={selectedTrace ? `Trace ${selectedTrace.id ?? "latest"}` : "No trace selected"}
                  icon={Sparkles}
                  className="min-h-[620px]"
                >
                  {selectedTrace ? (
                    <div className="space-y-4">
                      <div className="grid gap-3 md:grid-cols-3">
                        <DetailRow label="Mode" value={selectedTrace.interaction_mode || "unknown"} />
                        <DetailRow label="Strategy" value={selectedTrace.strategy_action || "unknown"} />
                        <DetailRow label="Delivery" value={selectedTrace.delivery_shape || "unknown"} />
                      </div>
                      <div className="grid gap-4 lg:grid-cols-2">
                        <div>
                          <div className="mb-2 text-[10px] font-mono uppercase tracking-widest text-white/35">
                            Behavior frame
                          </div>
                          <JsonPreview value={selectedTrace.behavior_frame} />
                        </div>
                        <div>
                          <div className="mb-2 text-[10px] font-mono uppercase tracking-widest text-white/35">
                            Outcome
                          </div>
                          <JsonPreview value={selectedTrace.outcome} />
                          <div className="mt-4 text-[10px] font-mono uppercase tracking-widest text-white/35">
                            Controls on this turn
                          </div>
                          <JsonPreview value={selectedTrace.active_controls || []} />
                        </div>
                      </div>
                    </div>
                  ) : (
                    <EmptyState title="No selected frame" message="The trace stream has not returned a selectable cognitive frame yet." />
                  )}
                </AgentPanel>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

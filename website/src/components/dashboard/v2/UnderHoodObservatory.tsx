"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  BrainCircuit,
  Clock3,
  Cpu,
  Database,
  Eye,
  GitBranch,
  Loader2,
  Network,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Sparkles,
  Waves,
  Zap,
} from "lucide-react";
import { type LucideIcon } from "lucide-react";

import { apiGet } from "@/lib/api";
import { devLogError } from "@/lib/devLog";
import { cn } from "@/lib/utils";
import { formatRelativeTime, shorten } from "@/lib/agentControl";
import {
  EmptyState,
  InspectionUnavailableState,
  SmallButton,
  StatusPill,
} from "@/components/dashboard/v2/agentControlShared";

type ObservatorySource = {
  status?: string;
  error?: string;
};

type ObservatoryHealth = {
  runtime_status?: string;
  mission_sections?: number;
  degraded_sources?: string[];
};

type ObservatorySubsystem = {
  id?: string;
  label?: string;
  kind?: string;
  status?: string;
  metric?: number;
  detail?: string;
};

type ObservatoryRelation = {
  source?: string;
  target?: string;
  label?: string;
  strength?: number;
};

type ObservatoryActivity = {
  kind?: string;
  title?: string;
  detail?: string;
  created_at?: number | string | null;
  status?: string;
  source?: string;
};

type MemoryGalaxyNode = {
  id?: string;
  label?: string;
  summary?: string;
  group?: string;
  source_kind?: string;
  source_id?: string;
  salience?: string;
  weight?: number;
  tags?: string[];
  related_refs?: string[];
  updated_at?: number | string | null;
  count?: number;
};

type MemoryGalaxyLink = {
  source?: string;
  target?: string;
  kind?: string;
  strength?: number;
};

type MemoryGalaxyPayload = {
  nodes?: MemoryGalaxyNode[];
  links?: MemoryGalaxyLink[];
  counts?: {
    nodes?: number;
    links?: number;
    captures?: number;
    indexed_items?: number;
  };
};

type ObservatoryPayload = {
  section?: string;
  owner_user_id?: number;
  updated_at?: number;
  sources?: Record<string, ObservatorySource>;
  health?: ObservatoryHealth;
  subsystems?: ObservatorySubsystem[];
  relations?: ObservatoryRelation[];
  activity?: ObservatoryActivity[];
  memory_galaxy?: MemoryGalaxyPayload;
  cognitive?: {
    live?: {
      status?: string;
      mode?: string;
      strategy_action?: string;
      delivery_shape?: string;
      age_seconds?: number | null;
    };
    stats?: {
      trace_count?: number;
      active_control_count?: number;
      mode_counts?: Record<string, number>;
      strategy_counts?: Record<string, number>;
      delivery_counts?: Record<string, number>;
    };
  };
};

type PositionedNode = {
  node: ObservatorySubsystem;
  x: number;
  y: number;
};

type PositionedGalaxyNode = {
  node: MemoryGalaxyNode;
  x: number;
  y: number;
  radius: number;
  color: string;
};

const INSPECTION_LINKS = [
  { href: "/dashboard", label: "Mission Control" },
  { href: "/account/identity", label: "Identity workspace" },
];

const SUBSYSTEM_ICON_BY_KIND: Record<string, LucideIcon> = {
  agency: GitBranch,
  control: Cpu,
  io: Activity,
  initiative: Zap,
  language: Waves,
  memory: Database,
  presence: Radio,
  reasoning: BrainCircuit,
  situation: Eye,
  surface: Network,
  work: Clock3,
};

function cleanText(value: unknown, fallback = ""): string {
  const text = String(value ?? "").trim().replace(/\s+/g, " ");
  return text || fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function hashSeed(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) % 100000;
  }
  return hash / 100000;
}

function statusClasses(status?: string): string {
  const clean = cleanText(status).toLowerCase();
  if (["active", "ready", "online", "trace"].includes(clean)) {
    return "border-emerald-300/25 bg-emerald-400/10 text-emerald-100";
  }
  if (["error", "failed", "degraded"].includes(clean)) {
    return "border-rose-300/25 bg-rose-400/10 text-rose-100";
  }
  if (["idle", "quiet", "warm"].includes(clean)) {
    return "border-amber-300/20 bg-amber-400/10 text-amber-100";
  }
  return "border-white/10 bg-white/5 text-white/60";
}

function kindColor(kind?: string): string {
  const clean = cleanText(kind).toLowerCase();
  if (clean === "memory") return "#a78bfa";
  if (clean === "reasoning") return "#22d3ee";
  if (clean === "agency") return "#34d399";
  if (clean === "presence") return "#f472b6";
  if (clean === "language") return "#fbbf24";
  if (clean === "io") return "#60a5fa";
  if (clean === "initiative") return "#fb7185";
  if (clean === "situation") return "#2dd4bf";
  if (clean === "surface") return "#c084fc";
  return "#e5e7eb";
}

function galaxyColor(group?: string): string {
  const clean = cleanText(group).toLowerCase();
  if (clean === "memory") return "#c084fc";
  if (clean === "tag") return "#22d3ee";
  if (clean === "continuity_ref") return "#34d399";
  if (clean === "source_kind") return "#fbbf24";
  return "#e5e7eb";
}

function formatCount(value: unknown): string {
  const parsed = asNumber(value, 0);
  if (parsed >= 1000) return `${(parsed / 1000).toFixed(1)}k`;
  return String(Math.round(parsed));
}

function sourceDisplayName(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function MetricShard({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-white/[0.045] p-4 shadow-xl">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_15%_15%,rgba(255,255,255,0.12),transparent_28%)]" />
      <div className="relative">
        <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-white/40">
          <Icon className="h-3.5 w-3.5" />
          <span>{label}</span>
        </div>
        <div className="mt-3 truncate text-2xl font-light text-white/95">{value}</div>
        <div className="mt-1 truncate text-xs text-white/45">{detail}</div>
      </div>
    </div>
  );
}

function SubsystemRail({
  subsystems,
  selectedId,
  onSelect,
}: {
  subsystems: ObservatorySubsystem[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {subsystems.map((subsystem) => {
        const id = cleanText(subsystem.id, cleanText(subsystem.label));
        const Icon = SUBSYSTEM_ICON_BY_KIND[cleanText(subsystem.kind).toLowerCase()] || Sparkles;
        const color = kindColor(subsystem.kind);
        return (
          <button
            key={id}
            type="button"
            onClick={() => onSelect(id)}
            className={cn(
              "group relative overflow-hidden rounded-2xl border p-4 text-left transition-all",
              selectedId === id
                ? "border-white/25 bg-white/[0.085] shadow-2xl"
                : "border-white/10 bg-black/25 hover:border-white/20 hover:bg-white/[0.045]",
            )}
          >
            <div
              className="absolute -right-8 -top-8 h-24 w-24 rounded-full opacity-20 blur-2xl transition-opacity group-hover:opacity-35"
              style={{ backgroundColor: color }}
            />
            <div className="relative flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-white/40">
                  <Icon className="h-3.5 w-3.5" style={{ color }} />
                  <span>{subsystem.kind || "system"}</span>
                </div>
                <div className="mt-2 truncate text-base text-white/90">{subsystem.label || id}</div>
              </div>
              <span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-mono uppercase tracking-widest", statusClasses(subsystem.status))}>
                {subsystem.status || "unknown"}
              </span>
            </div>
            <div className="relative mt-4 flex items-end justify-between gap-3">
              <div className="min-w-0 text-xs leading-relaxed text-white/45">{shorten(subsystem.detail || "", 86)}</div>
              <div className="text-2xl font-light text-white/80">{formatCount(subsystem.metric)}</div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function RuntimeTopology({
  subsystems,
  relations,
  selectedId,
  onSelect,
}: {
  subsystems: ObservatorySubsystem[];
  relations: ObservatoryRelation[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const positioned = useMemo<PositionedNode[]>(() => {
    const centerX = 500;
    const centerY = 280;
    const radiusX = 350;
    const radiusY = 180;
    const count = Math.max(subsystems.length, 1);
    return subsystems.map((node, index) => {
      const angle = (index / count) * Math.PI * 2 - Math.PI / 2;
      return {
        node,
        x: centerX + Math.cos(angle) * radiusX,
        y: centerY + Math.sin(angle) * radiusY,
      };
    });
  }, [subsystems]);

  const byId = useMemo(() => {
    const map = new Map<string, PositionedNode>();
    positioned.forEach((item) => map.set(cleanText(item.node.id, cleanText(item.node.label)), item));
    return map;
  }, [positioned]);

  return (
    <div className="relative min-h-[520px] overflow-hidden rounded-3xl border border-white/10 bg-black/35">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(34,211,238,0.13),transparent_30%),radial-gradient(circle_at_20%_20%,rgba(167,139,250,0.12),transparent_26%),radial-gradient(circle_at_80%_72%,rgba(52,211,153,0.10),transparent_24%)]" />
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1000 560" role="img" aria-label="PaLLi subsystem relation map">
        <defs>
          <radialGradient id="topologyCore" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(255,255,255,0.32)" />
            <stop offset="42%" stopColor="rgba(34,211,238,0.20)" />
            <stop offset="100%" stopColor="rgba(34,211,238,0)" />
          </radialGradient>
          <filter id="softGlow">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <circle cx="500" cy="280" r="82" fill="url(#topologyCore)" />
        <circle cx="500" cy="280" r="164" fill="none" stroke="rgba(255,255,255,0.08)" strokeDasharray="6 12" />
        <circle cx="500" cy="280" r="236" fill="none" stroke="rgba(255,255,255,0.06)" />
        <circle cx="500" cy="280" r="18" fill="rgba(255,255,255,0.45)" filter="url(#softGlow)" />

        {relations.map((relation) => {
          const source = byId.get(cleanText(relation.source));
          const target = byId.get(cleanText(relation.target));
          if (!source || !target) return null;
          const strength = Math.max(0.25, Math.min(asNumber(relation.strength, 0.5), 1));
          const active = selectedId === relation.source || selectedId === relation.target;
          return (
            <g key={`${relation.source}:${relation.target}:${relation.label}`}>
              <line
                x1={source.x}
                y1={source.y}
                x2={target.x}
                y2={target.y}
                stroke={active ? "rgba(255,255,255,0.74)" : "rgba(255,255,255,0.16)"}
                strokeWidth={active ? 2.5 : 0.8 + strength * 1.8}
                strokeLinecap="round"
              />
              {active ? (
                <text
                  x={(source.x + target.x) / 2}
                  y={(source.y + target.y) / 2 - 8}
                  textAnchor="middle"
                  fill="rgba(255,255,255,0.72)"
                  fontSize="12"
                  fontFamily="monospace"
                >
                  {relation.label}
                </text>
              ) : null}
            </g>
          );
        })}

        {positioned.map((item) => {
          const id = cleanText(item.node.id, cleanText(item.node.label));
          const selected = selectedId === id;
          const color = kindColor(item.node.kind);
          return (
            <g
              key={id}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") onSelect(id);
              }}
              className="cursor-pointer"
            >
              <circle
                cx={item.x}
                cy={item.y}
                r={selected ? 35 : 29}
                fill={color}
                opacity={selected ? 0.34 : 0.22}
                filter="url(#softGlow)"
              />
              <circle
                cx={item.x}
                cy={item.y}
                r={selected ? 23 : 18}
                fill="rgba(0,0,0,0.72)"
                stroke={color}
                strokeWidth={selected ? 2.5 : 1.4}
              />
              <text
                x={item.x}
                y={item.y + 48}
                textAnchor="middle"
                fill="rgba(255,255,255,0.82)"
                fontSize="13"
              >
                {shorten(item.node.label || id, 18)}
              </text>
              <text
                x={item.x}
                y={item.y + 64}
                textAnchor="middle"
                fill="rgba(255,255,255,0.38)"
                fontSize="10"
                fontFamily="monospace"
              >
                {item.node.status || "unknown"} / {formatCount(item.node.metric)}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="relative z-10 flex h-full min-h-[520px] flex-col justify-between p-5 pointer-events-none">
        <div className="max-w-sm">
          <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.26em] text-white/45">
            <GitBranch className="h-3.5 w-3.5" />
            <span>Runtime Constellation</span>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-white/55">
            Subsystems are clickable. Relationship labels appear when a connected node is selected.
          </p>
        </div>
      </div>
    </div>
  );
}

function MemoryGalaxyView({
  galaxy,
  selectedId,
  onSelect,
}: {
  galaxy: MemoryGalaxyPayload;
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const nodes = galaxy.nodes || [];
  const links = galaxy.links || [];
  const positioned = useMemo<PositionedGalaxyNode[]>(() => {
    const centerX = 460;
    const centerY = 260;
    return nodes.map((node, index) => {
      const id = cleanText(node.id, `node:${index}`);
      const group = cleanText(node.group);
      const seed = hashSeed(id);
      const angle = seed * Math.PI * 2 + index * 0.34;
      const baseRadius =
        group === "memory" ? 72 + (index % 7) * 22 : group === "tag" ? 178 : group === "source_kind" ? 120 : 220;
      const wobble = (hashSeed(`${id}:wobble`) - 0.5) * 46;
      const weight = Math.max(0.3, Math.min(asNumber(node.weight, 0.5), 1.4));
      const radius = group === "memory" ? 7 + weight * 8 : group === "tag" ? 5 : group === "source_kind" ? 8 : 4;
      return {
        node,
        x: centerX + Math.cos(angle) * (baseRadius + wobble),
        y: centerY + Math.sin(angle) * (baseRadius * 0.72 + wobble * 0.35),
        radius,
        color: galaxyColor(group),
      };
    });
  }, [nodes]);

  const byId = useMemo(() => {
    const map = new Map<string, PositionedGalaxyNode>();
    positioned.forEach((item) => map.set(cleanText(item.node.id), item));
    return map;
  }, [positioned]);

  const selected = byId.get(selectedId)?.node || nodes.find((node) => cleanText(node.id) === selectedId) || nodes[0];

  if (!nodes.length) {
    return (
      <div className="min-h-[520px] rounded-3xl border border-white/10 bg-black/30 p-5">
        <EmptyState
          title="No memory galaxy yet"
          message="The memory index has not returned captures, tags, or continuity references for this owner."
        />
      </div>
    );
  }

  return (
    <div className="grid min-h-[560px] gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-black/35">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_52%_48%,rgba(167,139,250,0.20),transparent_24%),radial-gradient(circle_at_30%_20%,rgba(34,211,238,0.14),transparent_22%),radial-gradient(circle_at_76%_72%,rgba(251,191,36,0.12),transparent_23%)]" />
        <svg className="relative h-full min-h-[560px] w-full" viewBox="0 0 920 520" role="img" aria-label="Memory galaxy">
          <defs>
            <filter id="memoryGlow">
              <feGaussianBlur stdDeviation="3.5" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          {Array.from({ length: 64 }, (_, index) => {
            const seed = hashSeed(`star:${index}`);
            return (
              <circle
                key={`star:${index}`}
                cx={20 + seed * 880}
                cy={18 + hashSeed(`star-y:${index}`) * 484}
                r={0.7 + hashSeed(`star-r:${index}`) * 1.5}
                fill="rgba(255,255,255,0.42)"
              />
            );
          })}
          <ellipse cx="460" cy="260" rx="320" ry="178" fill="none" stroke="rgba(255,255,255,0.07)" />
          <ellipse cx="460" cy="260" rx="218" ry="118" fill="none" stroke="rgba(255,255,255,0.08)" strokeDasharray="8 14" />
          <ellipse cx="460" cy="260" rx="118" ry="62" fill="none" stroke="rgba(255,255,255,0.10)" />

          {links.map((link) => {
            const source = byId.get(cleanText(link.source));
            const target = byId.get(cleanText(link.target));
            if (!source || !target) return null;
            const selectedLine = selectedId === link.source || selectedId === link.target;
            return (
              <line
                key={`${link.source}:${link.target}:${link.kind}`}
                x1={source.x}
                y1={source.y}
                x2={target.x}
                y2={target.y}
                stroke={selectedLine ? "rgba(255,255,255,0.70)" : "rgba(255,255,255,0.13)"}
                strokeWidth={selectedLine ? 1.8 : 0.7 + asNumber(link.strength, 0.4)}
                strokeLinecap="round"
              />
            );
          })}

          {positioned.map((item) => {
            const id = cleanText(item.node.id);
            const selectedNode = selectedId === id;
            return (
              <g
                key={id}
                role="button"
                tabIndex={0}
                className="cursor-pointer"
                onClick={() => onSelect(id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") onSelect(id);
                }}
              >
                <circle
                  cx={item.x}
                  cy={item.y}
                  r={selectedNode ? item.radius + 8 : item.radius + 3}
                  fill={item.color}
                  opacity={selectedNode ? 0.28 : 0.14}
                  filter="url(#memoryGlow)"
                />
                <circle
                  cx={item.x}
                  cy={item.y}
                  r={selectedNode ? item.radius + 1.5 : item.radius}
                  fill={item.color}
                  opacity={selectedNode ? 0.95 : 0.74}
                />
                {item.node.group === "memory" || selectedNode ? (
                  <text
                    x={item.x}
                    y={item.y - item.radius - 10}
                    textAnchor="middle"
                    fill={selectedNode ? "rgba(255,255,255,0.92)" : "rgba(255,255,255,0.48)"}
                    fontSize={selectedNode ? 12 : 10}
                  >
                    {shorten(item.node.label || id, selectedNode ? 24 : 14)}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
        <div className="pointer-events-none absolute left-5 top-5">
          <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.26em] text-white/45">
            <Database className="h-3.5 w-3.5" />
            <span>Memory Galaxy</span>
          </div>
          <p className="mt-2 max-w-sm text-sm leading-relaxed text-white/55">
            Captures, source kinds, tags, and continuity refs are drawn from the live memory index.
          </p>
        </div>
      </div>

      <div className="rounded-3xl border border-white/10 bg-black/35 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">Selected object</div>
            <div className="mt-1 truncate text-base text-white/90">{selected?.label || selected?.id || "Memory node"}</div>
          </div>
          <StatusPill status={selected?.group || "node"} />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3">
          <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-3">
            <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">Nodes</div>
            <div className="mt-2 text-xl font-light text-white/85">{formatCount(galaxy.counts?.nodes)}</div>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-3">
            <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">Links</div>
            <div className="mt-2 text-xl font-light text-white/85">{formatCount(galaxy.counts?.links)}</div>
          </div>
        </div>
        <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.025] p-3">
          <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">Summary</div>
          <p className="mt-2 min-h-20 text-sm leading-relaxed text-white/60">
            {selected?.summary || selected?.source_kind || "No summary returned for this node."}
          </p>
        </div>
        <div className="mt-4 space-y-3">
          <DetailLine label="Salience" value={selected?.salience || "n/a"} />
          <DetailLine label="Source" value={selected?.source_kind || selected?.source_id || "n/a"} />
          <DetailLine label="Updated" value={selected?.updated_at ? formatRelativeTime(selected.updated_at) : "n/a"} />
        </div>
        {selected?.tags?.length ? (
          <div className="mt-4 flex flex-wrap gap-2">
            {selected.tags.map((tag) => (
              <span key={tag} className="rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2 py-1 text-[10px] text-cyan-100/80">
                {tag}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function DetailLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-white/5 pb-2 text-xs">
      <span className="font-mono uppercase tracking-widest text-white/35">{label}</span>
      <span className="min-w-0 truncate text-white/70">{value}</span>
    </div>
  );
}

function ActivityRiver({ activity }: { activity: ObservatoryActivity[] }) {
  if (!activity.length) {
    return <EmptyState title="No activity" message="The observatory feed has not returned live activity yet." />;
  }
  return (
    <div className="space-y-3">
      {activity.slice(0, 18).map((item, index) => {
        const color = kindColor(item.source || item.kind);
        return (
          <div
            key={`${item.kind}:${item.title}:${item.created_at}:${index}`}
            className="group relative overflow-hidden rounded-2xl border border-white/10 bg-white/[0.035] p-3"
          >
            <div
              className="absolute inset-y-0 left-0 w-1 opacity-90"
              style={{ backgroundColor: color }}
            />
            <div className="ml-2 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-sm text-white/88">{item.title || "Activity"}</span>
                  <span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-mono uppercase tracking-widest", statusClasses(item.status))}>
                    {item.status || item.kind || "signal"}
                  </span>
                </div>
                {item.detail ? <div className="mt-1 truncate text-xs text-white/45">{item.detail}</div> : null}
              </div>
              <div className="shrink-0 text-right text-[10px] font-mono uppercase tracking-widest text-white/35">
                {item.created_at ? formatRelativeTime(item.created_at) : "live"}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SourceHealth({ sources }: { sources: Record<string, ObservatorySource> }) {
  const entries = Object.entries(sources);
  if (!entries.length) {
    return <EmptyState title="No source map" message="The observatory did not return source health metadata." />;
  }
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
      {entries.map(([key, source]) => (
        <div key={key} className="rounded-2xl border border-white/10 bg-black/20 px-3 py-2">
          <div className="flex items-center justify-between gap-3">
            <span className="truncate text-xs text-white/70">{sourceDisplayName(key)}</span>
            <span className={cn("rounded-full border px-2 py-0.5 text-[10px] font-mono uppercase tracking-widest", statusClasses(source.status))}>
              {source.status || "unknown"}
            </span>
          </div>
          {source.error ? <div className="mt-1 truncate text-[10px] text-rose-100/65">{source.error}</div> : null}
        </div>
      ))}
    </div>
  );
}

export function UnderHoodObservatory() {
  const [payload, setPayload] = useState<ObservatoryPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [selectedSubsystemId, setSelectedSubsystemId] = useState("cognitive");
  const [selectedMemoryId, setSelectedMemoryId] = useState("");
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);

  const load = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) setLoading(true);
    setError(null);
    try {
      const nextPayload = await apiGet<ObservatoryPayload>("/api/web/runtime/under-hood?limit=40");
      setPayload(nextPayload);
      setLastLoadedAt(Date.now());
      setSelectedSubsystemId((current) => {
        const subsystems = nextPayload.subsystems || [];
        if (current && subsystems.some((item) => item.id === current)) return current;
        return cleanText(subsystems[0]?.id, "cognitive");
      });
      setSelectedMemoryId((current) => {
        const nodes = nextPayload.memory_galaxy?.nodes || [];
        if (current && nodes.some((node) => node.id === current)) return current;
        return cleanText(nodes.find((node) => node.group === "memory")?.id || nodes[0]?.id);
      });
    } catch (cause) {
      devLogError("Failed to load under-hood observatory", cause);
      setPayload(null);
      setError(cause instanceof Error ? cause.message : "Failed to load under-the-hood telemetry.");
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
    }, 6000);
    return () => window.clearInterval(timer);
  }, [load, paused]);

  const subsystems = useMemo(() => payload?.subsystems || [], [payload?.subsystems]);
  const relations = useMemo(() => payload?.relations || [], [payload?.relations]);
  const activity = useMemo(() => payload?.activity || [], [payload?.activity]);
  const selectedSubsystem = subsystems.find((item) => item.id === selectedSubsystemId) || subsystems[0];
  const degradedCount = payload?.health?.degraded_sources?.length || 0;
  const live = payload?.cognitive?.live;

  return (
    <section className="relative overflow-hidden rounded-[2rem] border border-white/10 bg-[#030712]/90 shadow-2xl">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_10%_8%,rgba(34,211,238,0.18),transparent_24%),radial-gradient(circle_at_78%_0%,rgba(244,114,182,0.16),transparent_26%),radial-gradient(circle_at_82%_86%,rgba(52,211,153,0.12),transparent_26%),linear-gradient(135deg,rgba(255,255,255,0.055),transparent_42%,rgba(251,191,36,0.035))]" />
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/40 to-transparent" />
      <div className="relative flex flex-col gap-6 p-5 md:p-6">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-4xl">
            <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-[0.3em] text-cyan-100/45">
              <Sparkles className="h-3.5 w-3.5" />
              <span>Under-the-Hood Observatory</span>
            </div>
            <h2 className="mt-3 text-2xl font-light tracking-tight text-white md:text-4xl">
              Live map of PaLLi&apos;s runtime, memory, agency, and social-intelligence flow
            </h2>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <StatusPill status={payload?.health?.runtime_status || "unknown"} />
              <StatusPill status={live?.mode || "mode"} />
              <StatusPill status={live?.strategy_action || "strategy"} />
              <StatusPill status={live?.delivery_shape || "delivery"} />
              {degradedCount ? <StatusPill status={`${degradedCount} degraded`} /> : <StatusPill status="all sources checked" />}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SmallButton onClick={() => setPaused((current) => !current)} tone={paused ? "brand" : "neutral"}>
              <span className="inline-flex items-center gap-2">
                {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
                {paused ? "Resume live" : "Pause live"}
              </span>
            </SmallButton>
            <SmallButton onClick={() => void load()} disabled={loading}>
              <span className="inline-flex items-center gap-2">
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Refresh
              </span>
            </SmallButton>
          </div>
        </div>

        {error ? (
          <InspectionUnavailableState
            title="Under-the-hood telemetry unavailable"
            message={error}
            context="The runtime can keep responding while this observatory feed recovers."
            onRetry={() => void load()}
            links={INSPECTION_LINKS}
          />
        ) : loading && !payload ? (
          <div className="flex min-h-[520px] items-center justify-center text-white/25">
            <Loader2 className="h-10 w-10 animate-spin" />
          </div>
        ) : payload ? (
          <>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <MetricShard
                icon={Activity}
                label="Live age"
                value={live?.age_seconds == null ? "n/a" : `${Math.round(live.age_seconds)}s`}
                detail={lastLoadedAt ? `refreshed ${formatRelativeTime(lastLoadedAt)}` : "waiting for first refresh"}
              />
              <MetricShard
                icon={BrainCircuit}
                label="Cognitive traces"
                value={formatCount(payload.cognitive?.stats?.trace_count)}
                detail={`${payload.cognitive?.stats?.active_control_count || 0} active steering controls`}
              />
              <MetricShard
                icon={Database}
                label="Memory objects"
                value={formatCount(payload.memory_galaxy?.counts?.nodes)}
                detail={`${formatCount(payload.memory_galaxy?.counts?.links)} visible relations`}
              />
              <MetricShard
                icon={Network}
                label="Mission sections"
                value={formatCount(payload.health?.mission_sections)}
                detail={degradedCount ? `${degradedCount} degraded source${degradedCount === 1 ? "" : "s"}` : "no degraded sources reported"}
              />
            </div>

            <SubsystemRail
              subsystems={subsystems}
              selectedId={selectedSubsystemId}
              onSelect={setSelectedSubsystemId}
            />

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
              <RuntimeTopology
                subsystems={subsystems}
                relations={relations}
                selectedId={selectedSubsystemId}
                onSelect={setSelectedSubsystemId}
              />
              <div className="rounded-3xl border border-white/10 bg-black/35 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">Selected subsystem</div>
                    <div className="mt-2 truncate text-xl font-light text-white/90">
                      {selectedSubsystem?.label || selectedSubsystemId || "Subsystem"}
                    </div>
                    <p className="mt-3 text-sm leading-relaxed text-white/55">
                      {selectedSubsystem?.detail || "Select a node in the constellation to inspect its role."}
                    </p>
                  </div>
                  <StatusPill status={selectedSubsystem?.status || "unknown"} />
                </div>
                <div className="mt-5 grid grid-cols-2 gap-3">
                  <MetricShard
                    icon={Cpu}
                    label="Kind"
                    value={selectedSubsystem?.kind || "n/a"}
                    detail="runtime class"
                  />
                  <MetricShard
                    icon={Sparkles}
                    label="Metric"
                    value={formatCount(selectedSubsystem?.metric)}
                    detail="current magnitude"
                  />
                </div>
                <div className="mt-5">
                  <div className="mb-2 text-[10px] font-mono uppercase tracking-widest text-white/35">Connected relations</div>
                  <div className="space-y-2">
                    {relations
                      .filter((relation) => relation.source === selectedSubsystemId || relation.target === selectedSubsystemId)
                      .map((relation) => (
                        <div key={`${relation.source}:${relation.target}:${relation.label}`} className="rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2">
                          <div className="flex items-center justify-between gap-3 text-xs">
                            <span className="truncate text-white/70">
                              {relation.source} {"->"} {relation.target}
                            </span>
                            <span className="font-mono text-white/35">{Math.round(asNumber(relation.strength, 0) * 100)}%</span>
                          </div>
                          <div className="mt-1 text-xs text-white/45">{relation.label || "relation"}</div>
                        </div>
                      ))}
                  </div>
                </div>
              </div>
            </div>

            <MemoryGalaxyView
              galaxy={payload.memory_galaxy || {}}
              selectedId={selectedMemoryId}
              onSelect={setSelectedMemoryId}
            />

            <div className="grid gap-5 xl:grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)]">
              <div className="rounded-3xl border border-white/10 bg-black/35 p-4">
                <div className="mb-4 flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.24em] text-white/40">
                  <Radio className="h-3.5 w-3.5" />
                  <span>Source Health</span>
                </div>
                <SourceHealth sources={payload.sources || {}} />
              </div>
              <div className="rounded-3xl border border-white/10 bg-black/35 p-4">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.24em] text-white/40">
                    <Waves className="h-3.5 w-3.5" />
                    <span>Signal River</span>
                  </div>
                  <span className="text-[10px] font-mono uppercase tracking-widest text-white/30">{activity.length} events</span>
                </div>
                <ActivityRiver activity={activity} />
              </div>
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}

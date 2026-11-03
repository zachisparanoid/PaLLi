"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Save, ScrollText, Shield, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { apiGet, apiPost } from "@/lib/api";
import { devLogError } from "@/lib/devLog";

type PolicyDescriptor = {
  key: string;
  label: string;
  summary: string;
  description: string;
};

type IdentityDocumentPolicy = {
  scope: string;
  owner: string;
  ownershipClass: string;
  mutability: string;
  visibility: string;
  editableVia: string[];
  scopeInfo?: PolicyDescriptor;
  ownerInfo?: PolicyDescriptor;
  ownershipClassInfo?: PolicyDescriptor;
  mutabilityInfo?: PolicyDescriptor;
  reviewRequirement: string;
  reviewRequirementInfo?: PolicyDescriptor;
  editableViaInfo?: PolicyDescriptor[];
  rawMarkdownEditable: boolean;
  structuredEditable: boolean;
  silentMaintenanceAllowed: boolean;
  dashboardEditable: boolean;
  chatEditable: boolean;
  allowedActors: {
    rawMarkdown?: string[];
    structured?: string[];
    silentMaintenance?: string[];
  };
};

type IdentityDocument = {
  docKey: string;
  label: string;
  path: string;
  policy: IdentityDocumentPolicy;
  frontmatter: Record<string, unknown>;
  content: string;
  markdown: string;
  editable: boolean;
};

type IdentityDocumentReference = {
  docKey: string;
  label: string;
  path?: string;
  version?: number | null;
  editable?: boolean;
  policy?: IdentityDocumentPolicy | null;
};

type IdentityGovernanceResponse = {
  documentOrder: string[];
  documents: Record<
    string,
    {
      docKey: string;
      label: string;
      policy: IdentityDocumentPolicy;
    }
  >;
  scopes: Record<string, PolicyDescriptor>;
  owners: Record<string, PolicyDescriptor>;
  ownershipClasses: Record<string, PolicyDescriptor>;
  mutabilityStates: Record<string, PolicyDescriptor>;
  reviewRequirements: Record<string, PolicyDescriptor>;
  editPaths: Record<string, PolicyDescriptor>;
};

type IdentityBundleResponse = {
  userId: number;
  platform: string;
  ownerUserId?: number | null;
  coreDocuments: Record<string, IdentityDocument>;
  userDocuments: Record<string, IdentityDocument>;
  governance: IdentityGovernanceResponse;
  history: IdentityHistoryEntry[];
};

type IdentityHistoryEntry = {
  doc_key: string;
  version: number;
  actor_type: string;
  actor_id?: string;
  reason?: string;
  created_at: number;
  document?: IdentityDocumentReference | null;
};

type IdentityHistoryResponse = {
  history: IdentityHistoryEntry[];
};

type IdentityHistoryDetail = IdentityHistoryEntry & {
  file_path?: string;
  frontmatter: Record<string, unknown>;
  content: string;
  markdown: string;
  comparison?: IdentityHistoryComparison;
};

type IdentityHistoryScope = "selected" | "all";

type IdentityHistoryComparisonLine = {
  kind: "context" | "added" | "removed";
  history_line_number?: number | null;
  current_line_number?: number | null;
  text: string;
};

type IdentityHistoryComparisonHunk = {
  history_start_line: number;
  current_start_line: number;
  lines: IdentityHistoryComparisonLine[];
};

type IdentityHistoryComparison = {
  target: string;
  current_label: string;
  current_path: string;
  current_version?: number | null;
  currentDocument?: IdentityDocumentReference | null;
  summary: {
    history_line_count: number;
    current_line_count: number;
    added_lines: number;
    removed_lines: number;
    unchanged_lines: number;
    changed_blocks: number;
    has_changes: boolean;
  };
  hunks: IdentityHistoryComparisonHunk[];
};

type CompiledWarning = {
  code: string;
  message: string;
  docKey?: string | null;
  field?: string | null;
};

type CompiledSnapshotDocument = IdentityDocumentReference & {
  path: string;
  version: number;
  platform: string;
  userId: number;
  ownerUserId?: number | null;
  policy: IdentityDocumentPolicy;
  frontmatter: Record<string, unknown>;
  content: string;
  excerpt: string;
};

type CompiledActionPolicy = {
  _materialized?: boolean;
  autonomy_mode?: string;
  initiative_level?: string;
  post_action_reporting_mode?: string;
  interruption_threshold?: string;
  standing_orders_policy?: string;
  followthrough_intensity?: string;
  environment_optimization_mode?: string;
  background_watch_mode?: string;
  external_contact_policy?: string;
  social_context_sensitivity?: string;
  quiet_hours_mode?: string;
  group_voice_behavior?: string;
  presence_awareness_mode?: string;
  arrival_useful_context_policy?: string;
  ambient_discord_delivery_policy?: string;
  ambient_voice_output_policy?: string;
  operator_initiative_mode?: string;
  operator_autonomy_profile?: string;
  operator_active_workspace_mode?: string;
  allow_bounded_reflection?: boolean;
  allow_proactive_followups?: boolean;
  allow_silent_beneficial_actions?: boolean;
  allow_silent_low_risk_actions?: boolean;
  allow_privileged_cli_actions?: boolean;
  allow_package_install_actions?: boolean;
  allow_schedule_creation?: boolean;
  allow_email_triage_actions?: boolean;
  allow_research_and_alert_loops?: boolean;
  require_confirmation_for_destructive_actions?: boolean;
  require_confirmation_for_external_spending?: boolean;
  effectivePermissions?: Record<string, boolean>;
  configGates?: Record<string, boolean>;
  warnings?: CompiledWarning[];
};

type CompiledIdentityResponse = {
  userId: number;
  platform: string;
  ownerUserId?: number | null;
  snapshots: {
    text: {
      promptContext: string;
      documents: Record<string, CompiledSnapshotDocument>;
      traits: Record<string, Record<string, unknown>>;
      warnings: CompiledWarning[];
    };
    voice: {
      promptContext: string;
      documents: Record<string, CompiledSnapshotDocument>;
      preferences: Record<string, unknown>;
      actionCoupling: Record<string, unknown>;
      materialized: boolean;
      warnings: CompiledWarning[];
    };
    action: {
      promptContext: string;
      documents: Record<string, CompiledSnapshotDocument>;
      preferences: Record<string, unknown>;
      relationship: Record<string, unknown>;
      memory: Record<string, unknown>;
      routines: Record<string, unknown>;
      materialized: boolean;
      warnings: CompiledWarning[];
    };
  };
  policy: Partial<CompiledActionPolicy>;
  provenance: Record<
    string,
    IdentityDocumentReference & {
      path: string;
      version: number;
      platform: string;
      userId: number;
      ownerUserId?: number | null;
      policy: IdentityDocumentPolicy;
    }
  >;
  warnings: CompiledWarning[];
};

type SimpleAutonomyPreset = "cautious" | "balanced" | "trusted";

const PERSONALITY_FIELDS = [
  ["tone", "Tone"],
  ["warmth", "Warmth"],
  ["humor", "Humor"],
  ["response_length", "Response Length"],
] as const;

const COMPILED_POLICY_POSTURE_FIELDS: Array<{ key: keyof CompiledActionPolicy; label: string }> = [
  { key: "autonomy_mode", label: "Autonomy Mode" },
  { key: "initiative_level", label: "Initiative Level" },
  { key: "environment_optimization_mode", label: "Environment Optimization" },
  { key: "background_watch_mode", label: "Background Watch" },
  { key: "external_contact_policy", label: "External Contact" },
  { key: "quiet_hours_mode", label: "Quiet Hours" },
  { key: "operator_initiative_mode", label: "Operator Initiative" },
  { key: "operator_autonomy_profile", label: "Operator Autonomy" },
];

const COMPILED_POLICY_REQUESTED_FLAG_FIELDS: Array<{ key: keyof CompiledActionPolicy; label: string }> = [
  { key: "_materialized", label: "Materialized" },
  { key: "allow_bounded_reflection", label: "Bounded Reflection" },
  { key: "allow_proactive_followups", label: "Proactive Follow-Ups" },
  { key: "allow_silent_beneficial_actions", label: "Silent Beneficial Actions" },
  { key: "allow_silent_low_risk_actions", label: "Silent Low-Risk Actions" },
  { key: "allow_privileged_cli_actions", label: "Privileged CLI Actions" },
  { key: "allow_package_install_actions", label: "Package Install Actions" },
  { key: "allow_schedule_creation", label: "Schedule Creation" },
  { key: "allow_email_triage_actions", label: "Email Triage Actions" },
  { key: "allow_research_and_alert_loops", label: "Research And Alert Loops" },
  { key: "require_confirmation_for_destructive_actions", label: "Confirm Destructive Actions" },
  { key: "require_confirmation_for_external_spending", label: "Confirm External Spending" },
];

const COMPILED_EFFECTIVE_PERMISSION_FIELDS = [
  { key: "bounded_reflection", label: "Bounded Reflection" },
  { key: "proactive_outreach", label: "Proactive Outreach" },
  { key: "background_watch", label: "Background Watch" },
  { key: "schedule_creation", label: "Schedule Creation" },
  { key: "silent_beneficial_actions", label: "Silent Beneficial Actions" },
  { key: "silent_low_risk_actions", label: "Silent Low-Risk Actions" },
  { key: "privileged_cli_autonomy", label: "Privileged CLI Autonomy" },
  { key: "package_install_autonomy", label: "Package Install Autonomy" },
  { key: "research_loops", label: "Research Loops" },
  { key: "email_triage", label: "Email Triage" },
  { key: "external_contact_without_confirmation", label: "External Contact Without Confirmation" },
] as const;

const COMPILED_CONFIG_GATE_FIELDS = [
  { key: "autonomous_cli", label: "Autonomous CLI Enabled" },
  { key: "privileged_cli", label: "Privileged CLI Enabled" },
  { key: "package_installs", label: "Package Installs Enabled" },
  { key: "package_install_confirm_required", label: "Package Install Confirmation Required" },
] as const;

const PERSONALITY_FIELD_OPTIONS: Record<
  (typeof PERSONALITY_FIELDS)[number][0],
  Array<{ value: string; label: string }>
> = {
  tone: [
    { value: "warm_companion", label: "Warm Companion" },
    { value: "grounded_direct", label: "Grounded Direct" },
    { value: "calm_supportive", label: "Calm Supportive" },
    { value: "playful_companion", label: "Playful Companion" },
  ],
  warmth: [
    { value: "low", label: "Low" },
    { value: "medium", label: "Medium" },
    { value: "high", label: "High" },
  ],
  humor: [
    { value: "none", label: "None" },
    { value: "light_and_human", label: "Light and Human" },
    { value: "dry", label: "Dry" },
    { value: "playful", label: "Playful" },
  ],
  response_length: [
    { value: "short", label: "Short" },
    { value: "adaptive", label: "Adaptive" },
    { value: "detailed", label: "Detailed" },
  ],
};

function formatTimestamp(epochSeconds: number) {
  if (!epochSeconds) return "unknown";
  return new Date(epochSeconds * 1000).toLocaleString();
}

function historyEntryKey(entry: Pick<IdentityHistoryEntry, "doc_key" | "version"> | null | undefined): string | null {
  if (!entry) return null;
  return `${entry.doc_key}:${entry.version}`;
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    const message = (error.message || "").trim();
    if (message) return message;
  }
  return fallback;
}

function formatValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null || value === undefined || value === "") return "unset";
  if (Array.isArray(value)) return value.map((item) => formatValue(item)).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function summarizeRecord(record: Record<string, unknown> | undefined, limit = 6): Array<[string, string]> {
  if (!record) return [];
  return Object.entries(record)
    .filter(([, value]) => value !== undefined)
    .slice(0, limit)
    .map(([key, value]) => [key, formatValue(value)]);
}

function summarizeNestedRecord(
  record: Record<string, Record<string, unknown>> | undefined,
  limit = 8,
): Array<[string, string]> {
  if (!record) return [];
  const rows: Array<[string, string]> = [];
  for (const [section, values] of Object.entries(record)) {
    for (const [key, value] of Object.entries(values || {})) {
      rows.push([`${section}.${key}`, formatValue(value)]);
      if (rows.length >= limit) return rows;
    }
  }
  return rows;
}

function orderCompiledSnapshotDocuments(
  documents: Record<string, CompiledSnapshotDocument> | undefined,
  governance: IdentityGovernanceResponse | null | undefined,
): CompiledSnapshotDocument[] {
  if (!documents) return [];
  const ordered: CompiledSnapshotDocument[] = [];
  const seen = new Set<string>();
  for (const docKey of governance?.documentOrder || []) {
    const doc = documents[docKey];
    if (!doc) continue;
    ordered.push(doc);
    seen.add(docKey);
  }
  for (const [docKey, doc] of Object.entries(documents)) {
    if (seen.has(docKey)) continue;
    ordered.push(doc);
  }
  return ordered;
}

function summarizeExcerpt(excerpt: string | undefined, limit = 180): string | null {
  const compact = String(excerpt || "").replace(/\s+/g, " ").trim();
  if (!compact) return null;
  if (compact.length <= limit) return compact;
  return `${compact.slice(0, limit - 3).trimEnd()}...`;
}

function joinDescriptorLabels(values: PolicyDescriptor[] | undefined): string {
  if (!values?.length) return "None";
  return values.map((value) => value.label).join(", ");
}

function summarizePolicyLine(policy: IdentityDocumentPolicy | null | undefined): string | null {
  if (!policy) return null;
  const parts = [
    policy.ownershipClassInfo?.label || formatValue(policy.ownershipClass),
    policy.mutabilityInfo?.label || formatValue(policy.mutability),
  ].filter((value) => Boolean(value) && value !== "unset");
  return parts.length ? parts.join(" · ") : null;
}

function reviewPolicyLabel(policy: IdentityDocumentPolicy | null | undefined): string | null {
  if (!policy) return null;
  const value = policy.reviewRequirementInfo?.label || formatValue(policy.reviewRequirement);
  return value && value !== "unset" ? value : null;
}

function formatPolicyKeyLabel(key: string): string {
  return key
    .replace(/^_/, "")
    .split("_")
    .filter((part) => part.length > 0)
    .map((part) => {
      const upper = part.toUpperCase();
      if (upper === "CLI" || upper === "DM" || upper === "TTS" || upper === "STT") {
        return upper;
      }
      return `${part.charAt(0).toUpperCase()}${part.slice(1)}`;
    })
    .join(" ");
}

function summarizeOrderedPolicyFields(
  policy: Partial<CompiledActionPolicy> | null | undefined,
  fields: Array<{ key: keyof CompiledActionPolicy; label: string }>,
  includeRemainder?: (key: string) => boolean,
): Array<[string, string]> {
  if (!policy) return [];
  const rows: Array<[string, string]> = [];
  const seen = new Set<string>();

  for (const field of fields) {
    const key = String(field.key);
    const value = policy[field.key];
    if (value === undefined) continue;
    rows.push([field.label, formatValue(value)]);
    seen.add(key);
  }

  if (includeRemainder) {
    for (const [key, value] of Object.entries(policy)) {
      if (seen.has(key) || value === undefined || !includeRemainder(key)) continue;
      rows.push([formatPolicyKeyLabel(key), formatValue(value)]);
    }
  }

  return rows.filter((entry): entry is [string, string] => Boolean(entry[1]) && entry[1] !== "unset");
}

function summarizeOrderedBooleanRecord(
  record: Record<string, boolean> | undefined,
  fields: ReadonlyArray<{ key: string; label: string }>,
): Array<[string, string]> {
  if (!record) return [];
  const rows: Array<[string, string]> = [];
  const seen = new Set<string>();

  for (const field of fields) {
    if (!(field.key in record)) continue;
    rows.push([field.label, formatValue(record[field.key])]);
    seen.add(field.key);
  }

  for (const [key, value] of Object.entries(record)) {
    if (seen.has(key) || value === undefined) continue;
    rows.push([formatPolicyKeyLabel(key), formatValue(value)]);
  }

  return rows.filter((entry): entry is [string, string] => Boolean(entry[1]) && entry[1] !== "unset");
}

function diffMarker(kind: IdentityHistoryComparisonLine["kind"]): string {
  if (kind === "added") return "+";
  if (kind === "removed") return "-";
  return " ";
}

function diffLineClassName(kind: IdentityHistoryComparisonLine["kind"]): string {
  if (kind === "added") {
    return "border-emerald-400/20 bg-emerald-500/10 text-emerald-50";
  }
  if (kind === "removed") {
    return "border-rose-400/20 bg-rose-500/10 text-rose-50";
  }
  return "border-white/5 bg-black/10 text-white/65";
}

function selectOptionsForField(
  key: (typeof PERSONALITY_FIELDS)[number][0],
  currentValue: unknown,
): Array<{ value: string; label: string }> {
  const baseOptions = PERSONALITY_FIELD_OPTIONS[key] || [];
  const normalized = String(currentValue ?? "").trim();
  if (!normalized || baseOptions.some((option) => option.value === normalized)) {
    return baseOptions;
  }
  return [{ value: normalized, label: `Current (${normalized})` }, ...baseOptions];
}

function deriveAutonomyPreset(actionValues: Record<string, unknown>): SimpleAutonomyPreset {
  const autonomyMode = String(actionValues.autonomy_mode || "");
  const allowPrivileged = Boolean(actionValues.allow_privileged_cli_actions);
  const allowLowRisk = Boolean(actionValues.allow_silent_low_risk_actions);

  if (autonomyMode === "ask_first" && !allowPrivileged && !allowLowRisk) {
    return "cautious";
  }
  if (autonomyMode === "trusted_autonomous" || allowPrivileged || allowLowRisk) {
    return "trusted";
  }
  return "balanced";
}

function applyAutonomyPreset(
  preset: SimpleAutonomyPreset,
  current: Record<string, unknown>,
): Record<string, unknown> {
  if (preset === "cautious") {
    return {
      ...current,
      autonomy_mode: "ask_first",
      allow_silent_beneficial_actions: false,
      allow_silent_low_risk_actions: false,
      allow_privileged_cli_actions: false,
      allow_package_install_actions: false,
      environment_optimization_mode: "suggest_only",
      external_contact_policy: "confirm_first",
      post_action_reporting_mode: "immediate",
    };
  }

  if (preset === "trusted") {
    return {
      ...current,
      autonomy_mode: "trusted_autonomous",
      allow_silent_beneficial_actions: true,
      allow_silent_low_risk_actions: true,
      allow_privileged_cli_actions: true,
      allow_package_install_actions: true,
      environment_optimization_mode: "safe_autonomous",
      external_contact_policy: "confirm_first",
      post_action_reporting_mode: "minimal",
    };
  }

  return {
    ...current,
    autonomy_mode: "balanced",
    allow_silent_beneficial_actions: true,
    allow_silent_low_risk_actions: false,
    allow_privileged_cli_actions: false,
    allow_package_install_actions: false,
    environment_optimization_mode: "suggest_only",
    external_contact_policy: "confirm_first",
    post_action_reporting_mode: "digest",
  };
}

type SummaryCardProps = {
  title: string;
  eyebrow: string;
  rows: Array<[string, string]>;
  warnings?: CompiledWarning[];
  promptContext?: string;
  sources?: CompiledSnapshotDocument[];
};

function SummaryCard({ title, eyebrow, rows, warnings, promptContext, sources }: SummaryCardProps) {
  return (
    <div className="rounded-3xl border border-white/10 bg-black/20 p-5">
      <div className="text-[11px] font-bold uppercase tracking-[0.24em] text-white/35">{eyebrow}</div>
      <h3 className="mt-2 text-lg font-medium text-white">{title}</h3>
      <div className="mt-4 space-y-2">
        {rows.length ? (
          rows.map(([key, value]) => (
            <div
              key={key}
              className="flex items-start justify-between gap-4 rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-white/75"
            >
              <span className="font-medium text-white/55">{key}</span>
              <span className="max-w-[60%] text-right text-white/85">{value}</span>
            </div>
          ))
        ) : (
          <div className="rounded-2xl border border-dashed border-white/10 px-3 py-4 text-xs text-white/45">
            No compiled values available yet.
          </div>
        )}
      </div>
      {warnings && warnings.length > 0 ? (
        <div className="mt-4 rounded-2xl border border-amber-400/20 bg-amber-400/10 px-3 py-3 text-xs text-amber-100/80">
          {warnings[0].message}
        </div>
      ) : null}
      {sources && sources.length > 0 ? (
        <div className="mt-4 rounded-2xl border border-white/10 bg-black/30 p-3">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">Snapshot Sources</div>
          <div className="space-y-2">
            {sources.map((source) => {
              const policySummary = summarizePolicyLine(source.policy);
              const reviewLabel = reviewPolicyLabel(source.policy);
              const excerpt = summarizeExcerpt(source.excerpt);
              return (
                <div
                  key={`${source.docKey}:${source.version}`}
                  className="rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-3 text-xs text-white/70"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-white/90">{source.label || source.docKey}</div>
                      {policySummary ? <div className="mt-1 text-white/45">{policySummary}</div> : null}
                      {reviewLabel ? <div className="mt-1 text-white/40">{reviewLabel}</div> : null}
                    </div>
                    <div className="text-right text-white/45">
                      <div>v{source.version}</div>
                      <div>{source.platform}</div>
                    </div>
                  </div>
                  {excerpt ? <div className="mt-2 text-[11px] leading-5 text-white/55">{excerpt}</div> : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
      {promptContext ? (
        <div className="mt-4 rounded-2xl border border-white/10 bg-black/30 p-3">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">Prompt Preview</div>
          <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap font-mono text-[11px] leading-5 text-white/70">
            {promptContext.slice(0, 1000)}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

type DetailSectionProps = {
  title: string;
  description: string;
  rows: Array<[string, string]>;
  emptyMessage?: string;
};

function DetailSection({ title, description, rows, emptyMessage }: DetailSectionProps) {
  return (
    <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
      <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">{title}</div>
      <div className="mt-2 text-xs leading-5 text-white/50">{description}</div>
      <div className="mt-4 space-y-2">
        {rows.length ? (
          rows.map(([key, value]) => (
            <div
              key={key}
              className="flex items-start justify-between gap-4 rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-white/75"
            >
              <span className="font-medium text-white/55">{key}</span>
              <span className="max-w-[58%] text-right text-white/85">{value}</span>
            </div>
          ))
        ) : (
          <div className="rounded-2xl border border-dashed border-white/10 px-3 py-4 text-xs text-white/45">
            {emptyMessage || "No compiled values available yet."}
          </div>
        )}
      </div>
    </div>
  );
}

function RuntimePermissionsCard({ policy }: { policy: Partial<CompiledActionPolicy> | null | undefined }) {
  const postureRows = summarizeOrderedPolicyFields(policy, COMPILED_POLICY_POSTURE_FIELDS, (key) => {
    return (
      key !== "effectivePermissions" &&
      key !== "configGates" &&
      key !== "warnings" &&
      key !== "_materialized" &&
      !key.startsWith("allow_") &&
      !key.startsWith("require_")
    );
  });
  const requestedFlagRows = summarizeOrderedPolicyFields(policy, COMPILED_POLICY_REQUESTED_FLAG_FIELDS, (key) => {
    return key === "_materialized" || key.startsWith("allow_") || key.startsWith("require_");
  });
  const effectivePermissionRows = summarizeOrderedBooleanRecord(
    policy?.effectivePermissions,
    COMPILED_EFFECTIVE_PERMISSION_FIELDS,
  );
  const configGateRows = summarizeOrderedBooleanRecord(policy?.configGates, COMPILED_CONFIG_GATE_FIELDS);

  return (
    <div className="rounded-3xl border border-white/10 bg-black/20 p-5 xl:col-span-2 2xl:col-span-2">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="text-[11px] font-bold uppercase tracking-[0.24em] text-white/35">Compiled Policy</div>
          <h3 className="mt-2 text-lg font-medium text-white">Runtime Permissions</h3>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-white/60">
            Requested identity policy, effective runtime permissions, and hard config gates are shown separately so
            the owner can see where local runtime config narrows autonomy.
          </p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3 text-right text-xs text-white/65">
          <div>{policy?.warnings?.length || 0} policy warning(s)</div>
          <div className="mt-1">{effectivePermissionRows.length} effective permission row(s)</div>
          <div className="mt-1">{configGateRows.length} runtime gate row(s)</div>
        </div>
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-2">
        <DetailSection
          title="Requested Posture"
          description="Compiled identity posture before runtime-level gating is applied."
          rows={postureRows}
          emptyMessage="No requested posture values were compiled."
        />
        <DetailSection
          title="Requested Flags"
          description="Owner-facing identity toggles that request what PaLLi should be allowed to do."
          rows={requestedFlagRows}
          emptyMessage="No requested action flags were compiled."
        />
        <DetailSection
          title="Effective Permissions"
          description="What the runtime will actually allow after compiled identity policy is resolved."
          rows={effectivePermissionRows}
          emptyMessage="No effective runtime permissions were compiled."
        />
        <DetailSection
          title="Runtime Config Gates"
          description="Hard runtime switches from local config that can narrow autonomy even when identity asks for more."
          rows={configGateRows}
          emptyMessage="No runtime config gates were reported by the backend."
        />
      </div>

      {policy?.warnings && policy.warnings.length > 0 ? (
        <div className="mt-4 rounded-2xl border border-amber-400/20 bg-amber-400/10 px-4 py-3 text-xs text-amber-100/85">
          <div className="font-medium text-amber-100">Policy Warnings</div>
          <div className="mt-2 space-y-2">
            {policy.warnings.map((warning) => (
              <div key={`${warning.code}-${warning.field || "none"}-${warning.docKey || "none"}`}>
                {warning.message}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function IdentityWorkbench() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [bundle, setBundle] = useState<IdentityBundleResponse | null>(null);
  const [compiled, setCompiled] = useState<CompiledIdentityResponse | null>(null);
  const [compiledLoadError, setCompiledLoadError] = useState<string | null>(null);
  const [selectedDocKey, setSelectedDocKey] = useState("personality_profile");
  const [rawMarkdown, setRawMarkdown] = useState("");
  const [personalityValues, setPersonalityValues] = useState<Record<string, unknown>>({});
  const [actionValues, setActionValues] = useState<Record<string, unknown>>({});
  const [historyScope, setHistoryScope] = useState<IdentityHistoryScope>("selected");
  const [historyEntries, setHistoryEntries] = useState<IdentityHistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyLoadError, setHistoryLoadError] = useState<string | null>(null);
  const [selectedHistoryKey, setSelectedHistoryKey] = useState<string | null>(null);
  const [selectedHistoryDetail, setSelectedHistoryDetail] = useState<IdentityHistoryDetail | null>(null);
  const [historyDetailLoading, setHistoryDetailLoading] = useState(false);
  const [historyDetailError, setHistoryDetailError] = useState<string | null>(null);
  const selectedDocKeyRef = useRef(selectedDocKey);
  const historyRequestIdRef = useRef(0);
  const historyDetailRequestIdRef = useRef(0);

  useEffect(() => {
    selectedDocKeyRef.current = selectedDocKey;
  }, [selectedDocKey]);

  const loadHistory = useCallback(async (docKey?: string | null) => {
    const requestId = historyRequestIdRef.current + 1;
    historyRequestIdRef.current = requestId;
    historyDetailRequestIdRef.current += 1;
    setHistoryLoading(true);
    setHistoryLoadError(null);
    setHistoryEntries([]);
    setSelectedHistoryKey(null);
    setSelectedHistoryDetail(null);
    setHistoryDetailError(null);
    setHistoryDetailLoading(false);

    const params = new URLSearchParams({ limit: "40" });
    if (docKey) {
      params.set("doc_key", docKey);
    }

    try {
      const data = await apiGet<IdentityHistoryResponse>(`/api/web/account/identity/history?${params.toString()}`);
      if (historyRequestIdRef.current !== requestId) return;
      setHistoryEntries(data.history || []);
    } catch (error) {
      if (historyRequestIdRef.current !== requestId) return;
      devLogError("Failed to load identity history", error);
      setHistoryLoadError(errorMessage(error, "Failed to load identity history."));
    } finally {
      if (historyRequestIdRef.current === requestId) {
        setHistoryLoading(false);
      }
    }
  }, []);

  const loadHistoryDetail = useCallback(async (entry: IdentityHistoryEntry) => {
    const requestId = historyDetailRequestIdRef.current + 1;
    historyDetailRequestIdRef.current = requestId;
    setHistoryDetailLoading(true);
    setHistoryDetailError(null);
    setSelectedHistoryDetail(null);

    try {
      const docKey = encodeURIComponent(entry.doc_key);
      const data = await apiGet<IdentityHistoryDetail>(`/api/web/account/identity/history/${docKey}/${entry.version}`);
      if (historyDetailRequestIdRef.current !== requestId) return;
      setSelectedHistoryDetail(data);
    } catch (error) {
      if (historyDetailRequestIdRef.current !== requestId) return;
      devLogError("Failed to load identity history detail", error);
      setHistoryDetailError(errorMessage(error, "Failed to load the selected history version."));
    } finally {
      if (historyDetailRequestIdRef.current === requestId) {
        setHistoryDetailLoading(false);
      }
    }
  }, []);

  const loadBundle = useCallback(async (opts?: { preserveDoc?: boolean }) => {
    const preserveDoc = Boolean(opts?.preserveDoc);
    try {
      const data = await apiGet<IdentityBundleResponse>("/api/web/account/identity");
      setBundle(data);

      const personality = data.userDocuments?.personality_profile?.frontmatter || {};
      const actionPrefs = data.userDocuments?.action_preferences?.frontmatter || {};
      setPersonalityValues(personality);
      setActionValues(actionPrefs);

      const allDocs = {
        ...(data.coreDocuments || {}),
        ...(data.userDocuments || {}),
      };
      const currentSelectedDocKey = selectedDocKeyRef.current;
      const nextKey =
        preserveDoc && allDocs[currentSelectedDocKey]
          ? currentSelectedDocKey
          : data.userDocuments?.personality_profile
            ? "personality_profile"
            : Object.keys(allDocs)[0];
      setSelectedDocKey(nextKey);
      setRawMarkdown(allDocs[nextKey]?.markdown || "");

      try {
        const compiledData = await apiGet<CompiledIdentityResponse>("/api/web/account/identity/compiled");
        setCompiled(compiledData);
        setCompiledLoadError(null);
      } catch (error) {
        devLogError("Failed to load compiled identity bundle", error);
        setCompiled(null);
        setCompiledLoadError(errorMessage(error, "Compiled identity inspect is temporarily unavailable."));
      }
    } catch (error) {
      devLogError("Failed to load identity bundle", error);
      toast({
        variant: "error",
        title: "Identity",
        message: errorMessage(error, "Failed to load identity workspace."),
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void loadBundle();
  }, [loadBundle]);

  useEffect(() => {
    if (!bundle) return;
    const allDocs = {
      ...(bundle.coreDocuments || {}),
      ...(bundle.userDocuments || {}),
    };
    setRawMarkdown(allDocs[selectedDocKey]?.markdown || "");
  }, [selectedDocKey, bundle]);

  useEffect(() => {
    if (!bundle) return;
    if (historyScope === "selected" && !selectedDocKey) {
      setHistoryEntries([]);
      setHistoryLoading(false);
      setHistoryLoadError(null);
      setSelectedHistoryKey(null);
      setSelectedHistoryDetail(null);
      setHistoryDetailError(null);
      setHistoryDetailLoading(false);
      return;
    }
    void loadHistory(historyScope === "selected" ? selectedDocKey : null);
  }, [bundle, selectedDocKey, historyScope, loadHistory]);

  useEffect(() => {
    if (!historyEntries.length) {
      setSelectedHistoryKey(null);
      setSelectedHistoryDetail(null);
      setHistoryDetailError(null);
      setHistoryDetailLoading(false);
      return;
    }

    setSelectedHistoryKey((current) => {
      if (current && historyEntries.some((entry) => historyEntryKey(entry) === current)) {
        return current;
      }
      return historyEntryKey(historyEntries[0]);
    });
  }, [historyEntries]);

  useEffect(() => {
    if (!selectedHistoryKey) {
      setSelectedHistoryDetail(null);
      setHistoryDetailError(null);
      setHistoryDetailLoading(false);
      return;
    }
    const entry = historyEntries.find((item) => historyEntryKey(item) === selectedHistoryKey);
    if (!entry) {
      setSelectedHistoryDetail(null);
      setHistoryDetailError(null);
      setHistoryDetailLoading(false);
      return;
    }
    void loadHistoryDetail(entry);
  }, [historyEntries, selectedHistoryKey, loadHistoryDetail]);

  async function saveWorkspaceShaping() {
    setSaving(true);
    try {
      await apiPost("/api/web/account/identity/personality-profile", personalityValues);
      await apiPost("/api/web/account/identity/action-preferences", actionValues);
      await loadBundle({ preserveDoc: true });
      toast({
        variant: "success",
        title: "Identity workspace",
        message: "Owner shaping and boundary preferences were updated.",
      });
    } catch (error) {
      devLogError(error);
      toast({
        variant: "error",
        title: "Identity workspace",
        message: errorMessage(error, "Failed to save identity workspace changes."),
      });
    } finally {
      setSaving(false);
    }
  }

  async function saveRawDocument() {
    if (!selectedDocKey) return;
    setSaving(true);
    try {
      await apiPost(`/api/web/account/identity/document/${selectedDocKey}`, {
        markdown: rawMarkdown,
        reason: "dashboard_raw_edit",
      });
      await loadBundle({ preserveDoc: true });
      toast({ variant: "success", title: "Identity", message: "Document saved." });
    } catch (error) {
      devLogError(error);
      toast({
        variant: "error",
        title: "Identity",
        message: errorMessage(error, "Failed to save document."),
      });
    } finally {
      setSaving(false);
    }
  }

  const allDocuments = bundle
    ? { ...(bundle.coreDocuments || {}), ...(bundle.userDocuments || {}) }
    : {};
  const governance = bundle?.governance;
  const orderedDocuments = (governance?.documentOrder || Object.keys(allDocuments))
    .map((docKey) => allDocuments[docKey])
    .filter((doc): doc is IdentityDocument => Boolean(doc));
  const selectedDocument = selectedDocKey ? allDocuments[selectedDocKey] : null;
  const selectedDocumentPolicy = selectedDocument?.policy;
  const historyTargetLabel =
    historyScope === "selected"
      ? selectedDocument?.label || governance?.documents?.[selectedDocKey]?.label || selectedDocKey || "Selected Layer"
      : "All Identity Layers";
  const selectedHistoryEntry = historyEntries.find((entry) => historyEntryKey(entry) === selectedHistoryKey) || null;
  const selectedHistoryDocument = selectedHistoryDetail?.document || selectedHistoryEntry?.document || null;
  const selectedHistoryPolicy = selectedHistoryDocument?.policy || null;
  const selectedHistoryLabel = selectedHistoryEntry
    ? selectedHistoryDocument?.label || selectedHistoryEntry.doc_key
    : null;
  const selectedHistoryMetadataRows: Array<[string, string]> = selectedHistoryDetail
    ? [
        ["doc", selectedHistoryDocument?.label || formatValue(selectedHistoryDetail.doc_key)],
        ["ownership", formatValue(selectedHistoryPolicy?.ownershipClassInfo?.label || selectedHistoryPolicy?.ownershipClass)],
        ["mutability", formatValue(selectedHistoryPolicy?.mutabilityInfo?.label || selectedHistoryPolicy?.mutability)],
        [
          "review posture",
          formatValue(
            selectedHistoryPolicy?.reviewRequirementInfo?.label || selectedHistoryPolicy?.reviewRequirement,
          ),
        ],
        ["edit paths", joinDescriptorLabels(selectedHistoryPolicy?.editableViaInfo)],
        ["version", formatValue(selectedHistoryDetail.version)],
        ["saved at", formatTimestamp(selectedHistoryDetail.created_at)],
        [
          "actor",
          selectedHistoryDetail.actor_id
            ? `${selectedHistoryDetail.actor_type}:${selectedHistoryDetail.actor_id}`
            : selectedHistoryDetail.actor_type,
        ],
        ["reason", formatValue(selectedHistoryDetail.reason)],
        ["file path", formatValue(selectedHistoryDocument?.path || selectedHistoryDetail.file_path)],
      ].filter((entry): entry is [string, string] => Boolean(entry[1]) && entry[1] !== "unset")
    : [];
  const selectedHistoryComparison = selectedHistoryDetail?.comparison || null;
  const selectedHistoryCurrentDocument = selectedHistoryComparison?.currentDocument || null;
  const selectedHistoryCurrentPolicy = selectedHistoryCurrentDocument?.policy || null;
  const selectedHistoryCurrentLabel =
    selectedHistoryCurrentDocument?.label || selectedHistoryComparison?.current_label || null;
  const selectedHistoryCurrentVersion =
    selectedHistoryCurrentDocument?.version ?? selectedHistoryComparison?.current_version ?? null;
  const selectedHistoryCurrentPath =
    selectedHistoryCurrentDocument?.path || selectedHistoryComparison?.current_path || null;
  const selectedHistoryComparisonRows: Array<[string, string]> = selectedHistoryComparison
    ? [
        ["current version", formatValue(selectedHistoryCurrentVersion)],
        [
          "current ownership",
          formatValue(
            selectedHistoryCurrentPolicy?.ownershipClassInfo?.label || selectedHistoryCurrentPolicy?.ownershipClass,
          ),
        ],
        [
          "current mutability",
          formatValue(selectedHistoryCurrentPolicy?.mutabilityInfo?.label || selectedHistoryCurrentPolicy?.mutability),
        ],
        [
          "current review posture",
          formatValue(
            selectedHistoryCurrentPolicy?.reviewRequirementInfo?.label
              || selectedHistoryCurrentPolicy?.reviewRequirement,
          ),
        ],
        ["stored lines", formatValue(selectedHistoryComparison.summary.history_line_count)],
        ["current lines", formatValue(selectedHistoryComparison.summary.current_line_count)],
        ["added lines", formatValue(selectedHistoryComparison.summary.added_lines)],
        ["removed lines", formatValue(selectedHistoryComparison.summary.removed_lines)],
        ["changed blocks", formatValue(selectedHistoryComparison.summary.changed_blocks)],
      ].filter((entry): entry is [string, string] => Boolean(entry[1]) && entry[1] !== "unset")
    : [];
  const ownershipGroups = Object.entries(governance?.ownershipClasses || {})
    .map(([ownershipClass, descriptor]) => ({
      ownershipClass,
      descriptor,
      docs: orderedDocuments.filter((doc) => doc.policy?.ownershipClass === ownershipClass),
    }))
    .filter(({ docs }) => docs.length > 0);
  const selectedDocumentMetadataRows: Array<[string, string]> = selectedDocumentPolicy
    ? [
        ["scope", selectedDocumentPolicy.scopeInfo?.label || formatValue(selectedDocumentPolicy.scope)],
        ["owner", selectedDocumentPolicy.ownerInfo?.label || formatValue(selectedDocumentPolicy.owner)],
        ["edit paths", joinDescriptorLabels(selectedDocumentPolicy.editableViaInfo)],
        ["raw markdown actors", formatValue(selectedDocumentPolicy.allowedActors?.rawMarkdown || [])],
        ["structured actors", formatValue(selectedDocumentPolicy.allowedActors?.structured || [])],
        [
          "silent maintenance actors",
          formatValue(selectedDocumentPolicy.allowedActors?.silentMaintenance || []),
        ],
        ["version", formatValue(selectedDocument?.frontmatter?.version)],
        ["last updated by", formatValue(selectedDocument?.frontmatter?.last_updated_by)],
      ].filter((entry): entry is [string, string] => Boolean(entry[1]) && entry[1] !== "unset")
    : [];
  const autonomyPreset = deriveAutonomyPreset(actionValues);
  const compiledTextRows = summarizeNestedRecord(compiled?.snapshots.text.traits, 8);
  const compiledTextSources = orderCompiledSnapshotDocuments(compiled?.snapshots.text.documents, governance);
  const compiledVoiceRows = [
    ...summarizeRecord(compiled?.snapshots.voice.preferences, 5),
    ...summarizeRecord(compiled?.snapshots.voice.actionCoupling, 3),
  ];
  const compiledVoiceSources = orderCompiledSnapshotDocuments(compiled?.snapshots.voice.documents, governance);
  const compiledActionRows = [
    ...summarizeRecord(compiled?.snapshots.action.preferences, 6),
    ...summarizeRecord(compiled?.snapshots.action.relationship, 2),
  ];
  const compiledActionSources = orderCompiledSnapshotDocuments(compiled?.snapshots.action.documents, governance);
  const orderedProvenanceEntries = (governance?.documentOrder || Object.keys(compiled?.provenance || {}))
    .filter((docKey) => Boolean(compiled?.provenance?.[docKey]))
    .map((docKey) => [docKey, compiled?.provenance?.[docKey]] as const)
    .filter(
      (entry): entry is [string, NonNullable<CompiledIdentityResponse["provenance"][string]>] => Boolean(entry[1]),
    );

  if (loading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center text-white/40">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="rounded-3xl border border-white/10 bg-white/5 p-6 shadow-[0_30px_80px_rgba(0,0,0,0.25)]">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-[11px] font-bold uppercase tracking-[0.24em] text-white/35">Owner Shaping</div>
            <h1 className="mt-2 text-3xl font-semibold text-white">Shape PaLLi&apos;s companion posture</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-white/60">
              Adjust the relationship and autonomy layers PaLLi carries across chat, voice, Discord, and operator work.
              The document, provenance, and compiled-runtime lanes stay available below so you can inspect what the
              runtime is actually using.
            </p>
          </div>
          <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
            <div>User {bundle?.userId}</div>
            <div className="text-emerald-200/70">Platform: {bundle?.platform}</div>
            {bundle?.ownerUserId ? <div className="text-emerald-200/70">Owner: {bundle.ownerUserId}</div> : null}
          </div>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <section className="rounded-3xl border border-white/10 bg-white/5 p-6">
          <div className="mb-5 flex items-center gap-2 text-white/85">
            <Sparkles className="h-4 w-4 text-pink-300" />
            <h2 className="text-lg font-medium">Companion Feel</h2>
          </div>
          <div className="space-y-4">
            {PERSONALITY_FIELDS.map(([key, label]) => (
              <div key={key} className="space-y-2">
                <label className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/35">{label}</label>
                <select
                  value={String(personalityValues[key] ?? "")}
                  onChange={(event) =>
                    setPersonalityValues((current) => ({ ...current, [key]: event.target.value }))
                  }
                  className="h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white outline-none"
                >
                  {selectOptionsForField(key, personalityValues[key]).map((option) => (
                    <option key={option.value} value={option.value} className="bg-slate-950 text-white">
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
          <p className="mt-4 text-sm leading-6 text-white/50">
            These fields shape how PaLLi comes across in normal conversation. The deeper document lane below stays
            available when you want to inspect or author the file-backed layer directly.
          </p>
        </section>

        <section className="rounded-3xl border border-white/10 bg-white/5 p-6">
          <div className="mb-5 flex items-center gap-2 text-white/85">
            <Shield className="h-4 w-4 text-amber-300" />
            <h2 className="text-lg font-medium">Autonomy and Boundaries</h2>
          </div>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/35">Independence</label>
              <select
                value={autonomyPreset}
                onChange={(event) =>
                  setActionValues((current) => applyAutonomyPreset(event.target.value as SimpleAutonomyPreset, current))
                }
                className="h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white outline-none"
              >
                <option value="cautious" className="bg-slate-950 text-white">Ask First</option>
                <option value="balanced" className="bg-slate-950 text-white">Balanced</option>
                <option value="trusted" className="bg-slate-950 text-white">Trusted</option>
              </select>
              <p className="text-xs text-white/45">
                Controls how willing PaLLi is to quietly handle safe work without stopping.
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/35">Follow-through</label>
              <select
                value={String(actionValues.followthrough_intensity ?? "steady")}
                onChange={(event) =>
                  setActionValues((current) => {
                    const next = event.target.value;
                    return {
                      ...current,
                      initiative_level: next === "light" ? "low" : next === "persistent" ? "high" : "medium",
                      followthrough_intensity: next,
                      standing_orders_policy: next === "persistent" ? "enabled" : "explicit_only",
                    };
                  })
                }
                className="h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white outline-none"
              >
                <option value="light" className="bg-slate-950 text-white">Light</option>
                <option value="steady" className="bg-slate-950 text-white">Steady</option>
                <option value="persistent" className="bg-slate-950 text-white">Persistent</option>
              </select>
              <p className="text-xs text-white/45">
                Controls how much PaLLi keeps open loops alive instead of treating them as one-off tasks.
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/35">Quiet Hours</label>
              <select
                value={String(actionValues.quiet_hours_mode ?? "adaptive")}
                onChange={(event) =>
                  setActionValues((current) => ({ ...current, quiet_hours_mode: event.target.value }))
                }
                className="h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white outline-none"
              >
                <option value="off" className="bg-slate-950 text-white">Off</option>
                <option value="gentle" className="bg-slate-950 text-white">Gentle</option>
                <option value="adaptive" className="bg-slate-950 text-white">Adaptive</option>
              </select>
            </div>

            <div className="space-y-2">
              <label className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/35">Voice Presence</label>
              <select
                value={String(actionValues.group_voice_behavior ?? "socially_adaptive")}
                onChange={(event) =>
                  setActionValues((current) => ({ ...current, group_voice_behavior: event.target.value }))
                }
                className="h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white outline-none"
              >
                <option value="reserved" className="bg-slate-950 text-white">Reserved</option>
                <option value="socially_adaptive" className="bg-slate-950 text-white">Balanced</option>
                <option value="companion_forward" className="bg-slate-950 text-white">Companion Forward</option>
              </select>
            </div>
          </div>
          <p className="mt-4 text-sm leading-6 text-white/50">
            Mission control and owner controls still handle live runtime status and day-to-day basics. This workspace
            is where longer-lived shaping, boundaries, and inspectable identity posture belong.
          </p>
        </section>
      </div>

      <div className="rounded-3xl border border-white/10 bg-white/5 p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-[11px] font-bold uppercase tracking-[0.24em] text-white/35">Workspace Save</div>
            <h2 className="mt-2 text-xl font-medium text-white">Apply owner-facing shaping</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">
              This writes the visible relationship posture and boundary preferences that sit above runtime controls.
            </p>
          </div>
          <Button onClick={saveWorkspaceShaping} disabled={saving}>
            {saving ? "Saving..." : "Save workspace changes"}
          </Button>
        </div>
      </div>

      <details className="rounded-3xl border border-white/10 bg-white/5 p-6">
        <summary className="cursor-pointer list-none text-lg font-medium text-white">
          Document inspect, provenance, and raw layers
        </summary>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-white/60">
          This workspace keeps compiled inspect, governance, raw markdown, and recent history in one place so the owner
          can audit how file-backed identity becomes runtime behavior. It stays secondary to normal conversation,
          not a setup wizard.
        </p>

        <section className="mt-6 rounded-3xl border border-white/10 bg-black/20 p-6">
          <div className="mb-5 flex items-center justify-between gap-4">
            <div>
              <div className="text-[11px] font-bold uppercase tracking-[0.24em] text-white/35">Governance Map</div>
              <h2 className="mt-2 text-2xl font-medium text-white">Identity Layer Ownership</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">
                These runtime-enforced classes explain which identity documents are creator-owned foundations, direct
                owner preferences, evolving system-maintained summaries, or append-only operational traces.
              </p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm text-white/75">
              <div>{orderedDocuments.length} total document(s)</div>
              <div className="text-white/45">{ownershipGroups.length} active governance class(es)</div>
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-4">
            {ownershipGroups.map(({ ownershipClass, descriptor, docs }) => (
              <div key={ownershipClass} className="rounded-3xl border border-white/10 bg-white/[0.03] p-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">
                    {docs.length} document(s)
                  </div>
                  <div className="rounded-full border border-white/10 px-3 py-1 text-xs text-white/55">
                    {descriptor.label}
                  </div>
                </div>
                <h3 className="mt-3 text-lg font-medium text-white">{descriptor.summary}</h3>
                <p className="mt-3 text-sm leading-6 text-white/55">{descriptor.description}</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {docs.map((doc) => (
                    <span
                      key={doc.docKey}
                      className="rounded-full border border-white/10 bg-black/20 px-3 py-1 text-xs text-white/70"
                    >
                      {doc.label}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-6 rounded-3xl border border-white/10 bg-black/20 p-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="text-[11px] font-bold uppercase tracking-[0.24em] text-white/35">Advanced Behavior</div>
              <h2 className="mt-2 text-xl font-medium text-white">Bounded Reflection</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">
                This lets PaLLi do private background reflection to tighten memory summaries, relationship notes, and
                routines. It does not let PaLLi message you or take actions on its own.
              </p>
            </div>
            <button
              type="button"
              onClick={() =>
                setActionValues((current) => ({
                  ...current,
                  allow_bounded_reflection: !Boolean(current.allow_bounded_reflection),
                }))
              }
              className={`relative inline-flex h-8 w-14 items-center rounded-full border transition ${
                Boolean(actionValues.allow_bounded_reflection)
                  ? "border-emerald-400/40 bg-emerald-500/30"
                  : "border-white/10 bg-white/10"
              }`}
            >
              <span
                className={`absolute h-6 w-6 rounded-full bg-white transition ${
                  Boolean(actionValues.allow_bounded_reflection) ? "left-7" : "left-1"
                }`}
              />
            </button>
          </div>
          <div className="mt-4 text-xs text-white/45">
            Current state: {Boolean(actionValues.allow_bounded_reflection) ? "Enabled" : "Disabled"}
          </div>
        </section>

        <section className="mt-6 rounded-3xl border border-white/10 bg-black/20 p-6">
          <div className="mb-5 flex items-center justify-between gap-4">
            <div>
              <div className="text-[11px] font-bold uppercase tracking-[0.24em] text-white/35">Compiled Inspect</div>
              <h2 className="mt-2 text-2xl font-medium text-white">Effective Companion State</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">
                This is the derived identity state the runtime actually uses for prompts, voice behavior, reminders,
                proactive outreach, and agentic policy checks.
              </p>
            </div>
            <div className="rounded-2xl border border-sky-400/20 bg-sky-500/10 px-4 py-3 text-sm text-sky-100">
              <div>{compiled?.warnings.length || 0} compile warning(s)</div>
              <div className="text-sky-100/70">{Object.keys(compiled?.provenance || {}).length} source document(s)</div>
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2 2xl:grid-cols-4">
            <SummaryCard
              eyebrow="Text Snapshot"
              title="Reply Identity"
              rows={compiledTextRows}
              sources={compiledTextSources}
              warnings={compiled?.snapshots.text.warnings}
              promptContext={compiled?.snapshots.text.promptContext}
            />
            <SummaryCard
              eyebrow="Voice Snapshot"
              title="Voice Runtime"
              rows={compiledVoiceRows}
              sources={compiledVoiceSources}
              warnings={compiled?.snapshots.voice.warnings}
              promptContext={compiled?.snapshots.voice.promptContext}
            />
            <SummaryCard
              eyebrow="Action Snapshot"
              title="Action Context"
              rows={compiledActionRows}
              sources={compiledActionSources}
              warnings={compiled?.snapshots.action.warnings}
              promptContext={compiled?.snapshots.action.promptContext}
            />
            <RuntimePermissionsCard policy={compiled?.policy} />
          </div>

          <div className="mt-6 rounded-3xl border border-white/10 bg-black/20 p-4">
            <div className="mb-3 text-sm font-medium text-white">Source Provenance</div>
            {compiledLoadError ? (
              <div className="mb-4 rounded-2xl border border-amber-400/20 bg-amber-400/10 px-4 py-3 text-sm text-amber-100/85">
                {compiledLoadError}
              </div>
            ) : null}
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {orderedProvenanceEntries.map(([docKey, entry]) => {
                const policy = entry.policy;
                const docLabel = entry.label || entry.docKey || docKey;
                return (
                  <div key={docKey} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3 text-xs text-white/70">
                    <div className="font-medium text-white/90">{docLabel}</div>
                    <div className="mt-1 text-white/50">
                      {policy?.ownershipClassInfo?.label || docKey}
                      {policy?.mutabilityInfo?.label ? ` · ${policy.mutabilityInfo.label}` : ""}
                    </div>
                    <div className="mt-1">
                      v{entry.version} · {entry.platform}
                    </div>
                    {policy?.reviewRequirementInfo?.label ? (
                      <div className="mt-1 text-white/45">{policy.reviewRequirementInfo.label}</div>
                    ) : null}
                    <div className="mt-1 break-all text-white/45">{entry.path}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        <section className="mt-6 rounded-3xl border border-white/10 bg-black/20 p-6">
          <div className="mb-4 flex items-center gap-2 text-white/85">
            <ScrollText className="h-4 w-4 text-sky-300" />
            <h2 className="text-lg font-medium">Documents, Raw View, and History</h2>
          </div>

          <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
            <div className="space-y-2">
              {orderedDocuments.map((doc) => (
                <button
                  key={doc.docKey}
                  type="button"
                  onClick={() => setSelectedDocKey(doc.docKey)}
                  className={`w-full rounded-2xl border px-3 py-3 text-left transition ${
                    selectedDocKey === doc.docKey
                      ? "border-sky-400/40 bg-sky-500/10 text-white"
                      : "border-white/10 bg-black/20 text-white/70 hover:border-white/20 hover:text-white"
                  }`}
                >
                  <div className="text-sm font-medium">{doc.label}</div>
                  <div className="mt-1 text-[11px] uppercase tracking-[0.18em] text-white/35">
                    {doc.policy?.ownershipClassInfo?.label || "Unknown Layer"}
                  </div>
                  <div className="mt-1 text-xs text-white/45">
                    {doc.policy?.mutabilityInfo?.label || "Unknown mutability"} · {doc.editable ? "owner raw edit" : "read only here"}
                  </div>
                </button>
              ))}
            </div>

            <div className="space-y-4">
              <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                <div className="mb-2 flex items-center justify-between gap-4">
                  <div>
                    <div className="text-sm font-medium text-white">{selectedDocument?.label || "Select a document"}</div>
                    <div className="text-xs text-white/40">{selectedDocument?.path}</div>
                    <div className="mt-2 text-xs text-white/50">
                      {selectedDocument?.editable
                        ? "Owner raw markdown edits are allowed here."
                        : selectedDocumentPolicy?.ownershipClassInfo?.label
                          ? `${selectedDocumentPolicy.ownershipClassInfo.label} layers stay visible here, but this identity workspace keeps them read only.`
                          : "This document is read only in the identity workspace."}
                    </div>
                  </div>
                  <Button onClick={saveRawDocument} disabled={saving || !selectedDocument?.editable}>
                    <Save className="mr-2 h-4 w-4" />
                    Save Raw Markdown
                  </Button>
                </div>
                {selectedDocumentPolicy ? (
                  <div className="mb-4 grid gap-3 xl:grid-cols-4">
                    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
                      <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">Ownership</div>
                      <div className="mt-2 text-sm font-medium text-white">
                        {selectedDocumentPolicy.ownershipClassInfo?.label || formatValue(selectedDocumentPolicy.ownershipClass)}
                      </div>
                      <div className="mt-2 text-xs leading-5 text-white/50">
                        {selectedDocumentPolicy.ownershipClassInfo?.summary || "No ownership summary available."}
                      </div>
                    </div>
                    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
                      <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">Mutability</div>
                      <div className="mt-2 text-sm font-medium text-white">
                        {selectedDocumentPolicy.mutabilityInfo?.label || formatValue(selectedDocumentPolicy.mutability)}
                      </div>
                      <div className="mt-2 text-xs leading-5 text-white/50">
                        {selectedDocumentPolicy.mutabilityInfo?.summary || "No mutability summary available."}
                      </div>
                    </div>
                    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
                      <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">Review Posture</div>
                      <div className="mt-2 text-sm font-medium text-white">
                        {selectedDocumentPolicy.reviewRequirementInfo?.label || formatValue(selectedDocumentPolicy.reviewRequirement)}
                      </div>
                      <div className="mt-2 text-xs leading-5 text-white/50">
                        {selectedDocumentPolicy.reviewRequirementInfo?.summary || "No review posture summary available."}
                      </div>
                    </div>
                    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
                      <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">Edit Paths</div>
                      <div className="mt-2 text-sm font-medium text-white">
                        {joinDescriptorLabels(selectedDocumentPolicy.editableViaInfo)}
                      </div>
                      <div className="mt-2 text-xs leading-5 text-white/50">
                        {selectedDocumentPolicy.dashboardEditable
                          ? "This layer has an owner-facing editing path here."
                          : "This layer is not exposed through the owner-facing form controls."}
                      </div>
                    </div>
                  </div>
                ) : null}
                {selectedDocumentMetadataRows.length ? (
                  <div className="mb-4 grid gap-2 md:grid-cols-2">
                    {selectedDocumentMetadataRows.map(([key, value]) => (
                      <div
                        key={key}
                        className="flex items-start justify-between gap-4 rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-white/70"
                      >
                        <span className="font-medium text-white/50">{key}</span>
                        <span className="max-w-[60%] text-right text-white/85">{value}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
                <textarea
                  value={rawMarkdown}
                  onChange={(event) => setRawMarkdown(event.target.value)}
                  disabled={!selectedDocument?.editable}
                  className="min-h-[420px] w-full rounded-2xl border border-white/10 bg-black/40 p-4 font-mono text-xs leading-6 text-white/85 outline-none disabled:opacity-80"
                />
              </div>

              <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-white">Recent Identity History</div>
                    <div className="mt-1 text-xs text-white/45">
                      {historyScope === "selected"
                        ? `Showing provenance for ${historyTargetLabel}.`
                        : "Showing the combined changelog across every identity layer."}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => setHistoryScope("selected")}
                      className={`rounded-full border px-3 py-1 text-[11px] font-bold uppercase tracking-[0.18em] transition ${
                        historyScope === "selected"
                          ? "border-sky-400/40 bg-sky-500/10 text-sky-100"
                          : "border-white/10 bg-black/20 text-white/55 hover:border-white/20 hover:text-white/75"
                      }`}
                    >
                      Selected Layer
                    </button>
                    <button
                      type="button"
                      onClick={() => setHistoryScope("all")}
                      className={`rounded-full border px-3 py-1 text-[11px] font-bold uppercase tracking-[0.18em] transition ${
                        historyScope === "all"
                          ? "border-sky-400/40 bg-sky-500/10 text-sky-100"
                          : "border-white/10 bg-black/20 text-white/55 hover:border-white/20 hover:text-white/75"
                      }`}
                    >
                      All Layers
                    </button>
                  </div>
                </div>
                {historyLoadError ? (
                  <div className="rounded-2xl border border-amber-400/20 bg-amber-400/10 px-4 py-3 text-sm text-amber-100/85">
                    {historyLoadError}
                  </div>
                ) : historyLoading ? (
                  <div className="flex min-h-[180px] items-center justify-center text-white/40">
                    <Loader2 className="h-5 w-5 animate-spin" />
                  </div>
                ) : historyEntries.length ? (
                  <div className="grid gap-4 xl:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
                    <div className="max-h-[360px] space-y-2 overflow-y-auto pr-2">
                      {historyEntries.map((entry, index) => {
                        const entryLabel = entry.document?.label || entry.doc_key;
                        const entryPolicySummary = summarizePolicyLine(entry.document?.policy);
                        const entryReviewLabel = reviewPolicyLabel(entry.document?.policy);
                        const isSelected = historyEntryKey(entry) === selectedHistoryKey;
                        return (
                          <button
                            key={`${entry.doc_key}-${entry.version}-${index}`}
                            type="button"
                            onClick={() => setSelectedHistoryKey(historyEntryKey(entry))}
                            className={`w-full rounded-xl border p-3 text-left text-xs transition ${
                              isSelected
                                ? "border-sky-400/40 bg-sky-500/10 text-white"
                                : "border-white/10 bg-white/[0.03] text-white/70 hover:border-white/20 hover:text-white"
                            }`}
                          >
                            <div className="font-medium text-white/90">
                              {entryLabel} v{entry.version}
                            </div>
                            {entryPolicySummary ? (
                              <div className="mt-1 text-white/45">{entryPolicySummary}</div>
                            ) : null}
                            {entryReviewLabel ? (
                              <div className="mt-1 text-white/45">{entryReviewLabel}</div>
                            ) : null}
                            {historyScope === "all" ? (
                              <div className="mt-1 text-white/45">{entry.doc_key}</div>
                            ) : null}
                            <div className="mt-1">{formatTimestamp(entry.created_at)}</div>
                            <div className="mt-1">
                              actor: {entry.actor_type}
                              {entry.actor_id ? `:${entry.actor_id}` : ""}
                            </div>
                            {entry.reason ? <div className="mt-1 text-white/45">reason: {entry.reason}</div> : null}
                          </button>
                        );
                      })}
                    </div>

                    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="text-sm font-medium text-white">Stored Version Review</div>
                          <div className="mt-1 text-xs text-white/45">
                            {selectedHistoryLabel && selectedHistoryEntry
                              ? `Inspecting ${selectedHistoryLabel} v${selectedHistoryEntry.version}.`
                              : "Select a history entry to inspect its stored markdown and frontmatter."}
                          </div>
                        </div>
                        {selectedHistoryEntry?.doc_key && selectedHistoryEntry.doc_key !== selectedDocKey ? (
                          <button
                            type="button"
                            onClick={() => setSelectedDocKey(selectedHistoryEntry.doc_key)}
                            className="rounded-full border border-white/10 bg-black/20 px-3 py-1 text-[11px] font-bold uppercase tracking-[0.18em] text-white/65 transition hover:border-white/20 hover:text-white"
                          >
                            Focus Current Layer
                          </button>
                        ) : null}
                      </div>

                      {historyDetailError ? (
                        <div className="mt-4 rounded-2xl border border-amber-400/20 bg-amber-400/10 px-4 py-3 text-sm text-amber-100/85">
                          {historyDetailError}
                        </div>
                      ) : historyDetailLoading ? (
                        <div className="flex min-h-[220px] items-center justify-center text-white/40">
                          <Loader2 className="h-5 w-5 animate-spin" />
                        </div>
                      ) : selectedHistoryDetail ? (
                        <>
                          {selectedHistoryMetadataRows.length ? (
                            <div className="mt-4 grid gap-2 md:grid-cols-2">
                              {selectedHistoryMetadataRows.map(([key, value]) => (
                                <div
                                  key={key}
                                  className="flex items-start justify-between gap-4 rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-xs text-white/70"
                                >
                                  <span className="font-medium text-white/50">{key}</span>
                                  <span className="max-w-[60%] text-right text-white/85">{value}</span>
                                </div>
                              ))}
                            </div>
                          ) : null}

                          <div className="mt-4 grid gap-4 xl:grid-cols-2">
                            <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                              <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">
                                Stored Frontmatter
                              </div>
                              <pre className="mt-3 max-h-[220px] overflow-y-auto whitespace-pre-wrap rounded-2xl border border-white/10 bg-black/40 p-3 font-mono text-[11px] leading-5 text-white/75">
                                {JSON.stringify(selectedHistoryDetail.frontmatter || {}, null, 2)}
                              </pre>
                            </div>
                            <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                              <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">
                                Stored Body
                              </div>
                              <pre className="mt-3 max-h-[220px] overflow-y-auto whitespace-pre-wrap rounded-2xl border border-white/10 bg-black/40 p-3 font-mono text-[11px] leading-5 text-white/75">
                                {selectedHistoryDetail.content || "No body content stored for this version."}
                              </pre>
                            </div>
                          </div>

                          <div className="mt-4 rounded-2xl border border-white/10 bg-black/20 p-4">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                              <div>
                                <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">
                                  Current vs Stored Diff
                                </div>
                                <div className="mt-1 text-xs text-white/45">
                                  {selectedHistoryComparison
                                    ? selectedHistoryComparison.summary.has_changes
                                      ? `Comparing this stored version against the current saved ${selectedHistoryCurrentLabel || "current"} layer.`
                                      : `The current saved ${selectedHistoryCurrentLabel || "current"} layer matches this stored version.`
                                    : "Comparison data is unavailable for this stored version."}
                                </div>
                              </div>
                              {selectedHistoryCurrentVersion ? (
                                <div className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-[11px] font-bold uppercase tracking-[0.18em] text-white/60">
                                  Current v{selectedHistoryCurrentVersion}
                                </div>
                              ) : null}
                            </div>

                            {selectedHistoryComparison ? (
                              <>
                                {selectedHistoryComparisonRows.length ? (
                                  <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                                    {selectedHistoryComparisonRows.map(([key, value]) => (
                                      <div
                                        key={key}
                                        className="flex items-start justify-between gap-4 rounded-2xl border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-white/70"
                                      >
                                        <span className="font-medium text-white/50">{key}</span>
                                        <span className="max-w-[60%] text-right text-white/85">{value}</span>
                                      </div>
                                    ))}
                                  </div>
                                ) : null}

                                {selectedHistoryComparison.summary.has_changes ? (
                                  <div className="mt-4 space-y-4">
                                    {selectedHistoryComparison.hunks.map((hunk, hunkIndex) => (
                                      <div
                                        key={`${hunk.history_start_line}-${hunk.current_start_line}-${hunkIndex}`}
                                        className="overflow-hidden rounded-2xl border border-white/10"
                                      >
                                        <div className="grid grid-cols-[72px_72px_20px_minmax(0,1fr)] gap-0 border-b border-white/10 bg-white/[0.03] px-3 py-2 font-mono text-[11px] uppercase tracking-[0.14em] text-white/40">
                                          <span>Hist</span>
                                          <span>Curr</span>
                                          <span />
                                          <span>
                                            h{hunk.history_start_line} / c{hunk.current_start_line}
                                          </span>
                                        </div>
                                        <div className="divide-y divide-white/5">
                                          {hunk.lines.map((line, lineIndex) => (
                                            <div
                                              key={`${line.kind}-${line.history_line_number ?? "none"}-${line.current_line_number ?? "none"}-${lineIndex}`}
                                              className={`grid grid-cols-[72px_72px_20px_minmax(0,1fr)] gap-0 px-3 py-2 font-mono text-[11px] leading-5 ${diffLineClassName(line.kind)}`}
                                            >
                                              <span className="pr-2 text-white/35">
                                                {line.history_line_number ?? ""}
                                              </span>
                                              <span className="pr-2 text-white/35">
                                                {line.current_line_number ?? ""}
                                              </span>
                                              <span className="pr-2 font-bold text-white/70">{diffMarker(line.kind)}</span>
                                              <span className="whitespace-pre-wrap break-words">{line.text || " "}</span>
                                            </div>
                                          ))}
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <div className="mt-4 rounded-2xl border border-dashed border-white/10 px-4 py-5 text-sm text-white/45">
                                    No line-level differences remain between this stored snapshot and the current saved
                                    document.
                                  </div>
                                )}

                                <div className="mt-4 break-all text-xs text-white/40">
                                  Current path: {selectedHistoryCurrentPath || "Unknown"}
                                </div>
                              </>
                            ) : (
                              <div className="mt-4 rounded-2xl border border-dashed border-white/10 px-4 py-5 text-sm text-white/45">
                                The backend did not return a current-document comparison for this history entry.
                              </div>
                            )}
                          </div>

                          <div className="mt-4 rounded-2xl border border-white/10 bg-black/20 p-4">
                            <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/35">
                              Stored Markdown Snapshot
                            </div>
                            <textarea
                              value={selectedHistoryDetail.markdown}
                              readOnly
                              className="mt-3 min-h-[260px] w-full rounded-2xl border border-white/10 bg-black/40 p-4 font-mono text-xs leading-6 text-white/85 outline-none"
                            />
                          </div>
                        </>
                      ) : (
                        <div className="mt-4 rounded-2xl border border-dashed border-white/10 px-4 py-5 text-sm text-white/45">
                          Select a history entry to inspect the stored version details.
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="rounded-2xl border border-dashed border-white/10 px-4 py-5 text-sm text-white/45">
                    {historyScope === "selected"
                      ? `No stored history entries were found yet for ${historyTargetLabel}.`
                      : "No stored identity history entries were found yet."}
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>
      </details>
    </div>
  );
}

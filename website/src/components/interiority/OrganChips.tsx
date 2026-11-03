"use client";

import { formatTimestamp } from "@/lib/agentControl";
import type { InteriorityOrganChip } from "@/lib/types";

const ORGAN_LABELS: Record<string, string> = {
  night_cycle: "Night cycle",
  consolidation: "Consolidation",
  narrative_self: "Narrative self",
  stake_ledger: "Stake ledger",
  caring_memory: "Caring memory",
  introspection: "Introspection",
  passions: "Passions",
  present_moment: "Present Moment",
};

function chipDates(chip: InteriorityOrganChip): string {
  if (chip.error) return "read failed";
  if (chip.first_artifact_ts === null || chip.last_artifact_ts === null) {
    return "no artifacts";
  }
  const first = formatTimestamp(chip.first_artifact_ts * 1000);
  const last = formatTimestamp(chip.last_artifact_ts * 1000);
  return first === last ? `artifacts: ${first}` : `artifacts: ${first} → ${last}`;
}

export function OrganChips({ organs }: { organs: InteriorityOrganChip[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {organs.map((chip) => (
        <div
          key={chip.key}
          className="flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5"
          title={chipDates(chip)}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              chip.error
                ? "bg-rose-400/80"
                : chip.enabled
                  ? "bg-emerald-400/80"
                  : "bg-white/20"
            }`}
          />
          <span className="text-xs text-white/75">
            {ORGAN_LABELS[chip.key] ?? chip.key}
          </span>
          <span className="text-[10px] font-mono uppercase tracking-widest text-white/35">
            {chip.enabled ? `${chip.count_since} since` : "off"}
          </span>
        </div>
      ))}
    </div>
  );
}

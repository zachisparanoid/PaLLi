"use client";

import type { InteriorityOrganChip } from "@/lib/types";

const ORGAN_SHORT: Record<string, string> = {
  night_cycle: "night cycle",
  stake_ledger: "stakes",
  caring_memory: "caring memory",
  introspection: "introspection",
};

export function SincePicker({
  sinceDays,
  sinceEpoch,
  organs,
  onPickDays,
  onPickEpoch,
}: {
  sinceDays: number;
  sinceEpoch: number | null;
  organs: InteriorityOrganChip[];
  onPickDays: (days: number) => void;
  onPickEpoch: (epoch: number) => void;
}) {
  const dayPresets = [7, 30];
  const organPresets = organs.filter((o) => o.first_artifact_ts !== null);

  const baseClass =
    "rounded-full border px-3 py-1.5 text-xs transition-colors";
  const active = "border-brand-500/40 bg-brand-500/10 text-brand-200";
  const idle = "border-white/10 bg-white/[0.03] text-white/55 hover:bg-white/10";

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[10px] font-mono uppercase tracking-widest text-white/35">
        since
      </span>
      {dayPresets.map((days) => (
        <button
          key={days}
          type="button"
          className={`${baseClass} ${sinceEpoch === null && sinceDays === days ? active : idle}`}
          onClick={() => onPickDays(days)}
        >
          {days}d
        </button>
      ))}
      {organPresets.map((organ) => (
        <button
          key={organ.key}
          type="button"
          className={`${baseClass} ${sinceEpoch === organ.first_artifact_ts ? active : idle}`}
          onClick={() => onPickEpoch(organ.first_artifact_ts as number)}
        >
          since {ORGAN_SHORT[organ.key] ?? organ.key}&apos;s first note
        </button>
      ))}
    </div>
  );
}

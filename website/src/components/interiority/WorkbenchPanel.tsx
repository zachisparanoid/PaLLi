"use client";

import { Hammer } from "lucide-react";

import { AgentPanel, EmptyState } from "@/components/dashboard/v2/agentControlShared";
import { formatRelativeTime } from "@/lib/agentControl";
import type { InteriorityOverview } from "@/lib/types";

/**
 * Her own work (roadmap 16-17). Sign-offs only — the one line SHE wrote
 * about where she stopped. The work itself lives in her workspace, which is
 * his to read directly; this is a window, not a copy.
 */
export function WorkbenchPanel({
  section,
}: {
  section: InteriorityOverview["workbench"];
}) {
  return (
    <AgentPanel
      title="Her Workbench"
      subtitle="Her own work, in her own words."
      icon={Hammer}
    >
      {!section.enabled ? (
        <EmptyState
          title="Organ off"
          message="The own-work flag is off — she has no work sessions."
        />
      ) : section.error ? (
        <EmptyState
          title="Read failed"
          message="The session store errored — this is an error, not an empty workbench."
        />
      ) : section.recent.length === 0 ? (
        <EmptyState
          title="Honest zero"
          message="No sessions yet. Nothing is owed."
        />
      ) : (
        <div className="flex flex-col gap-2">
          {section.recent.map((s, i) => (
            <div
              key={`${s.ts ?? i}-${i}`}
              className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3"
            >
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-white/85">
                {s.signoff || "(a quiet session)"}
              </p>
              <p className="mt-1 text-[11px] uppercase tracking-wide text-white/40">
                {s.ts ? formatRelativeTime(s.ts) : "—"}
                {s.shared ? " · told them" : ""}
              </p>
            </div>
          ))}
        </div>
      )}
    </AgentPanel>
  );
}

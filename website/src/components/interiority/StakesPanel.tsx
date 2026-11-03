"use client";

import { Anchor } from "lucide-react";

import { AgentPanel, EmptyState } from "@/components/dashboard/v2/agentControlShared";
import { formatRelativeTime } from "@/lib/agentControl";
import type { InteriorityOverview } from "@/lib/types";

export function StakesPanel({
  section,
}: {
  section: InteriorityOverview["stakes"];
}) {
  const empty = section.open.length === 0 && section.resolved.length === 0;
  return (
    <AgentPanel
      title="Stakes"
      subtitle="What she is holding, and what resolved."
      icon={Anchor}
    >
      {!section.enabled ? (
        <EmptyState title="Organ off" message="The stake ledger flag is off — nothing is being held." />
      ) : section.error ? (
        <EmptyState title="Read failed" message="The stake ledger errored — this is an error, not an empty ledger." />
      ) : empty ? (
        <EmptyState title="Honest zero" message="The ledger is on but holds nothing in this window." />
      ) : (
        <div className="flex flex-col gap-4">
          {section.open.length > 0 ? (
            <div>
              <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">
                open ({section.open.length})
              </div>
              <ul className="mt-2 flex flex-col gap-2">
                {section.open.map((stake) => (
                  <li
                    key={stake.id}
                    className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[10px] font-mono uppercase tracking-widest text-brand-300/70">
                        {stake.kind}
                      </span>
                      <span className="text-[10px] text-white/30">
                        {formatRelativeTime(stake.created_at * 1000)}
                      </span>
                    </div>
                    <p className="mt-1.5 text-sm leading-relaxed text-white/75">{stake.stake}</p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {section.resolved.length > 0 ? (
            <div>
              <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">
                resolved ({section.resolved.length})
              </div>
              <ul className="mt-2 flex flex-col gap-2">
                {section.resolved.map((stake) => (
                  <li
                    key={stake.id}
                    className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[10px] font-mono uppercase tracking-widest text-emerald-300/60">
                        {stake.kind}
                      </span>
                      <span className="text-[10px] text-white/30">
                        {stake.resolved_at ? formatRelativeTime(stake.resolved_at * 1000) : "—"}
                      </span>
                    </div>
                    <p className="mt-1.5 text-sm leading-relaxed text-white/60">{stake.stake}</p>
                    {stake.resolution ? (
                      <p className="mt-1 text-sm leading-relaxed text-emerald-200/60">
                        {stake.resolution}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </AgentPanel>
  );
}

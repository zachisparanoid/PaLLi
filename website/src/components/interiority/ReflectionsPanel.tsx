"use client";

import { MoonStar } from "lucide-react";

import { AgentPanel, EmptyState } from "@/components/dashboard/v2/agentControlShared";
import { formatRelativeTime } from "@/lib/agentControl";
import type { InteriorityOverview } from "@/lib/types";

export function ReflectionsPanel({
  section,
}: {
  section: InteriorityOverview["reflections"];
}) {
  return (
    <AgentPanel
      title="Night reflections"
      subtitle="What each day meant, written by the night cycle."
      icon={MoonStar}
    >
      {!section.enabled ? (
        <EmptyState title="Organ off" message="The night cycle flag is off — no reflections are being written." />
      ) : section.error ? (
        <EmptyState title="Read failed" message="The reflections store errored — this is an error, not an empty record." />
      ) : section.items.length === 0 ? (
        <EmptyState title="Honest zero" message="The night cycle is on but has not written anything in this window." />
      ) : (
        <ul className="flex flex-col gap-3">
          {section.items.map((item) => (
            <li
              key={`${item.date}-${item.created_at}`}
              className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] font-mono uppercase tracking-widest text-white/35">
                  {item.date}
                </span>
                <span className="text-[10px] text-white/30">
                  {formatRelativeTime(item.created_at * 1000)}
                </span>
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-white/75">{item.text}</p>
            </li>
          ))}
        </ul>
      )}
    </AgentPanel>
  );
}

"use client";

import { GitBranch } from "lucide-react";

import { AgentPanel, EmptyState } from "@/components/dashboard/v2/agentControlShared";
import { formatRelativeTime } from "@/lib/agentControl";
import type { InteriorityOverview } from "@/lib/types";

export function ThreadsPanel({
  section,
}: {
  section: InteriorityOverview["threads"];
}) {
  return (
    <AgentPanel
      title="Open threads"
      subtitle="Topics she is keeping alive."
      icon={GitBranch}
    >
      {section.error ? (
        <EmptyState title="Read failed" message="The open-threads reader errored — this is an error, not an empty list." />
      ) : section.items.length === 0 ? (
        <EmptyState title="Nothing open" message="No active threads in the record." />
      ) : (
        <ul className="flex flex-col gap-2">
          {section.items.map((thread) => (
            <li
              key={`${thread.topic_summary}-${thread.created_at}`}
              className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm leading-relaxed text-white/80">{thread.topic_summary}</p>
                <span className="shrink-0 text-[10px] text-white/30">
                  {formatRelativeTime(thread.updated_at * 1000)}
                </span>
              </div>
              <p className="mt-1 text-sm leading-relaxed text-white/50">{thread.why_it_matters}</p>
            </li>
          ))}
        </ul>
      )}
    </AgentPanel>
  );
}

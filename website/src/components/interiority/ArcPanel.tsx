"use client";

import type { JSX } from "react";
import { HeartPulse } from "lucide-react";

import { AgentPanel, EmptyState } from "@/components/dashboard/v2/agentControlShared";
import { formatRelativeTime } from "@/lib/agentControl";
import type { InteriorityOverview } from "@/lib/types";

function intensityBar(value: number, tone: "brand" | "rose"): JSX.Element {
  const clamped = Math.max(0, Math.min(1, value));
  const color = tone === "rose" ? "bg-rose-400/70" : "bg-brand-400/70";
  return (
    <span className="inline-block h-1 w-16 overflow-hidden rounded-full bg-white/10 align-middle">
      <span
        className={`block h-full ${color}`}
        style={{ width: `${Math.round(clamped * 100)}%` }}
      />
    </span>
  );
}

export function ArcPanel({
  section,
}: {
  section: InteriorityOverview["timeline"];
}) {
  return (
    <AgentPanel
      title="Emotional arc"
      subtitle="How she read you, turn by turn."
      icon={HeartPulse}
    >
      {!section.enabled ? (
        <EmptyState title="Organ off" message="Caring memory is off — no reads are being recorded." />
      ) : section.error ? (
        <EmptyState title="Read failed" message="The timeline reader errored — this is an error, not an empty arc." />
      ) : section.items.length === 0 ? (
        <EmptyState title="Honest zero" message="Caring memory is on but recorded nothing in this window." />
      ) : (
        <ul className="flex flex-col gap-2">
          {section.items.map((item) => (
            <li
              key={item.created_at}
              className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] text-white/30">
                  {formatRelativeTime(item.created_at * 1000)}
                </span>
                <span className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-white/35">
                  weight {intensityBar(item.weight, "brand")}
                  {item.needs_support > 0 ? (
                    <>support {intensityBar(item.needs_support, "rose")}</>
                  ) : null}
                </span>
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-white/75">{item.read_text}</p>
            </li>
          ))}
        </ul>
      )}
    </AgentPanel>
  );
}

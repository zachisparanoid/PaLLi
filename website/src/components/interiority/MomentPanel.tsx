"use client";

import { Aperture } from "lucide-react";

import { AgentPanel, EmptyState } from "@/components/dashboard/v2/agentControlShared";
import { formatRelativeTime } from "@/lib/agentControl";
import type { InteriorityOverview } from "@/lib/types";

function formatAge(ageSeconds: number): string {
  const s = Math.max(0, Math.round(ageSeconds));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.round(h / 24);
  return `${d}d`;
}

function impulseLine(impulse: { kind?: string; why?: string }): string {
  const kind = impulse.kind || "none";
  if (kind === "none") return "would have: none — quiet attention";
  const why = (impulse.why || "").trim();
  return `would have: ${kind}${why ? ` — ${why}` : ""}`;
}

export function MomentPanel({
  section,
}: {
  section: InteriorityOverview["moment"];
}) {
  const current = section.current;
  const empty = !current && section.recent.length === 0;

  return (
    <AgentPanel
      title="The Moment"
      subtitle="Where she is standing, right now."
      icon={Aperture}
    >
      {!section.enabled ? (
        <EmptyState
          title="Organ off"
          message="The present moment flag is off — no moments are being written."
        />
      ) : section.error ? (
        <EmptyState
          title="Read failed"
          message="The moment store errored — this is an error, not an empty present."
        />
      ) : empty ? (
        <EmptyState
          title="Honest zero"
          message="No moments yet — she hasn't stood in the present."
        />
      ) : (
        <div className="flex flex-col gap-4">
          {current ? (
            <div className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-white/85">
                {current.text}
              </p>
              {current.undercurrent ? (
                <p className="mt-2 text-xs italic text-white/45">
                  underneath: {current.undercurrent}
                </p>
              ) : null}
              <div className="mt-2 text-[10px] font-mono uppercase tracking-widest text-white/35">
                written {formatAge(current.age_seconds)} ago
                {current.stale ? " · stale" : ""}
              </div>
              {current.woke_on.length > 0 ? (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {current.woke_on.map((w, idx) => (
                    <span
                      key={`${w.kind}-${idx}`}
                      className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] font-mono uppercase tracking-widest text-white/50"
                    >
                      {w.kind}
                      {w.count && w.count > 1 ? ` ×${w.count}` : ""}
                    </span>
                  ))}
                </div>
              ) : null}
              <p className="mt-2 text-xs text-white/55">{impulseLine(current.impulse)}</p>
              {Object.keys(current.sense_health).length > 0 ? (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {Object.entries(current.sense_health).map(([sense, status]) => (
                    <span key={sense} className="inline-flex items-center gap-1.5" title={status}>
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${
                          status === "ok" ? "bg-emerald-400/80" : "bg-amber-400/80"
                        }`}
                      />
                      <span className="text-[10px] font-mono uppercase tracking-widest text-white/35">
                        {sense}
                      </span>
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-white/40">
              No moment in the last 14 days — the record below is older.
            </p>
          )}

          {section.recent.length > 0 ? (
            <div>
              <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">
                recent ({section.recent.length})
              </div>
              <ul className="mt-2 flex flex-col gap-2">
                {section.recent.map((entry) => {
                  const preview =
                    entry.text.length > 140 ? `${entry.text.slice(0, 140)}…` : entry.text;
                  const isNone = !entry.impulse || entry.impulse === "none";
                  return (
                    <li
                      key={entry.ts}
                      className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[10px] text-white/30">
                          {formatRelativeTime(entry.ts * 1000)}
                        </span>
                        <span
                          className={`text-[10px] font-mono uppercase tracking-widest ${
                            isNone ? "text-white/25" : "text-brand-300/80"
                          }`}
                        >
                          {entry.impulse || "none"}
                        </span>
                      </div>
                      {entry.impulse_why ? (
                        <p className="mt-1 text-xs italic text-white/45">{entry.impulse_why}</p>
                      ) : null}
                      <p className="mt-1.5 text-sm leading-relaxed text-white/65">{preview}</p>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </AgentPanel>
  );
}

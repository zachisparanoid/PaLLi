"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, ShieldAlert } from "lucide-react";

import { OrganChips } from "@/components/interiority/OrganChips";
import { SincePicker } from "@/components/interiority/SincePicker";
import { MomentPanel } from "@/components/interiority/MomentPanel";
import { WantingPanel } from "@/components/interiority/WantingPanel";
import { WorkbenchPanel } from "@/components/interiority/WorkbenchPanel";
import { ReflectionsPanel } from "@/components/interiority/ReflectionsPanel";
import { StakesPanel } from "@/components/interiority/StakesPanel";
import { ArcPanel } from "@/components/interiority/ArcPanel";
import { ThreadsPanel } from "@/components/interiority/ThreadsPanel";
import { MonologuePanel } from "@/components/interiority/MonologuePanel";
import { EmptyState } from "@/components/dashboard/v2/agentControlShared";
import { apiGet } from "@/lib/api";
import type { InteriorityOverview } from "@/lib/types";

const DAY_SECONDS = 86400;

export function InteriorityConsole() {
  const [sinceDays, setSinceDays] = useState<number>(30);
  const [sinceEpoch, setSinceEpoch] = useState<number | null>(null);
  const [overview, setOverview] = useState<InteriorityOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const since = useMemo(() => {
    if (sinceEpoch !== null) return sinceEpoch;
    return Date.now() / 1000 - sinceDays * DAY_SECONDS;
  }, [sinceDays, sinceEpoch]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await apiGet<InteriorityOverview>(
        `/api/web/interiority/overview?since=${encodeURIComponent(since)}`,
      );
      setOverview(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load the inner record.");
    } finally {
      setLoading(false);
    }
  }, [since]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !overview) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center text-white/25">
        <Loader2 className="h-7 w-7 animate-spin" />
      </div>
    );
  }

  if (!overview) {
    return (
      <EmptyState
        title="Inner record unavailable"
        message={error ?? "The overview endpoint returned nothing."}
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <OrganChips organs={overview.organs} />
        <SincePicker
          sinceDays={sinceDays}
          sinceEpoch={sinceEpoch}
          organs={overview.organs}
          onPickDays={(days) => {
            setSinceEpoch(null);
            setSinceDays(days);
          }}
          onPickEpoch={(epoch) => setSinceEpoch(epoch)}
        />
      </div>

      {error ? (
        <div className="flex items-center gap-2 rounded-xl border border-rose-500/20 bg-rose-500/5 px-3 py-2 text-xs text-rose-200/80">
          <ShieldAlert className="h-3.5 w-3.5" />
          {error}
        </div>
      ) : null}

      <MomentPanel section={overview.moment} />
      <WantingPanel section={overview.wanting} />

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <ReflectionsPanel section={overview.reflections} />
        <StakesPanel section={overview.stakes} />
        <ArcPanel section={overview.timeline} />
        <ThreadsPanel section={overview.threads} />
        <WorkbenchPanel section={overview.workbench} />
      </div>

      <MonologuePanel since={overview.since} />
    </div>
  );
}

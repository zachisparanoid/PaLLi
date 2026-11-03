"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BookLock, BookOpen, NotebookPen } from "lucide-react";

import {
  AgentPanel,
  EmptyState,
  SmallButton,
} from "@/components/dashboard/v2/agentControlShared";
import { formatTimestamp } from "@/lib/agentControl";
import { apiGet } from "@/lib/api";
import type { InteriorityMonologue } from "@/lib/types";

const DAY_SECONDS = 86400;

function densityBuckets(
  entries: InteriorityMonologue["entries"],
  since: number,
): { day: string; count: number }[] {
  // Anchor at the NEWEST end: never chart more than the latest 60 days, and
  // always include today, even when `since` isn't day-aligned.
  const nowSec = Date.now() / 1000;
  const start = Math.max(since, nowSec - 60 * DAY_SECONDS);
  const startOfDay = Math.floor(start / DAY_SECONDS) * DAY_SECONDS;
  const todayKey = new Date(nowSec * 1000).toISOString().slice(0, 10);
  const buckets = new Map<string, number>();
  let cursor = startOfDay;
  let key = new Date(cursor * 1000).toISOString().slice(0, 10);
  while (key <= todayKey) {
    buckets.set(key, 0);
    cursor += DAY_SECONDS;
    key = new Date(cursor * 1000).toISOString().slice(0, 10);
  }
  for (const entry of entries) {
    const entryKey = new Date(entry.ts * 1000).toISOString().slice(0, 10);
    if (buckets.has(entryKey)) buckets.set(entryKey, (buckets.get(entryKey) ?? 0) + 1);
  }
  return Array.from(buckets.entries()).map(([day, count]) => ({ day, count }));
}

export function MonologuePanel({ since }: { since: number }) {
  const [raw, setRaw] = useState(false); // per-visit only — never persisted
  const [payload, setPayload] = useState<InteriorityMonologue | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (rawMode: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<InteriorityMonologue>(
        `/api/web/interiority/monologue?since=${encodeURIComponent(since)}&raw=${rawMode ? 1 : 0}`,
      );
      setPayload(data);
    } catch (cause) {
      setPayload(null);
      setError(cause instanceof Error ? cause.message : "Failed to load the monologue shape.");
    } finally {
      setLoading(false);
    }
  }, [since]);

  useEffect(() => {
    void load(raw);
  }, [load, raw]);

  const buckets = useMemo(
    () => (payload ? densityBuckets(payload.entries, payload.since) : []),
    [payload],
  );
  const maxBucket = useMemo(
    () => Math.max(1, ...buckets.map((b) => b.count)),
    [buckets],
  );
  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of payload?.entries ?? []) {
      if (!entry.tag) continue;
      counts.set(entry.tag, (counts.get(entry.tag) ?? 0) + 1);
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  }, [payload]);

  function toggleDiary() {
    if (raw) {
      setRaw(false);
      return;
    }
    if (window.confirm("Her private record — read gently. Open the raw diary view?")) {
      setRaw(true);
    }
  }

  return (
    <AgentPanel
      title="Private monologue"
      subtitle={
        raw
          ? "Her private record — read gently."
          : "Presence, not content: the shape of her inner life without printing it."
      }
      icon={raw ? BookOpen : NotebookPen}
      actions={
        <SmallButton onClick={toggleDiary} tone={raw ? "brand" : "neutral"}>
          <span className="inline-flex items-center gap-1.5">
            <BookLock className="h-3.5 w-3.5" />
            {raw ? "Close diary" : "Open diary"}
          </span>
        </SmallButton>
      }
    >
      {loading && !payload ? (
        <div className="text-sm text-white/35">Loading…</div>
      ) : error || !payload ? (
        <EmptyState title="Monologue unavailable" message={error ?? "No payload."} />
      ) : payload.error ? (
        <EmptyState title="Read failed" message="The monologue store errored — this is an error, not silence." />
      ) : payload.count === 0 ? (
        <EmptyState title="Quiet window" message="No inner-monologue entries in this window." />
      ) : (
        <div className="flex flex-col gap-4">
          <div>
            <div className="flex items-center justify-between text-[10px] font-mono uppercase tracking-widest text-white/35">
              <span>
                {payload.count} thoughts in window
                {payload.count > payload.entries.length
                  ? ` · showing latest ${payload.entries.length}`
                  : ""}
              </span>
              <span>
                digests: {Object.entries(payload.digests)
                  .map(([tier, count]) => `${tier} ${count}`)
                  .join(" · ")}
              </span>
            </div>
            <div className="mt-2 flex h-10 items-end gap-[2px]">
              {buckets.map((bucket) => (
                <div
                  key={bucket.day}
                  title={`${bucket.day}: ${bucket.count}`}
                  className="min-w-[3px] flex-1 rounded-t bg-brand-400/50"
                  style={{ height: `${Math.max(6, (bucket.count / maxBucket) * 100)}%`, opacity: bucket.count === 0 ? 0.15 : 1 }}
                />
              ))}
            </div>
          </div>

          {tagCounts.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              {tagCounts.map(([tag, count]) => (
                <span
                  key={tag}
                  className="rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-1 text-[10px] font-mono uppercase tracking-widest text-white/55"
                >
                  {tag} · {count}
                </span>
              ))}
            </div>
          ) : null}

          {raw && payload.raw ? (
            <ul className="flex flex-col gap-2">
              {payload.entries.map((entry) => (
                <li
                  key={entry.ts}
                  className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[10px] text-white/30">
                      {formatTimestamp(entry.ts * 1000)}
                    </span>
                    {entry.tag ? (
                      <span className="text-[10px] font-mono uppercase tracking-widest text-white/35">
                        {entry.tag}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-1.5 whitespace-pre-wrap text-sm leading-relaxed text-white/75">
                    {entry.thought ?? ""}
                  </p>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </AgentPanel>
  );
}

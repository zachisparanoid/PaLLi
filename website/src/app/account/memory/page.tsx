"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, Loader2, Search } from "lucide-react";

import { Navbar } from "@/components/site/navbar";
import { Footer } from "@/components/site/footer";
import { Container } from "@/components/ui/container";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiGet } from "@/lib/api";
import { devLogError } from "@/lib/devLog";
import { useLocalMode } from "@/lib/useLocalMode";

type MemoryResult = {
  item_id: string;
  source_kind: string;
  title: string;
  snippet: string;
  salience: string;
  related_refs: string[];
  updated_at?: number | null;
  score?: number;
  score_explanation?: string;
};

type MemoryOverview = {
  counts: Record<string, number>;
  capability: {
    mode: string;
    local_embeddings_ready?: boolean;
    remote_fallback_enabled?: boolean;
  };
  recent_captures: MemoryResult[];
  days: Array<{ date: string; entry_count: number; latest_summary?: string }>;
};

type MemoryDocs = {
  memory: {
    path: string;
    sections: Record<string, string>;
  };
  memory_summary: {
    path: string;
    frontmatter: Record<string, unknown>;
    sections: Record<string, string>;
  };
};

type DayEntry = {
  entry_id: string;
  summary: string;
  why_it_matters: string;
  salience: string;
  tags: string[];
  related_refs: string[];
  timestamp?: string;
};

type DayPayload = {
  date: string;
  entries: DayEntry[];
  markdown: string;
};

type SearchPayload = {
  query: string;
  mode: string;
  results: MemoryResult[];
  count: number;
  capability: {
    mode: string;
  };
};

type MemoryDocumentCardProps = {
  eyebrow: string;
  title: string;
  path?: string;
  sections: Array<[string, string]>;
  emptyState: string;
};

function formatTimestamp(value?: number | null): string {
  if (!value) return "Unknown";
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString();
}

function formatSourceKind(value?: string): string {
  const cleaned = String(value || "memory").replace(/_/g, " ").trim();
  if (!cleaned) return "Memory";
  return cleaned
    .split(/\s+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function MemoryDocumentCard({ eyebrow, title, path, sections, emptyState }: MemoryDocumentCardProps) {
  return (
    <div className="rounded-xl border border-white/10 bg-black/20 p-4">
      <div className="text-xs uppercase tracking-[0.22em] text-white/35">{eyebrow}</div>
      <div className="mt-2 text-lg font-semibold text-white/90">{title}</div>
      {path ? <div className="mt-2 text-xs text-white/45">{path}</div> : null}
      <div className="mt-4 space-y-4">
        {sections.length ? (
          sections.map(([section, body]) => (
            <div key={section} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
              <div className="text-sm font-medium text-white/85">{section}</div>
              <pre className="mt-3 whitespace-pre-wrap text-sm leading-6 text-white/65">{body}</pre>
            </div>
          ))
        ) : (
          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 text-sm text-white/55">{emptyState}</div>
        )}
      </div>
    </div>
  );
}

export default function AccountMemoryPage() {
  // First door, task 4 (2026-09-17): this workspace stays in every mode, but the
  // way back to the SaaS-era owner page does not exist on a self-hosted install.
  const showOwnerControls = useLocalMode() === false;
  const [overview, setOverview] = useState<MemoryOverview | null>(null);
  const [docs, setDocs] = useState<MemoryDocs | null>(null);
  const [selectedDay, setSelectedDay] = useState<string>("");
  const [dayPayload, setDayPayload] = useState<DayPayload | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<SearchPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    async function load() {
      try {
        const [overviewData, docsData] = await Promise.all([
          apiGet<MemoryOverview>("/api/web/account/memory/overview"),
          apiGet<MemoryDocs>("/api/web/account/memory/docs"),
        ]);

        if (!mounted) return;

        setOverview(overviewData);
        setDocs(docsData);

        const newestDay = overviewData.days?.[0]?.date || "";
        setSelectedDay(newestDay);

        if (newestDay) {
          const dayData = await apiGet<DayPayload>(`/api/web/account/memory/day/${newestDay}`);
          if (!mounted) return;
          setDayPayload(dayData);
        }
      } catch (err) {
        devLogError(err);
        if (!mounted) return;
        setError(err instanceof Error ? err.message : "Failed to load memory workspace.");
      } finally {
        if (mounted) setLoading(false);
      }
    }

    void load();
    return () => {
      mounted = false;
    };
  }, []);

  async function loadDay(day: string) {
    setSelectedDay(day);
    try {
      const payload = await apiGet<DayPayload>(`/api/web/account/memory/day/${day}`);
      setDayPayload(payload);
    } catch (err) {
      devLogError(err);
      setError(err instanceof Error ? err.message : "Failed to load memory day.");
    }
  }

  async function runSearch() {
    setSearching(true);
    try {
      const payload = await apiGet<SearchPayload>(`/api/web/account/memory/search?q=${encodeURIComponent(query.trim())}`);
      setSearch(payload);
    } catch (err) {
      devLogError(err);
      setError(err instanceof Error ? err.message : "Failed to search memory.");
    } finally {
      setSearching(false);
    }
  }

  const recentCaptures = overview?.recent_captures || [];
  const capturedDays = overview?.days || [];
  const workflowContinuity = recentCaptures.filter((item) =>
    ["workflow_memory", "followthrough_item", "standing_order_run"].includes(String(item.source_kind || ""))
  );
  const totalMemoryItems = useMemo(() => {
    return Object.values(overview?.counts || {}).reduce((sum, value) => sum + value, 0);
  }, [overview]);
  const recallMode = String(search?.mode || overview?.capability?.mode || "lexical_only");
  const curatedMemorySections = Object.entries(docs?.memory?.sections || {});
  const livingSummarySections = Object.entries(docs?.memory_summary?.sections || {});

  return (
    <div className="min-h-screen bg-[#070b14] text-white">
      <Navbar />
      <main className="pb-20 pt-14">
        <Container className="space-y-6">
          <section className="rounded-3xl border border-brand-500/15 bg-brand-500/5 p-6 shadow-[0_0_60px_-32px_rgba(56,189,248,0.55)]">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
              <div className="max-w-3xl">
                <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-brand-300/70">
                  Owner Controls
                </div>
                <h1 className="mt-3 text-4xl font-semibold tracking-tight text-white">Memory workspace</h1>
                <p className="mt-3 text-sm leading-6 text-white/65">
                  Inspect what PaLLi is carrying forward over time here. Mission control only mirrors the latest
                  captures, owner controls handle maintenance, and the identity workspace keeps the shaping documents
                  that influence recall, follow-through, and continuity separate.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {showOwnerControls ? (
                    <Link href="/account">
                      <Button>
                        <ArrowLeft className="h-4 w-4" />
                        Back to owner controls
                      </Button>
                    </Link>
                  ) : null}
                  <Link href="/dashboard">
                    <Button variant="secondary">
                      Open mission control
                      <ArrowRight className="h-4 w-4" />
                    </Button>
                  </Link>
                  <Link href="/account/identity">
                    <Button variant="secondary">Open identity workspace</Button>
                  </Link>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-3 lg:max-w-[760px]">
                <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-4">
                  <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Tracked recall items</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{totalMemoryItems}</div>
                  <div className="mt-2 text-sm text-white/60">
                    Durable memory and continuity records currently visible to the owner workspace.
                  </div>
                </div>
                <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-4">
                  <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Captured days</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{capturedDays.length}</div>
                  <div className="mt-2 text-sm text-white/60">
                    Daily logs PaLLi can pull from later when recall or reflection needs a wider window.
                  </div>
                </div>
                <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-4">
                  <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Recall mode</div>
                  <div className="mt-2 text-2xl font-semibold text-white">{recallMode}</div>
                  <div className="mt-2 text-sm text-white/60">
                    The current owner-facing lookup path for continuity search and durable recall inspection.
                  </div>
                </div>
              </div>
            </div>
          </section>

          {loading ? (
            <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-6 text-white/70">
              <Loader2 className="h-5 w-5 animate-spin" />
              Loading memory workspace...
            </div>
          ) : error ? (
            <div className="rounded-2xl border border-red-400/20 bg-red-400/10 p-6 text-sm text-red-100">{error}</div>
          ) : (
            <div className="space-y-6">
              <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                <div className="flex flex-wrap items-end gap-3">
                  <div className="min-w-[260px] flex-1">
                    <div className="mb-2 text-xs uppercase tracking-[0.22em] text-white/35">Continuity Search</div>
                    <p className="mb-3 max-w-2xl text-sm leading-6 text-white/55">
                      Search daily captures, workflow carry-forward, and curated recall when you need the full owner
                      memory surface instead of the lighter mission-control preview.
                    </p>
                    <div className="flex gap-2">
                      <Input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Search daily captures, workflow carry-forward, or durable recall..."
                        className="bg-black/30 text-white"
                      />
                      <Button onClick={runSearch} disabled={!query.trim() || searching}>
                        {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                      </Button>
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-2 text-xs text-white/70">
                    Recall mode: {recallMode}
                  </div>
                </div>
                {Array.isArray(search?.results) && search.results.length ? (
                  <div className="mt-4 grid gap-3">
                    {search.results.map((item) => (
                      <div key={item.item_id} className="rounded-xl border border-white/10 bg-black/20 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="text-sm font-medium text-white/90">
                            {item.title || formatSourceKind(item.source_kind)}
                          </div>
                          <div className="text-xs text-white/45">
                            {[formatSourceKind(item.source_kind), item.salience, item.score_explanation].filter(Boolean).join(" / ")}
                          </div>
                        </div>
                        <div className="mt-2 text-sm text-white/75">{item.snippet}</div>
                        {item.related_refs?.length ? (
                          <div className="mt-2 text-xs text-white/45">Related: {item.related_refs.join(", ")}</div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : search && query.trim() ? (
                  <div className="mt-4 text-sm text-white/55">No matching continuity items yet.</div>
                ) : null}
              </section>

              <section className="grid gap-6 lg:grid-cols-2">
                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-xs uppercase tracking-[0.22em] text-white/35">Recent Continuity</div>
                      <h2 className="mt-2 text-xl font-semibold">Latest Carry-Forward</h2>
                    </div>
                    <div className="text-xs text-white/45">{recallMode}</div>
                  </div>
                  <div className="mt-4 space-y-3">
                    {recentCaptures.length ? (
                      recentCaptures.slice(0, 8).map((item) => (
                        <div key={item.item_id} className="rounded-xl border border-white/10 bg-black/20 p-3">
                          <div className="flex items-center justify-between gap-2 text-xs text-white/45">
                            <span>{formatSourceKind(item.source_kind)}</span>
                            <span>{item.updated_at ? formatTimestamp(item.updated_at) : "recent"}</span>
                          </div>
                          <div className="mt-2 text-sm text-white/80">{item.snippet}</div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-sm text-white/55">
                        New captures will show up here as PaLLi records carry-forward context from conversation,
                        reminders, and workflow work.
                      </div>
                    )}
                  </div>
                </div>

                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/35">Workflow Continuity</div>
                  <h2 className="mt-2 text-xl font-semibold">Workflow Carry-Forward</h2>
                  <div className="mt-4 space-y-3">
                    {workflowContinuity.length ? (
                      workflowContinuity.slice(0, 6).map((item) => (
                        <div key={item.item_id} className="rounded-xl border border-white/10 bg-black/20 p-3">
                          <div className="text-xs text-white/45">{formatSourceKind(item.source_kind)}</div>
                          <div className="mt-2 text-sm text-white/80">{item.snippet}</div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-sm text-white/55">
                        Workflow memories, follow-through items, and standing-order summaries will surface here once
                        PaLLi starts carrying those threads forward.
                      </div>
                    )}
                  </div>
                </div>
              </section>

              <section className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/35">Daily Memory Log</div>
                  <h2 className="mt-2 text-xl font-semibold">Captured Days</h2>
                  <div className="mt-4 space-y-2">
                    {capturedDays.length ? (
                      capturedDays.map((day) => (
                        <button
                          key={day.date}
                          type="button"
                          onClick={() => loadDay(day.date)}
                          className={`w-full rounded-xl border px-3 py-2 text-left text-sm transition-colors ${
                            selectedDay === day.date
                              ? "border-sky-400/30 bg-sky-400/10 text-white"
                              : "border-white/10 bg-black/20 text-white/70 hover:bg-white/5"
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span>{day.date}</span>
                            <span className="text-xs text-white/45">{day.entry_count}</span>
                          </div>
                          {day.latest_summary ? <div className="mt-1 text-xs text-white/45">{day.latest_summary}</div> : null}
                        </button>
                      ))
                    ) : (
                      <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-sm text-white/55">
                        No day logs are available yet.
                      </div>
                    )}
                  </div>
                </div>

                <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/35">Daily Memory Log</div>
                  <h2 className="mt-2 text-xl font-semibold">{selectedDay || "No day selected"}</h2>
                  <p className="mt-3 max-w-2xl text-sm leading-6 text-white/55">
                    Each day log keeps the owner-facing continuity record that PaLLi can later pull into reminders,
                    workflow follow-through, and longer-lived recall.
                  </p>
                  <div className="mt-4 space-y-3">
                    {(dayPayload?.entries || []).length ? (
                      dayPayload?.entries.map((entry) => (
                        <div key={entry.entry_id} className="rounded-xl border border-white/10 bg-black/20 p-3">
                          <div className="flex items-center justify-between gap-2 text-xs text-white/45">
                            <span>{entry.salience}</span>
                            <span>{entry.timestamp || entry.entry_id}</span>
                          </div>
                          <div className="mt-2 text-sm text-white/85">{entry.summary}</div>
                          <div className="mt-2 text-sm text-white/55">{entry.why_it_matters}</div>
                          {entry.tags?.length ? <div className="mt-2 text-xs text-white/45">Tags: {entry.tags.join(", ")}</div> : null}
                        </div>
                      ))
                    ) : (
                      <div className="rounded-xl border border-white/10 bg-black/20 p-3 text-sm text-white/55">
                        No captured entries for this day yet.
                      </div>
                    )}
                  </div>
                </div>
              </section>

              <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
                <div className="text-xs uppercase tracking-[0.22em] text-white/35">Durable Recall Docs</div>
                <h2 className="mt-2 text-xl font-semibold">Living Summary and Curated Recall</h2>
                <p className="mt-3 max-w-3xl text-sm leading-6 text-white/55">
                  <span className="font-mono text-white/80">MEMORY_SUMMARY.md</span> is the distilled continuity PaLLi
                  is actively carrying forward. <span className="font-mono text-white/80">MEMORY.md</span> holds the
                  curated recall that should stay legible and inspectable over longer stretches of time.
                </p>
                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                  <MemoryDocumentCard
                    eyebrow="Living Summary"
                    title="MEMORY_SUMMARY.md"
                    path={docs?.memory_summary?.path}
                    sections={livingSummarySections}
                    emptyState="No living summary sections are available yet."
                  />
                  <MemoryDocumentCard
                    eyebrow="Curated Recall"
                    title="MEMORY.md"
                    path={docs?.memory?.path}
                    sections={curatedMemorySections}
                    emptyState="No curated memory sections are available yet."
                  />
                </div>
              </section>
            </div>
          )}
        </Container>
      </main>
      <Footer />
    </div>
  );
}

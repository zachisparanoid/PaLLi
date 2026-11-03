"use client";

import Link from "next/link";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Archive, Loader2, MessageSquareText, RefreshCw, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiGet } from "@/lib/api";
import { formatRelativeTime, formatTimestamp } from "@/lib/agentControl";
import { devLogError } from "@/lib/devLog";

type ConversationThreadSummary = {
  thread_id: string;
  thread_kind: string;
  thread_kind_label?: string;
  status: string;
  status_label?: string;
  label?: string;
  workspace_root?: string;
  workspace_label?: string;
  last_activity?: number | null;
  updated_at?: number | null;
  archived_at?: number | null;
  reset_count?: number;
  platform?: string;
  is_archived?: boolean;
};

type ConversationFilterOption = {
  value: string;
  label: string;
};

type ConversationCatalogResponse = {
  section: string;
  status: string;
  counts?: {
    scanned?: number;
    matched?: number;
    returned?: number;
    active?: number;
    archived?: number;
  };
  filters?: {
    statuses?: ConversationFilterOption[];
    thread_kinds?: ConversationFilterOption[];
  };
  items?: ConversationThreadSummary[];
};

type ThreadHistoryEntry = {
  id?: string | number | null;
  role?: string;
  role_label?: string;
  content?: string;
  excerpt?: string;
  timestamp?: number | string | null;
  voice_turn_decision_trace?: {
    behavior?: string;
    behavior_label?: string;
    summary?: string;
    mirror_policy_label?: string;
  } | null;
};

type ThreadHistoryResponse = {
  thread?: ConversationThreadSummary;
  history?: {
    status?: string;
    count?: number;
    limit?: number;
    has_more?: boolean;
    latest_timestamp?: number | null;
    scope?: string;
    anchor_message_id?: string | number | null;
    items?: ThreadHistoryEntry[];
  };
};

type ConversationSearchResult = ThreadHistoryEntry & {
  thread?: ConversationThreadSummary | null;
};

type ConversationSearchResponse = {
  section: string;
  status: string;
  counts?: {
    threads_scanned?: number;
    threads_considered?: number;
    returned?: number;
    matched_threads?: number;
  };
  has_more?: boolean;
  items?: ConversationSearchResult[];
};

const THREAD_LIMIT = 60;
const THREAD_HISTORY_BATCH_SIZE = 12;
const THREAD_HISTORY_MAX_LIMIT = 60;
const CONVERSATION_SEARCH_LIMIT = 24;

function traceSummary(entry: ThreadHistoryEntry): string {
  const trace = entry.voice_turn_decision_trace;
  if (!trace || typeof trace !== "object") return "";
  const summary = String(trace.summary || "").trim();
  if (summary) return summary;
  const behavior = String(trace.behavior_label || trace.behavior || "").trim();
  const mirror = String(trace.mirror_policy_label || "").trim();
  if (behavior && mirror) return `${behavior} • ${mirror}`;
  return behavior || mirror;
}

function threadMetaLine(thread: ConversationThreadSummary): string {
  const parts: string[] = [];
  const kindLabel = String(thread.thread_kind_label || thread.thread_kind || "").trim();
  const statusLabel = String(thread.status_label || thread.status || "").trim();
  const platform = String(thread.platform || "").trim();
  const workspaceLabel = String(thread.workspace_label || "").trim();

  if (kindLabel) parts.push(kindLabel);
  if (statusLabel) parts.push(statusLabel);
  if (platform) parts.push(platform);
  if (workspaceLabel) parts.push(workspaceLabel);

  return parts.join(" • ");
}

function localDateStartTimestamp(value: string): number | null {
  const clean = String(value || "").trim();
  if (!clean) return null;
  const parsed = new Date(`${clean}T00:00:00`);
  const timestamp = parsed.getTime();
  if (!Number.isFinite(timestamp)) return null;
  return Math.floor(timestamp / 1000);
}

function localDateEndTimestamp(value: string): number | null {
  const clean = String(value || "").trim();
  if (!clean) return null;
  const parsed = new Date(`${clean}T23:59:59.999`);
  const timestamp = parsed.getTime();
  if (!Number.isFinite(timestamp)) return null;
  return timestamp / 1000;
}

function normalizeMessageId(value: string | number | null | undefined): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return String(value || "").trim();
}

export function ConversationHistoryWorkspace() {
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [transcriptSearch, setTranscriptSearch] = useState("");
  const deferredTranscriptSearch = useDeferredValue(transcriptSearch);
  const [transcriptStartDate, setTranscriptStartDate] = useState("");
  const [transcriptEndDate, setTranscriptEndDate] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [threadKindFilter, setThreadKindFilter] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [catalog, setCatalog] = useState<ConversationCatalogResponse | null>(null);
  const [catalogBusy, setCatalogBusy] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [conversationSearch, setConversationSearch] = useState<ConversationSearchResponse | null>(null);
  const [conversationSearchBusy, setConversationSearchBusy] = useState(false);
  const [conversationSearchError, setConversationSearchError] = useState("");
  const [selectedThreadId, setSelectedThreadId] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);
  const [threadHistoryById, setThreadHistoryById] = useState<Record<string, ThreadHistoryResponse>>({});
  const [threadHistoryErrors, setThreadHistoryErrors] = useState<Record<string, string>>({});
  const [threadHistoryBusyKey, setThreadHistoryBusyKey] = useState("");
  const historyEntryRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const lastScrolledAnchorKeyRef = useRef("");

  useEffect(() => {
    let cancelled = false;

    async function loadCatalog() {
      setCatalogBusy(true);
      setCatalogError("");
      try {
        const params = new URLSearchParams();
        const trimmedSearch = deferredSearch.trim();
        if (trimmedSearch) params.set("q", trimmedSearch);
        if (statusFilter) params.set("status", statusFilter);
        if (threadKindFilter) params.set("thread_kind", threadKindFilter);
        if (includeArchived) params.set("include_archived", "true");
        params.set("limit", String(THREAD_LIMIT));
        const endpoint = `/api/web/runtime/conversations?${params.toString()}`;
        const response = await apiGet<ConversationCatalogResponse>(endpoint);
        if (cancelled) return;
        setCatalog(response);
        setSelectedThreadId((current) => {
          const items = Array.isArray(response.items) ? response.items : [];
          if (current) {
            return current;
          }
          return String(items[0]?.thread_id || "");
        });
      } catch (error) {
        if (cancelled) return;
        devLogError("Failed to load conversation history catalog", error);
        setCatalog(null);
        setCatalogError(error instanceof Error ? error.message : "Failed to load conversation history.");
        setSelectedThreadId("");
      } finally {
        if (!cancelled) setCatalogBusy(false);
      }
    }

    void loadCatalog();
    return () => {
      cancelled = true;
    };
  }, [deferredSearch, statusFilter, threadKindFilter, includeArchived, refreshTick]);

  useEffect(() => {
    let cancelled = false;

    async function loadConversationSearch() {
      const trimmedSearch = deferredTranscriptSearch.trim();
      const after = localDateStartTimestamp(transcriptStartDate);
      const before = localDateEndTimestamp(transcriptEndDate);
      const hasCriteria = Boolean(trimmedSearch || after || before);
      if (!hasCriteria) {
        setConversationSearch(null);
        setConversationSearchError("");
        setConversationSearchBusy(false);
        return;
      }

      setConversationSearchBusy(true);
      setConversationSearchError("");
      try {
        const params = new URLSearchParams();
        if (trimmedSearch) params.set("q", trimmedSearch);
        if (statusFilter) params.set("status", statusFilter);
        if (threadKindFilter) params.set("thread_kind", threadKindFilter);
        if (includeArchived) params.set("include_archived", "true");
        if (after) params.set("after", String(after));
        if (before) params.set("before", String(before));
        params.set("limit", String(CONVERSATION_SEARCH_LIMIT));
        const endpoint = `/api/web/runtime/conversations/search?${params.toString()}`;
        const response = await apiGet<ConversationSearchResponse>(endpoint);
        if (cancelled) return;
        setConversationSearch(response);
      } catch (error) {
        if (cancelled) return;
        devLogError("Failed to search stored conversation history", error);
        setConversationSearch(null);
        setConversationSearchError(error instanceof Error ? error.message : "Failed to search conversation history.");
      } finally {
        if (!cancelled) setConversationSearchBusy(false);
      }
    }

    void loadConversationSearch();
    return () => {
      cancelled = true;
    };
  }, [
    deferredTranscriptSearch,
    transcriptStartDate,
    transcriptEndDate,
    statusFilter,
    threadKindFilter,
    includeArchived,
    refreshTick,
  ]);

  async function loadThreadHistory(
    threadId: string,
    limit: number,
    aroundMessageId?: string | number | null,
  ) {
    const cleanThreadId = String(threadId || "").trim();
    if (!cleanThreadId) return;
    const normalizedLimit = Math.max(1, Math.min(limit, THREAD_HISTORY_MAX_LIMIT));
    const cleanAroundMessageId = normalizeMessageId(aroundMessageId);
    const busyKey = `${cleanThreadId}:${normalizedLimit}:${cleanAroundMessageId || "latest"}`;
    setThreadHistoryBusyKey(busyKey);
    setThreadHistoryErrors((current) => ({ ...current, [cleanThreadId]: "" }));
    try {
      const params = new URLSearchParams();
      params.set("limit", String(normalizedLimit));
      if (cleanAroundMessageId) params.set("around_message_id", cleanAroundMessageId);
      const response = await apiGet<ThreadHistoryResponse>(
        `/api/web/runtime/threads/${encodeURIComponent(cleanThreadId)}/history?${params.toString()}`,
      );
      setThreadHistoryById((current) => ({ ...current, [cleanThreadId]: response }));
    } catch (error) {
      devLogError(`Failed to load thread history for ${cleanThreadId}`, error);
      const message = error instanceof Error ? error.message : "Failed to load thread history.";
      setThreadHistoryErrors((current) => ({ ...current, [cleanThreadId]: message }));
    } finally {
      setThreadHistoryBusyKey((current) => (current === busyKey ? "" : current));
    }
  }

  useEffect(() => {
    const cleanThreadId = String(selectedThreadId || "").trim();
    if (!cleanThreadId) return;
    if (threadHistoryById[cleanThreadId]) return;
    void loadThreadHistory(cleanThreadId, THREAD_HISTORY_BATCH_SIZE);
  }, [selectedThreadId, threadHistoryById]);

  const items = useMemo(() => (Array.isArray(catalog?.items) ? catalog?.items : []), [catalog?.items]);
  const conversationSearchActive = Boolean(
    deferredTranscriptSearch.trim() || transcriptStartDate || transcriptEndDate,
  );
  const conversationSearchItems = useMemo(
    () => (Array.isArray(conversationSearch?.items) ? conversationSearch.items : []),
    [conversationSearch?.items],
  );
  const conversationSearchThreadsById = useMemo(() => {
    const mapping: Record<string, ConversationThreadSummary> = {};
    for (const item of conversationSearchItems) {
      const threadId = String(item.thread?.thread_id || "").trim();
      if (!threadId || !item.thread) continue;
      mapping[threadId] = item.thread;
    }
    return mapping;
  }, [conversationSearchItems]);
  const selectedThread = useMemo(
    () =>
      items.find((item) => item.thread_id === selectedThreadId) ||
      conversationSearchThreadsById[selectedThreadId] ||
      threadHistoryById[selectedThreadId]?.thread ||
      null,
    [items, selectedThreadId, conversationSearchThreadsById, threadHistoryById],
  );
  const selectedHistory = selectedThreadId ? threadHistoryById[selectedThreadId] : null;
  const selectedHistoryItems = Array.isArray(selectedHistory?.history?.items) ? selectedHistory.history.items : [];
  const selectedHistoryError = selectedThreadId ? String(threadHistoryErrors[selectedThreadId] || "").trim() : "";
  const selectedHistoryLoading = Boolean(selectedThreadId) && threadHistoryBusyKey.startsWith(`${selectedThreadId}:`);
  const selectedHistoryLimit = Number(selectedHistory?.history?.limit || THREAD_HISTORY_BATCH_SIZE);
  const selectedHistoryHasMore = Boolean(selectedHistory?.history?.has_more);
  const selectedHistoryScope = String(selectedHistory?.history?.scope || "latest").trim().toLowerCase() === "anchor"
    ? "anchor"
    : "latest";
  const selectedHistoryAnchorId = normalizeMessageId(selectedHistory?.history?.anchor_message_id || "");
  const statusOptions = Array.isArray(catalog?.filters?.statuses) ? catalog.filters.statuses : [];
  const threadKindOptions = Array.isArray(catalog?.filters?.thread_kinds) ? catalog.filters.thread_kinds : [];

  useEffect(() => {
    const anchorKey =
      selectedThreadId && selectedHistoryScope === "anchor" && selectedHistoryAnchorId
        ? `${selectedThreadId}:${selectedHistoryAnchorId}`
        : "";
    if (!anchorKey) {
      lastScrolledAnchorKeyRef.current = "";
      return;
    }
    if (lastScrolledAnchorKeyRef.current === anchorKey) return;

    const node = historyEntryRefs.current[anchorKey];
    if (!node) return;
    lastScrolledAnchorKeyRef.current = anchorKey;
    window.requestAnimationFrame(() => {
      node.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }, [selectedHistoryAnchorId, selectedHistoryItems.length, selectedHistoryScope, selectedThreadId]);

  function clearConversationSearch() {
    setTranscriptSearch("");
    setTranscriptStartDate("");
    setTranscriptEndDate("");
  }

  function openThread(
    threadId: string,
    options?: {
      historyLimit?: number;
      aroundMessageId?: string | number | null;
    },
  ) {
    const cleanThreadId = String(threadId || "").trim();
    if (!cleanThreadId) return;
    const historyLimit = options?.historyLimit ?? THREAD_HISTORY_BATCH_SIZE;
    const cleanAroundMessageId = normalizeMessageId(options?.aroundMessageId);
    lastScrolledAnchorKeyRef.current = "";
    setSelectedThreadId(cleanThreadId);
    void loadThreadHistory(cleanThreadId, historyLimit, cleanAroundMessageId || undefined);
  }

  return (
    <section className="rounded-3xl border border-white/10 bg-black/20 p-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="text-[11px] uppercase tracking-[0.24em] text-white/35">Stored Transcript Review</div>
          <h2 className="mt-2 text-2xl font-medium text-white">Review stored transcripts by thread</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">
            Use this lane when you need to verify what PaLLi actually stored after the fact. Filter the saved thread
            catalog, inspect archived lanes, and open matched exchanges without treating this like a live runtime
            inspector.
          </p>
        </div>
        <Button variant="secondary" onClick={() => setRefreshTick((current) => current + 1)} disabled={catalogBusy}>
          {catalogBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </Button>
      </div>

      <div className="mt-5 grid gap-3 lg:grid-cols-3">
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-4">
          <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Stored record lane</div>
          <p className="mt-2 text-sm leading-6 text-white/60">
            Use this workspace to confirm the saved exchange, inspect archived threads, and review the voice decision
            trace PaLLi kept with the transcript.
          </p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-4">
          <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Live runtime truth</div>
          <p className="mt-2 text-sm leading-6 text-white/60">
            Current voice lanes, model routing, runtime drift, and live health still belong in mission control.
          </p>
          <div className="mt-4">
            <Link href="/dashboard">
              <Button variant="secondary" size="sm">Open mission control</Button>
            </Link>
          </div>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-4">
          <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Continuity browsing</div>
          <p className="mt-2 text-sm leading-6 text-white/60">
            For carry-forward, daily captures, and durable recall, use the memory workspace instead of this
            record-by-record transcript lane.
          </p>
          <div className="mt-4">
            <Link href="/account/memory">
              <Button variant="secondary" size="sm">Open memory workspace</Button>
            </Link>
          </div>
        </div>
      </div>

      <div className="mt-5 grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_repeat(3,minmax(0,0.6fr))]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/30" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search thread label, kind, workspace, or id"
            className="pl-10"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(event) => {
            const next = event.target.value;
            setStatusFilter(next);
            if (next === "archived") setIncludeArchived(true);
          }}
          className="h-11 rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
        >
          <option value="" className="bg-slate-950">All statuses</option>
          {statusOptions.map((option) => (
            <option key={option.value} value={option.value} className="bg-slate-950">
              {option.label}
            </option>
          ))}
        </select>
        <select
          value={threadKindFilter}
          onChange={(event) => setThreadKindFilter(event.target.value)}
          className="h-11 rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
        >
          <option value="" className="bg-slate-950">All thread kinds</option>
          {threadKindOptions.map((option) => (
            <option key={option.value} value={option.value} className="bg-slate-950">
              {option.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => setIncludeArchived((current) => !current)}
          className={`rounded-xl border px-3 py-2 text-left text-sm transition ${
            includeArchived
              ? "border-amber-400/30 bg-amber-500/10 text-white"
              : "border-white/10 bg-black/20 text-white/65"
          }`}
        >
          <div className="flex items-center gap-2">
            <Archive className="h-4 w-4" />
            Include archived
          </div>
        </button>
      </div>

      <div className="mt-4 flex flex-wrap gap-3 text-xs uppercase tracking-[0.18em] text-white/40">
        <span>{Number(catalog?.counts?.matched || items.length)} matched</span>
        <span>{Number(catalog?.counts?.active || 0)} active</span>
        <span>{Number(catalog?.counts?.archived || 0)} archived</span>
        <span>{Number(catalog?.counts?.scanned || 0)} scanned</span>
      </div>

      {catalogError ? (
        <div className="mt-4 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          {catalogError}
        </div>
      ) : null}

      <div className="mt-6 rounded-3xl border border-white/10 bg-black/20 p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Stored message search</div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">
              Search saved message text across the filtered thread set, then open the surrounding exchange in the
              stored thread window when you need more context. Live runtime inspection still belongs in mission
              control.
            </p>
          </div>
          <div className="flex flex-wrap gap-3 text-xs uppercase tracking-[0.18em] text-white/40">
            <span>{Number(conversationSearch?.counts?.returned || conversationSearchItems.length)} returned</span>
            <span>{Number(conversationSearch?.counts?.matched_threads || 0)} threads</span>
            <span>{Number(conversationSearch?.counts?.threads_considered || 0)} considered</span>
            {conversationSearch?.has_more ? <span>More available</span> : null}
          </div>
        </div>

        <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_repeat(2,minmax(0,0.5fr))_auto]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/30" />
            <Input
              value={transcriptSearch}
              onChange={(event) => setTranscriptSearch(event.target.value)}
              placeholder="Search stored message text across threads"
              className="pl-10"
            />
          </div>
          <Input type="date" value={transcriptStartDate} onChange={(event) => setTranscriptStartDate(event.target.value)} />
          <Input type="date" value={transcriptEndDate} onChange={(event) => setTranscriptEndDate(event.target.value)} />
          <Button
            variant="secondary"
            onClick={clearConversationSearch}
            disabled={!conversationSearchActive && !conversationSearchError}
          >
            Clear
          </Button>
        </div>

        {conversationSearchError ? (
          <div className="mt-4 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            {conversationSearchError}
          </div>
        ) : null}

        <div className="mt-4 max-h-[20rem] space-y-3 overflow-y-auto pr-1">
          {conversationSearchBusy ? (
            <div className="flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-4 text-sm text-white/55">
              <Loader2 className="h-4 w-4 animate-spin" />
              Searching stored transcript records...
            </div>
          ) : null}

          {!conversationSearchBusy && !conversationSearchActive ? (
            <div className="rounded-2xl border border-dashed border-white/10 px-4 py-6 text-sm text-white/45">
              Add message text or a date range to search across saved transcript entries.
            </div>
          ) : null}

          {!conversationSearchBusy && conversationSearchActive && !conversationSearchError && !conversationSearchItems.length ? (
            <div className="rounded-2xl border border-dashed border-white/10 px-4 py-6 text-sm text-white/45">
              No saved transcript entries matched the current search.
            </div>
          ) : null}

          {!conversationSearchBusy
            ? conversationSearchItems.map((item, index) => {
                const thread = item.thread || null;
                const threadId = String(thread?.thread_id || "").trim();
                const trace = traceSummary(item);
                return (
                  <button
                    key={`${threadId || "search"}:${String(item.id || index)}`}
                    type="button"
                    onClick={() =>
                      openThread(threadId, {
                        historyLimit: THREAD_HISTORY_BATCH_SIZE,
                        aroundMessageId: item.id,
                      })
                    }
                    disabled={!threadId}
                    className="w-full rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3 text-left text-white/80 transition hover:border-white/20 hover:text-white disabled:cursor-default disabled:hover:border-white/10 disabled:hover:text-white/80"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-medium text-white">
                          {thread?.label || threadId || "Conversation thread"}
                        </div>
                        <div className="mt-1 text-xs text-white/45">
                          {thread ? threadMetaLine(thread) : "Stored transcript match"}
                        </div>
                      </div>
                      <div className="text-right text-[11px] uppercase tracking-[0.18em] text-white/35">
                        {item.timestamp ? formatTimestamp(item.timestamp) : "Unknown"}
                      </div>
                    </div>
                    <div className="mt-3 text-sm font-medium text-white/85">
                      {String(item.role_label || item.role || "Message").trim() || "Message"}
                    </div>
                    <div className="mt-2 whitespace-pre-wrap text-sm leading-6 text-white/75">
                      {String(item.content || item.excerpt || "").trim() || "(empty transcript entry)"}
                    </div>
                    {trace ? (
                      <div className="mt-3 rounded-2xl border border-brand-400/15 bg-brand-500/10 px-3 py-2 text-xs text-brand-100/85">
                        Voice decision: {trace}
                      </div>
                    ) : null}
                    {threadId && normalizeMessageId(item.id) ? (
                      <div className="mt-3 text-xs uppercase tracking-[0.18em] text-brand-100/65">
                        Open matched exchange in stored thread window
                      </div>
                    ) : null}
                  </button>
                );
              })
            : null}
        </div>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[0.92fr_1.08fr]">
        <div className="rounded-3xl border border-white/10 bg-black/20 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-white">
            <MessageSquareText className="h-4 w-4 text-brand-300" />
            Stored thread catalog
          </div>
          <div className="max-h-[32rem] space-y-3 overflow-y-auto pr-1">
            {catalogBusy ? (
              <div className="flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-4 text-sm text-white/55">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading stored thread catalog...
              </div>
            ) : null}

            {!catalogBusy && !items.length ? (
              <div className="rounded-2xl border border-dashed border-white/10 px-4 py-6 text-sm text-white/45">
                No stored threads matched the current filters.
              </div>
            ) : null}

            {!catalogBusy
              ? items.map((thread) => {
                  const selected = thread.thread_id === selectedThreadId;
                  const relativeTime = thread.last_activity || thread.updated_at || thread.archived_at;
                  return (
                    <button
                      key={thread.thread_id}
                      type="button"
                      onClick={() => openThread(thread.thread_id)}
                      className={`w-full rounded-2xl border px-4 py-3 text-left transition ${
                        selected
                          ? "border-brand-400/35 bg-brand-500/10 text-white"
                          : "border-white/10 bg-white/[0.03] text-white/75 hover:border-white/20 hover:text-white"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <div className="text-sm font-medium text-white">{thread.label || thread.thread_id}</div>
                          <div className="mt-1 text-xs text-white/45">{threadMetaLine(thread)}</div>
                        </div>
                        <div className="text-right text-[11px] uppercase tracking-[0.18em] text-white/35">
                          {relativeTime ? formatRelativeTime(relativeTime) : "Unknown"}
                        </div>
                      </div>
                      <div className="mt-3 font-mono text-[11px] text-white/35">{thread.thread_id}</div>
                    </button>
                  );
                })
              : null}
          </div>
        </div>

        <div className="rounded-3xl border border-white/10 bg-black/20 p-4">
          {selectedThread ? (
            <>
              <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Stored thread window</div>
                  <h3 className="mt-2 text-xl font-medium text-white">{selectedThread.label || selectedThread.thread_id}</h3>
                  <div className="mt-2 text-sm text-white/55">
                    {threadMetaLine(selectedThread) || "Conversation thread"}
                    {selectedThread.last_activity || selectedThread.updated_at ? (
                      <span> • Updated {formatTimestamp(selectedThread.last_activity || selectedThread.updated_at)}</span>
                    ) : null}
                  </div>
                  <div className="mt-2 font-mono text-[11px] text-white/35">{selectedThread.thread_id}</div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() =>
                      void loadThreadHistory(
                        selectedThread.thread_id,
                        selectedHistoryScope === "anchor" ? selectedHistoryLimit : THREAD_HISTORY_BATCH_SIZE,
                        selectedHistoryScope === "anchor" ? selectedHistoryAnchorId : undefined,
                      )
                    }
                    disabled={selectedHistoryLoading}
                  >
                    {selectedHistoryLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                    Reload window
                  </Button>
                  {selectedHistoryScope === "anchor" ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => openThread(selectedThread.thread_id)}
                      disabled={selectedHistoryLoading}
                    >
                      Jump to newest window
                    </Button>
                  ) : null}
                  {selectedHistoryScope !== "anchor" && selectedHistoryHasMore && selectedHistoryLimit < THREAD_HISTORY_MAX_LIMIT ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() =>
                        void loadThreadHistory(selectedThread.thread_id, selectedHistoryLimit + THREAD_HISTORY_BATCH_SIZE)
                      }
                      disabled={selectedHistoryLoading}
                    >
                      Load older turns
                    </Button>
                  ) : null}
                </div>
              </div>

              {selectedHistoryScope === "anchor" && selectedHistoryAnchorId ? (
                <div className="mt-4 rounded-2xl border border-brand-400/20 bg-brand-500/10 px-4 py-3 text-sm text-brand-100/85">
                  Focused around the matched stored exchange so you can review the surrounding saved turns without
                  jumping back to the newest transcript window.
                </div>
              ) : null}

              {selectedHistoryError ? (
                <div className="mt-4 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
                  {selectedHistoryError}
                </div>
              ) : null}

              <div className="mt-4 max-h-[32rem] space-y-3 overflow-y-auto pr-1">
                {selectedHistoryLoading && !selectedHistoryItems.length ? (
                  <div className="flex items-center gap-2 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-4 text-sm text-white/55">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Loading stored thread window...
                  </div>
                ) : null}

                {!selectedHistoryLoading && !selectedHistoryError && !selectedHistoryItems.length ? (
                  <div className="rounded-2xl border border-dashed border-white/10 px-4 py-6 text-sm text-white/45">
                    No saved transcript detail for this thread yet.
                  </div>
                ) : null}

                {selectedHistoryItems.map((entry, index) => {
                  const roleLabel = String(entry.role_label || entry.role || "Message").trim() || "Message";
                  const rawContent = String(entry.content || entry.excerpt || "").trim();
                  const trace = traceSummary(entry);
                  const entryId = normalizeMessageId(entry.id);
                  const isAnchoredMatch = selectedHistoryScope === "anchor" && entryId && entryId === selectedHistoryAnchorId;
                  return (
                    <div
                      key={`${selectedThread.thread_id}:${String(entry.id || index)}`}
                      ref={(node) => {
                        if (entryId) {
                          historyEntryRefs.current[`${selectedThread.thread_id}:${entryId}`] = node;
                        }
                      }}
                      className={`rounded-2xl border px-4 py-3 ${
                        isAnchoredMatch
                          ? "border-brand-300/40 bg-brand-500/10 shadow-[0_0_0_1px_rgba(255,255,255,0.02)]"
                          : "border-white/10 bg-white/[0.03]"
                      }`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="text-sm font-medium text-white">
                          {roleLabel}
                          {isAnchoredMatch ? (
                            <span className="ml-2 rounded-full border border-brand-300/30 bg-brand-500/15 px-2 py-0.5 text-[10px] uppercase tracking-[0.18em] text-brand-100/85">
                              Search match
                            </span>
                          ) : null}
                        </div>
                        <div className="text-xs uppercase tracking-[0.18em] text-white/35">
                          {entry.timestamp ? formatTimestamp(entry.timestamp) : "Unknown"}
                        </div>
                      </div>
                      <div className="mt-3 whitespace-pre-wrap text-sm leading-6 text-white/80">
                        {rawContent || "(empty transcript entry)"}
                      </div>
                      {trace ? (
                        <div className="mt-3 rounded-2xl border border-brand-400/15 bg-brand-500/10 px-3 py-2 text-xs text-brand-100/85">
                          Voice decision: {trace}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="flex h-full min-h-[20rem] items-center justify-center rounded-2xl border border-dashed border-white/10 px-4 text-center text-sm text-white/45">
              Pick a stored thread from the left to review what PaLLi kept in this lane.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

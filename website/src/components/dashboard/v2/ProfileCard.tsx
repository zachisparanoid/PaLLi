"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, Sparkles, User } from "lucide-react";
import { apiGet } from "@/lib/api";
import { devLogError } from "@/lib/devLog";

type RecentStyle = {
  summary?: string;
  rationale?: string;
  responseGuidance?: string;
  confidence?: number;
  updatedAt?: number;
  windowMessages?: number;
};

type ProfileResponse = {
  name: string;
  personalityPreset: string;
  recentStyle?: RecentStyle | null;
};

function formatTimeAgo(epochSeconds: number) {
  const now = Date.now() / 1000;
  const delta = Math.max(0, now - epochSeconds);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString();
}

export function ProfileCard() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);

  const fetchProfile = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = Boolean(opts?.silent);
    if (!silent) setLoading(true);
    setRefreshing(silent);
    try {
      const data = await apiGet<ProfileResponse>("/api/web/account/profile");
      setProfile(data);
      setError(null);
    } catch (e) {
      devLogError("Failed to load profile snapshot", e);
      setError("Failed to load profile snapshot");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  // Refresh after settings save (BotControls) or manual triggers elsewhere.
  useEffect(() => {
    const onRefresh = () => fetchProfile({ silent: true });
    window.addEventListener("refresh_profile_card", onRefresh);
    window.addEventListener("refresh_memory_galaxy", onRefresh);
    return () => {
      window.removeEventListener("refresh_profile_card", onRefresh);
      window.removeEventListener("refresh_memory_galaxy", onRefresh);
    };
  }, [fetchProfile]);

  const recent = profile?.recentStyle || null;
  const hasRecent =
    Boolean(recent?.summary) ||
    Boolean(recent?.rationale) ||
    Boolean(recent?.responseGuidance);

  return (
    <div className="flex flex-col h-full bg-black/40 border border-white/5 rounded-2xl backdrop-blur-md overflow-hidden shadow-xl">
      <div className="h-12 border-b border-white/5 bg-white/[0.02] flex items-center justify-between px-4">
        <div className="flex items-center gap-2 text-xs font-mono tracking-widest uppercase text-white/40">
          <User className="h-3.5 w-3.5" />
          <span>Profile</span>
        </div>
        <button
          type="button"
          onClick={() => fetchProfile({ silent: true })}
          className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-white/60 transition-colors disabled:opacity-50"
          disabled={refreshing}
          aria-label="Refresh profile snapshot"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-6 scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent">
        {loading ? (
          <div className="h-full flex items-center justify-center text-white/20">
            <Loader2 className="animate-spin" />
          </div>
        ) : error ? (
          <div className="text-sm text-red-300/80">{error}</div>
        ) : (
          <>
            <div className="space-y-1">
              <div className="text-[10px] font-mono tracking-widest uppercase text-white/40">
                Display Name
              </div>
              <div className="text-sm text-white/90">
                {profile?.name?.trim() ? profile?.name : "Unknown"}
              </div>
            </div>

            <section className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-white/80 font-medium">
                  <div className="p-1.5 rounded-lg bg-brand-500/10 text-brand-400">
                    <Sparkles className="h-4 w-4" />
                  </div>
                  <h3>Recent Style</h3>
                </div>
                <div className="text-[10px] text-white/40 font-mono uppercase tracking-widest">
                  {typeof recent?.windowMessages === "number" && recent.windowMessages > 0
                    ? `Last ${recent.windowMessages} msgs`
                    : "Recent"}
                </div>
              </div>

              {hasRecent ? (
                <div className="space-y-2">
                  {recent?.summary && (
                    <div className="text-sm text-white/90 leading-relaxed">
                      {recent.summary}
                    </div>
                  )}
                  {recent?.rationale && (
                    <div className="text-xs text-white/50 leading-relaxed">
                      Why: {recent.rationale}
                    </div>
                  )}
                  {recent?.responseGuidance && (
                    <div className="text-xs text-white/50 leading-relaxed">
                      How PaLLi adapts: {recent.responseGuidance}
                    </div>
                  )}
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-white/30 font-mono">
                    {typeof recent?.updatedAt === "number" && (
                      <span>updated {formatTimeAgo(recent.updatedAt)}</span>
                    )}
                    {typeof recent?.confidence === "number" && (
                      <span>{Math.round(recent.confidence * 100)}% confidence</span>
                    )}
                  </div>
                </div>
              ) : (
                <div className="text-xs text-white/50 leading-relaxed">
                  No snapshot yet. Talk with PaLLi a bit more and this will appear (updated about every 5 messages).
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";

import DashboardV2 from "@/components/dashboard/dashboardV2";
import { OwnerSurfaceFallbackCard } from "@/components/ui/OwnerSurfaceFallbackCard";
import { apiGet } from "@/lib/api";
import type { MeResponse } from "@/lib/types";
import { Loader2 } from "lucide-react";

export default function DashboardPage() {
  const [user, setUser] = useState<MeResponse["user"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // First door, task 4 (2026-09-17): the shell already asks `/auth/me`, so it
  // already knows whether this is a self-hosted install. It starts TRUE because
  // the fallback below only renders when that question could NOT be answered,
  // and a page that could not ask must not offer the SaaS-era owner page.
  const [localMode, setLocalMode] = useState(true);

  useEffect(() => {
    let mounted = true;
    async function loadLocalOwner() {
      try {
        const res = await apiGet<MeResponse>("/api/web/auth/me");
        if (!mounted) return;
        setLocalMode(res.local_mode === true);
        if (res.user) {
          setUser(res.user);
        } else {
          setError("Local owner is unavailable.");
        }
      } catch (e) {
        if (!mounted) return;
        setError(e instanceof Error ? e.message : "Failed to load local owner.");
      } finally {
        if (mounted) setLoading(false);
      }
    }
    void loadLocalOwner();
    return () => {
      mounted = false;
    };
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-[color:var(--bg)] text-white flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-white/30" />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-[color:var(--bg)] text-white flex items-center justify-center px-6 text-center">
        <OwnerSurfaceFallbackCard
          eyebrow="Mission Control"
          title="Local owner unavailable"
          description={(
            <>
              Mission control could not load the local owner for this install.
              <div className="mt-3">{error || "Check the backend startup logs and confirm the local runtime is up."}</div>
              <div className="mt-3">
                When the backend is available again, return here for live runtime truth or use the owner-facing
                workspaces below for maintenance and shaping.
              </div>
            </>
          )}
          actions={[
            ...(localMode ? [] : [{ href: "/account", label: "Owner controls" }]),
            { href: "/account/identity", label: "Identity workspace", variant: "secondary" as const },
          ]}
          className="text-left"
        />
      </div>
    );
  }

  return <DashboardV2 />;
}

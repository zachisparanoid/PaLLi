"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, BrainCircuit, Loader2, ShieldAlert } from "lucide-react";

import { Navbar } from "@/components/site/navbar";
import { Footer } from "@/components/site/footer";
import { AdminCognitiveArchitectureConsole } from "@/components/dashboard/v2/AdminCognitiveArchitectureConsole";
import { UnderHoodObservatory } from "@/components/dashboard/v2/UnderHoodObservatory";
import { OwnerSurfaceFallbackCard } from "@/components/ui/OwnerSurfaceFallbackCard";
import { apiGet } from "@/lib/api";
import { type MeResponse, type PublicUser } from "@/lib/types";
import { useLocalMode } from "@/lib/useLocalMode";

export default function AdminCognitiveArchitecturePage() {
  const [user, setUser] = useState<PublicUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // First door, task 4 (2026-09-17). `null` — /auth/me has not answered — is
  // treated as local: a surface that cannot tell must not offer the SaaS page.
  const localMode = useLocalMode() !== false;

  useEffect(() => {
    let mounted = true;

    async function loadLocalOwner() {
      try {
        const response = await apiGet<MeResponse>("/api/web/auth/me");
        if (!mounted) return;
        if (!response.user) {
          setError("Local owner is unavailable.");
          return;
        }
        setUser(response.user);
      } catch (cause) {
        if (!mounted) return;
        setError(cause instanceof Error ? cause.message : "Failed to load the local owner.");
      } finally {
        if (mounted) setLoading(false);
      }
    }

    void loadLocalOwner();
    return () => {
      mounted = false;
    };
  }, []);

  const isAdmin = user?.role === "admin";

  return (
    <div className="min-h-screen bg-[color:var(--bg)] text-white selection:bg-brand-500/25">
      <Navbar />

      <main className="min-h-[calc(100vh-80px)] px-4 py-6 md:py-8 lg:overflow-y-auto">
        <div className="mx-auto flex max-w-[1600px] flex-col gap-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-[0.28em] text-white/35">
                <BrainCircuit className="h-3.5 w-3.5" />
                <span>Deep inspection</span>
              </div>
              <h1 className="mt-2 text-2xl font-light tracking-tight text-white/90">
                Cognitive architecture dashboard
              </h1>
              <p className="mt-3 max-w-3xl text-sm leading-relaxed text-white/60">
                A live readout of perception, state, strategy, and delivery signals from PaLLi&apos;s continuous-presence stack.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Link
                href="/dashboard"
                className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-white/75 transition-colors hover:bg-white/10 hover:text-white"
              >
                <ArrowLeft className="h-4 w-4" />
                Back to mission control
              </Link>
              <Link
                href="/account/identity"
                className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-white/75 transition-colors hover:bg-white/10 hover:text-white"
              >
                Identity workspace
              </Link>
            </div>
          </div>

          {loading ? (
            <div className="flex min-h-[60vh] items-center justify-center text-white/25">
              <Loader2 className="h-8 w-8 animate-spin" />
            </div>
          ) : error ? (
            <div className="flex min-h-[50vh] items-center justify-center">
              <OwnerSurfaceFallbackCard
                eyebrow="Cognitive Architecture"
                icon={<ShieldAlert className="h-6 w-6" />}
                tone="warning"
                title="Local owner unavailable"
                description={error}
                actions={[
                  { href: "/dashboard", label: "Open mission control" },
                  { href: "/account/identity", label: "Identity workspace", variant: "secondary" },
                ]}
                className="text-left"
              />
            </div>
          ) : !isAdmin ? (
            <div className="flex min-h-[50vh] items-center justify-center">
              <OwnerSurfaceFallbackCard
                eyebrow="Cognitive Architecture"
                icon={<ShieldAlert className="h-6 w-6" />}
                tone="warning"
                title="Cognitive inspection stays behind admin access"
                description="This route exposes internal cognitive traces, behavior controls, and raw response-shaping records."
                actions={[
                  { href: "/dashboard", label: "Open mission control" },
                  // First door, task 4 (2026-09-17): the SaaS-era owner page is
                  // not offered on a self-hosted install.
                  ...(localMode
                    ? []
                    : [{ href: "/account", label: "Owner controls", variant: "secondary" as const }]),
                  { href: "/account/identity", label: "Identity workspace", variant: "secondary" as const },
                ]}
                className="text-left"
              />
            </div>
          ) : (
            <>
              <UnderHoodObservatory />
              <AdminCognitiveArchitectureConsole />
            </>
          )}
        </div>
      </main>

      <div className="lg:hidden">
        <Footer />
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Loader2, ShieldAlert, Sparkles } from "lucide-react";

import { Navbar } from "@/components/site/navbar";
import { Footer } from "@/components/site/footer";
import { InteriorityConsole } from "@/components/interiority/InteriorityConsole";
import { OwnerSurfaceFallbackCard } from "@/components/ui/OwnerSurfaceFallbackCard";
import { apiGet } from "@/lib/api";
import type { MeResponse } from "@/lib/types";

export default function InteriorityPage() {
  const [user, setUser] = useState<MeResponse["user"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="min-h-screen bg-[color:var(--bg)] text-white selection:bg-brand-500/25">
      <Navbar />

      <main className="min-h-[calc(100vh-80px)] px-4 py-6 md:py-8 lg:overflow-y-auto">
        <div className="mx-auto flex max-w-[1600px] flex-col gap-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-[0.28em] text-white/35">
                <Sparkles className="h-3.5 w-3.5" />
                <span>Inner record</span>
              </div>
              <h1 className="mt-2 text-2xl font-light tracking-tight text-white/90">
                Interiority
              </h1>
              <p className="mt-3 max-w-3xl text-sm leading-relaxed text-white/60">
                What PaLLi has been holding — night reflections, stakes, the emotional
                arc, open threads, and the shape of her private monologue. Read-only:
                this page changes nothing about her.
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
            </div>
          </div>

          {loading ? (
            <div className="flex min-h-[60vh] items-center justify-center text-white/25">
              <Loader2 className="h-8 w-8 animate-spin" />
            </div>
          ) : error || !user ? (
            <div className="flex min-h-[50vh] items-center justify-center">
              <OwnerSurfaceFallbackCard
                eyebrow="Interiority"
                icon={<ShieldAlert className="h-6 w-6" />}
                tone="warning"
                title="Local owner unavailable"
                description={error ?? "Local owner is unavailable."}
                className="text-left"
              />
            </div>
          ) : (
            <InteriorityConsole />
          )}
        </div>
      </main>

      <div className="lg:hidden">
        <Footer />
      </div>
    </div>
  );
}

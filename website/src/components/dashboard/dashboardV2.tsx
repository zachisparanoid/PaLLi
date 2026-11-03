"use client";

import Link from "next/link";
import { Footer } from "@/components/site/footer";
import { Navbar } from "@/components/site/navbar";
import { ArrowRight, Sparkles, Waypoints } from "lucide-react";
import { NeuralFeed } from "@/components/dashboard/v2/NeuralFeed";
import { BotControls } from "@/components/dashboard/v2/BotControls";
import { RuntimeMissionCard } from "@/components/dashboard/v2/RuntimeMissionCard";
import { CompanionCapabilitiesCard } from "@/components/dashboard/v2/CompanionCapabilitiesCard";
import { GuidedSetupCard } from "@/components/dashboard/v2/GuidedSetupCard";
import { MemoryGalaxyCard } from "@/components/dashboard/v2/MemoryGalaxyCard";
import { useLocalMode } from "@/lib/useLocalMode";

type NavPath = {
  href: string;
  label: string;
  description?: string;
  /**
   * Only means something on a hosted install with sign-in and accounts. Local
   * mode does not render it (first door, task 4, 2026-09-17; option A — hidden,
   * not deleted, and the route still answers).
   */
  saasEra?: boolean;
};

const PRIMARY_PATHS: readonly NavPath[] = [
  // `/` is the door now, not a redirect to this page (first door, task 3), so
  // mission control needs a way back to it — first, because it is where she is
  // actually talked to.
  {
    href: "/",
    label: "Home",
    description: "Back to the door: her brain, the chat panel, and the two day-to-day doors on one page.",
  },
  {
    href: "/account",
    label: "Owner controls",
    description: "Keep owner basics, optional channel linking, transcript review, backup, and recovery tools on the separate owner surface.",
    saasEra: true,
  },
  {
    href: "/account/identity",
    label: "Identity workspace",
    description: "Inspect shaping docs, compiled action posture, and document provenance without turning mission control into a catch-all control panel.",
  },
];

const DEEP_INSPECTION_PATHS: readonly NavPath[] = [
  { href: "/dashboard/memory", label: "Open Memory Galaxy" },
  { href: "/dashboard/interiority", label: "Open the inner record" },
  { href: "/dashboard/admin/cognitive", label: "Watch cognitive architecture" },
  { href: "/account", label: "Owner controls", saasEra: true },
  { href: "/account/identity", label: "Identity workspace" },
];

/** `null` means `/auth/me` has not answered: hide the SaaS-era entries until it does. */
function visiblePaths(paths: readonly NavPath[], localMode: boolean | null): readonly NavPath[] {
  if (localMode === false) return paths;
  return paths.filter((path) => !path.saasEra);
}

export default function DashboardV2() {
  const localMode = useLocalMode();
  const primaryPaths = visiblePaths(PRIMARY_PATHS, localMode);
  const deepInspectionPaths = visiblePaths(DEEP_INSPECTION_PATHS, localMode);

  return (
    <div className="min-h-screen bg-[color:var(--bg)] text-white selection:bg-brand-500/25">
      <Navbar />

      <main className="min-h-[calc(100vh-80px)] px-4 py-6 md:py-8 lg:h-[calc(100vh-80px)] lg:overflow-y-auto">
        <div className="max-w-[1600px] mx-auto w-full flex flex-col gap-6">
          <div className="flex flex-col gap-6">
            <div className="flex items-center gap-4 flex-shrink-0">
              <div className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-brand-500/10 text-brand-400 border border-brand-500/20 shadow-[0_0_15px_-3px_rgba(56,189,248,0.18)]">
                <Waypoints className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-xl font-light tracking-tight text-white/90">Mission Control</h1>
                <p className="text-xs text-white/40 uppercase tracking-widest">Live Companion Runtime</p>
              </div>
            </div>

            <div className="relative overflow-hidden rounded-xl border border-brand-500/20 bg-brand-500/5 p-4">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand-500/15 text-brand-300">
                    <Sparkles className="h-5 w-5" />
                  </div>
                  <div>
                    <h3 className="font-medium text-white">One runtime, three clear lanes</h3>
                    <p className="text-sm text-white/60">
                      This surface stays focused on live runtime truth. Use the voice lane when you want to talk,
                      owner controls when you need owner basics, maintenance, or channel work, and the identity
                      workspace when you want to inspect how file-backed shaping becomes runtime behavior.
                    </p>
                  </div>
                </div>
                <Link
                  href="/account/identity"
                  className="flex shrink-0 items-center gap-2 rounded-lg bg-white px-4 py-2 text-sm font-medium text-black transition-transform hover:scale-105"
                >
                  Open identity workspace
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
              <div className="mt-4 grid gap-3 lg:grid-cols-3">
                {primaryPaths.map((path) => (
                  <Link
                    key={path.href}
                    href={path.href}
                    className="rounded-xl border border-white/10 bg-white/[0.03] p-4 text-white/80 transition-colors hover:bg-white/[0.06]"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-medium">{path.label}</div>
                      <ArrowRight className="h-4 w-4 shrink-0" />
                    </div>
                    <p className="mt-3 text-xs leading-5 text-white/55">
                      {path.description}
                    </p>
                  </Link>
                ))}
              </div>
              <div className="mt-4 flex flex-wrap gap-2 text-xs text-white/55">
                <div className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5">
                  Mission control mirrors live runtime state.
                </div>
                <div className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5">
                  Owner basics and channel maintenance stay in owner controls.
                </div>
                <div className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5">
                  Identity shaping and provenance stay in the identity workspace.
                </div>
              </div>
            </div>
          </div>

          <div className="flex-1 flex flex-col gap-6 min-h-0">
            <GuidedSetupCard />

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 min-h-0">
              <div className="lg:col-span-7 h-[520px] relative min-h-0">
                <CompanionCapabilitiesCard />
              </div>
              <div className="lg:col-span-5 h-[520px] relative min-h-0">
                <NeuralFeed />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-6">
              <div className="relative h-[480px] min-h-0">
                <MemoryGalaxyCard />
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-6 min-h-[320px] relative">
                <BotControls />
              </div>
              <div className="lg:col-span-6 min-h-[420px] relative">
                <RuntimeMissionCard />
              </div>
            </div>

            <details className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
              <summary className="cursor-pointer list-none text-sm font-medium text-white">
                Deep inspection tools
              </summary>
              <div className="mt-4 flex flex-col gap-4 text-sm text-white/65">
                <p className="max-w-4xl leading-6">
                  Raw runtime internals, identity artifacts, and the full agent session console still exist, but they
                  are secondary to the mission-control shell. Reach for them when you need debugging, audits, or deep
                  operator inspection.
                </p>
                <div className="flex flex-wrap gap-3">
                  {deepInspectionPaths.map((path) => (
                    <Link
                      key={path.href}
                      href={path.href}
                      className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-white/80 transition-colors hover:bg-white/[0.06]"
                    >
                      {path.label}
                      <ArrowRight className="h-4 w-4" />
                    </Link>
                  ))}
                </div>
              </div>
            </details>
          </div>
        </div>
      </main>
      
      {/* Footer only on mobile or if content scrolls, otherwise hidden to keep "app" feel */}
      <div className="lg:hidden">
          <Footer />
      </div>
    </div>
  );
}

"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { motion } from "framer-motion";
import {
  ArrowRight,
  Brain,
  Clock,
  Cpu,
  Database,
  Eye,
  Layers,
  Settings,
  Zap,
} from "lucide-react";

import { Footer } from "@/components/site/footer";
import { Navbar } from "@/components/site/navbar";
import { Container } from "@/components/ui/container";

const ownerSurfaceLinks = [
  {
    href: "/dashboard",
    title: "Mission Control",
    body:
      "Use the live runtime mirror for sessions, voice posture, routing, and drift. Reference explains the stack; it does not replace the cockpit.",
    icon: <Cpu className="h-5 w-5" />,
  },
  {
    href: "/account",
    title: "Owner controls",
    body:
      "Profile, channel linking, exports, and local maintenance stay here instead of spreading back into generic settings copy.",
    icon: <Settings className="h-5 w-5" />,
  },
  {
    href: "/account/identity",
    title: "Identity workspace",
    body:
      "Inspect shaping docs, compiled posture, provenance, and raw artifacts without turning Mission Control into an identity editor.",
    icon: <Eye className="h-5 w-5" />,
  },
  {
    href: "/account/memory",
    title: "Memory workspace",
    body:
      "Search continuity, inspect daily captures, and read curated recall when you need the durable memory surface.",
    icon: <Database className="h-5 w-5" />,
  },
] as const;

export default function ReferencePage() {
  return (
    <div className="min-h-screen bg-[color:var(--bg)] text-white selection:bg-brand-500/25">
      <Navbar />

      <main className="relative">
        <div className="pointer-events-none absolute inset-0 bg-[url('/grid.svg')] bg-top opacity-[0.05]" />

        <section className="relative border-b border-white/10 pb-16 pt-32">
          <Container>
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="max-w-3xl"
            >
              <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-brand-500/20 bg-brand-500/5 px-3 py-1 text-xs text-brand-400">
                <span className="font-mono uppercase tracking-widest">Secondary Reference</span>
              </div>
              <h1 className="text-4xl font-light tracking-tight text-white sm:text-6xl">
                Reference and deep inspection.
              </h1>
              <p className="mt-6 text-lg font-light leading-relaxed text-white/60">
                Use this page to understand how PaLLi Local is layered. For live runtime
                review, use Mission Control; for maintenance, shaping, and continuity work,
                move to the dedicated owner workspaces.
              </p>
            </motion.div>
          </Container>
        </section>

        <section className="py-24">
          <Container className="space-y-20">
            <div className="space-y-6">
              <div className="flex items-center gap-3 text-brand-300">
                <Cpu className="h-6 w-6" />
                <h2 className="text-2xl font-medium text-white">Use the right surface</h2>
              </div>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                {ownerSurfaceLinks.map((item) => (
                  <OwnerSurfaceLink
                    key={item.href}
                    href={item.href}
                    title={item.title}
                    body={item.body}
                    icon={item.icon}
                  />
                ))}
              </div>
            </div>

            <div className="grid gap-8 lg:grid-cols-2">
              <div className="space-y-8">
                <div className="flex items-center gap-3 text-brand-300">
                  <Brain className="h-6 w-6" />
                  <h2 className="text-2xl font-medium text-white">Identity layers</h2>
                </div>
                <div className="prose prose-invert prose-lg text-white/60">
                  <p>
                    PaLLi Local keeps shaping readable and layered instead of hiding it in one
                    prompt blob. Stable identity, relationship posture, action preferences,
                    memory summaries, and routines live in distinct documents with different
                    roles.
                  </p>
                  <p>
                    Those layers are composed selectively depending on what PaLLi is doing.
                    Normal replies, voice replies, and action-oriented reasoning do not all
                    receive the same prompt context. Mission Control shows the live result;
                    the Identity workspace is where you inspect the compiled bundle,
                    provenance, and editable shaping.
                  </p>
                </div>
              </div>

              <div className="rounded-3xl border border-white/10 bg-white/5 p-6">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                    <div className="mb-2 flex items-center gap-2 text-brand-300">
                      <Layers className="h-4 w-4" />
                      <div className="text-sm font-medium text-white">Core docs</div>
                    </div>
                    <p className="text-sm text-white/60">
                      Stable identity, operating principles, voice posture, and global
                      capability boundaries.
                    </p>
                  </div>
                  <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                    <div className="mb-2 flex items-center gap-2 text-brand-300">
                      <UserReferenceIcon />
                      <div className="text-sm font-medium text-white">Owner docs</div>
                    </div>
                    <p className="text-sm text-white/60">
                      Personality profile, action preferences, relationship state, memory
                      summary, and routines.
                    </p>
                  </div>
                  <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                    <div className="mb-2 flex items-center gap-2 text-brand-300">
                      <Settings className="h-4 w-4" />
                      <div className="text-sm font-medium text-white">Autonomy policy</div>
                    </div>
                    <p className="text-sm text-white/60">
                      `ACTION_PREFERENCES.md` drives initiative, interruption, reporting,
                      follow-through, and more.
                    </p>
                  </div>
                  <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                    <div className="mb-2 flex items-center gap-2 text-brand-300">
                      <Eye className="h-4 w-4" />
                      <div className="text-sm font-medium text-white">Identity workspace</div>
                    </div>
                    <p className="text-sm text-white/60">
                      Shaping docs, compiled posture, provenance, and raw artifacts stay here
                      so Mission Control can remain focused on live runtime state.
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-8">
              <div className="flex items-center gap-3 text-brand-300">
                <Database className="h-6 w-6" />
                <h2 className="text-2xl font-medium text-white">
                  Runtime, memory, and durable truth
                </h2>
              </div>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
                  <div className="text-[11px] font-mono uppercase tracking-[0.22em] text-white/35">
                    Mission Control
                  </div>
                  <p className="mt-3 text-sm leading-6 text-white/65">
                    Live sessions, voice state, jobs, and route drift belong in Mission
                    Control. This page only explains how those layers fit together.
                  </p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
                  <div className="text-[11px] font-mono uppercase tracking-[0.22em] text-white/35">
                    SQLite
                  </div>
                  <p className="mt-3 text-sm leading-6 text-white/65">
                    Durable operational truth lives in the local SQLite database:
                    reminders, jobs, audit logs, sessions, workflows, and other execution
                    state.
                  </p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
                  <div className="text-[11px] font-mono uppercase tracking-[0.22em] text-white/35">
                    Identity files
                  </div>
                  <p className="mt-3 text-sm leading-6 text-white/65">
                    Human-readable markdown defines how PaLLi thinks about itself and this
                    local operator relationship.
                  </p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
                  <div className="text-[11px] font-mono uppercase tracking-[0.22em] text-white/35">
                    Memory workspace
                  </div>
                  <p className="mt-3 text-sm leading-6 text-white/65">
                    Daily logs, curated recall, and continuity search live in the owner
                    memory surface instead of inside a generic reference page.
                  </p>
                </div>
              </div>
            </div>

            <div className="grid gap-8 lg:grid-cols-2">
              <div className="space-y-8">
                <div className="flex items-center gap-3 text-amber-300">
                  <Zap className="h-6 w-6" />
                  <h2 className="text-2xl font-medium text-white">
                    Autonomy and follow-through
                  </h2>
                </div>
                <div className="prose prose-invert prose-lg text-white/60">
                  <p>
                    The local product line keeps the stronger autonomy model from the recent
                    identity work. PaLLi can carry standing context, maintain relationship
                    summaries, and shape proactive behavior through the action preference
                    layer.
                  </p>
                  <p>
                    High-risk execution is still constrained by deeper command policy and
                    environment gates. Local mode is more trusted, but not intentionally
                    reckless. Mission Control shows the live runtime posture, while the
                    Identity and Memory workspaces stay responsible for the shaping and
                    continuity layers that feed that posture.
                  </p>
                </div>
              </div>

              <div className="rounded-3xl border border-white/10 bg-white/5 p-6">
                <div className="space-y-4">
                  <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                    <div className="mb-1 text-[11px] font-mono uppercase tracking-[0.22em] text-white/35">
                      Stage 1
                    </div>
                    <h3 className="text-base font-medium text-white">Context intake</h3>
                    <p className="mt-2 text-sm text-white/60">
                      Incoming messages, current memory, and identity context are assembled
                      for the active modality.
                    </p>
                  </div>
                  <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                    <div className="mb-1 text-[11px] font-mono uppercase tracking-[0.22em] text-white/35">
                      Stage 2
                    </div>
                    <h3 className="text-base font-medium text-white">Policy shaping</h3>
                    <p className="mt-2 text-sm text-white/60">
                      Action preferences and guardrails influence whether PaLLi replies,
                      plans, nudges, or acts.
                    </p>
                  </div>
                  <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                    <div className="mb-1 text-[11px] font-mono uppercase tracking-[0.22em] text-white/35">
                      Stage 3
                    </div>
                    <h3 className="text-base font-medium text-white">Execution</h3>
                    <p className="mt-2 text-sm text-white/60">
                      The runtime may answer directly, use tools, queue jobs, update
                      identity summaries, or continue existing work.
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-8">
              <div className="flex items-center gap-3 text-brand-300">
                <Cpu className="h-6 w-6" />
                <h2 className="text-2xl font-medium text-white">
                  What this local fork is aiming toward
                </h2>
              </div>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <ReferencePill
                  icon={<Clock className="h-4 w-4" />}
                  title="Ambient continuity"
                  body="Long-running awareness instead of one-shot chat replies."
                />
                <ReferencePill
                  icon={<Database className="h-4 w-4" />}
                  title="Machine-native context"
                  body="A cleaner path toward files, models, hardware, and environment state."
                />
                <ReferencePill
                  icon={<Brain className="h-4 w-4" />}
                  title="Identity-first behavior"
                  body="Conversation, action, and follow-through all anchored to the same self-model."
                />
                <ReferencePill
                  icon={<Zap className="h-4 w-4" />}
                  title="Trusted local operation"
                  body="A single-owner product with less SaaS friction and more room for deeper autonomy."
                />
              </div>
            </div>
          </Container>
        </section>
      </main>

      <Footer />
    </div>
  );
}

function ReferencePill({
  icon,
  title,
  body,
}: {
  icon: ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
      <div className="flex items-center gap-2 text-brand-300">{icon}</div>
      <div className="mt-3 text-base font-medium text-white">{title}</div>
      <p className="mt-2 text-sm leading-6 text-white/60">{body}</p>
    </div>
  );
}

function OwnerSurfaceLink({
  href,
  icon,
  title,
  body,
}: {
  href: string;
  icon: ReactNode;
  title: string;
  body: string;
}) {
  return (
    <Link
      href={href}
      className="group rounded-2xl border border-white/10 bg-white/5 p-5 transition-colors hover:border-brand-400/30 hover:bg-white/10"
    >
      <div className="flex items-center justify-between gap-3 text-brand-300">
        <div className="flex items-center gap-2">
          {icon}
          <div className="text-sm font-medium text-white">{title}</div>
        </div>
        <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
      </div>
      <p className="mt-3 text-sm leading-6 text-white/65">{body}</p>
    </Link>
  );
}

function UserReferenceIcon() {
  return (
    <div className="flex h-4 w-4 items-center justify-center rounded-full border border-current text-[9px] font-bold">
      U
    </div>
  );
}

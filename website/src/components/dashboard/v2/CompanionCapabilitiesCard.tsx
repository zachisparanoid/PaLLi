"use client";

import Link from "next/link";
import { ArrowRight, BellRing, Bot, Mic, Search, TerminalSquare } from "lucide-react";

import { useLocalMode } from "@/lib/useLocalMode";

const CAPABILITY_GROUPS = [
  {
    title: "Talk naturally",
    icon: Mic,
    copy: "Use PaLLi like a companion, not a command line. Voice, Discord, and mission control should all feel like the same presence.",
    prompts: [
      "Keep me company while I work, but stay brief.",
      "Can you hear me? Give me a one-sentence reply.",
    ],
  },
  {
    title: "Follow through",
    icon: BellRing,
    copy: "PaLLi can remember things that matter, schedule reminders, and check in later when you want follow-through.",
    prompts: [
      "Remind me in 20 minutes to switch the laundry.",
      "Check in on me about sleep this week, but don't be annoying.",
    ],
  },
  {
    title: "Do real work",
    icon: TerminalSquare,
    copy: "When a task needs action instead of a text answer, PaLLi can inspect files, research live info, and run bounded local tasks.",
    prompts: [
      "Look through this repo and tell me what is broken.",
      "Read this link and summarize what changed.",
    ],
  },
] as const;

const QUICK_ACTIONS = [
  { href: "/account/identity", label: "Identity workspace" },
  // SaaS-era: hidden on a self-hosted install (first door, task 4, 2026-09-17).
  { href: "/account", label: "Owner controls", saasEra: true },
  { href: "/account/memory", label: "Memory workspace" },
] as const;

export function CompanionCapabilitiesCard() {
  const localMode = useLocalMode();
  const quickActions = QUICK_ACTIONS.filter(
    (action) => localMode === false || !("saasEra" in action),
  );

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-white/5 bg-black/40 shadow-xl backdrop-blur-md">
      <div className="flex h-12 items-center justify-between border-b border-white/5 bg-white/[0.02] px-4">
        <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-white/40">
          <Bot className="h-3.5 w-3.5" />
          <span>What PaLLi Can Do</span>
        </div>
        <div className="rounded-full border border-brand-500/20 bg-brand-500/10 px-2.5 py-1 text-[10px] uppercase tracking-[0.22em] text-brand-300">
          Local Companion
        </div>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-5 scrollbar-thin scrollbar-thumb-white/10">
        <div className="rounded-2xl border border-brand-500/15 bg-brand-500/8 p-4">
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-brand-500/15 p-2 text-brand-300">
              <Search className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-base font-medium text-white">Start with outcomes, not control panels</h3>
              <p className="mt-2 text-sm leading-6 text-white/65">
                PaLLi is most useful when you ask for what you want done. Mission control should mostly help you see
                status, adjust a few basics, and stay out of the way.
              </p>
            </div>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          {CAPABILITY_GROUPS.map((group) => {
            const Icon = group.icon;
            return (
              <section key={group.title} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                <div className="flex items-center gap-2 text-white/90">
                  <div className="rounded-lg bg-white/5 p-2 text-white/70">
                    <Icon className="h-4 w-4" />
                  </div>
                  <h3 className="text-sm font-medium">{group.title}</h3>
                </div>
                <p className="mt-3 text-sm leading-6 text-white/60">{group.copy}</p>
                <div className="mt-4 space-y-2">
                  {group.prompts.map((prompt) => (
                    <div
                      key={prompt}
                      className="rounded-xl border border-white/10 bg-black/25 px-3 py-2 text-xs leading-5 text-white/75"
                    >
                      {prompt}
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
        </div>

        <div className="rounded-2xl border border-white/10 bg-black/25 p-4">
          <div className="text-[11px] font-bold uppercase tracking-[0.24em] text-white/35">Quick Paths</div>
          <div className="mt-4 flex flex-wrap gap-3">
            {quickActions.map((action) => (
              <Link
                key={action.href}
                href={action.href}
                className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white/80 transition-colors hover:bg-white/[0.06]"
              >
                {action.label}
                <ArrowRight className="h-4 w-4" />
              </Link>
            ))}
          </div>
          <p className="mt-4 text-xs leading-5 text-white/45">
            Stay on this page for live runtime truth. Use owner controls for owner basics, channel maintenance,
            transcript review, backup, and recovery tools, the identity workspace for shaping docs and action
            posture, and the memory workspace when you want daily captures, curated recall, or workflow carry-forward
            continuity.
          </p>
        </div>
      </div>
    </div>
  );
}

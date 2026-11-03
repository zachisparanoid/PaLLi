"use client";

import Link from "next/link";
import { ArrowLeft, FileText, MessagesSquare, ShieldCheck } from "lucide-react";

import { Navbar } from "@/components/site/navbar";
import { Footer } from "@/components/site/footer";
import { Container } from "@/components/ui/container";
import { Button } from "@/components/ui/button";
import { IdentityWorkbench } from "@/components/account/IdentityWorkbench";
import { useLocalMode } from "@/lib/useLocalMode";

export default function AccountIdentityPage() {
  // First door, task 4 (2026-09-17): this workspace stays in every mode, but the
  // way back to the SaaS-era owner page does not exist on a self-hosted install.
  const showOwnerControls = useLocalMode() === false;

  return (
    <div className="min-h-screen bg-[color:var(--bg)] text-white selection:bg-brand-500/25">
      <Navbar />
      <main className="pb-20 pt-14">
        <Container className="space-y-8">
          <section className="rounded-3xl border border-brand-500/15 bg-brand-500/5 p-6 shadow-[0_0_60px_-32px_rgba(56,189,248,0.55)]">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
              <div className="max-w-3xl">
                <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-brand-300/70">
                  Owner Controls
                </div>
                <h1 className="mt-3 text-4xl font-semibold tracking-tight text-white">Identity workspace</h1>
                <p className="mt-3 text-sm leading-6 text-white/65">
                  Use this workspace to shape how PaLLi relates to you, inspect the file-backed identity layers, and
                  review the compiled posture they become at runtime. First contact still starts in normal chat or
                  voice; this page is the owner workspace for longer-lived shaping and provenance, while the memory
                  workspace keeps daily capture and durable recall separate.
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
                    <Button variant="secondary">Open mission control</Button>
                  </Link>
                  <Link href="/account/memory">
                    <Button variant="secondary">Open memory workspace</Button>
                  </Link>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-3 lg:max-w-[620px]">
                <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-4">
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-white/35">
                    <MessagesSquare className="h-4 w-4" />
                    Conversation First
                  </div>
                  <div className="mt-3 text-sm text-white/90">Chat and voice stay the main way to meet and use PaLLi.</div>
                </div>
                <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-4">
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-white/35">
                    <ShieldCheck className="h-4 w-4" />
                    Shaping Layers
                  </div>
                  <div className="mt-3 text-sm text-white/90">Tune relationship posture, autonomy boundaries, and voice behavior.</div>
                </div>
                <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-4">
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-white/35">
                    <FileText className="h-4 w-4" />
                    Provenance
                  </div>
                  <div className="mt-3 text-sm text-white/90">Audit compiled policy, raw documents, and identity history in one place.</div>
                </div>
              </div>
            </div>
          </section>

          <div>
            <p className="mb-4 max-w-3xl text-sm leading-6 text-white/55">
              The workbench below keeps the owner-facing shaping controls first, then exposes document governance,
              compiled runtime posture, raw markdown, and version history when deeper inspection is needed.
            </p>
          </div>

          <div>
            <IdentityWorkbench />
          </div>
        </Container>
      </main>
      <Footer />
    </div>
  );
}

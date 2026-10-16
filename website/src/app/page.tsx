"use client";

/**
 * The first door (first door, task 3, 2026-09-17 — spec §1).
 *
 * `/` used to redirect to `/dashboard`. It is now where a stranger lands the
 * first time they start her: no brain, both chat doors shut, a browser open on
 * the address the punch list printed. Three steps on one page — pick a brain
 * and paste a key, watch it tested live, say hello — and then the card that
 * tells them Discord and Telegram exist.
 *
 * Everything this page shows comes from `GET /api/setup/status`, including the
 * list of brains it can offer. The one thing it decides for itself is which of
 * the two halves to show: no brain yet means the key step and nothing else;
 * a brain means the chat panel, the doors, and a quiet way back to the key step
 * — because the shipped default is a LOCAL server that needs no key, so an
 * untouched install can arrive here with a brain that is configured and not
 * running.
 *
 * It never spins forever. Every failure this page can have — a backend that is
 * not up, one that answers with something that is not the status — ends in a
 * sentence and a button.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";

import { BrainStep } from "@/components/door/BrainStep";
import { ChatPanel } from "@/components/door/ChatPanel";
import { DoorsCard } from "@/components/door/DoorsCard";
import { Button } from "@/components/ui/button";
import {
  RESTART_COMMAND,
  START_COMMAND,
  fetchSetupStatus,
  readFailure,
  type BrainSaved,
  type DoorFailure,
  type SetupStatus,
} from "@/lib/setup";
import { SOURCE_CODE_URL } from "@/lib/externalLinks";

/**
 * What a save said, kept visible across whatever happens next.
 *
 * It is its own component because it has to render in TWO places: above the
 * page, and above the failure card — a refetch that fails right after a
 * successful save must not swallow the news that a key was written to
 * `.env.local` (fix round 1, minor).
 */
function SavedBanner({ saved }: { saved: BrainSaved }) {
  const named = [saved.provider, saved.model].filter(Boolean).join(" · ");
  return (
    <div
      className={[
        "rounded-2xl border px-5 py-4 text-sm",
        saved.restart_required
          ? "border-amber-300/25 bg-amber-400/10 text-amber-100"
          : "border-emerald-300/25 bg-emerald-400/10 text-emerald-100",
      ].join(" ")}
    >
      {saved.restart_required ? (
        <>
          {/* "Her voice", not "her brain" (final wave, important 3): the door
              writes env, and env is what her VOICE reads. The nine other lanes
              follow data/palli.yaml's routing and stay where that file points
              them until `palli_ctl brain` moves them — so a banner that said
              "her brain is live" promised a whole mind on one key. */}
          <p className="font-medium">Her voice: saved — restart to use it.</p>
          <p className="mt-1 opacity-80">
            {named ? `${named}: the key tested fine and is in ` : "The key tested fine and is in "}
            <code>.env.local</code>, but she could not swap it in while running.
          </p>
          <pre className="mt-2 overflow-x-auto rounded-lg bg-black/40 px-3 py-2 text-xs text-white/70">
            {RESTART_COMMAND}
          </pre>
        </>
      ) : (
        <>
          <p className="font-medium">Her voice is live: {named}</p>
          {/* The balance belongs to a brain that is actually running; under the
              amber variant it would be a boast about something not in use. */}
          <p className="mt-1 opacity-80">
            {saved.balance ? `balance: ${saved.balance}` : "balance: not reported by this provider"}
          </p>
        </>
      )}
    </div>
  );
}

export default function Door() {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<DoorFailure | null>(null);
  const [saved, setSaved] = useState<BrainSaved | null>(null);
  const [changingBrain, setChangingBrain] = useState(false);
  const [savedCount, setSavedCount] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const fresh = await fetchSetupStatus();
      setStatus(fresh);
      setFailure(null);
    } catch (caught) {
      setStatus(null);
      setFailure(readFailure(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function onSaved(result: BrainSaved) {
    setSaved(result);
    setSavedCount((count) => count + 1);
    setChangingBrain(false);
    // No reload: the backend swapped her provider in place, so re-reading the
    // status is enough to reveal the chat panel.
    void load();
  }

  if (loading && !status) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4 text-white">
        <div className="flex items-center gap-3 text-white/50">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span className="text-sm">Opening the door…</span>
        </div>
      </main>
    );
  }

  if (!status) {
    const silent = failure?.silent ?? true;
    return (
      <main className="flex min-h-screen items-center justify-center px-4 py-10 text-white">
        <div className="flex w-full max-w-xl flex-col gap-4">
          {/* A key that was just written is news no failure gets to swallow. */}
          {saved && <SavedBanner saved={saved} />}
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6">
            <h1 className="text-lg font-medium">
              {silent ? "Her backend is not answering." : failure?.sentence}
            </h1>
            {silent && (
              <>
                <p className="mt-2 text-sm leading-relaxed text-white/60">
                  This page talks to the process you started in the terminal. Start it, then try
                  again:
                </p>
                <pre className="mt-3 overflow-x-auto rounded-lg bg-black/40 px-3 py-2 text-xs text-white/70">
                  {START_COMMAND}
                </pre>
              </>
            )}
            {/* Its own words as well, never instead — and now reachable, because
                the sentence and the words are no longer the same value (fix
                round 1, important 1). */}
            {failure?.raw && (
              <p className="mt-2 text-xs break-words whitespace-pre-wrap text-white/35">
                {failure.raw}
              </p>
            )}
            <div className="mt-4">
              <Button type="button" onClick={() => void load()} disabled={loading}>
                {loading ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Trying…
                  </>
                ) : (
                  <>
                    <RefreshCw className="h-4 w-4" />
                    Try again
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      </main>
    );
  }

  const showBrainStep = !status.brain_ready || changingBrain;

  return (
    <main className="min-h-screen px-4 py-8 text-white sm:py-12">
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-5">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-brand-500/20 bg-brand-500/10 text-brand-300">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-xl font-light tracking-tight text-white/90">PaLLi</h1>
              <p className="text-xs uppercase tracking-widest text-white/40">
                {status.brain_ready ? "she is awake" : "setup — she has no brain yet"}
              </p>
            </div>
          </div>
          <Link
            href="/dashboard"
            className="text-xs text-white/40 underline underline-offset-4 hover:text-white/70"
          >
            Mission control
          </Link>
        </header>

        {saved && <SavedBanner saved={saved} />}

        {showBrainStep && (
          <BrainStep
            providers={status.providers}
            configured={status.provider}
            onSaved={onSaved}
            onAttempt={() => setSaved(null)}
            onCancel={
              status.brain_ready
                ? () => {
                    // Cancelling retires the last save's banner too: it was the
                    // news of a moment that has passed.
                    setSaved(null);
                    setChangingBrain(false);
                  }
                : undefined
            }
            takeFocus={changingBrain}
          />
        )}

        {status.brain_ready && (
          <>
            <ChatPanel
              provider={status.provider}
              model={status.model}
              onChangeBrain={() => setChangingBrain(true)}
              focusSignal={changingBrain ? 0 : savedCount}
            />
            {!changingBrain && (
              <div className="-mt-2">
                <button
                  type="button"
                  onClick={() => setChangingBrain(true)}
                  className="text-xs text-white/40 underline underline-offset-4 hover:text-white/70"
                >
                  Change her brain
                </button>
              </div>
            )}
          </>
        )}

        <DoorsCard
          doors={status.doors}
          brainReady={status.brain_ready}
          doorsNeedRestart={status.doors_need_restart}
        />

        <p className="text-center text-xs text-white/40">
          PaLLi is free software under the AGPL-3.0.{" "}
          <a
            href={SOURCE_CODE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-4 hover:text-white/70"
          >
            Source code
          </a>
        </p>
      </div>
    </main>
  );
}

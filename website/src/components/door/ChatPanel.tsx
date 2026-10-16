"use client";

/**
 * Step three of the door: say hello (first door, task 3, 2026-09-17 — spec §1).
 *
 * This is the first conversation, and it happens before any bot token exists.
 * It talks to the chat endpoint the dashboard has always had, so nothing here
 * is a second way to reach her — it is the same spine, with a composer in front
 * of it.
 *
 * Two rules worth stating because they are easy to get wrong:
 *
 *   * The 30-second mark is a NOTE, not a timeout. Her replies routinely take
 *     twenty seconds and a turn that picks up a tool runs for minutes. At 30
 *     seconds the panel says she is taking longer than usual and keeps
 *     listening; only a five-minute ceiling (in lib/setup.ts) ever gives up,
 *     and it says so inline.
 *   * The list is the web conversation's own record, not the page's
 *     (web-page reminders, 2026-10-10). On opening, the panel loads the recent
 *     messages (GET /api/chat/history); while the page is on screen it asks
 *     every 15 seconds for reminders that arrived on their own, after the
 *     newest message it has. No browser storage and no websocket: a reload
 *     shows the same conversation again, because the backend has it.
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Bell, Paperclip, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  fetchChatHistory,
  readFailure,
  sendChatMessage,
  type ChatAttachment,
  type ChatHistoryMessage,
} from "@/lib/setup";

/** How often an open page asks for reminders that arrived on their own. */
const REMINDER_CHECK_MS = 15_000;
/** How many earlier messages the page shows when it opens. */
const HISTORY_ON_OPEN = 50;
/** How far back each check looks for a reminder (the backend's own ceiling). */
const CHECK_WINDOW = 200;

type Bubble = {
  id: number;
  /** who is speaking: the person, her, her delegated work, or the page itself */
  from: "you" | "her" | "delegation" | "trouble";
  text: string;
  /** the backend's own words under a "trouble" bubble, when it had any */
  raw?: string;
  attachments?: ChatAttachment[];
  /** a reminder she delivered on her own (her bubble, with a small label) */
  reminder?: boolean;
};

export type ChatPanelProps = {
  /** Shown quietly under the title once a brain exists. */
  provider: string;
  model: string;
  /** The way out when it is her brain that is wrong, not her. */
  onChangeBrain: () => void;
  /**
   * Bumped by the page when a brain has just been saved. The panel has only
   * this moment arrived on screen, so it takes the cursor: the next thing to do
   * is say hello, and nobody should have to hunt for the box.
   */
  focusSignal?: number;
};

function AttachmentList({ attachments }: { attachments: ChatAttachment[] }) {
  return (
    <ul className="mt-2 flex flex-col gap-1">
      {attachments.map((file, index) => {
        // Neither a link nor a path nor even a name: there is nothing to point
        // at, and saying so is better than an empty bullet (fix round 1, minor).
        const nameless = !file.filename && !file.path && !file.url;
        const name = nameless
          ? "a file she produced could not be linked"
          : file.filename || file.path;
        return (
          <li key={`${name}-${index}`} className="flex items-start gap-2 text-sm">
            <Paperclip className="mt-0.5 h-3.5 w-3.5 shrink-0 text-white/40" />
            {file.url ? (
              <a
                href={file.url}
                target="_blank"
                rel="noreferrer"
                className="break-all text-brand-300 underline underline-offset-2"
              >
                {name}
              </a>
            ) : (
              // Nothing serves these over HTTP: the chat endpoint hands back a
              // path on the machine she runs on, and this install has no route
              // that reads it. Naming the file and where it is beats a link
              // that would 404.
              <span
                className={[
                  "min-w-0 break-all",
                  nameless ? "text-white/45 italic" : "text-white/70",
                ].join(" ")}
              >
                {name}
                {file.path && file.filename && (
                  <span className="block text-xs text-white/40">
                    on her machine: {file.path}
                  </span>
                )}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ChatPanel({
  provider,
  model,
  onChangeBrain,
  focusSignal = 0,
}: ChatPanelProps) {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [slow, setSlow] = useState(false);
  const nextId = useRef(1);
  const list = useRef<HTMLDivElement | null>(null);
  const composer = useRef<HTMLTextAreaElement | null>(null);
  // The newest record the page has, and every reminder already drawn. Until
  // the first load succeeds there is no cursor, so the check never mistakes an
  // old reminder for a new one: it retries the load instead.
  const cursor = useRef(0);
  const loaded = useRef(false);
  const shownReminders = useRef<Set<number>>(new Set());
  // One check at a time (Fable's review, 2026-10-10): a slow first load
  // overlapped by the timer or a tab switch used to prepend the history twice.
  const busy = useRef(false);
  // When the panel opened, in the backend's seconds. A first load that only
  // succeeds later (her backend was still starting) skips what was said here
  // since — those bubbles are already on screen — but keeps any reminder.
  const openedAt = useRef(Date.now() / 1000);

  useEffect(() => {
    let stopped = false;

    function asBubble(message: ChatHistoryMessage): Omit<Bubble, "id"> {
      return message.role === "user"
        ? { from: "you", text: message.text }
        : { from: "her", text: message.text, reminder: message.reminder };
    }

    async function check() {
      if (stopped || busy.current || document.visibilityState !== "visible") return;
      busy.current = true;
      try {
        if (!loaded.current) {
          const fetched = await fetchChatHistory({ limit: HISTORY_ON_OPEN });
          if (stopped) return;
          loaded.current = true;
          for (const message of fetched) {
            cursor.current = Math.max(cursor.current, message.id);
            if (message.reminder) shownReminders.current.add(message.id);
          }
          const earlier = fetched.filter(
            (message) => message.reminder || message.timestamp < openedAt.current,
          );
          // Earlier messages go ABOVE anything typed while they were loading.
          setBubbles((current) => [
            ...earlier.map((message) => ({ ...asBubble(message), id: nextId.current++ })),
            ...current,
          ]);
          return;
        }
        const arrived = await fetchChatHistory({
          sinceId: cursor.current,
          remindersOnly: true,
          limit: CHECK_WINDOW,
        });
        if (stopped) return;
        const fresh = arrived.filter((message) => !shownReminders.current.has(message.id));
        for (const message of fresh) {
          shownReminders.current.add(message.id);
          cursor.current = Math.max(cursor.current, message.id);
        }
        if (fresh.length) {
          setBubbles((current) => [
            ...current,
            ...fresh.map((message) => ({ ...asBubble(message), id: nextId.current++ })),
          ]);
        }
      } catch {
        // Quiet: the page's own status line says when her backend is down, and
        // the next check simply tries again.
      } finally {
        busy.current = false;
      }
    }

    void check();
    const timer = window.setInterval(() => void check(), REMINDER_CHECK_MS);
    const onVisible = () => void check();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  useEffect(() => {
    // The LIST is scrolled, not the window, and only once there is something in
    // it: `scrollIntoView` on an empty panel moved the whole page on mount (fix
    // round 1, minor).
    if (!bubbles.length && !waiting) return;
    const box = list.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [bubbles, waiting]);

  useEffect(() => {
    if (focusSignal > 0) composer.current?.focus({ preventScroll: true });
  }, [focusSignal]);

  function say(bubble: Omit<Bubble, "id">) {
    setBubbles((current) => [...current, { ...bubble, id: nextId.current++ }]);
  }

  async function send() {
    const message = draft.trim();
    if (!message || waiting) return;
    setDraft("");
    setSlow(false);
    setWaiting(true);
    say({ from: "you", text: message });
    try {
      const reply = await sendChatMessage(message, { onSlow: () => setSlow(true) });
      const beats = reply.beats.filter((beat) => beat.text.trim());
      if (beats.length) {
        // Her cadence: each beat is a message she would have sent on its own,
        // in order.
        for (const beat of beats) say({ from: "her", text: beat.text });
      } else if (reply.response.trim()) {
        say({ from: "her", text: reply.response });
      }
      if (reply.delegation_text.trim()) {
        say({ from: "delegation", text: reply.delegation_text });
      }
      if (reply.attachments.length) {
        say({ from: "delegation", text: "", attachments: reply.attachments });
      }
      // She chose to stay quiet (Batch 8): her choice, not a fault. Nothing
      // is drawn for her, and the page does not call it trouble.
      if (!reply.quiet && !beats.length && !reply.response.trim() && !reply.delegation_text.trim() && !reply.attachments.length) {
        say({ from: "trouble", text: "She answered with nothing at all. The log will say why." });
      }
    } catch (caught) {
      const failure = readFailure(caught);
      say({ from: "trouble", text: failure.sentence, raw: failure.raw });
    } finally {
      setWaiting(false);
      setSlow(false);
    }
  }

  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-medium text-white">Say hello</h2>
        {(provider || model) && (
          <p className="text-xs text-white/40">
            {[provider, model].filter(Boolean).join(" · ")}
          </p>
        )}
      </div>

      {/* The commonest first hour there is, and until the final wave the page
          said nothing about it: the shipped template points at a local model
          server, and a stranger who has not started one gets a 200 — her own
          outage line, in her own bubble, with no pointer anywhere near it
          (dispatch.py's _apply_outage_fallback; her words are a design law of
          2026-08-11 and stay as they are). So the panel says it BEFORE she
          does, once, quietly, for the one provider it can be true of. */}
      {provider === "local_openai" && (
        <p className="mt-2 text-xs text-white/45">
          Her brain is a local model server. If she answers that her provider timed out, it is
          not running —{" "}
          <button
            type="button"
            onClick={onChangeBrain}
            className="underline underline-offset-2 hover:text-white/70"
          >
            change her brain
          </button>
          .
        </p>
      )}

      <div
        ref={list}
        className="mt-4 flex max-h-[55vh] min-h-[200px] flex-col gap-3 overflow-y-auto pr-1"
      >
        {bubbles.length === 0 && !waiting && (
          <p className="text-sm text-white/40">
            She is listening. Your conversation stays here when you come back, and a reminder
            you ask for in this window arrives here when there is no chat app to send it to.
          </p>
        )}

        {bubbles.map((bubble) => {
          if (bubble.from === "you") {
            return (
              <div key={bubble.id} className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-sm border border-brand-500/25 bg-brand-500/10 px-4 py-2.5 text-sm whitespace-pre-wrap break-words text-white">
                  {bubble.text}
                </div>
              </div>
            );
          }
          if (bubble.from === "trouble") {
            return (
              <div key={bubble.id} className="rounded-xl border border-red-400/25 bg-red-500/10 px-4 py-3">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-300" />
                  <div className="min-w-0 text-sm text-red-200">
                    <p className="break-words whitespace-pre-wrap">{bubble.text}</p>
                    {bubble.raw && (
                      // Its own words as well as the sentence (fix round 1,
                      // important 1) — a 500 from a running backend says
                      // something, and it is the only clue there is.
                      <p className="mt-1 text-xs break-words whitespace-pre-wrap text-red-200/60">
                        {bubble.raw}
                      </p>
                    )}
                    <p className="mt-1 text-red-200/70">
                      Her brain may not be answering — check the log, or{" "}
                      <button
                        type="button"
                        onClick={onChangeBrain}
                        className="underline underline-offset-2 hover:text-red-100"
                      >
                        change her brain
                      </button>
                      .
                    </p>
                  </div>
                </div>
              </div>
            );
          }
          return (
            <div key={bubble.id} className="flex justify-start">
              <div
                className={[
                  "max-w-[85%] rounded-2xl rounded-bl-sm border px-4 py-2.5 text-sm whitespace-pre-wrap break-words",
                  bubble.from === "delegation"
                    ? "border-white/8 bg-white/[0.02] text-white/60"
                    : "border-white/10 bg-white/5 text-white/90",
                ].join(" ")}
              >
                {bubble.reminder && (
                  <span className="mb-1 flex items-center gap-1 text-xs text-brand-300">
                    <Bell className="h-3 w-3" aria-hidden="true" />
                    Reminder
                  </span>
                )}
                {bubble.text}
                {bubble.attachments && <AttachmentList attachments={bubble.attachments} />}
              </div>
            </div>
          );
        })}

        {waiting && (
          // The live region covers the note as well as the dots: the note is the
          // part a screen reader most needs to hear, and it arrives thirty
          // seconds after the region was announced (fix round 1, minor).
          <div className="flex flex-col gap-1" aria-live="polite">
            <div className="flex items-center gap-1.5 text-white/40">
              <span className="sr-only">She is writing</span>
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white/50" />
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white/50 [animation-delay:150ms]" />
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white/50 [animation-delay:300ms]" />
            </div>
            {slow && (
              <p className="text-xs text-white/40">
                She is taking longer than usual — the log will say why. Still listening.
              </p>
            )}
          </div>
        )}
      </div>

      <div className="mt-4 flex items-end gap-2">
        <textarea
          ref={composer}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter is a newline — the shape every chat box
            // has, so nobody has to be told.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
          disabled={waiting}
          rows={2}
          placeholder={waiting ? "waiting for her…" : "say hello"}
          aria-label="Your message"
          className="min-h-[52px] w-full resize-y rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm text-white placeholder:text-white/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:opacity-50"
        />
        <Button
          type="button"
          onClick={() => void send()}
          disabled={waiting || draft.trim().length === 0}
          aria-label="Send"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
      <p className="mt-2 text-xs text-white/35">
        Enter sends · Shift+Enter starts a new line
      </p>
    </section>
  );
}

export default ChatPanel;

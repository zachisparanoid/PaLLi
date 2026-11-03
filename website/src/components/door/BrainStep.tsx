"use client";

/**
 * Step one and two of the door: pick a brain, paste a key, watch it tested live
 * (first door, task 3, 2026-09-17 — spec §1).
 *
 * The picker is the API's, not this file's: `GET /api/setup/status` returns the
 * list with the capability matrix's own labels and its own refusals, so a brain
 * that becomes usable becomes selectable here without anyone editing this page.
 * A row that is not available is shown DISABLED with its reason in plain sight
 * rather than hidden — a stranger looking for Claude should find out why it is
 * not there, not wonder whether they missed it.
 *
 * The key lives in this component's state and nowhere else: never localStorage,
 * never the URL, never a log line. It is cleared the moment a save succeeds; it
 * is KEPT when the provider refuses it, because the fix is usually one
 * character and re-pasting is a punishment for the provider's mistake.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, KeyRound, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  START_COMMAND,
  readFailure,
  saveBrain,
  type BrainSaved,
  type DoorFailure,
  type ProviderOption,
} from "@/lib/setup";

// The one label this page overrides. The capability matrix names this row after
// the machine it was written for; on a stranger's install it is whatever local
// server they happen to run.
const LOCAL_SERVER_LABEL = "A local model server (Ollama, LM Studio, vLLM)";

const LOCAL_SERVER_HINT = "http://127.0.0.1:11434/v1";

function labelFor(option: ProviderOption): string {
  return option.name === "local_openai" ? LOCAL_SERVER_LABEL : option.label;
}

function firstChoice(providers: ProviderOption[], configured: string): string {
  // Only if it is still USABLE (fix round 1, minor): a tree configured for a
  // provider the matrix now blocks would otherwise open on a row that has no
  // fields and no button — a page with nothing on it to do.
  const preselected = providers.find((option) => option.name === configured && option.available);
  if (preselected) return preselected.name;
  const available = providers.find((option) => option.available);
  return (available || providers[0])?.name || "";
}

export type BrainStepProps = {
  providers: ProviderOption[];
  /** `status.provider` — pre-selected when the API still offers it, and it works. */
  configured: string;
  onSaved: (saved: BrainSaved) => void;
  /** Called when a test STARTS, so the page can retire a stale saved-banner. */
  onAttempt?: () => void;
  /** Present when the step was revealed by "change her brain", not by setup mode. */
  onCancel?: () => void;
  /** True when it was just revealed: take the focus and come into view. */
  takeFocus?: boolean;
};

export function BrainStep({
  providers,
  configured,
  onSaved,
  onAttempt,
  onCancel,
  takeFocus = false,
}: BrainStepProps) {
  const [chosen, setChosen] = useState<string>(() => firstChoice(providers, configured));
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [testing, setTesting] = useState(false);
  const [failure, setFailure] = useState<DoorFailure | null>(null);
  const section = useRef<HTMLElement | null>(null);
  const firstField = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    // Revealed from the chat panel's error, this step can open below the fold —
    // a stranger who clicked "change her brain" would see nothing happen.
    if (!takeFocus) return;
    section.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    firstField.current?.focus({ preventScroll: true });
  }, [takeFocus]);

  const option = useMemo(
    () => providers.find((row) => row.name === chosen) || null,
    [providers, chosen],
  );
  const wantsKey = Boolean(option?.needs.includes("api_key"));
  const wantsLocalServer = Boolean(option?.needs.includes("base_url"));
  const blocked = Boolean(option && !option.available);

  function pick(name: string) {
    if (name === chosen) return;
    setChosen(name);
    // A key pasted for one provider is never the right key for another, and a
    // model name rarely is either. Start the new choice clean.
    setApiKey("");
    setBaseUrl("");
    setModel("");
    setFailure(null);
  }

  async function test() {
    if (!option || blocked || testing) return;
    setTesting(true);
    setFailure(null);
    // Whatever the last save said is about to stop being the news.
    onAttempt?.();
    try {
      const result = await saveBrain({
        provider: option.name,
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
        model: model.trim() || undefined,
      });
      if (!result.ok) {
        // The provider's own words. Nothing was saved and nothing changed, so
        // the field stays exactly as it was typed.
        setFailure({ sentence: result.error, raw: "", silent: false });
        return;
      }
      setApiKey("");
      onSaved(result);
    } catch (caught) {
      setFailure(readFailure(caught));
    } finally {
      setTesting(false);
    }
  }

  const canSubmit =
    !testing &&
    !blocked &&
    Boolean(option) &&
    (!wantsKey || apiKey.trim().length > 0) &&
    (!wantsLocalServer || (baseUrl.trim().length > 0 && model.trim().length > 0));

  return (
    <section ref={section} className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-500/15 text-brand-300">
          <KeyRound className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-medium text-white">Give her a brain</h2>
          <p className="mt-1 text-sm text-white/60">
            Pick where her thinking happens and paste the credential. She tests it live, in front
            of you, and nothing is written to <code className="text-white/70">.env.local</code>{" "}
            until it answers.
          </p>
        </div>
      </div>

      <div className="mt-5 flex flex-col gap-2">
        {providers.map((row) => {
          const selected = row.name === chosen;
          return (
            <div key={row.name}>
              <button
                type="button"
                onClick={() => pick(row.name)}
                disabled={!row.available}
                aria-pressed={selected}
                className={[
                  "w-full rounded-xl border px-4 py-3 text-left text-sm transition",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]",
                  selected
                    ? "border-brand-500/40 bg-brand-500/10 text-white"
                    : "border-white/10 bg-white/[0.02] text-white/80 hover:border-white/20",
                  row.available ? "" : "cursor-not-allowed opacity-60",
                ].join(" ")}
              >
                <span className="flex items-center justify-between gap-3">
                  <span className="font-medium">{labelFor(row)}</span>
                  {!row.available && (
                    <span className="shrink-0 text-xs uppercase tracking-wide text-white/40">
                      not available
                    </span>
                  )}
                </span>
              </button>
              {!row.available && row.reason && (
                <p className="mt-1 px-1 text-xs leading-relaxed text-white/45">{row.reason}</p>
              )}
            </div>
          );
        })}
      </div>

      {option && !blocked && (
        <div className="mt-5 flex flex-col gap-4">
          {wantsLocalServer && (
            <>
              <div>
                <label htmlFor="door-base-url" className="mb-1.5 block text-sm text-white/70">
                  The address it answers on
                </label>
                <Input
                  id="door-base-url"
                  ref={firstField}
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder={LOCAL_SERVER_HINT}
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              <div>
                <label htmlFor="door-model" className="mb-1.5 block text-sm text-white/70">
                  The model to run
                </label>
                <Input
                  id="door-model"
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder="the name your server lists"
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
            </>
          )}

          {(wantsKey || wantsLocalServer) && (
            <div>
              <label htmlFor="door-api-key" className="mb-1.5 block text-sm text-white/70">
                API key{wantsLocalServer ? " (only if your server asks for one)" : ""}
              </label>
              <Input
                id="door-api-key"
                // The first field is the base URL when there is one.
                ref={wantsLocalServer ? undefined : firstField}
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={wantsLocalServer ? "usually blank" : "paste it here"}
                // `off` is advisory and most browsers ignore it on a password
                // field — they offered to save the key under a caption that
                // says it is never stored in this browser (final wave, minor
                // 6). `new-password` is the one value they honour, and the two
                // manager hints cover 1Password and LastPass.
                autoComplete="new-password"
                data-1p-ignore
                data-lpignore="true"
                spellCheck={false}
              />
              <p className="mt-1.5 text-xs text-white/40">
                It goes to the provider and to <code className="text-white/60">.env.local</code>,
                and nowhere else. It is never stored in this browser.
              </p>
            </div>
          )}

          {wantsKey && !wantsLocalServer && (
            <details className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3">
              <summary className="cursor-pointer text-sm text-white/60">
                Choose a model (optional)
              </summary>
              <div className="mt-3">
                <label
                  htmlFor="door-optional-model"
                  className="mb-1.5 block text-sm text-white/70"
                >
                  The model to run
                </label>
                <Input
                  id="door-optional-model"
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder="leave blank for this provider's default"
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
            </details>
          )}

          {failure && (
            <div className="rounded-xl border border-red-400/25 bg-red-500/10 px-4 py-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-300" />
                <div className="min-w-0">
                  {/* whitespace-pre-wrap: a provider's own refusal can be several
                      lines, and flattening them loses the shape it was written in. */}
                  <p className="text-sm leading-relaxed break-words whitespace-pre-wrap text-red-200">
                    {failure.sentence}
                  </p>
                  {failure.silent && (
                    <pre className="mt-2 overflow-x-auto rounded-lg bg-black/40 px-3 py-2 text-xs text-white/70">
                      {START_COMMAND}
                    </pre>
                  )}
                  {failure.raw && (
                    // What it said in its own words, as well as the sentence —
                    // never instead of it (fix round 1, important 1).
                    <p className="mt-2 text-xs break-words whitespace-pre-wrap text-red-200/60">
                      {failure.raw}
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" onClick={() => void test()} disabled={!canSubmit}>
              {testing ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Testing it live…
                </>
              ) : (
                "Test it and save"
              )}
            </Button>
            {onCancel && (
              <Button type="button" variant="ghost" onClick={onCancel} disabled={testing}>
                Cancel
              </Button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

export default BrainStep;

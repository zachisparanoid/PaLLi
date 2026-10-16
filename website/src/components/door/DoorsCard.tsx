"use client";

/**
 * "After hello": the card where a stranger learns the day-to-day doors exist
 * (first door, task 3, 2026-09-17 — spec §1).
 *
 * Discord and Telegram are where she lives once the first hour is over. This
 * card does not open them — opening them is two or three lines in `.env.local`
 * and a restart, because the bot clients are started once, at boot, from the
 * map boot built. So the card's whole job is to say exactly which lines, and to
 * be honest about what a door is doing right now:
 *
 *   open   — she is on it this boot.
 *   unset  — no token: here are the lines.
 *   closed — a token is set and the door is not open. Either she has no brain
 *            yet (setup mode shuts both doors deliberately), or it was shut for
 *            that reason at boot and is waiting on a restart.
 */

import { Check, DoorOpen, RefreshCw } from "lucide-react";

import { RESTART_COMMAND, type DoorState } from "@/lib/setup";
import { PhonePairing } from "@/components/door/PhonePairing";

type DoorRow = {
  key: "discord" | "telegram" | "mobile";
  name: string;
  /** The exact lines to add to .env.local, as .env.template and SETUP.md name them. */
  envLines: string[];
  /** The switch alone — what a door with a token but no flag is missing. */
  enableLine: string;
  /** Topic, not a section number — SETUP.md's sections are being renamed. */
  where: string;
  /** What a shut door with its switch on says (a door with no token has none to name). */
  closedLine?: string;
};

const DOORS: DoorRow[] = [
  {
    key: "discord",
    name: "Discord",
    envLines: [
      "ENABLE_DISCORD_BOT=true",
      "DISCORD_TOKEN=your-bot-token",
      "LOCAL_OWNER_DISCORD_USER_ID=your-numeric-user-id",
    ],
    enableLine: "ENABLE_DISCORD_BOT=true",
    where: "SETUP.md, the Discord application section",
  },
  {
    key: "telegram",
    name: "Telegram",
    envLines: [
      "ENABLE_TELEGRAM_BOT=true",
      "TELEGRAM_BOT_TOKEN=your-bot-token",
      "LOCAL_OWNER_TELEGRAM_USER_ID=your-numeric-user-id",
    ],
    enableLine: "ENABLE_TELEGRAM_BOT=true",
    where: "SETUP.md, the Telegram bot section",
  },
  {
    key: "mobile",
    name: "Phone app",
    envLines: [
      "ENABLE_MOBILE_DOOR=true",
      "MOBILE_PUBLIC_URL=http://your-host.your-tailnet.ts.net:8000",
      "BACKEND_HOST=0.0.0.0",
    ],
    enableLine: "ENABLE_MOBILE_DOOR=true",
    where: "SETUP.md, the phone app section",
    closedLine: "It is switched on, but the door is not open this boot.",
  },
];

export type DoorsCardProps = {
  doors: { discord: DoorState; telegram: DoorState; mobile: DoorState };
  brainReady: boolean;
  doorsNeedRestart: boolean;
};

function stateLabel(state: DoorState): string {
  if (state === "open") return "open";
  if (state === "closed") return "closed";
  return "not set up";
}

export function DoorsCard({ doors, brainReady, doorsNeedRestart }: DoorsCardProps) {
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/5 text-white/60">
          <DoorOpen className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-medium text-white">Doors</h2>
          <p className="mt-1 text-sm text-white/60">
            Where she lives with you day to day. None is needed for this first hour.
          </p>
        </div>
      </div>

      {doorsNeedRestart && (
        <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-300/25 bg-amber-400/10 px-4 py-3">
          <RefreshCw className="mt-0.5 h-4 w-4 shrink-0 text-amber-200" />
          <div className="min-w-0 text-sm text-amber-100">
            <p>A door was shut at boot because there was no brain. A restart will open it.</p>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-black/40 px-3 py-2 text-xs text-white/70">
              {RESTART_COMMAND}
            </pre>
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-col gap-3">
        {DOORS.map((door) => {
          const state = doors[door.key];
          return (
            <div key={door.key} className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium text-white">{door.name}</span>
                <span
                  className={[
                    "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                    state === "open"
                      ? "border-emerald-300/25 bg-emerald-400/10 text-emerald-200"
                      : "border-white/10 bg-white/5 text-white/60",
                  ].join(" ")}
                >
                  {state === "open" && <Check className="h-3.5 w-3.5" />}
                  {stateLabel(state)}
                </span>
              </div>

              {state === "closed" && !brainReady && (
                <p className="mt-2 text-sm text-white/55">Closed until a brain is configured.</p>
              )}

              {state === "closed" && brainReady && (
                <div className="mt-2">
                  {/* A token is set and the door is still shut. The API's own
                      comment lists every way that happens — the flag off (which
                      is how `ENABLE_TELEGRAM_BOT` SHIPS), the library missing,
                      or a boot that had no brain yet — and the page cannot tell
                      them apart. So it names both remedies unconditionally
                      rather than leaving a stranger with a sentence and no way
                      out (fix round 1, important 2). */}
                  {door.closedLine ? (
                    <p className="text-sm text-white/55">{door.closedLine}</p>
                  ) : (
                    <>
                      <p className="text-sm text-white/55">
                        Its token is set, but the door is not open this boot. Check that its switch is
                        on in <code className="text-white/70">.env.local</code>:
                      </p>
                      <pre className="mt-2 overflow-x-auto rounded-lg bg-black/40 px-3 py-2 text-xs leading-relaxed text-white/70">
                        {door.enableLine}
                      </pre>
                    </>
                  )}
                  <p className="mt-2 text-sm text-white/55">then restart her:</p>
                  <pre className="mt-2 overflow-x-auto rounded-lg bg-black/40 px-3 py-2 text-xs leading-relaxed text-white/70">
                    {RESTART_COMMAND}
                  </pre>
                  <p className="mt-2 text-xs text-white/40">
                    If the switch is already on, the boot log names what stopped it —{" "}
                    {door.where} covers the rest.
                  </p>
                </div>
              )}

              {state === "unset" && (
                <div className="mt-2">
                  <p className="text-sm text-white/55">
                    Add these lines to <code className="text-white/70">.env.local</code>, then
                    restart her:
                  </p>
                  <pre className="mt-2 overflow-x-auto rounded-lg bg-black/40 px-3 py-2 text-xs leading-relaxed text-white/70">
                    {door.envLines.join("\n")}
                  </pre>
                  <p className="mt-2 text-xs text-white/40">
                    {door.key === "mobile"
                      ? `${door.where} walks through all of it.`
                      : `${door.where} walks through making the bot and finding your own user id.`}
                  </p>
                </div>
              )}

              {door.key === "mobile" && state === "open" && <PhonePairing />}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default DoorsCard;

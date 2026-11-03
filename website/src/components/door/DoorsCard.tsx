"use client";

import { useState, type FormEvent } from "react";
import { DoorOpen, Loader2, RefreshCw } from "lucide-react";
import { PhonePairing } from "@/components/door/PhonePairing";
import { saveDoor, requestRestart, readFailure, type DoorState, type DoorSettings, type DoorConfiguration, type RestartInfo } from "@/lib/setup";

type Channel = "discord" | "telegram" | "mobile";
const NAMES: Record<Channel, string> = { discord: "Discord", telegram: "Telegram", mobile: "Phone app" };
export type DoorsCardProps = {
  doors: Record<Channel, DoorState>; brainReady: boolean; doorsNeedRestart: boolean;
  configuration?: DoorSettings; restart?: RestartInfo; address?: string; onUpdated?: () => void;
};

function ConnectionForm({ channel, state, settings, address, onSaved }: {
  channel: Channel; state: DoorState; settings?: DoorConfiguration; address: string; onSaved: () => void;
}) {
  const name = NAMES[channel];
  const [enabled, setEnabled] = useState(settings?.configured ? settings.enabled : true);
  const [token, setToken] = useState("");
  const [owner, setOwner] = useState(settings?.owner_id || "");
  const [publicUrl, setPublicUrl] = useState(() => {
    if (settings?.public_url) return settings.public_url;
    if (typeof window !== "undefined" && !["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname)) return window.location.origin;
    return address;
  });
  const [savedSettings, setSavedSettings] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function save(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(""); setNotice("");
    try {
      const result = await saveDoor({ channel, enabled, token: token.trim() || undefined,
        owner_id: owner.trim() || undefined, public_url: publicUrl.trim() || undefined });
      setToken(""); setSavedSettings(result.settings[channel]);
      setNotice(enabled ? `${name} settings saved. Restart PaLLi to connect.` : `${name} will be disabled after restarting PaLLi.`);
      onSaved();
    } catch (cause) { setError(readFailure(cause).sentence); }
    finally { setSaving(false); }
  }

  return <form onSubmit={(event) => void save(event)} className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-medium text-white">{name}</h3>
      <span className="text-xs text-white/50">{state === "open" ? "Enabled" : savedSettings?.configured ? "Saved" : "Not set up"}</span>
    </div>
    <label className="mt-3 flex items-center gap-2 text-sm text-white/75">
      <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Enable {name} after saving
    </label>
    {channel === "mobile" ? <label className="mt-3 block text-sm text-white/70">
      Address your phone will use
      <input type="url" required={enabled} disabled={!enabled} value={publicUrl} onChange={(event) => setPublicUrl(event.target.value)}
        placeholder="http://192.168.1.50:3000" className="mt-1.5 w-full rounded-lg border border-white/15 bg-black/30 p-2.5" />
      <span className="mt-1 block text-xs text-white/45">Use a LAN, tailnet or HTTPS address. Pair your phone with a private, one-use code.</span>
    </label> : <>
      <label className="mt-3 block text-sm text-white/70">{name} bot token
        <input type="password" required={enabled && !savedSettings?.configured} disabled={!enabled} value={token}
          onChange={(event) => setToken(event.target.value)} autoComplete="new-password" data-1p-ignore data-lpignore="true"
          placeholder={savedSettings?.configured ? "Leave blank to keep your saved token" : "Paste your bot token"}
          className="mt-1.5 w-full rounded-lg border border-white/15 bg-black/30 p-2.5" />
      </label>
      <label className="mt-3 block text-sm text-white/70">Your {name} user ID
        <input type="text" inputMode="numeric" pattern="[0-9]+" required={enabled} disabled={!enabled} value={owner}
          onChange={(event) => setOwner(event.target.value)} autoComplete="off" className="mt-1.5 w-full rounded-lg border border-white/15 bg-black/30 p-2.5" />
        <span className="mt-1 block text-xs text-white/45">Your numeric account ID, so PaLLi recognizes you as her owner.</span>
      </label>
      <details className="mt-3 text-xs text-white/50"><summary className="cursor-pointer">Where do I get these?</summary>
        {channel === "discord" ? <p className="mt-2 leading-relaxed">
          Create an application in the <a href="https://discord.com/developers/applications" target="_blank" rel="noopener noreferrer" className="underline">Discord Developer Portal</a>.
          Get its bot token from the Bot page and enable Message Content, Server Members and Presence intents.
          For your user ID, enable Developer Mode in Discord settings, then use Copy User ID on your own profile.
          After saving, use the invite button below to add your bot to a server.
        </p> : <p className="mt-2 leading-relaxed">
          Create your bot with <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer" className="underline">BotFather</a> and enter its token here.
          The owner field needs your numeric Telegram account ID, which is different from your @username.
        </p>}
      </details>
    </>}
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <button type="submit" disabled={saving} className="inline-flex items-center gap-2 rounded-lg bg-brand-500/20 px-3 py-2 text-sm text-brand-200 hover:bg-brand-500/30 disabled:opacity-50">
        {saving && <Loader2 className="h-4 w-4 animate-spin" />}{saving ? "Checking and saving…" : `Save ${name}`}
      </button>
      {savedSettings?.link && <a href={savedSettings.link} target="_blank" rel="noopener noreferrer" className="text-sm text-brand-300 underline">
        {channel === "discord" ? "Invite bot to Discord" : "Open bot in Telegram"}
      </a>}
      {savedSettings?.name && <span className="text-xs text-white/45">Bot: {savedSettings.name}</span>}
    </div>
    {notice && <p role="status" className="mt-3 text-sm text-emerald-200">{notice}</p>}
    {error && <p role="alert" className="mt-3 text-sm text-red-300">{error}</p>}
    {channel === "mobile" && state === "open" && <PhonePairing />}
  </form>;
}

export function DoorsCard({ doors, brainReady, doorsNeedRestart, configuration, restart, address = "", onUpdated }: DoorsCardProps) {
  const [changed, setChanged] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [restartError, setRestartError] = useState("");
  async function restartNow() {
    setRestarting(true); setRestartError("");
    try {
      const before = await requestRestart();
      const deadline = Date.now() + 150_000;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 1800));
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 4000);
        try {
          const response = await fetch("/api/setup/status", { credentials: "include", signal: controller.signal, cache: "no-store" });
          if (response.ok) {
            const status = await response.json();
            if (status.restart?.instance && status.restart.instance !== before.instance) { window.location.reload(); return; }
          }
        } catch { /* Both services can be briefly offline. */ }
        finally { clearTimeout(timeout); }
      }
      throw new Error("Could not confirm the restart. Refresh the page or check your server.");
    } catch (cause) { setRestartError(readFailure(cause).sentence); setRestarting(false); }
  }
  return <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-5 sm:p-6">
    <div className="flex items-start gap-3">
      <DoorOpen className="mt-1 h-5 w-5 shrink-0 text-white/60" />
      <div><h2 className="text-base font-medium text-white">Doors</h2>
        <p className="mt-1 text-sm text-white/60">Connect the places where you want to talk with PaLLi. Web chat works without connecting any.</p>
      </div>
    </div>
    <div className="mt-4 flex flex-col gap-3">
      {(["discord", "telegram", "mobile"] as Channel[]).map((channel) => <ConnectionForm key={channel} channel={channel}
        state={doors[channel]} settings={configuration?.[channel]} address={address}
        onSaved={() => { setChanged(true); onUpdated?.(); }} />)}
    </div>
    {(changed || doorsNeedRestart) && <div className="mt-4 rounded-xl border border-amber-300/25 bg-amber-400/10 p-4 text-sm text-amber-100">
      <p>{brainReady ? "Your changes are saved. Restart PaLLi to apply them." : "Your changes are saved. Choose a model above, then restart PaLLi to connect."}</p>
      <p className="mt-1 text-xs opacity-70">Restarting briefly disconnects the dashboard and can stop a reply in progress.</p>
      <button type="button" onClick={() => void restartNow()} disabled={restarting || !restart?.available}
        className="mt-3 inline-flex items-center gap-2 rounded-lg border border-amber-200/30 px-3 py-2 disabled:opacity-50">
        <RefreshCw className={`h-4 w-4 ${restarting ? "animate-spin" : ""}`} />{restarting ? "Restarting — waiting for PaLLi…" : "Restart PaLLi"}
      </button>
      {!restart?.available && <p className="mt-2 text-xs">This installation's host manages restarts; restart it through that host.</p>}
      {restartError && <p role="alert" className="mt-2 text-red-300">{restartError}</p>}
    </div>}
  </section>;
}
export default DoorsCard;

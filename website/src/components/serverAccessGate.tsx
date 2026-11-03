"use client";

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { apiGet, apiPost } from "@/lib/api";
import type { MeResponse } from "@/lib/types";

/** A server's access key is exchanged for an HttpOnly cookie, never saved in browser storage. */
export function ServerAccessGate({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [server, setServer] = useState(false);
  const [connected, setConnected] = useState(false);
  const [accessKey, setAccessKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const me = await apiGet<MeResponse>("/api/web/auth/me");
      setServer(me.auth_surface.dashboard_auth_mode === "server");
      setConnected(Boolean(me.user));
    } catch {
      setError("PaLLi's server is not answering. Check that it is running, then try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const expired = () => { setConnected(false); setAccessKey(""); };
    window.addEventListener("palli:unauthorized", expired);
    return () => window.removeEventListener("palli:unauthorized", expired);
  }, [refresh]);

  async function connect(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await apiPost("/api/web/auth/server-login", { access_key: accessKey });
      setAccessKey("");
      setConnected(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not connect. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    try {
      await apiPost("/api/web/auth/logout", {});
      setConnected(false);
    } catch {
      setError("Could not disconnect. Try again.");
    }
  }

  if (!loading && connected && server) {
    return <>
      <div className="flex items-center justify-end gap-3 border-b border-white/10 px-4 py-2 text-sm text-white/60">
        Connected to your PaLLi server
        <button onClick={() => void disconnect()} className="underline">Disconnect</button>
        {error && <span role="alert">{error}</span>}
      </div>
      {children}
    </>;
  }
  if (!loading && !server && !error) return <>{children}</>;
  return <main className="flex min-h-screen items-center justify-center px-6 text-white">
    <section className="w-full max-w-md rounded-2xl border border-white/10 bg-black/30 p-6">
      <h1 className="text-xl font-medium">Connect to your PaLLi server</h1>
      {loading ? <p className="mt-4" role="status">Connecting…</p> : <>
        {server && <form onSubmit={(event) => void connect(event)} className="mt-4 space-y-4">
          <p className="text-sm text-white/60">Paste the server access key printed in the terminal when PaLLi was started.</p>
          <label className="block">
            Server access key
            <input type="password" value={accessKey} onChange={(event) => setAccessKey(event.target.value)}
              autoComplete="current-password" autoFocus required maxLength={512}
              className="mt-2 w-full rounded-lg border border-white/20 bg-black/30 p-3" />
          </label>
          <button type="submit" disabled={busy || !accessKey.trim()} className="rounded-lg bg-brand-500 px-4 py-2 disabled:opacity-50">
            {busy ? "Connecting…" : "Connect"}
          </button>
        </form>}
        {error && <p role="alert" className="mt-4 text-red-300">{error}</p>}
        {!server && <button onClick={() => void refresh()} className="mt-4 underline">Try again</button>}
      </>}
    </section>
  </main>;
}

"use client";

/**
 * The phone app's pairing panel (the mobile door, 2026-10-13, spec §3).
 *
 * "Pair a phone" asks the backend for a one-use code and shows it with its QR
 * (an <img> of the backend's SVG — never injected markup) and a ten-minute
 * countdown. The list below shows the paired phones; one can be unpaired.
 */

import { useCallback, useEffect, useState } from "react";
import { QrCode, Smartphone, Trash2 } from "lucide-react";

import {
  listPhones,
  readFailure,
  startPhonePairing,
  unpairPhone,
  type PairStart,
  type PairedPhone,
} from "@/lib/setup";

function when(ts: number | null): string {
  return ts ? new Date(ts * 1000).toLocaleString() : "never";
}

export function PhonePairing() {
  const [pairing, setPairing] = useState<PairStart | null>(null);
  const [phones, setPhones] = useState<PairedPhone[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);

  const refresh = useCallback(async () => {
    try {
      setPhones(await listPhones());
    } catch (e) {
      setError(readFailure(e).sentence);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!pairing) return;
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000);
    const poll = setInterval(() => void refresh(), 5000);
    return () => {
      clearInterval(tick);
      clearInterval(poll);
    };
  }, [pairing, refresh]);

  const start = async () => {
    setError(null);
    try {
      setPairing(await startPhonePairing());
      setNow(Date.now() / 1000);
    } catch (e) {
      setError(readFailure(e).sentence);
    }
  };

  const unpair = async (phone: PairedPhone) => {
    if (!window.confirm(`Unpair ${phone.name}? It will have to pair again to reach her.`)) return;
    try {
      await unpairPhone(phone.id);
      await refresh();
    } catch (e) {
      setError(readFailure(e).sentence);
    }
  };

  const left = pairing ? Math.max(0, Math.round(pairing.expires_at - now)) : 0;

  return (
    <div className="mt-3 flex flex-col gap-3">
      {!pairing || left === 0 ? (
        <button
          type="button"
          onClick={() => void start()}
          className="inline-flex w-fit items-center gap-2 rounded-lg border border-brand-500/30 bg-brand-500/10 px-3 py-1.5 text-sm text-brand-300 hover:bg-brand-500/20"
        >
          <QrCode className="h-4 w-4" />
          {pairing ? "That code expired — make a new one" : "Pair a phone"}
        </button>
      ) : (
        <div className="flex flex-wrap items-start gap-4 rounded-xl border border-white/10 bg-black/30 p-4">
          {pairing.qr_svg ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={`data:image/svg+xml;utf8,${encodeURIComponent(pairing.qr_svg)}`}
              alt="Pairing QR code"
              className="h-44 w-44 rounded-lg bg-white p-2"
            />
          ) : null}
          <div className="min-w-0 text-sm text-white/70">
            <p>Scan it with the PaLLi app, or type these in:</p>
            <p className="mt-2">
              Address: <code className="text-white">{pairing.url ?? "your MOBILE_PUBLIC_URL"}</code>
            </p>
            <p>
              Code: <code className="text-lg tracking-widest text-white">{pairing.code}</code>
            </p>
            <p className="mt-2 text-xs text-white/40">
              Good once, for {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} more.
            </p>
          </div>
        </div>
      )}

      {phones.length > 0 && (
        <ul className="flex flex-col gap-2">
          {phones.map((phone) => (
            <li key={phone.id} className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.03] px-3 py-2">
              <span className="flex min-w-0 items-center gap-2 text-sm text-white/80">
                <Smartphone className="h-4 w-4 shrink-0 text-white/50" />
                <span className="truncate">{phone.name}</span>
                <span className="text-xs text-white/40">last seen {when(phone.last_seen_at)}</span>
              </span>
              <button
                type="button"
                onClick={() => void unpair(phone)}
                className="rounded-md p-1 text-white/40 hover:bg-white/5 hover:text-red-300"
                aria-label={`Unpair ${phone.name}`}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="text-sm text-red-300">{error}</p>}
    </div>
  );
}

export default PhonePairing;

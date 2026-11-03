"use client";

/**
 * "Is this a self-hosted install?", asked once per page (first door, task 4,
 * 2026-09-17).
 *
 * The owner's ruling of 2026-09-10 #2 — ONE owner with remembered users, for
 * good — means the SaaS-era account pages have nothing to do on a self-hosted
 * box. His choice of 2026-09-16 was option A: HIDE them, do not delete them.
 * Every surface that offers one of those pages asks this hook first.
 *
 * The answer comes from `GET /api/web/auth/me`, whose `local_mode` field the
 * backend reads from `core.local_mode.local_mode_enabled()` — the same function
 * `GET /api/setup/status` answers from, so the door and the dashboard can never
 * disagree about the same install.
 *
 * The request is shared: the navbar and the footer both render on nearly every
 * page, and a hook that fetched per component would ask the same question three
 * times on one load. A FAILED read is never cached — the next mount asks again.
 *
 * `null` means "it has not answered yet", and every caller treats it the same
 * way the pending state is treated: the SaaS-era entry stays hidden. A
 * self-hosted install must never flash a page that belongs to a product it is
 * not running.
 */

import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import type { MeResponse } from "@/lib/types";

/** `null` until `/auth/me` has answered. */
export type LocalModeAnswer = boolean | null;

let shared: Promise<boolean> | null = null;

function readLocalMode(): Promise<boolean> {
  if (!shared) {
    shared = apiGet<MeResponse>("/api/web/auth/me")
      .then((me) => me.local_mode === true)
      .catch((error) => {
        shared = null;
        throw error;
      });
  }
  return shared;
}

export function useLocalMode(): LocalModeAnswer {
  const [localMode, setLocalMode] = useState<LocalModeAnswer>(null);

  useEffect(() => {
    let mounted = true;
    readLocalMode().then(
      (answer) => {
        if (mounted) setLocalMode(answer);
      },
      () => {
        // Swallowed on purpose. A backend that cannot say leaves the answer at
        // `null`, and `null` already means "hide the SaaS-era entries". The
        // page that a stranger is actually looking at reports the outage in its
        // own words; a navbar has no business doing it twice.
      },
    );
    return () => {
      mounted = false;
    };
  }, []);

  return localMode;
}

"use client";

import { useEffect, useMemo, useState } from "react";
import { apiGet } from "@/lib/api";
import type { MeResponse, PublicUser } from "@/lib/types";

export function useMe() {
  const [user, setUser] = useState<PublicUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setLoading(true);
      setError(null);
      const data = await apiGet<MeResponse>("/api/web/auth/me");
      setUser(data.user);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load session");
      setUser(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return useMemo(() => ({ user, loading, error, refresh }), [user, loading, error]);
}


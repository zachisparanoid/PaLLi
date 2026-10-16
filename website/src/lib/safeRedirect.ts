export class UnsafeRedirectError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UnsafeRedirectError";
  }
}

function parseAllowedHosts(raw: string | undefined | null): Set<string> {
  const set = new Set<string>();
  if (!raw) return set;
  for (const part of raw.split(",")) {
    const host = part.trim().toLowerCase();
    if (host) set.add(host);
  }
  return set;
}

function allowedExternalRedirectHosts(): Set<string> {
  // Build-time configurable list for your deployment.
  const extra = parseAllowedHosts(process.env.NEXT_PUBLIC_ALLOWED_REDIRECT_HOSTS);

  // Safe defaults for PaLLi Local. Legacy hosted billing hosts stay opt-in
  // through NEXT_PUBLIC_ALLOWED_REDIRECT_HOSTS.
  const defaults = new Set<string>([
    "discord.com",
    "discord.gg",
  ]);

  for (const host of extra) defaults.add(host);
  return defaults;
}

export function safeRedirect(rawUrl: string): void {
  if (typeof window === "undefined") return;

  const value = (rawUrl || "").trim();
  if (!value) throw new UnsafeRedirectError("Missing redirect URL");

  let url: URL;
  try {
    url = new URL(value, window.location.origin);
  } catch {
    throw new UnsafeRedirectError("Invalid redirect URL");
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new UnsafeRedirectError("Blocked non-http(s) redirect");
  }

  if (url.origin === window.location.origin) {
    window.location.href = url.toString();
    return;
  }

  const allowedHosts = allowedExternalRedirectHosts();
  const host = url.hostname.toLowerCase();
  if (!allowedHosts.has(host)) {
    throw new UnsafeRedirectError(`Blocked redirect to untrusted host: ${host}`);
  }

  window.location.href = url.toString();
}

export function sanitizeInternalPath(
  rawPath: string | null | undefined,
  fallback = "/dashboard",
): string {
  const fallbackValue =
    typeof fallback === "string" && fallback.startsWith("/") && !fallback.startsWith("//")
      ? fallback
      : "/dashboard";

  const value = (rawPath || "").trim();
  if (!value) return fallbackValue;

  // Only allow root-relative paths to avoid open redirects.
  if (!value.startsWith("/")) return fallbackValue;
  if (value.startsWith("//")) return fallbackValue;

  // Block header-splitting / malformed path vectors.
  if (value.includes("\r") || value.includes("\n")) return fallbackValue;

  return value;
}

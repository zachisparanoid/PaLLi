import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Simple API wrapper for JSON requests
// Expects /api proxy to be set up in next.config.ts

export class ApiError extends Error {
  status: number;
  data: any;

  constructor(message: string, status: number, data?: any) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

function getApiTimeoutMs(): number {
  const raw = process.env.NEXT_PUBLIC_API_TIMEOUT_MS;
  const parsed = raw ? Number(raw) : 30000;
  if (!Number.isFinite(parsed) || parsed < 0) return 30000;
  return parsed;
}

function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop()?.split(";").shift() || null;
  return null;
}

async function handleUnauthorized(): Promise<void> {
  return;
}

function isLocalDevHost(): boolean {
  if (typeof window === "undefined") return false;
  const host = window.location.hostname;
  return host === "localhost" || host === "127.0.0.1";
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function getConfiguredApiProxyTarget(): string | null {
  const raw = process.env.NEXT_PUBLIC_API_PROXY_TARGET;
  const clean = String(raw || "").trim();
  return clean ? trimTrailingSlash(clean) : null;
}

function getDefaultBackendOrigin(): string {
  const explicit =
    String(process.env.NEXT_PUBLIC_BACKEND_URL || "").trim()
    || String(process.env.BACKEND_URL || "").trim();
  return trimTrailingSlash(explicit || "http://127.0.0.1:8000");
}

export function getBackendHttpUrl(path: string): string {
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  if (typeof window === "undefined") {
    return `${getDefaultBackendOrigin()}${cleanPath}`;
  }

  const proxyTarget = getConfiguredApiProxyTarget();
  if (proxyTarget) {
    return cleanPath;
  }

  if (isLocalDevHost() && cleanPath.startsWith("/api/")) {
    return `${getDefaultBackendOrigin()}${cleanPath}`;
  }

  return cleanPath;
}

export function getBackendWebSocketUrl(path: string): string {
  if (typeof window === "undefined") return "";
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  const proxyTarget = getConfiguredApiProxyTarget();
  if (proxyTarget) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${cleanPath}`;
  }

  if (isLocalDevHost() && cleanPath.startsWith("/api/")) {
    const backendOrigin = new URL(getDefaultBackendOrigin());
    const protocol = backendOrigin.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${backendOrigin.host}${cleanPath}`;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${cleanPath}`;
}

function backendUnavailableMessage(): string {
  return "Backend API is unavailable. Run `scripts/palli_ctl.sh prepare`, then `scripts/palli_ctl.sh start`.";
}

async function apiRequest<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const csrfToken = getCookie("palli_csrf");
  
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const controller = new AbortController();
  if (options.signal) {
    if (options.signal.aborted) {
      controller.abort();
    } else {
      options.signal.addEventListener("abort", () => controller.abort(), { once: true });
    }
  }

  const timeoutMs = getApiTimeoutMs();
  const timeoutHandle = timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null;
  const requestUrl = typeof endpoint === "string" ? getBackendHttpUrl(endpoint) : endpoint;

  let res: Response;
  try {
    res = await fetch(requestUrl, {
      ...options,
      headers,
      credentials: "include", // Ensure cookies are sent
      signal: controller.signal,
    });
  } catch (err: any) {
    if (err?.name === "AbortError") {
      throw new ApiError("Request timed out. Please try again.", 408);
    }
    if (typeof endpoint === "string" && endpoint.startsWith("/api/") && isLocalDevHost()) {
      throw new ApiError(backendUnavailableMessage(), 503);
    }
    throw err;
  } finally {
    if (timeoutHandle) clearTimeout(timeoutHandle);
  }

  if (res.status === 401) {
    void handleUnauthorized();
  }

  if (!res.ok) {
    let errorMessage = "An error occurred";
    let errorData = null;
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      try {
        errorData = await res.json();
        errorMessage = errorData.detail || errorData.message || errorMessage;
      } catch {
        // ignore JSON parse error
      }
    } else {
      let rawText = "";
      try {
        rawText = await res.text();
      } catch {
        rawText = "";
      }
      const looksLikeHtml = /<!doctype html>|<html/i.test(rawText);
      if (looksLikeHtml && typeof endpoint === "string" && endpoint.startsWith("/api/") && isLocalDevHost()) {
        errorMessage = backendUnavailableMessage();
      }
    }
    throw new ApiError(errorMessage, res.status, errorData);
  }

  // Handle empty responses (like 204 No Content)
  if (res.status === 204) {
    return {} as T;
  }

  try {
    return await res.json();
  } catch {
    return {} as T;
  }
}

export function apiGet<T>(endpoint: string) {
  return apiRequest<T>(endpoint, { method: "GET" });
}

export function apiPost<T>(endpoint: string, body: any) {
  return apiRequest<T>(endpoint, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function apiPut<T>(endpoint: string, body: any) {
  return apiRequest<T>(endpoint, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function apiPatch<T>(endpoint: string, body: any) {
  return apiRequest<T>(endpoint, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function apiDelete<T>(endpoint: string) {
  return apiRequest<T>(endpoint, { method: "DELETE" });
}

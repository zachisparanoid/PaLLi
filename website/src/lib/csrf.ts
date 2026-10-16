export function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = document.cookie.match(new RegExp(`(^|; )${escaped}=([^;]*)`));
  return match ? decodeURIComponent(match[2]) : null;
}

export function getCsrfToken(): string | null {
  return getCookie("palli_csrf");
}

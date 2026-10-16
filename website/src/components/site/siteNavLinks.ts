// First door, task 4 (2026-09-17): `saasEra` marks the entries that only mean
// something on a hosted install with sign-in and accounts. The owner's ruling of
// 2026-09-10 #2 — one owner with remembered users, for good — makes them noise
// on a self-hosted box, so local mode hides them. Hidden, not deleted (option A,
// 2026-09-16): the route still exists and the redesign after launch decides its
// fate. `/account/identity` and `/account/memory` are NOT SaaS-era — they are
// the identity and memory workspaces, and they stay in every mode.
export type SiteNavLink = {
  href: string;
  label: string;
  saasEra?: boolean;
};

export const siteNavLinks: readonly SiteNavLink[] = [
  { href: "/dashboard", label: "Mission Control" },
  { href: "/account", label: "Owner controls", saasEra: true },
  { href: "/account/identity", label: "Identity workspace" },
  { href: "/account/memory", label: "Memory workspace" },
  { href: "/reference", label: "Reference" },
];

/** `null` means `/auth/me` has not answered: hide the SaaS-era entries until it does. */
export function visibleSiteNavLinks(localMode: boolean | null): readonly SiteNavLink[] {
  if (localMode === false) return siteNavLinks;
  return siteNavLinks.filter((item) => !item.saasEra);
}

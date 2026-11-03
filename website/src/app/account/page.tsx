"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArrowRight,
  Download,
  Link2,
  Loader2,
  Radio,
  RefreshCw,
  Shield,
  Trash2,
  Unlink,
  User,
} from "lucide-react";

import { Footer } from "@/components/site/footer";
import { Navbar } from "@/components/site/navbar";
import { ConversationHistoryWorkspace } from "@/components/account/ConversationHistoryWorkspace";
import { Button } from "@/components/ui/button";
import { Container } from "@/components/ui/container";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { apiGet, apiPost, getBackendHttpUrl } from "@/lib/api";
import { devLogError } from "@/lib/devLog";
import { DISCORD_INVITE_URL } from "@/lib/externalLinks";
import { safeRedirect } from "@/lib/safeRedirect";
import type { MeResponse, PublicUser } from "@/lib/types";

type ProfileData = {
  name: string;
  interests: string;
  checkInFreq: string;
  voiceEnabled: boolean;
  proactiveEnabled: boolean;
  personalityPreset: string;
};

type AuditLog = {
  action?: string;
  details?: string;
  created_at?: number | string | null;
  occurred_at?: number | string | null;
  timestamp?: number | string | null;
  ip_address?: string | null;
};

type PairingCodeResponse = {
  code: string;
  expires_in: number;
};

const PAIRING_PLATFORMS = ["discord", "telegram", "slack"] as const;
type PairingPlatform = (typeof PAIRING_PLATFORMS)[number];

type PlatformConnection = {
  platform?: string;
  platform_user_id?: string;
  display_name?: string;
  platform_username?: string;
  is_verified?: boolean;
};

type SpaceConnection = {
  platform?: string;
  space_id?: string;
  space_label?: string;
  reply_gate?: string;
};

const PURGE_OPTIONS = [
  { key: "conversations", label: "Conversation history" },
  { key: "memories", label: "Memory entries" },
  { key: "reminders", label: "Reminders" },
  { key: "proactive", label: "Proactive actions" },
  { key: "audit", label: "Audit log entries" },
  { key: "pairing_codes", label: "Channel pairing codes" },
] as const;

const PERSONALITY_PRESETS = [
  { value: "default", label: "Warm Neutral" },
  { value: "professional", label: "Direct Helper" },
  { value: "casual_slang", label: "Relaxed" },
  { value: "minimalist", label: "Minimal" },
  { value: "zen", label: "Calm" },
];

const ADVANCED_OWNER_RECORDS = [
  {
    title: "Stored transcript review",
    copy: "Review what PaLLi actually saved across active and archived threads. Live voice, model, and runtime state still belong in mission control.",
  },
  {
    title: "Backup and reset tools",
    copy: "Download a JSON archive before deliberate cleanup, then use selective resets only when you truly want to clear local state.",
  },
  {
    title: "Accountability log",
    copy: "Recent local maintenance and security-relevant actions stay visible here without pushing you into raw audit or admin tooling.",
  },
] as const;

function formatCountLabel(count: number, singular: string, plural?: string): string {
  if (count === 1) {
    return `1 ${singular}`;
  }
  return `${count} ${plural ?? `${singular}s`}`;
}

function formatPlatformLabel(value: string | null | undefined): string {
  const normalized = (value || "").trim().toLowerCase();
  if (!normalized) return "Unknown";
  return normalized
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatReplyGateLabel(value: string | null | undefined): string {
  const normalized = (value || "").trim().toLowerCase();
  switch (normalized) {
    case "mention_only":
      return "Direct mentions only";
    case "mention_or_reply":
      return "Mentions or replies";
    case "open":
      return "Open replies";
    default:
      return normalized
        ? normalized
          .split(/[_\s-]+/)
          .filter(Boolean)
          .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
          .join(" ")
        : "Mentions or replies";
  }
}

function formatTimestamp(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "Unknown time";

  if (typeof value === "string") {
    const asNumber = Number(value);
    if (Number.isFinite(asNumber)) {
      value = asNumber;
    } else {
      const parsed = new Date(value);
      return Number.isNaN(parsed.getTime()) ? "Unknown time" : parsed.toLocaleString();
    }
  }

  const timestamp = value > 1_000_000_000_000 ? value : value * 1000;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? "Unknown time" : date.toLocaleString();
}

export default function AccountPage() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<PublicUser | null>(null);
  const [authSurface, setAuthSurface] = useState<MeResponse["auth_surface"] | null>(null);
  // First door, task 4 (2026-09-17). It starts TRUE on purpose: this is PaLLi
  // Local, the shipped deployment mode is `local`, and a page that could not ask
  // must not show the SaaS surface to a stranger. Only a `/auth/me` that answers
  // "this is a hosted install" opens the page below.
  const [localMode, setLocalMode] = useState(true);
  const [linksUser, setLinksUser] = useState<PublicUser | null>(null);
  const [platformConnections, setPlatformConnections] = useState<PlatformConnection[]>([]);
  const [spaceConnections, setSpaceConnections] = useState<SpaceConnection[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [profile, setProfile] = useState<ProfileData>({
    name: "",
    interests: "",
    checkInFreq: "normal",
    voiceEnabled: true,
    proactiveEnabled: true,
    personalityPreset: "default",
  });

  const [savingProfile, setSavingProfile] = useState(false);
  const [discordBusy, setDiscordBusy] = useState(false);
  const [pairingBusy, setPairingBusy] = useState(false);
  const [pairingCodes, setPairingCodes] = useState<Record<string, PairingCodeResponse>>({});
  const [purgeConfirm, setPurgeConfirm] = useState("");
  const [purgeBusy, setPurgeBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [purgeTargets, setPurgeTargets] = useState<Record<string, boolean>>({
    conversations: true,
    memories: false,
    reminders: false,
    proactive: false,
    audit: false,
    pairing_codes: false,
  });

  useEffect(() => {
    let mounted = true;

    async function loadSettings() {
      try {
        const [meResult, profileResult, linksResult, auditResult] = await Promise.allSettled([
          apiGet<MeResponse>("/api/web/auth/me"),
          apiGet<ProfileData>("/api/web/account/profile"),
          apiGet<{ user: PublicUser | null; platform_connections?: PlatformConnection[]; space_connections?: SpaceConnection[] }>("/api/web/account/links"),
          apiGet<{ logs: AuditLog[] }>("/api/web/account/audit-logs"),
        ]);

        if (!mounted) return;

        if (meResult.status === "fulfilled" && meResult.value.user) {
          setAuthSurface(meResult.value.auth_surface);
          setLocalMode(meResult.value.local_mode === true);
          setUser(meResult.value.user);
        } else if (meResult.status === "fulfilled") {
          setAuthSurface(meResult.value.auth_surface);
          setLocalMode(meResult.value.local_mode === true);
        } else {
          setLoadError("PaLLi Local could not resolve the local owner.");
        }

        if (profileResult.status === "fulfilled") {
          setProfile(profileResult.value);
        }

        if (linksResult.status === "fulfilled") {
          setLinksUser(linksResult.value.user);
          setPlatformConnections(linksResult.value.platform_connections ?? []);
          setSpaceConnections(linksResult.value.space_connections ?? []);
        }

        if (auditResult.status === "fulfilled") {
          setAuditLogs(auditResult.value.logs ?? []);
        }
      } catch (error) {
        if (!mounted) return;
        devLogError(error);
        setLoadError(error instanceof Error ? error.message : "Failed to load owner controls.");
      } finally {
        if (mounted) setLoading(false);
      }
    }

    void loadSettings();
    return () => {
      mounted = false;
    };
  }, []);

  const personalityPresetOptions = useMemo(() => {
    if (PERSONALITY_PRESETS.some((preset) => preset.value === profile.personalityPreset)) {
      return PERSONALITY_PRESETS;
    }
    if (!profile.personalityPreset) {
      return PERSONALITY_PRESETS;
    }
    return [
      { value: profile.personalityPreset, label: `Current (${profile.personalityPreset})` },
      ...PERSONALITY_PRESETS,
    ];
  }, [profile.personalityPreset]);

  const missionControlEntryLabel = authSurface?.dashboard_auth_mode === "session"
    ? "Session-protected mission control entry"
    : "Direct mission control entry";
  const authRoutesLabel = authSurface?.cloud_auth_routes_enabled ? "Enabled" : "Dormant";
  const securityRoutesLabel = authSurface?.account_security_routes_enabled ? "Enabled" : "Dormant";
  const authFlowNote = authSurface?.cloud_auth_routes_enabled
    ? "Cloud-style sign-in, verification, and recovery routes are enabled on this deployment."
    : "Cloud sign-in, verification, and recovery routes stay dormant in this local deployment.";
  const securityFlowNote = authSurface?.account_security_routes_enabled
    ? "Password and MFA endpoints are enabled for this deployment."
    : "Password and MFA routes stay dormant in this local deployment.";

  function updateProfile<K extends keyof ProfileData>(key: K, value: ProfileData[K]) {
    setProfile((current) => ({ ...current, [key]: value }));
  }

  async function saveProfile() {
    setSavingProfile(true);
    try {
      await apiPost("/api/web/account/profile", {
        name: profile.name,
        interests: profile.interests,
        check_in_freq: profile.checkInFreq,
        voice_enabled: profile.voiceEnabled,
        proactive_enabled: profile.proactiveEnabled,
        personality_preset: profile.personalityPreset,
      });
      window.dispatchEvent(new Event("refresh_memory_galaxy"));
      window.dispatchEvent(new Event("refresh_profile_card"));
      toast({
        variant: "success",
        title: "Profile saved",
        message: "Owner profile and behavior controls updated.",
      });
    } catch (error) {
      devLogError(error);
      toast({
        variant: "error",
        title: "Save failed",
        message: error instanceof Error ? error.message : "Could not update owner profile controls.",
      });
    } finally {
      setSavingProfile(false);
    }
  }

  async function startDiscordLink() {
    setDiscordBusy(true);
    try {
      const result = await apiPost<{ redirect_url?: string }>("/api/web/auth/discord/start", {});
      if (!result.redirect_url) {
        throw new Error("Missing Discord redirect URL");
      }
      safeRedirect(result.redirect_url);
    } catch (error) {
      devLogError(error);
      toast({
        variant: "error",
        title: "Discord link failed",
        message: error instanceof Error ? error.message : "Could not start Discord linking.",
      });
      setDiscordBusy(false);
    }
  }

  async function unlinkDiscord() {
    if (!confirm("Unlink Discord from this local PaLLi instance?")) return;

    setDiscordBusy(true);
    try {
      const result = await apiPost<{ user?: PublicUser | null }>("/api/web/account/links", {
        discord_user_id: "",
      });
      setLinksUser(result.user ?? null);
      toast({
        variant: "success",
        title: "Discord unlinked",
        message: "PaLLi Local will stop treating Discord as the connected account.",
      });
    } catch (error) {
      devLogError(error);
      toast({
        variant: "error",
        title: "Unlink failed",
        message: error instanceof Error ? error.message : "Could not unlink Discord.",
      });
    } finally {
      setDiscordBusy(false);
    }
  }

  async function generatePairingCode(platform: PairingPlatform) {
    setPairingBusy(true);
    try {
      const result = await apiPost<PairingCodeResponse>(`/api/web/account/link/${platform}/code`, {});
      setPairingCodes((current) => ({ ...current, [platform]: result }));
      toast({
        variant: "success",
        title: "Pairing code ready",
        message: `Use the code in ${platform} to link that DM.`,
      });
    } catch (error) {
      devLogError(error);
      toast({
        variant: "error",
        title: "Pairing failed",
        message: error instanceof Error ? error.message : "Could not generate a pairing code.",
      });
    } finally {
      setPairingBusy(false);
    }
  }

  function exportConversationHistory() {
    setExportBusy(true);
    try {
      window.location.href = getBackendHttpUrl("/api/web/account/conversations/export");
      toast({
        variant: "info",
        title: "Export started",
        message: "Your browser should begin downloading the conversation archive.",
      });
    } catch (error) {
      devLogError(error);
      toast({
        variant: "error",
        title: "Export failed",
        message: "Could not start the conversation export.",
      });
    } finally {
      setTimeout(() => setExportBusy(false), 1000);
    }
  }

  function togglePurgeTarget(key: string) {
    setPurgeTargets((current) => ({ ...current, [key]: !current[key] }));
  }

  async function purgeSelectedData() {
    const targets = Object.entries(purgeTargets)
      .filter(([, enabled]) => enabled)
      .map(([key]) => key);

    if (targets.length === 0) {
      toast({
        variant: "error",
        title: "Nothing selected",
        message: "Pick at least one data category to purge.",
      });
      return;
    }

    if (purgeConfirm.trim().toUpperCase() !== "DELETE") {
      toast({
        variant: "error",
        title: "Confirmation required",
        message: 'Type "DELETE" to confirm the purge.',
      });
      return;
    }

    if (!confirm("This will permanently delete the selected PaLLi Local data. Continue?")) {
      return;
    }

    setPurgeBusy(true);
    try {
      await apiPost("/api/web/account/purge", {
        confirm_text: purgeConfirm,
        targets,
      });
      setPurgeConfirm("");
      toast({
        variant: "success",
        title: "Data purged",
        message: "Selected local data was deleted successfully.",
      });
    } catch (error) {
      devLogError(error);
      toast({
        variant: "error",
        title: "Purge failed",
        message: error instanceof Error ? error.message : "Could not purge the selected data.",
      });
    } finally {
      setPurgeBusy(false);
    }
  }

  const linkedDms = platformConnections.filter((item) => Boolean(item.is_verified));
  const discordLinked = Boolean(linksUser?.discord_user_id || user?.discord_user_id);
  const discordConnection = linkedDms.find((item) => (item.platform || "").trim().toLowerCase() === "discord");
  const discordStatusLabel = discordLinked
    ? discordConnection?.display_name || discordConnection?.platform_username || "Linked"
    : "Not linked yet";
  const discordStatusDetail = discordConnection?.platform_user_id || linksUser?.discord_user_id || user?.discord_user_id || null;
  const linkedDmCountLabel = formatCountLabel(linkedDms.length, "linked DM");
  const mappedSpaceCountLabel = formatCountLabel(spaceConnections.length, "mapped space");
  const hasChannelRecords = linkedDms.length > 0 || spaceConnections.length > 0;

  if (loading) {
    // Nothing SaaS-shaped renders before `/auth/me` has answered: a self-hosted
    // install must never flash the owner-account surface at a stranger (first
    // door, task 4, 2026-09-17).
    return (
      <div className="min-h-screen bg-[color:var(--bg)] text-white">
        <Navbar />
        <main className="flex min-h-[calc(100vh-80px)] flex-col items-center justify-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-white/30" />
          <p className="text-sm text-white/40">Checking this install…</p>
        </main>
      </div>
    );
  }

  if (localMode) {
    // The owner's ruling of 2026-09-10 #2: ONE owner with remembered users, for
    // good. There is no account to manage on a self-hosted install, so this page
    // says that in one card and points at the two places that do exist. The SaaS
    // surface below is HIDDEN, not deleted (option A, 2026-09-16) — the route
    // still answers, and the redesign after launch decides its fate.
    return (
      <div className="min-h-screen bg-[color:var(--bg)] text-white selection:bg-brand-500/25">
        <Navbar />

        <main className="pb-20 pt-14">
          <Container>
            <div className="max-w-xl rounded-3xl border border-white/10 bg-white/5 p-8 backdrop-blur-xl">
              <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-brand-300/70">
                Self-hosted
              </div>
              <h1 className="mt-3 text-2xl font-medium text-white">
                Accounts are not used in a self-hosted install.
              </h1>
              <p className="mt-3 text-sm leading-6 text-white/60">
                You are the owner of this one, and she remembers everyone who talks to her. There is
                nothing to sign in to.
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                <Link href="/">
                  <Button>
                    Home
                    <ArrowRight className="h-4 w-4" />
                  </Button>
                </Link>
                <Link href="/dashboard">
                  <Button variant="secondary">Open the control panel</Button>
                </Link>
              </div>
            </div>
          </Container>
        </main>

        <Footer />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[color:var(--bg)] text-white selection:bg-brand-500/25">
      <Navbar />

      <main className="pb-20 pt-14">
        <Container className="space-y-8">
          <section className="rounded-3xl border border-brand-500/15 bg-brand-500/5 p-6 shadow-[0_0_60px_-32px_rgba(56,189,248,0.55)]">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
              <div className="max-w-3xl">
                <div className="text-[11px] font-semibold uppercase tracking-[0.28em] text-brand-300/70">
                  Local Owner Surface
                </div>
                <h1 className="mt-3 text-4xl font-semibold tracking-tight text-white">Owner controls</h1>
                <p className="mt-3 text-sm leading-6 text-white/65">
                  Use this page for owner profile, optional channel linking, and maintenance. Live voice posture,
                  model routing, and runtime health now live in mission control; deeper shaping and document provenance
                  live in the identity workspace; daily captures and durable recall live in the memory workspace so
                  this surface can stay calmer.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Link href="/dashboard">
                    <Button>
                      Open mission control
                      <ArrowRight className="h-4 w-4" />
                    </Button>
                  </Link>
                  <Link href="/account/memory">
                    <Button variant="secondary">Open memory workspace</Button>
                  </Link>
                  <Link href="/account/identity">
                    <Button variant="secondary">Open identity workspace</Button>
                  </Link>
                </div>
                <div className="mt-4 flex flex-wrap gap-2 text-xs text-white/55">
                  <div className="rounded-full border border-white/10 bg-black/20 px-3 py-1.5">
                    Owner basics and channel linking live here.
                  </div>
                  <div className="rounded-full border border-white/10 bg-black/20 px-3 py-1.5">
                    Identity shaping and provenance stay in the identity workspace.
                  </div>
                  <div className="rounded-full border border-white/10 bg-black/20 px-3 py-1.5">
                    Live voice, model, and runtime state stay in mission control.
                  </div>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                  <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Owner</div>
                  <div className="mt-2 text-sm text-white/90">{user?.email ?? "Unavailable"}</div>
                </div>
                <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                  <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Mission Control Entry</div>
                  <div className="mt-2 text-sm text-white/90">{missionControlEntryLabel}</div>
                </div>
              </div>
            </div>

            {loadError ? (
              <div className="mt-5 rounded-2xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
                {loadError}
              </div>
            ) : null}
          </section>

          <section className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
            <div className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-xl">
              <div className="flex items-center gap-3">
                <div className="rounded-2xl bg-brand-500/10 p-3 text-brand-300">
                  <User className="h-5 w-5" />
                </div>
                <div>
                  <h2 className="text-xl font-medium text-white">Owner basics</h2>
                  <p className="text-sm text-white/55">
                    Set the lighter owner defaults here. Mission control still shows what voice lanes, model routing,
                    and runtime posture are active right now.
                  </p>
                </div>
              </div>

              <div className="mt-6 grid gap-5 md:grid-cols-2">
                <div>
                  <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">Display name</label>
                  <Input
                    value={profile.name}
                    onChange={(event) => updateProfile("name", event.target.value)}
                    placeholder="How PaLLi should address you"
                  />
                </div>

                <div>
                  <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">Personality preset</label>
                  <select
                    value={profile.personalityPreset}
                    onChange={(event) => updateProfile("personalityPreset", event.target.value)}
                    className="h-11 w-full rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                  >
                    {personalityPresetOptions.map((preset) => (
                      <option key={preset.value} value={preset.value} className="bg-slate-950">
                        {preset.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">Check-in frequency</label>
                  <select
                    value={profile.checkInFreq}
                    onChange={(event) => updateProfile("checkInFreq", event.target.value)}
                    className="h-11 w-full rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                  >
                    <option value="high" className="bg-slate-950">High</option>
                    <option value="normal" className="bg-slate-950">Normal</option>
                    <option value="low" className="bg-slate-950">Low</option>
                  </select>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    onClick={() => updateProfile("voiceEnabled", !profile.voiceEnabled)}
                    className={`rounded-2xl border px-4 py-3 text-left transition ${
                      profile.voiceEnabled
                        ? "border-brand-500/30 bg-brand-500/10 text-white"
                        : "border-white/10 bg-black/20 text-white/65"
                    }`}
                  >
                    <div className="text-xs uppercase tracking-[0.2em] text-white/35">Voice preference</div>
                    <div className="mt-2 text-sm font-medium">{profile.voiceEnabled ? "Allowed" : "Paused"}</div>
                    <div className="mt-1 text-xs leading-5 text-white/45">
                      Shapes whether PaLLi should answer by voice when a voice lane is already active.
                    </div>
                  </button>

                  <button
                    type="button"
                    onClick={() => updateProfile("proactiveEnabled", !profile.proactiveEnabled)}
                    className={`rounded-2xl border px-4 py-3 text-left transition ${
                      profile.proactiveEnabled
                        ? "border-brand-500/30 bg-brand-500/10 text-white"
                        : "border-white/10 bg-black/20 text-white/65"
                    }`}
                  >
                    <div className="text-xs uppercase tracking-[0.2em] text-white/35">Check-ins</div>
                    <div className="mt-2 text-sm font-medium">{profile.proactiveEnabled ? "Allowed" : "Paused"}</div>
                    <div className="mt-1 text-xs leading-5 text-white/45">
                      Shapes whether PaLLi may start the conversation. Recent activity still appears in mission control.
                    </div>
                  </button>
                </div>
              </div>

              <div className="mt-5">
                <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">Interests</label>
                <textarea
                  value={profile.interests}
                  onChange={(event) => updateProfile("interests", event.target.value)}
                  rows={4}
                  placeholder="Projects, interests, recurring topics, priorities..."
                  className="w-full rounded-2xl border border-white/10 bg-white/5 px-3 py-3 text-sm text-white placeholder:text-white/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                />
              </div>

              <div className="mt-6 flex flex-wrap items-center gap-3">
                <Button onClick={saveProfile} disabled={savingProfile}>
                  {savingProfile ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  Save owner basics
                </Button>
                <Link href="/account/identity">
                  <Button variant="secondary">
                    Open identity workspace
                    <ArrowRight className="h-4 w-4" />
                  </Button>
                </Link>
              </div>
            </div>

            <div className="space-y-6">
              <section className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-xl">
                <div className="flex items-center gap-3">
                  <div className="rounded-2xl bg-[#5865F2]/15 p-3 text-[#9ba3ff]">
                    <Link2 className="h-5 w-5" />
                  </div>
                  <div>
                    <h2 className="text-xl font-medium text-white">Optional channel links</h2>
                    <p className="text-sm text-white/55">
                      Link Discord or pair extra DMs and shared spaces only when this local instance should operate
                      there too.
                    </p>
                  </div>
                </div>

                <div className="mt-5 rounded-2xl border border-white/10 bg-black/20 p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/35">Primary Discord link</div>
                  <div className="mt-2 text-sm text-white/90">{discordStatusLabel}</div>
                  {discordStatusDetail ? (
                    <div className="mt-1 text-xs text-white/45">Discord ID {discordStatusDetail}</div>
                  ) : null}
                  <div className="mt-3 text-sm text-white/60">
                    {discordLinked
                      ? "PaLLi can use the linked Discord identity for DMs, voice, and mapped spaces."
                      : "Keep this disconnected unless you want PaLLi active in Discord."}
                  </div>
                </div>

                <div className="mt-5 flex flex-wrap gap-3">
                  {discordLinked ? (
                    <Button variant="secondary" onClick={unlinkDiscord} disabled={discordBusy}>
                      {discordBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Unlink className="h-4 w-4" />}
                      Unlink Discord
                    </Button>
                  ) : (
                    <Button onClick={startDiscordLink} disabled={discordBusy}>
                      {discordBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
                      Connect Discord
                    </Button>
                  )}

                  <a href={DISCORD_INVITE_URL} target="_blank" rel="noopener noreferrer">
                    <Button variant="outline">
                      Join Discord
                      <ArrowRight className="h-4 w-4" />
                    </Button>
                  </a>
                </div>

                <details className="mt-6 rounded-2xl border border-white/10 bg-black/20 p-4">
                  <summary className="flex cursor-pointer list-none items-start justify-between gap-4">
                    <div>
                      <div className="text-xs uppercase tracking-[0.22em] text-white/35">Advanced channel pairing</div>
                      <p className="mt-2 text-sm text-white/60">
                        Generate short-lived codes only when you want PaLLi attached to another DM or shared space.
                        Live presence and runtime state still belong in mission control.
                      </p>
                    </div>
                    <div className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-white/45">
                      {linkedDmCountLabel} | {mappedSpaceCountLabel}
                    </div>
                  </summary>

                  <div className="mt-4 grid gap-3 md:grid-cols-3">
                    <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-4">
                      <div className="text-xs uppercase tracking-[0.22em] text-white/35">Linked direct messages</div>
                      <div className="mt-2 text-lg font-medium text-white">{linkedDmCountLabel}</div>
                      <p className="mt-2 text-sm leading-6 text-white/55">
                        Verified owner DMs PaLLi can use after you pair them. Open the record list only when you need
                        to confirm which conversations are attached.
                      </p>
                    </div>
                    <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-4">
                      <div className="text-xs uppercase tracking-[0.22em] text-white/35">Mapped shared spaces</div>
                      <div className="mt-2 text-lg font-medium text-white">{mappedSpaceCountLabel}</div>
                      <p className="mt-2 text-sm leading-6 text-white/55">
                        Shared spaces PaLLi may answer in after pairing. Review the saved list here, but use mission
                        control when you want to inspect live activity.
                      </p>
                    </div>
                    <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-4">
                      <div className="text-xs uppercase tracking-[0.22em] text-white/35">Pairing flow</div>
                      <div className="mt-2 text-lg font-medium text-white">Short-lived code</div>
                      <p className="mt-2 text-sm leading-6 text-white/55">
                        Generate a code here, paste it into the target chat, and let PaLLi link that surface without
                        turning owner controls into a channel console.
                      </p>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-3 md:grid-cols-3">
                    {PAIRING_PLATFORMS.map((platform) => {
                      const code = pairingCodes[platform];
                      return (
                        <div key={platform} className="rounded-2xl border border-white/10 bg-brand-500/10 px-4 py-3">
                          <div className="flex items-center justify-between gap-3">
                            <div className="text-xs uppercase tracking-[0.22em] text-brand-200/70">{formatPlatformLabel(platform)}</div>
                            <Button variant="secondary" size="sm" onClick={() => void generatePairingCode(platform)} disabled={pairingBusy}>
                              {pairingBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Radio className="h-4 w-4" />}
                              Generate
                            </Button>
                          </div>
                          <div className="mt-3 font-mono text-xl tracking-[0.2em] text-white">{code?.code || "----"}</div>
                          <div className="mt-2 text-xs text-white/55">
                            {code ? `Expires in about ${Math.max(1, Math.round(code.expires_in / 60))} minutes.` : "No active code."}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <p className="mt-4 text-sm text-white/55">
                    Use the short code inside the target conversation. Discord still accepts{" "}
                    <span className="mx-1 font-mono text-white/80">/link_account CODE</span>, while Telegram and
                    Slack accept <span className="mx-1 font-mono text-white/80">link CODE</span>.
                  </p>

                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                    <details className="rounded-2xl border border-white/10 bg-black/20 p-4">
                      <summary className="flex cursor-pointer list-none items-start justify-between gap-3">
                        <div>
                          <div className="text-xs uppercase tracking-[0.22em] text-white/35">Review linked DM records</div>
                          <p className="mt-2 text-sm text-white/55">
                            Secondary reference only. This is the saved DM inventory, not a live channel inspector.
                          </p>
                        </div>
                        <div className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-white/45">
                          {linkedDmCountLabel}
                        </div>
                      </summary>
                      <div className="mt-4 space-y-2">
                        {linkedDms.length > 0 ? linkedDms.map((item) => (
                          <div key={`${item.platform}:${item.platform_user_id}`} className="rounded-xl border border-white/10 bg-white/5 px-3 py-3 text-sm text-white/80">
                            <div>{item.display_name || item.platform_username || item.platform_user_id || "Unnamed DM"}</div>
                            <div className="mt-1 text-xs text-white/45">
                              {formatPlatformLabel(item.platform)} linked DM
                            </div>
                            {item.platform_user_id ? (
                              <div className="mt-2 text-[11px] uppercase tracking-[0.18em] text-white/30">
                                Record {item.platform_user_id}
                              </div>
                            ) : null}
                          </div>
                        )) : <div className="text-sm text-white/45">No linked DMs yet.</div>}
                      </div>
                      </details>
                      <details className="rounded-2xl border border-white/10 bg-black/20 p-4">
                        <summary className="flex cursor-pointer list-none items-start justify-between gap-3">
                          <div>
                            <div className="text-xs uppercase tracking-[0.22em] text-white/35">Review mapped shared-space records</div>
                            <p className="mt-2 text-sm text-white/55">
                              Secondary reference only. Review saved shared spaces here, then use mission control for
                              live runtime truth.
                            </p>
                          </div>
                          <div className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-white/45">
                            {mappedSpaceCountLabel}
                          </div>
                        </summary>
                        <div className="mt-4 space-y-2">
                          {spaceConnections.length > 0 ? spaceConnections.map((item) => (
                            <div key={`${item.platform}:${item.space_id}`} className="rounded-xl border border-white/10 bg-white/5 px-3 py-3 text-sm text-white/80">
                              <div>{item.space_label || item.space_id || "Unnamed space"}</div>
                              <div className="mt-1 text-xs text-white/45">
                                {formatPlatformLabel(item.platform)} shared space
                              </div>
                              <div className="mt-2 text-[11px] uppercase tracking-[0.18em] text-white/30">
                                Reply gate: {formatReplyGateLabel(item.reply_gate)}
                              </div>
                            </div>
                          )) : <div className="text-sm text-white/45">No mapped spaces yet.</div>}
                        </div>
                      </details>
                    </div>

                    {!hasChannelRecords ? (
                      <div className="mt-4 rounded-2xl border border-dashed border-white/10 bg-white/[0.03] px-4 py-4 text-sm text-white/50">
                        No saved DM or shared-space records yet. Generate a pairing code only when you want PaLLi to
                        join another conversation surface.
                      </div>
                    ) : null}
                  </details>
              </section>

              <section className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-xl">
                <div className="flex items-center gap-3">
                  <div className="rounded-2xl bg-white/10 p-3 text-white">
                    <Shield className="h-5 w-5" />
                  </div>
                  <div>
                    <h2 className="text-xl font-medium text-white">Owner entry posture</h2>
                    <p className="text-sm text-white/55">
                      Read-only backend notes about how this local owner enters mission control and which cloud-era
                      routes stay dormant on this deployment.
                    </p>
                  </div>
                </div>

                <div className="mt-5 grid gap-3 sm:grid-cols-3">
                  <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                    <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Mission control entry</div>
                    <div className="mt-2 text-sm text-white/90">{missionControlEntryLabel}</div>
                  </div>
                  <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                    <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Cloud auth routes</div>
                    <div className="mt-2 text-sm text-white/90">{authRoutesLabel}</div>
                  </div>
                  <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                    <div className="text-[11px] uppercase tracking-[0.22em] text-white/35">Security routes</div>
                    <div className="mt-2 text-sm text-white/90">{securityRoutesLabel}</div>
                  </div>
                </div>

                <ul className="mt-5 space-y-3 text-sm text-white/65">
                  <li className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                    Mission control, not this page, is where live voice lanes, model routing, runtime drift, and
                    current health stay visible. This surface stays focused on owner defaults and optional channel
                    linking.
                  </li>
                  <li className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                    {authSurface?.dashboard_auth_mode === "session"
                      ? "This deployment expects a session-backed mission control sign-in."
                      : "Root route goes straight to mission control for the local owner."}
                  </li>
                  <li className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                    {authFlowNote}
                  </li>
                  <li className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                    {securityFlowNote}
                  </li>
                  <li className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                    Discord is optional and only needed if this instance should operate through Discord.
                  </li>
                </ul>

                <div className="mt-5">
                  <Link href="/dashboard">
                    <Button variant="secondary">
                      Open mission control
                      <ArrowRight className="h-4 w-4" />
                    </Button>
                  </Link>
                </div>
              </section>
            </div>
          </section>

          <details className="rounded-3xl border border-white/10 bg-white/5 p-6 backdrop-blur-xl">
            <summary className="cursor-pointer list-none text-xl font-medium text-white">
              Advanced records and recovery
            </summary>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-white/55">
              Stored transcript review, backup export, selective resets, and recent accountability notes stay here only
              when you need them. This is the owner records lane for what PaLLi already stored, not a live runtime
              inspector. Owner basics and channel linking stay above, mission control still owns current voice, model,
              and runtime state, and the memory workspace stays the calmer place for continuity browsing and durable
              recall.
            </p>

            <div className="mt-4 flex flex-wrap gap-3">
              <Link href="/dashboard">
                <Button variant="secondary">
                  Back to mission control
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
              <Link href="/account/memory">
                <Button variant="secondary">
                  Open memory workspace
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
            </div>

            <div className="mt-6 grid gap-4 lg:grid-cols-3">
              {ADVANCED_OWNER_RECORDS.map((item) => (
                <div key={item.title} className="rounded-2xl border border-white/10 bg-black/20 px-4 py-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/35">{item.title}</div>
                  <p className="mt-3 text-sm leading-6 text-white/60">{item.copy}</p>
                </div>
              ))}
            </div>

            <div className="mt-6">
              <ConversationHistoryWorkspace />
            </div>

            <section className="mt-6 grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
              <div className="rounded-3xl border border-white/10 bg-black/20 p-6">
                <div className="flex items-center gap-3">
                  <div className="rounded-2xl bg-white/10 p-3 text-white">
                    <Archive className="h-5 w-5" />
                  </div>
                  <div>
                    <h2 className="text-xl font-medium text-white">Backup and reset tools</h2>
                    <p className="text-sm text-white/55">
                      Keep a local archive handy and reserve resets for deliberate cleanup, not everyday continuity review.
                    </p>
                  </div>
                </div>

                <div className="mt-6 rounded-2xl border border-white/10 bg-black/20 p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-white/35">JSON backup</div>
                  <p className="mt-2 text-sm text-white/60">
                    Download stored conversations as JSON before larger cleanup or when you want an offline archive of
                    this local owner history.
                  </p>
                  <div className="mt-4">
                    <Button variant="secondary" onClick={exportConversationHistory} disabled={exportBusy}>
                      {exportBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                      Download JSON backup
                    </Button>
                  </div>
                </div>

                <div className="mt-5 rounded-2xl border border-rose-500/15 bg-rose-500/5 p-4">
                  <div className="text-xs uppercase tracking-[0.22em] text-rose-200/70">Selective reset</div>
                  <p className="mt-2 text-sm text-rose-100/75">
                    Use this only when you intentionally want to clear local state. For normal recall and continuity
                    browsing, stay in the memory workspace instead.
                  </p>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    {PURGE_OPTIONS.map((option) => {
                      const checked = purgeTargets[option.key];
                      return (
                        <button
                          key={option.key}
                          type="button"
                          onClick={() => togglePurgeTarget(option.key)}
                          className={`rounded-2xl border px-4 py-3 text-left transition ${
                            checked
                              ? "border-rose-400/30 bg-rose-500/10 text-white"
                              : "border-white/10 bg-black/20 text-white/65"
                          }`}
                        >
                          <div className="text-sm font-medium">{option.label}</div>
                          <div className="mt-1 text-xs uppercase tracking-[0.18em] text-white/35">
                            {checked ? "Selected" : "Not selected"}
                          </div>
                        </button>
                      );
                    })}
                  </div>

                  <div className="mt-4">
                    <label className="mb-2 block text-xs uppercase tracking-[0.22em] text-white/35">Type DELETE to confirm</label>
                    <Input value={purgeConfirm} onChange={(event) => setPurgeConfirm(event.target.value)} placeholder="DELETE" />
                  </div>

                  <div className="mt-4">
                    <Button variant="secondary" onClick={purgeSelectedData} disabled={purgeBusy}>
                      {purgeBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                      Clear selected data
                    </Button>
                  </div>
                </div>
              </div>

              <div className="rounded-3xl border border-white/10 bg-black/20 p-6">
                <div className="flex items-center gap-3">
                  <div className="rounded-2xl bg-white/10 p-3 text-white">
                    <Shield className="h-5 w-5" />
                  </div>
                  <div>
                    <h2 className="text-xl font-medium text-white">Recent accountability log</h2>
                    <p className="text-sm text-white/55">
                      A short owner-facing trail of recent local maintenance, safety, and owner-control actions.
                    </p>
                  </div>
                </div>

                <div className="mt-6 space-y-3">
                  {auditLogs.length ? (
                    auditLogs.slice(0, 12).map((log, index) => {
                      const when = log.created_at ?? log.occurred_at ?? log.timestamp ?? null;
                      return (
                        <div key={`${log.action}-${index}-${when ?? "unknown"}`} className="rounded-2xl border border-white/10 bg-black/20 px-4 py-3">
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="text-sm font-medium text-white">{log.action || "Unknown action"}</div>
                            <div className="text-xs uppercase tracking-[0.18em] text-white/35">{formatTimestamp(when)}</div>
                          </div>
                          {log.details ? <div className="mt-2 text-sm leading-6 text-white/60">{log.details}</div> : null}
                          {log.ip_address ? (
                            <div className="mt-2 text-xs uppercase tracking-[0.18em] text-white/30">ip {log.ip_address}</div>
                          ) : null}
                        </div>
                      );
                    })
                  ) : (
                    <div className="rounded-2xl border border-white/10 bg-black/20 px-4 py-5 text-sm text-white/55">
                      No recent accountability events yet for this local owner.
                    </div>
                  )}
                </div>
              </div>
            </section>
          </details>
        </Container>
      </main>

      <Footer />
    </div>
  );
}

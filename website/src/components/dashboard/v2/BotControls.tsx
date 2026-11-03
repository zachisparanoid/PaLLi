"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Loader2,
  MessageCircle,
  Radio,
  Save,
  Sparkles,
  User,
} from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import { devLogError } from "@/lib/devLog";
import { useLocalMode } from "@/lib/useLocalMode";

type ProfileData = {
  name: string;
  interests: string;
  checkInFreq: string;
  voiceEnabled: boolean;
  proactiveEnabled: boolean;
  discordUserId: string;
  personalityPreset: string;
};

const SIMPLE_PRESETS = [
  { value: "default", label: "Warm Neutral", description: "Balanced companion energy." },
  { value: "professional", label: "Direct Helper", description: "Clear and task-focused." },
  { value: "casual_slang", label: "Relaxed", description: "More casual and friendly." },
  { value: "minimalist", label: "Minimal", description: "Shorter, lighter replies." },
  { value: "zen", label: "Calm", description: "Softer and more grounded." },
] as const;

const CHECKIN_OPTIONS = [
  { value: "high", label: "Frequent" },
  { value: "normal", label: "Normal" },
  { value: "low", label: "Rare" },
] as const;

export function BotControls() {
  // First door, task 4 (2026-09-17): the two "Owner controls" doors out of this
  // card lead to the SaaS-era account page, which a self-hosted install hides.
  const showOwnerControls = useLocalMode() === false;
  const [profile, setProfile] = useState<ProfileData>({
    name: "",
    interests: "",
    checkInFreq: "normal",
    voiceEnabled: true,
    proactiveEnabled: false,
    discordUserId: "",
    personalityPreset: "default",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pinging, setPinging] = useState(false);

  useEffect(() => {
    async function loadProfile() {
      try {
        const [profileResult, linksResult] = await Promise.allSettled([
          apiGet<ProfileData>("/api/web/account/profile"),
          apiGet<{ user: { discord_user_id?: string } }>("/api/web/account/links"),
        ]);

        const profileData = profileResult.status === "fulfilled" ? profileResult.value : null;
        const linksData = linksResult.status === "fulfilled" ? linksResult.value : null;

        if (profileData) {
          setProfile({
            ...profileData,
            discordUserId: linksData?.user?.discord_user_id || "",
          });
        } else if (linksData) {
          setProfile((current) => ({ ...current, discordUserId: linksData.user?.discord_user_id || "" }));
        }
      } catch (error) {
        devLogError("Failed to load profile", error);
      } finally {
        setLoading(false);
      }
    }

    void loadProfile();

    function handleRefresh() {
      setLoading(true);
      void loadProfile();
    }

    window.addEventListener("refresh_companion_basics", handleRefresh);
    return () => {
      window.removeEventListener("refresh_companion_basics", handleRefresh);
    };
  }, []);

  const presetOptions = useMemo(() => {
    if (SIMPLE_PRESETS.some((preset) => preset.value === profile.personalityPreset)) {
      return SIMPLE_PRESETS;
    }
    if (!profile.personalityPreset) {
      return SIMPLE_PRESETS;
    }
    return [
      {
        value: profile.personalityPreset,
        label: `Current (${profile.personalityPreset})`,
        description: "A previously selected preset that is not shown in the simplified list.",
      },
      ...SIMPLE_PRESETS,
    ];
  }, [profile.personalityPreset]);

  async function handleSave() {
    setSaving(true);
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
    } catch (error) {
      devLogError("Save failed", error);
    } finally {
      setTimeout(() => setSaving(false), 500);
    }
  }

  async function handlePing() {
    setPinging(true);
    try {
      await apiPost("/api/web/account/test-ping", {});
    } catch (error) {
      devLogError(error);
    } finally {
      setTimeout(() => setPinging(false), 2000);
    }
  }

  function update<K extends keyof ProfileData>(key: K, value: ProfileData[K]) {
    setProfile((current) => ({ ...current, [key]: value }));
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center rounded-2xl border border-white/5 bg-black/40 text-white/20 backdrop-blur-md">
        <Loader2 className="animate-spin" />
      </div>
    );
  }

  const currentPreset = presetOptions.find((preset) => preset.value === profile.personalityPreset);

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-white/5 bg-black/40 shadow-xl backdrop-blur-md">
      <div className="flex h-12 items-center justify-between border-b border-white/5 bg-white/[0.02] px-4">
        <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-white/40">
          <User className="h-3.5 w-3.5" />
          <span>Owner Basics</span>
        </div>
        {saving ? <span className="text-[10px] text-brand-400 animate-pulse">SAVING...</span> : null}
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto p-5 scrollbar-thin scrollbar-thumb-white/10">
        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-white/5 p-2 text-white/70">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-medium text-white">Small set of controls, big effect</h3>
              <p className="mt-2 text-sm leading-6 text-white/60">
                These are the owner basics most people actually need. They update owner defaults, not live runtime
                status. Mission control still shows what is actually running now, while the identity workspace holds
                shaping docs and provenance, the memory workspace holds continuity and durable recall, and channel
                pairing plus maintenance stay off the mission-control shell.
              </p>
            </div>
          </div>
        </div>

        <section className="space-y-3">
          <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/35">How PaLLi relates to you</div>
          <div>
            <label className="mb-2 block text-xs text-white/50">What should PaLLi call you?</label>
            <input
              type="text"
              value={profile.name}
              onChange={(event) => update("name", event.target.value)}
              className="w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2.5 text-sm text-white outline-none"
              placeholder="Your name or nickname"
            />
          </div>
          <div>
            <label className="mb-2 block text-xs text-white/50">Companion style</label>
            <select
              value={profile.personalityPreset}
              onChange={(event) => update("personalityPreset", event.target.value)}
              className="w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2.5 text-sm text-white outline-none"
            >
              {presetOptions.map((preset) => (
                <option key={preset.value} value={preset.value} className="bg-slate-950 text-white">
                  {preset.label}
                </option>
              ))}
            </select>
            {currentPreset ? <p className="mt-2 text-xs text-white/45">{currentPreset.description}</p> : null}
          </div>
        </section>

        <section className="space-y-3">
          <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/35">Check-ins and follow-through</div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm text-white/90">Allow check-ins</div>
                <div className="mt-1 text-xs text-white/45">
                  Owner preference only. Mission control still shows recent proactive activity.
                </div>
              </div>
              <button
                type="button"
                onClick={() => update("proactiveEnabled", !profile.proactiveEnabled)}
                className={`relative h-6 w-11 rounded-full transition-colors ${
                  profile.proactiveEnabled ? "bg-amber-500" : "bg-white/10"
                }`}
              >
                <div
                  className={`absolute top-1 h-4 w-4 rounded-full bg-white transition-all ${
                    profile.proactiveEnabled ? "left-6" : "left-1"
                  }`}
                />
              </button>
            </div>
            <div className={`mt-4 ${profile.proactiveEnabled ? "" : "opacity-50"}`}>
              <label className="mb-2 block text-xs text-white/50">How often should PaLLi start the conversation?</label>
              <select
                value={profile.checkInFreq}
                disabled={!profile.proactiveEnabled}
                onChange={(event) => update("checkInFreq", event.target.value)}
                className="w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2.5 text-sm text-white outline-none disabled:cursor-not-allowed"
              >
                {CHECKIN_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value} className="bg-slate-950 text-white">
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </section>

        <section className="space-y-3">
          <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/35">Surface handoff</div>
          <div className="grid gap-3">
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="text-sm text-white/90">Browser voice console</div>
                  <div className="mt-1 text-xs text-white/45">
                    The dedicated browser voice lane keeps Native PaLLi primary and leaves direct OpenAI Realtime as the comparison path.
                  </div>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2 text-sm text-white/90">
                    <MessageCircle className="h-4 w-4 text-[#9ba3ff]" />
                    Discord status
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] ${
                        profile.discordUserId
                          ? "border border-emerald-400/20 bg-emerald-400/10 text-emerald-100"
                          : "border border-white/10 bg-white/[0.04] text-white/55"
                      }`}
                    >
                      {profile.discordUserId ? "Linked" : "Optional"}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-white/45">
                    {profile.discordUserId
                      ? "Mission control can show live Discord presence, while owner controls handle unlinking, pairing codes, and linked DM or shared-space records."
                      : "Link Discord from owner controls only if this local instance should operate there too."}
                  </div>
                </div>
                {showOwnerControls ? (
                  <Link
                    href="/account"
                    className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-white/80 transition-colors hover:bg-white/[0.06]"
                  >
                    Owner controls
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                ) : null}
              </div>
            </div>
          </div>
        </section>

        <div className="rounded-2xl border border-white/10 bg-black/25 p-4">
          <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/35">Need deeper inspection?</div>
          <p className="mt-2 text-xs leading-5 text-white/45">
            Owner controls handle optional channel linking, transcript review, JSON backup, selective resets, and the
            accountability log. The identity workspace keeps shaping docs and compiled posture separate, while the
            memory workspace keeps daily captures, curated recall, and continuity search outside the mission-control
            shell.
          </p>
          <div className="mt-3 flex flex-wrap gap-3">
            <Link
              href="/account/identity"
              className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white/80 transition-colors hover:bg-white/[0.06]"
            >
              Identity workspace
              <ArrowRight className="h-4 w-4" />
            </Link>
            {showOwnerControls ? (
              <Link
                href="/account"
                className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white/80 transition-colors hover:bg-white/[0.06]"
              >
                Owner controls
                <ArrowRight className="h-4 w-4" />
              </Link>
            ) : null}
            <Link
              href="/account/memory"
              className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white/80 transition-colors hover:bg-white/[0.06]"
            >
              Memory workspace
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between gap-4 border-t border-white/5 bg-black/20 p-4">
        <button
          type="button"
          onClick={handlePing}
          disabled={pinging}
          className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-medium text-white/70 transition-colors hover:bg-white/10 disabled:opacity-50"
        >
          <Radio className={`h-3.5 w-3.5 ${pinging ? "animate-ping" : ""}`} />
          {pinging ? "Sending..." : "Send test ping"}
        </button>

        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-xl bg-white px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-white/90 disabled:opacity-50"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {saving ? "Saving..." : "Save owner basics"}
        </button>
      </div>
    </div>
  );
}

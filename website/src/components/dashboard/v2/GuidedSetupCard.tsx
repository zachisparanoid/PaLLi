"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Loader2, Mic2, Shield, Sparkles, UserRound, type LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { apiGet, apiPost } from "@/lib/api";
import { cn } from "@/lib/utils";

type SetupDraft = {
  name: string;
  companionStyle: string;
  autonomyPreset: string;
  relationshipStyle: string;
  checkinPosture: string;
  voicePosture: string;
  proactiveEnabled: boolean;
  checkInFreq: string;
  voiceEnabled: boolean;
};

type SetupOption = {
  value: string;
  label: string;
  description?: string | null;
};

type SetupQuestionArtifact = {
  source: string;
  artifactKey: string;
  artifactLabel: string;
  artifactPath?: string | null;
  fieldPath?: string | null;
  fieldLabel?: string | null;
  reason: string;
  fields?: string[];
  fieldLabels?: string[];
};

type SetupQuestionProvenance = {
  completed: boolean;
  summary?: string | null;
  artifacts: SetupQuestionArtifact[];
};

type SetupQuestion = {
  target: string;
  title: string;
  prompt: string;
  note: string;
  inputKind: "text" | "single_choice";
  placeholder?: string | null;
  required: boolean;
  options: SetupOption[];
  provenance?: SetupQuestionProvenance | null;
};

type SetupContract = {
  questionOrder: string[];
  questions: SetupQuestion[];
  maxQuestionsPerTurn: number;
};

type SetupResponse = {
  setupCompleted: boolean;
  currentStep: string;
  completedSteps: string[];
  contract: SetupContract;
  draft: SetupDraft;
  identityPlatform?: string;
  ownerUserId?: number | null;
};

type GuidedSetupCardProps = {
  onComplete?: () => void;
};

const DEFAULT_DRAFT: SetupDraft = {
  name: "",
  companionStyle: "warm",
  autonomyPreset: "balanced",
  relationshipStyle: "",
  checkinPosture: "normal",
  voicePosture: "balanced",
  proactiveEnabled: true,
  checkInFreq: "normal",
  voiceEnabled: true,
};

const TARGET_ICONS: Record<string, LucideIcon> = {
  user_name: UserRound,
  companion_style: Sparkles,
  autonomy_preset: Shield,
  checkin_posture: Sparkles,
  voice_posture: Mic2,
  relationship_style: Sparkles,
};

function getQuestionValue(draft: SetupDraft, target: string): string {
  switch (target) {
    case "user_name":
      return draft.name;
    case "companion_style":
      return draft.companionStyle;
    case "autonomy_preset":
      return draft.autonomyPreset;
    case "checkin_posture":
      return draft.checkinPosture;
    case "voice_posture":
      return draft.voicePosture;
    case "relationship_style":
      return draft.relationshipStyle;
    default:
      return "";
  }
}

function buildDraftUpdate(target: string, value: string): Partial<SetupDraft> {
  switch (target) {
    case "user_name":
      return { name: value };
    case "companion_style":
      return { companionStyle: value };
    case "autonomy_preset":
      return { autonomyPreset: value };
    case "checkin_posture":
      return { checkinPosture: value };
    case "voice_posture":
      return { voicePosture: value };
    case "relationship_style":
      return { relationshipStyle: value };
    default:
      return {};
  }
}

function canContinueQuestion(question: SetupQuestion | null, draft: SetupDraft): boolean {
  if (!question) {
    return false;
  }
  if (!question.required) {
    return true;
  }
  if (question.inputKind === "text") {
    return draft.name.trim().length > 0;
  }
  return getQuestionValue(draft, question.target).trim().length > 0;
}

function isQuestionCompleted(question: SetupQuestion, completedSteps: Set<string>): boolean {
  return completedSteps.has(question.target) || Boolean(question.provenance?.completed);
}

function resolveStepIndex(setup: SetupResponse): number {
  const completed = new Set(setup.completedSteps.filter((step) => step !== "profile_setup"));
  const pendingQuestions = setup.contract.questions.filter((question) => !isQuestionCompleted(question, completed));
  if (pendingQuestions.length === 0) {
    return 0;
  }

  const pendingPrefix = "guided_setup:";
  if (setup.currentStep.startsWith(pendingPrefix)) {
    const pendingTarget = setup.currentStep.slice(pendingPrefix.length);
    const pendingIndex = pendingQuestions.findIndex((question) => question.target === pendingTarget);
    if (pendingIndex >= 0) {
      return pendingIndex;
    }
  }

  return 0;
}

function getOptionGridClasses(optionCount: number): string {
  if (optionCount >= 5) {
    return "grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5";
  }
  if (optionCount === 4) {
    return "grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4";
  }
  if (optionCount === 3) {
    return "grid grid-cols-1 gap-3 md:grid-cols-3";
  }
  return "grid grid-cols-1 gap-3 md:grid-cols-2";
}

function OptionCard({
  selected,
  title,
  description,
  onClick,
}: {
  selected: boolean;
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-2xl border p-4 text-left transition-colors",
        selected
          ? "border-brand-400/60 bg-brand-500/10 text-white"
          : "border-white/10 bg-white/[0.03] text-white/75 hover:bg-white/[0.05]",
      )}
    >
      <div className="text-sm font-medium">{title}</div>
      <div className="mt-2 text-sm leading-6 text-white/55">{description}</div>
    </button>
  );
}

export function GuidedSetupCard({ onComplete }: GuidedSetupCardProps) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [step, setStep] = useState(0);
  const [setup, setSetup] = useState<SetupResponse | null>(null);
  const [draft, setDraft] = useState<SetupDraft>(DEFAULT_DRAFT);

  useEffect(() => {
    let mounted = true;

    async function loadSetup() {
      try {
        const data = await apiGet<SetupResponse>("/api/web/account/setup");
        if (!mounted) return;
        setSetup(data);
        setDraft({ ...DEFAULT_DRAFT, ...data.draft });
        setStep(resolveStepIndex(data));
        setExpanded(Boolean(data.currentStep.startsWith("guided_setup:")));
      } catch (error) {
        if (!mounted) return;
        toast({
          title: "First contact",
          message: error instanceof Error ? error.message : "Failed to load guided setup.",
        });
      } finally {
        if (mounted) setLoading(false);
      }
    }

    void loadSetup();
    return () => {
      mounted = false;
    };
  }, [toast]);

  function updateTarget(target: string, value: string) {
    setDraft((current) => ({ ...current, ...buildDraftUpdate(target, value) }));
  }

  const questions = setup?.contract.questions ?? [];
  const completedSteps = new Set((setup?.completedSteps ?? []).filter((step) => step !== "profile_setup"));
  const pendingQuestions = questions.filter((question) => !isQuestionCompleted(question, completedSteps));
  const inferredQuestions = questions.filter(
    (question) => isQuestionCompleted(question, completedSteps) && question.provenance?.artifacts?.length,
  );
  const activeStep = pendingQuestions.length > 0 ? Math.min(step, pendingQuestions.length - 1) : 0;
  const currentQuestion = pendingQuestions[activeStep] ?? null;
  const currentValue = currentQuestion ? getQuestionValue(draft, currentQuestion.target) : "";
  const onLastStep = pendingQuestions.length > 0 && activeStep === pendingQuestions.length - 1;
  const remainingPromptCount = pendingQuestions.length;
  const learnedCount = inferredQuestions.length;

  useEffect(() => {
    if (step >= pendingQuestions.length && pendingQuestions.length > 0) {
      setStep(pendingQuestions.length - 1);
    }
  }, [pendingQuestions.length, step]);

  async function handleSave() {
    setSaving(true);
    try {
      await apiPost("/api/web/account/setup", {
        name: draft.name.trim(),
        companion_style: draft.companionStyle,
        autonomy_preset: draft.autonomyPreset,
        checkin_posture: draft.checkinPosture,
        voice_posture: draft.voicePosture,
        relationship_style: draft.relationshipStyle || undefined,
      });

      setSetup((current) => (current ? { ...current, setupCompleted: true } : current));
      toast({
        variant: "success",
        title: "Starter baseline saved",
        message: "Mission control saved the same first-contact baseline PaLLi can keep refining in conversation.",
      });
      window.dispatchEvent(new Event("refresh_profile_card"));
      window.dispatchEvent(new Event("refresh_companion_basics"));
      onComplete?.();
    } catch (error) {
      toast({
        title: "First contact",
        message: error instanceof Error ? error.message : "Failed to save guided setup.",
      });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[220px] items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03]">
        <Loader2 className="h-5 w-5 animate-spin text-white/35" />
      </div>
    );
  }

  if (!setup || setup.setupCompleted || dismissed) {
    return null;
  }

  if (pendingQuestions.length === 0 || !currentQuestion) {
    return null;
  }

  return (
    <div className="rounded-2xl border border-brand-500/25 bg-brand-500/5 p-5 md:p-6">
      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="max-w-3xl">
            <div className="text-[11px] font-bold uppercase tracking-[0.24em] text-brand-300/80">First-Contact Mirror</div>
            <h2 className="mt-2 text-2xl font-medium text-white">Conversation should shape this first</h2>
            <p className="mt-3 text-sm leading-6 text-white/65">
              PaLLi normally learns these owner basics in chat or voice. Mission control only exposes this fallback
              when you want to seed the same starting posture without turning first contact into a dashboard ritual.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button variant="secondary" size="sm" onClick={() => setExpanded((current) => !current)}>
              {expanded ? "Hide fallback prompts" : "Review fallback prompts"}
            </Button>
            <button
              type="button"
              onClick={() => setDismissed(true)}
              className="self-start text-sm text-white/45 transition-colors hover:text-white/75"
            >
              Skip and let conversation handle it
            </button>
          </div>
        </div>

        <div className="flex flex-wrap gap-2 text-xs text-white/55">
          <div className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5">
            Preferred path: normal chat or voice first.
          </div>
          <div className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5">
            {remainingPromptCount} fallback prompt{remainingPromptCount === 1 ? "" : "s"} left.
          </div>
          {learnedCount > 0 ? (
            <div className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5">
              {learnedCount} answer{learnedCount === 1 ? "" : "s"} already learned from current owner state.
            </div>
          ) : null}
        </div>

        {!expanded ? (
          <div className="rounded-2xl border border-white/10 bg-black/20 p-4 text-sm leading-6 text-white/60">
            Open the fallback only if you want to seed these answers from mission control now. PaLLi can keep learning
            naturally, and deeper shaping still belongs in the identity workspace instead of a setup wizard.
          </div>
        ) : null}

        {expanded ? (
          <>
            <div className="flex flex-wrap gap-2">
              {pendingQuestions.map((question, index) => {
                const Icon = TARGET_ICONS[question.target] ?? Sparkles;
                const active = index === activeStep;
                return (
                  <div
                    key={question.target}
                    className={cn(
                      "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs",
                      active
                        ? "border-brand-400/60 bg-brand-500/10 text-white"
                        : "border-white/10 text-white/45",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {question.title}
                  </div>
                );
              })}
            </div>

            {inferredQuestions.length > 0 ? (
              <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-white/45">Already Learned</div>
                <p className="mt-2 text-sm leading-6 text-white/60">
                  Mission control found these starter answers in the current owner state, so the fallback card does not
                  need to re-ask them.
                </p>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                  {inferredQuestions.map((question) => {
                    const Icon = TARGET_ICONS[question.target] ?? Sparkles;
                    return (
                      <div key={question.target} className="rounded-2xl border border-white/10 bg-black/20 p-4">
                        <div className="flex items-center gap-2 text-sm font-medium text-white">
                          <Icon className="h-4 w-4 text-brand-300/80" />
                          {question.title}
                        </div>
                        {question.provenance?.summary ? (
                          <p className="mt-2 text-sm leading-6 text-white/60">{question.provenance.summary}</p>
                        ) : null}
                        <div className="mt-3 flex flex-col gap-2">
                          {question.provenance?.artifacts.map((artifact) => (
                            <div
                              key={`${question.target}:${artifact.source}:${artifact.artifactKey}:${artifact.fieldPath ?? artifact.fields?.join(",") ?? ""}`}
                              className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2"
                            >
                              <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/40">
                                {artifact.artifactLabel}
                              </div>
                              <div className="mt-1 text-sm leading-6 text-white/70">{artifact.reason}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}

            <div className="rounded-2xl border border-white/10 bg-black/20 p-5">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="text-sm font-medium text-white">{currentQuestion.prompt}</div>
                  <p className="mt-2 text-sm leading-6 text-white/55">
                    {currentQuestion.note} PaLLi only needs a light starting answer here and can refine the rest in
                    normal conversation later.
                  </p>
                </div>
                {!currentQuestion.required ? (
                  <div className="rounded-full border border-white/10 px-3 py-1 text-[11px] uppercase tracking-[0.2em] text-white/45">
                    Optional
                  </div>
                ) : null}
              </div>

              {currentQuestion.inputKind === "text" ? (
                <input
                  type="text"
                  value={draft.name}
                  onChange={(event) => updateTarget("user_name", event.target.value)}
                  placeholder={currentQuestion.placeholder ?? "Your name or nickname"}
                  className="mt-4 w-full rounded-xl border border-white/10 bg-black/30 px-3 py-3 text-sm text-white outline-none"
                />
              ) : (
                <div className={cn("mt-4", getOptionGridClasses(currentQuestion.options.length))}>
                  {currentQuestion.options.map((option) => (
                    <OptionCard
                      key={option.value}
                      selected={currentValue === option.value}
                      title={option.label}
                      description={option.description ?? ""}
                      onClick={() => updateTarget(currentQuestion.target, option.value)}
                    />
                  ))}
                </div>
              )}
            </div>

            <div className="flex flex-col gap-3 border-t border-white/10 pt-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-sm text-white/45">
                Prompt {activeStep + 1} of {pendingQuestions.length} in this fallback card
              </div>
              <div className="flex flex-wrap gap-3">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setStep((current) => Math.max(0, current - 1))}
                  disabled={activeStep === 0 || saving}
                >
                  Back
                </Button>
                {!onLastStep ? (
                  <Button
                    size="sm"
                    onClick={() => setStep((current) => current + 1)}
                    disabled={!canContinueQuestion(currentQuestion, draft) || saving}
                  >
                    Next prompt
                  </Button>
                ) : (
                  <Button size="sm" onClick={() => void handleSave()} disabled={!canContinueQuestion(currentQuestion, draft) || saving}>
                    {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    Save starter baseline
                  </Button>
                )}
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

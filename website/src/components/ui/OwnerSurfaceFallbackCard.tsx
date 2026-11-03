"use client";

import type { ReactNode } from "react";
import { useRouter } from "next/navigation";

import { Button, type ButtonProps } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type FallbackAction = {
  href: string;
  label: string;
  variant?: ButtonProps["variant"];
};

type OwnerSurfaceFallbackCardProps = {
  title: string;
  description: ReactNode;
  actions?: FallbackAction[];
  eyebrow?: ReactNode;
  icon?: ReactNode;
  tone?: "neutral" | "warning";
  className?: string;
};

export function OwnerSurfaceFallbackCard({
  title,
  description,
  actions = [],
  eyebrow,
  icon,
  tone = "neutral",
  className,
}: OwnerSurfaceFallbackCardProps) {
  const router = useRouter();
  const isWarning = tone === "warning";

  return (
    <div
      className={cn(
        "max-w-xl rounded-3xl border p-8 shadow-2xl backdrop-blur-md",
        isWarning ? "border-rose-500/20 bg-rose-500/5" : "border-white/10 bg-white/5",
        className,
      )}
    >
      {icon ? (
        <div
          className={cn(
            "flex h-14 w-14 items-center justify-center rounded-2xl",
            isWarning ? "bg-rose-500/10 text-rose-300" : "bg-brand-500/10 text-brand-300",
          )}
        >
          {icon}
        </div>
      ) : null}

      {eyebrow ? (
        <div
          className={cn(
            "text-xs font-mono uppercase tracking-[0.28em]",
            icon ? "mt-5" : "",
            isWarning ? "text-rose-100/55" : "text-white/35",
          )}
        >
          {eyebrow}
        </div>
      ) : null}

      <h2 className="mt-3 text-xl font-medium text-white">{title}</h2>
      <div className="mt-3 text-sm leading-relaxed text-white/65">{description}</div>

      {actions.length ? (
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          {actions.map((action, index) => (
            <Button
              key={`${action.href}:${action.label}`}
              variant={action.variant ?? (index === 0 ? "primary" : "secondary")}
              onClick={() => router.push(action.href)}
            >
              {action.label}
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

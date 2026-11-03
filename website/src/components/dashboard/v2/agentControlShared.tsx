"use client";

import Link from "next/link";
import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { getStatusClasses } from "@/lib/agentControl";

export function AgentPanel({
  title,
  subtitle,
  icon: Icon,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title: string;
  subtitle?: string;
  icon: LucideIcon;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-2xl border border-white/5 bg-black/40 shadow-xl backdrop-blur-md",
        className,
      )}
    >
      <div className="flex min-h-12 items-center justify-between border-b border-white/5 bg-white/[0.02] px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-white/40">
            <Icon className="h-3.5 w-3.5" />
            <span>{title}</span>
          </div>
          {subtitle ? <p className="mt-1 text-xs text-white/40">{subtitle}</p> : null}
        </div>
        {actions ? <div className="ml-4 flex items-center gap-2">{actions}</div> : null}
      </div>
      <div
        className={cn(
          "min-h-0 flex-1 overflow-y-auto p-4 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-white/10",
          bodyClassName,
        )}
      >
        {children}
      </div>
    </section>
  );
}

export function StatusPill({ status }: { status?: string | null }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-mono uppercase tracking-widest",
        getStatusClasses(status),
      )}
    >
      {status || "unknown"}
    </span>
  );
}

export function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1.5 text-[11px] font-mono uppercase tracking-widest transition-colors",
        active
          ? "border-brand-500/30 bg-brand-500/15 text-brand-100"
          : "border-white/10 bg-white/5 text-white/45 hover:bg-white/10 hover:text-white/70",
      )}
    >
      {children}
    </button>
  );
}

export function SmallButton({
  onClick,
  children,
  disabled,
  tone = "neutral",
}: {
  onClick?: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  tone?: "neutral" | "brand" | "danger";
}) {
  const toneClass =
    tone === "brand"
      ? "border-brand-500/25 bg-brand-500/10 text-brand-100 hover:bg-brand-500/20"
      : tone === "danger"
        ? "border-rose-500/25 bg-rose-500/10 text-rose-100 hover:bg-rose-500/20"
        : "border-white/10 bg-white/5 text-white/75 hover:bg-white/10";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded-lg border px-3 py-2 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        toneClass,
      )}
    >
      {children}
    </button>
  );
}

export function EmptyState({
  title,
  message,
}: {
  title: string;
  message: string;
}) {
  return (
    <div className="flex h-full min-h-[180px] flex-col items-center justify-center rounded-2xl border border-dashed border-white/10 bg-white/[0.02] px-6 text-center">
      <div className="text-xs font-mono uppercase tracking-[0.24em] text-white/35">{title}</div>
      <p className="mt-3 max-w-xl text-sm leading-relaxed text-white/55">{message}</p>
    </div>
  );
}

export function DetailRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2">
      <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">{label}</div>
      <div className="mt-1 text-sm text-white/80">{value}</div>
    </div>
  );
}

export function InspectionBoundaryCard({
  title,
  description,
  links = [],
  eyebrow = "Surface boundary",
}: {
  title: string;
  description: string;
  links?: Array<{ href: string; label: string }>;
  eyebrow?: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-black/25 p-4">
      <div className="text-[10px] font-mono uppercase tracking-widest text-white/35">{eyebrow}</div>
      <div className="mt-2 text-sm text-white/90">{title}</div>
      <p className="mt-2 text-sm leading-relaxed text-white/55">{description}</p>
      {links.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {links.map((link) => (
            <Link
              key={`${link.href}:${link.label}`}
              href={link.href}
              className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-[10px] uppercase tracking-widest text-white/70 transition-colors hover:bg-white/10 hover:text-white"
            >
              {link.label}
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function InspectionUnavailableState({
  title,
  message,
  context,
  onRetry,
  retryLabel = "Retry",
  links = [],
}: {
  title: string;
  message: string;
  context?: string;
  onRetry?: () => void;
  retryLabel?: string;
  links?: Array<{ href: string; label: string }>;
}) {
  return (
    <div className="rounded-2xl border border-rose-500/20 bg-rose-500/5 p-4">
      <div className="text-[10px] font-mono uppercase tracking-widest text-rose-100/55">
        Inspection unavailable
      </div>
      <div className="mt-2 text-sm text-rose-50">{title}</div>
      <p className="mt-2 text-sm leading-relaxed text-rose-100/80">{message}</p>
      {context ? <p className="mt-2 text-sm leading-relaxed text-rose-100/60">{context}</p> : null}
      {onRetry || links.length ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {onRetry ? (
            <SmallButton onClick={onRetry} tone="neutral">
              {retryLabel}
            </SmallButton>
          ) : null}
          {links.map((link) => (
            <Link
              key={`${link.href}:${link.label}`}
              href={link.href}
              className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-[10px] uppercase tracking-widest text-white/70 transition-colors hover:bg-white/10 hover:text-white"
            >
              {link.label}
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}

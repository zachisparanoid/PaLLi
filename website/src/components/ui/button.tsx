"use client";

import { forwardRef } from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "outline";
type Size = "sm" | "md" | "lg";

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center gap-2 rounded-xl font-medium transition",
          "will-change-transform",
          "transition-transform duration-200 ease-out hover:-translate-y-px active:translate-y-0",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-black/40",
          "disabled:opacity-50 disabled:pointer-events-none",
          size === "sm" && "h-9 px-3 text-sm",
          size === "md" && "h-11 px-4 text-sm",
          size === "lg" && "h-12 px-5 text-base",
          variant === "primary" &&
            "bg-white text-black shadow-glow border border-black/10",
          variant === "secondary" && "bg-white/5 text-white border border-white/10",
          variant === "outline" && "border border-white/12 text-white",
          variant === "ghost" && "text-white/80",
          className,
        )}
        {...props}
      />
    );
  },
);

Button.displayName = "Button";

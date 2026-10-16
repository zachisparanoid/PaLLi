"use client";

import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { cn } from "@/lib/utils";

type ToastVariant = "success" | "error" | "info";

type ToastInput = {
  variant?: ToastVariant;
  title?: string;
  message: string;
  durationMs?: number;
};

type ToastItem = {
  id: number;
  variant: ToastVariant;
  title?: string;
  message: string;
  durationMs: number;
};

type ToastContextValue = {
  toast: (input: ToastInput) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="pointer-events-none fixed right-4 top-4 z-[100] flex w-[min(92vw,380px)] flex-col gap-2">
      <AnimatePresence initial={false}>
        {toasts.map((toast) => {
          const styles: Record<
            ToastVariant,
            { icon: React.ReactNode; className: string }
          > = {
            success: {
              icon: <CheckCircle2 className="h-4 w-4 text-brand-400" />,
              className: "border-brand-500/20 bg-brand-500/10",
            },
            error: {
              icon: <AlertTriangle className="h-4 w-4 text-rose-400" />,
              className: "border-rose-500/20 bg-rose-500/10",
            },
            info: {
              icon: <Info className="h-4 w-4 text-white/70" />,
              className: "border-white/10 bg-white/5",
            },
          };

          const def = styles[toast.variant];

          return (
            <motion.div
              key={toast.id}
              initial={{ opacity: 0, y: -8, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.98 }}
              transition={{ duration: 0.18 }}
              className={cn(
                "pointer-events-auto rounded-2xl border px-4 py-3 shadow-2xl backdrop-blur-xl",
                "text-sm text-white",
                def.className,
              )}
            >
              <div className="flex items-start gap-3">
                <div className="mt-0.5">{def.icon}</div>

                <div className="min-w-0 flex-1">
                  {toast.title ? (
                    <div className="text-xs font-semibold uppercase tracking-widest text-white/70">
                      {toast.title}
                    </div>
                  ) : null}
                  <div className="mt-0.5 text-white/80">{toast.message}</div>
                </div>

                <button
                  type="button"
                  onClick={() => onDismiss(toast.id)}
                  className="rounded-lg p-1 text-white/60 hover:bg-white/10 hover:text-white"
                  aria-label="Dismiss notification"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (input: ToastInput) => {
      const id = nextId.current++;
      const item: ToastItem = {
        id,
        variant: input.variant ?? "info",
        title: input.title,
        message: input.message,
        durationMs: input.durationMs ?? 4500,
      };

      setToasts((prev) => {
        const next = [...prev, item];
        // Keep the UI tidy if multiple things fail at once.
        return next.slice(-4);
      });

      if (item.durationMs > 0) {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id), item.durationMs),
        );
      }
    },
    [dismiss],
  );

  useEffect(() => {
    return () => {
      for (const timer of timers.current.values()) clearTimeout(timer);
      timers.current.clear();
    };
  }, []);

  const value = useMemo<ToastContextValue>(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast() must be used within <ToastProvider />");
  }
  return ctx;
}


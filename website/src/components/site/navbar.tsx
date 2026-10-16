"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Menu, X } from "lucide-react";

import { visibleSiteNavLinks } from "@/components/site/siteNavLinks";
import { Container } from "@/components/ui/container";
import { useLocalMode } from "@/lib/useLocalMode";
import { cn } from "@/lib/utils";

export function Navbar() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const pathname = usePathname();
  const reduceMotion = useReducedMotion();
  // First door, task 4 (2026-09-17): on a self-hosted install the SaaS-era
  // owner-account entry is not rendered at all (hidden, not deleted).
  const navLinks = visibleSiteNavLinks(useLocalMode());

  const menuTransition = {
    duration: reduceMotion ? 0 : 0.25,
    ease: [0.2, 0.8, 0.2, 1] as [number, number, number, number],
  } as const;
  const menuInitial = reduceMotion
    ? { opacity: 1, height: "auto" }
    : { opacity: 0, height: 0, y: -6 };
  const menuAnimate = reduceMotion
    ? { opacity: 1, height: "auto" }
    : { opacity: 1, height: "auto", y: 0 };
  const menuExit = reduceMotion
    ? { opacity: 1, height: 0 }
    : { opacity: 0, height: 0, y: -6 };

  useEffect(() => {
    function onScroll() {
      setScrolled(window.scrollY > 10);
    }
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={cn(
        "sticky top-0 z-50 border-b border-transparent",
        scrolled && "border-white/10 bg-black/50 backdrop-blur accent-divider",
      )}
    >
      <Container className="flex h-16 items-center justify-between">
        <Link href="/" className="flex items-center gap-2">
          <div className="relative h-9 w-9 overflow-hidden rounded-xl border border-white/20 shadow-glow">
            <Image src="/palli-macaw.png" alt="PaLLi (blue macaw)" fill className="object-cover" />
          </div>
          <div className="leading-none">
            <div className="text-sm font-semibold tracking-tight text-white">PaLLi Local</div>
            <div className="text-[11px] text-white/55">Single-owner | LAN-friendly</div>
          </div>
        </Link>

        <nav className="hidden items-center gap-6 md:flex">
          {navLinks.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "text-sm transition-colors",
                pathname === item.href ? "text-white" : "text-white/70 hover:text-white/90",
              )}
            >
              {item.label}
            </Link>
          ))}
          <div className="rounded-full border border-brand-500/20 bg-brand-500/10 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.24em] text-brand-300">
            Local Mode
          </div>
        </nav>

        <button
          className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/5 text-white/80 md:hidden"
          onClick={() => setOpen((value) => !value)}
          aria-label={open ? "Close menu" : "Open menu"}
        >
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </Container>

      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            initial={menuInitial}
            animate={menuAnimate}
            exit={menuExit}
            transition={menuTransition}
            style={{ overflow: "hidden" }}
            className="accent-divider-top border-t border-white/10 bg-black/40 backdrop-blur md:hidden"
          >
            <Container className="flex flex-col gap-3 py-4">
              {navLinks.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn("text-sm", pathname === item.href ? "text-white" : "text-white/70")}
                  onClick={() => setOpen(false)}
                >
                  {item.label}
                </Link>
              ))}
              <div className="pt-2 text-xs uppercase tracking-[0.24em] text-brand-300/80">
                Local Mode
              </div>
            </Container>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </header>
  );
}

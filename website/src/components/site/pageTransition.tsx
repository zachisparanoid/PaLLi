"use client";

import { usePathname } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useState, useEffect } from "react";

export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const reduceMotion = useReducedMotion();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return <>{children}</>;
  }

  const initial = reduceMotion
    ? { opacity: 1 }
    : { opacity: 0, y: 18, scale: 0.985, filter: "blur(8px)" };
  const animate = reduceMotion
    ? { opacity: 1 }
    : { opacity: 1, y: 0, scale: 1, filter: "blur(0px)" };
  const exit = reduceMotion
    ? { opacity: 1 }
    : { opacity: 0, y: -12, scale: 0.995, filter: "blur(6px)" };

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={pathname}
        initial={initial}
        animate={animate}
        exit={exit}
        style={{ willChange: "transform, opacity, filter" }}
        transition={{
          duration: reduceMotion ? 0 : 0.45,
          ease: [0.2, 0.8, 0.2, 1],
        }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}

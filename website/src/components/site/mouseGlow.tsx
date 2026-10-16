"use client";

import { useEffect } from "react";

export function MouseGlow() {
  useEffect(() => {
    const root = document.documentElement;
    let raf = 0;
    let lastX = 0;
    let lastY = 0;
    function update(x: number, y: number) {
      root.style.setProperty("--mouse-x", `${x}px`);
      root.style.setProperty("--mouse-y", `${y}px`);
    }

    function onMove(e: MouseEvent) {
      lastX = e.clientX;
      lastY = e.clientY;
      if (raf) return;
      raf = window.requestAnimationFrame(() => {
        raf = 0;
        update(lastX, lastY);
      });
    }

    window.addEventListener("mousemove", onMove, { passive: true });
    update(Math.round(window.innerWidth * 0.5), Math.round(window.innerHeight * 0.22));

    return () => {
      window.removeEventListener("mousemove", onMove);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, []);

  return null;
}

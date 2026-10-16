"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { MemoryGalaxy } from "@/components/dashboard/v2/MemoryGalaxy";

export function MemoryGalaxyCard() {
  return (
    <div className="relative h-full w-full overflow-hidden">
      <div className="pointer-events-none absolute left-5 top-4 z-20">
        <h3 className="text-sm font-medium text-white/90">Memory Galaxy</h3>
        <p className="text-[10px] uppercase tracking-widest text-white/40">
          What PaLLi remembers
        </p>
      </div>
      <Link
        href="/dashboard/memory"
        className="absolute right-4 top-4 z-20 inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-black/50 px-3 py-1.5 text-xs text-white/70 backdrop-blur transition-colors hover:bg-white/10 hover:text-white"
      >
        Open full view
        <ArrowUpRight className="h-3.5 w-3.5" />
      </Link>
      <MemoryGalaxy compact />
    </div>
  );
}

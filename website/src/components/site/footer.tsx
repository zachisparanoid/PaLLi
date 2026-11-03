"use client";

import Link from "next/link";

import { visibleSiteNavLinks } from "@/components/site/siteNavLinks";
import { Container } from "@/components/ui/container";
import { useLocalMode } from "@/lib/useLocalMode";
import { DISCORD_INVITE_URL, SOURCE_CODE_URL } from "@/lib/externalLinks";

export function Footer() {
  // First door, task 4 (2026-09-17): the footer carries the same nav list as the
  // navbar, so it hides the same SaaS-era entry on a self-hosted install.
  const navLinks = visibleSiteNavLinks(useLocalMode());

  return (
    <footer className="accent-divider-top border-t border-white/10">
      <Container className="flex flex-col gap-6 py-10 md:flex-row md:items-center md:justify-between">
        <div className="text-sm text-white/60">
          <div className="font-medium text-white">PaLLi Local</div>
          <div className="mt-1 max-w-md">
            A local-first AI companion with memory, voice, mission control, and
            owner workspaces, with room to grow into your machine.
          </div>
          <div className="mt-4 text-xs text-white/30">
            Local development line. Built for one trusted operator, with cloud
            concerns intentionally stripped out.
          </div>
          <div className="mt-2 text-xs text-white/20">
            PaLLi is free software under the AGPL-3.0.
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm text-white/70">
          {navLinks.map((item) => (
            <Link key={item.href} href={item.href}>
              {item.label}
            </Link>
          ))}
          <a href={DISCORD_INVITE_URL} target="_blank" rel="noopener noreferrer">
            Discord
          </a>
          <a href={SOURCE_CODE_URL} target="_blank" rel="noopener noreferrer">
            Source code
          </a>
        </div>
      </Container>
    </footer>
  );
}

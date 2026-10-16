"use client";

import { OwnerSurfaceFallbackCard } from "@/components/ui/OwnerSurfaceFallbackCard";
import { Container } from "@/components/ui/container";
import { useLocalMode } from "@/lib/useLocalMode";

export default function NotFound() {
  // First door, task 4 (2026-09-17): a stranger who mistypes a URL must not be
  // handed the SaaS-era owner-account page as one of their three ways out.
  // Hidden, not deleted — the route still answers.
  const localMode = useLocalMode();
  const showOwnerControls = localMode === false;

  return (
    <div className="min-h-screen">
      <Container className="pt-16 pb-16">
        <OwnerSurfaceFallbackCard
          eyebrow="404"
          title="Page not found"
          description={
            showOwnerControls
              ? "That route is not part of this local PaLLi install. Return to Mission Control for live runtime review, Owner controls for maintenance, or the Identity workspace for shaping and provenance."
              : "That route is not part of this local PaLLi install. Return to Mission Control for live runtime review, or the Identity workspace for shaping and provenance."
          }
          actions={[
            { href: "/dashboard", label: "Open mission control" },
            ...(showOwnerControls
              ? [{ href: "/account", label: "Owner controls", variant: "secondary" as const }]
              : []),
            { href: "/account/identity", label: "Identity workspace", variant: "secondary" as const },
          ]}
        />
      </Container>
    </div>
  );
}

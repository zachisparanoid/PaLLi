import type { Metadata } from "next";
import "./globals.css";
import { MouseGlow } from "@/components/site/mouseGlow";
import { PageTransition } from "@/components/site/pageTransition";
import { ToastProvider } from "@/components/ui/toast";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"),
  title: {
    default: "PaLLi Local",
    template: "%s - PaLLi Local",
  },
  description:
    "PaLLi Local is a single-owner AI companion running on your own machine, with voice, Discord integration, mission control, identity workspaces, memory, and agentic tooling.",
  openGraph: {
    title: "PaLLi Local",
    description:
      "A local-first AI companion with memory, voice, mission control, and machine-native ambitions.",
    type: "website",
    images: [
      {
        url: "/palli-macaw.png",
        width: 512,
        height: 512,
        alt: "PaLLi (blue macaw)",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "PaLLi Local",
    description:
      "A local-first AI companion with memory, voice, mission control, and machine-native ambitions.",
    images: ["/palli-macaw.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <MouseGlow />
        <div className="palli-noise">
          <ToastProvider>
            <PageTransition>{children}</PageTransition>
          </ToastProvider>
        </div>
      </body>
    </html>
  );
}

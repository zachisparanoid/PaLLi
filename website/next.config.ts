import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep slow tool-assisted conversations alive for the chat client's five-minute window.
  experimental: { proxyTimeout: 310_000 },
  async rewrites() {
    // The browser uses this server's /api proxy, even when PaLLi is on
    // another computer. Keep the API listener private by default.
    const target = process.env.PALLI_API_PROXY_TARGET
      || process.env.NEXT_PUBLIC_API_PROXY_TARGET
      || "http://127.0.0.1:8000";

    return [
      {
        source: "/api/:path*",
        destination: `${target.replace(/\/$/, "")}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

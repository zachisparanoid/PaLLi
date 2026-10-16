import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // Local launcher and dev convenience: proxy `/api/*` to the FastAPI backend.
    // Behind a reverse proxy, leave `NEXT_PUBLIC_API_PROXY_TARGET` unset.
    const target = process.env.NEXT_PUBLIC_API_PROXY_TARGET;
    if (!target) return [];

    return [
      {
        source: "/api/:path*",
        destination: `${target.replace(/\/$/, "")}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";

/**
 * Baseline security headers for every route (S4). The Content-Security-Policy is set per
 * request in src/proxy.ts (Report-Only first, see Documents/Backend/http-security.md).
 * Frame embedding is blocked here via X-Frame-Options and in the CSP via frame-ancestors.
 */
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // microphone stays allowed for same-origin: the chat has a voice-input feature.
  { key: "Permissions-Policy", value: "camera=(), microphone=(self), geolocation=(), payment=(), usb=()" },
  // allow-popups keeps OAuth sign-in popups working while still isolating the browsing context group.
  { key: "Cross-Origin-Opener-Policy", value: "same-origin-allow-popups" },
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  ...(isProd ? [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }] : []),
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // Docker (S5) sets NEXT_OUTPUT=standalone; a plain `next start` keeps working without it.
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" as const } : {}),
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;

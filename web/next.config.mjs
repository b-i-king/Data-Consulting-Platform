// Next.js configuration.
// Security headers mirror the Video_Game_Stats_Tracker template: the browser
// may only talk to this site and to our FastAPI backend (NEXT_PUBLIC_API_URL).

const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Friendly and old addresses forward to the current ones (lib/routes.ts has
  // the full address map). Anything after "?" (e.g. ?checkout=success from
  // Stripe) is passed along automatically. `permanent` tells browsers and
  // search engines the page has moved for good (HTTP 308).
  async redirects() {
    return [
      // The home page is "/"; "/home" is a common guess.
      { source: "/home", destination: "/", permanent: true },
      // Renamed 10/2026: /app -> /workspace and /start -> /get-started.
      { source: "/app", destination: "/workspace", permanent: true },
      { source: "/app/:path*", destination: "/workspace/:path*", permanent: true },
      { source: "/start", destination: "/get-started", permanent: true },
      { source: "/start/:path*", destination: "/get-started/:path*", permanent: true },
      // Common guesses for signing in or up.
      { source: "/login", destination: "/signin", permanent: true },
      { source: "/sign-in", destination: "/signin", permanent: true },
      { source: "/register", destination: "/signup", permanent: true },
      { source: "/sign-up", destination: "/signup", permanent: true },
      { source: "/settings", destination: "/workspace/settings/account", permanent: false },
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob:",
              "font-src 'self' data:",
              `connect-src 'self' ${API}`,
              "frame-ancestors 'self'",
            ].join("; "),
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;

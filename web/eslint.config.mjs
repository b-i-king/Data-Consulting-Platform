// ESLint settings (ESLint 9 "flat config"), same as the Video_Game_Stats_Tracker
// template: Next.js's recommended rules (React, hooks, accessibility, Next-specific
// checks). Run with `npm run lint`; GitHub runs it on every pull request
// (.github/workflows/vercel_checks.yml). Errors fail the check; warnings don't.
import nextConfig from "eslint-config-next";

const config = [
  ...nextConfig,
  {
    rules: {
      // "Don't call setState directly inside useEffect." Kept as a WARNING, not an
      // error: several components read the browser-only sign-in session or theme
      // (sessionStorage / localStorage) in an effect on purpose. Reading it during
      // the first render instead would make the server-rendered page and the
      // browser's first render disagree (a hydration error). The warnings stay
      // visible so new code can avoid the pattern where it isn't needed.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  // Generated / installed folders are never linted.
  { ignores: [".next/**", "out/**", "node_modules/**", "next-env.d.ts"] },
];

export default config;

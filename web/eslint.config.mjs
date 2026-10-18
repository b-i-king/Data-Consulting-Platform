// ESLint settings (ESLint 9 "flat config"), same as the Video_Game_Stats_Tracker
// template: Next.js's recommended rules (React, hooks, accessibility, Next-specific
// checks). Run with `npm run lint`; GitHub runs it on every pull request
// (.github/workflows/vercel_checks.yml). Errors fail the check; warnings don't.
import nextConfig from "eslint-config-next";
import jsxA11y from "eslint-plugin-jsx-a11y";

const config = [
  ...nextConfig,
  {
    rules: {
      // ACCESSIBILITY: every rule in jsx-a11y's recommended set (Next.js turns on
      // only a few). They catch images without alt text, form fields without
      // labels, clickable things a keyboard can't reach, and more — as ERRORS, so
      // the pull-request check fails until they're fixed. docs/ACCESSIBILITY.md.
      ...jsxA11y.flatConfigs.recommended.rules,
      // A label may wrap its input with the text a few elements deep
      // (<label><input/><span><span>Text</span></span></label>).
      "jsx-a11y/label-has-associated-control": ["error", { assert: "either", depth: 4 }],
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

// Light / Dark / System switch (shown in the header).
//
// How it works:
//   * "System" (the default) — no data-theme attribute on <html>, so the CSS
//     follows the computer's setting (see app/globals.css).
//   * "Light" / "Dark" — sets <html data-theme="light|dark"> and remembers
//     the choice in localStorage, so it sticks on the next visit.
//   * The tiny script in app/layout.tsx (THEME_INIT_SCRIPT) applies a saved
//     choice BEFORE the page paints, so there is no flash of the wrong theme.
//
// localStorage can be blocked (private browsing), so every access is wrapped
// in try/catch; the switch still works for the current page view.

"use client";

import { useEffect, useState } from "react";

type Choice = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "dcp.theme";

/** Runs inline in <head> before React loads. Keep it tiny and dependency-free. */
export const THEME_INIT_SCRIPT = `try{var t=localStorage.getItem("${THEME_STORAGE_KEY}");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}`;

function apply(choice: Choice) {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
  try {
    if (choice === "system") localStorage.removeItem(THEME_STORAGE_KEY);
    else localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    /* storage blocked — the choice lasts for this page view only */
  }
}

const OPTIONS: { id: Choice; label: string; icon: React.ReactNode }[] = [
  {
    id: "light",
    label: "Light",
    icon: (
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    ),
  },
  {
    id: "dark",
    label: "Dark",
    icon: (
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
    ),
  },
  {
    id: "system",
    label: "Match my computer",
    icon: (
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <rect x="3" y="4" width="18" height="12" rx="2" />
        <path d="M8 20h8M12 16v4" />
      </svg>
    ),
  },
];

export default function ThemeToggle() {
  const [choice, setChoice] = useState<Choice>("system");

  // Read the saved choice after mount (the server can't know it).
  useEffect(() => {
    try {
      const saved = localStorage.getItem(THEME_STORAGE_KEY);
      if (saved === "light" || saved === "dark") setChoice(saved);
    } catch {
      /* ignore */
    }
  }, []);

  return (
    <div role="radiogroup" aria-label="Color theme" className="flex rounded-full border border-line bg-surface-2 p-0.5">
      {OPTIONS.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={choice === o.id}
          aria-label={o.label}
          title={o.label}
          onClick={() => {
            setChoice(o.id);
            apply(o.id);
          }}
          className={`rounded-full p-1.5 transition-colors ${
            choice === o.id ? "bg-surface text-brand shadow-[var(--shadow-card)]" : "text-ink-muted hover:text-ink"
          }`}
        >
          {o.icon}
        </button>
      ))}
    </div>
  );
}

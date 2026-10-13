// ── Sign-in session (browser only) ────────────────────────────────────────────
// Keeps the login token in sessionStorage: it survives a page refresh but is
// cleared when the tab closes. Browsers can block storage (private mode,
// strict settings), so every access is wrapped in try/catch and the app still
// works for the current page view if storage is unavailable.

import type { Account } from "./types";

const KEY = "dcp.session";

export interface Session {
  token: string;
  account: Account;
}

export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(session: Session): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(session));
  } catch {
    /* storage blocked — session lives only in React state */
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

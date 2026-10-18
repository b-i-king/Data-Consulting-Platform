// ── Sign-in session (browser only) ────────────────────────────────────────────
// Keeps the login token in sessionStorage: it survives a page refresh but is
// cleared when the tab closes. Browsers can block storage (private mode,
// strict settings), so every access is wrapped in try/catch and the app still
// works for the current page view if storage is unavailable.

import type { Account } from "./types";

const KEY = "dcp.session";
/** Fired on window whenever someone signs in or out, so the header updates at once. */
export const SESSION_EVENT = "dcp-session";
const announce = () => {
  try {
    window.dispatchEvent(new Event(SESSION_EVENT));
  } catch {
    /* not in a browser */
  }
};

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
  announce();
}

const DEVICE_KEY = "dcp.device";

/**
 * This browser's device id: a random value kept in localStorage (NOT a secret),
 * sent at sign-in as X-Device-Id so every tab of this browser counts as ONE
 * device toward the 3-device limit (api/services/sessions.py).
 */
export function deviceId(): string {
  try {
    let id = localStorage.getItem(DEVICE_KEY);
    if (!id) {
      id = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random()).slice(2);
      localStorage.setItem(DEVICE_KEY, id);
    }
    return id;
  } catch {
    return ""; // storage blocked: the server treats it as a new device
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  announce();
}

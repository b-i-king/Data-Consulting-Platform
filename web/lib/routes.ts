// ── The site's addresses (URLs), in one place ───────────────────────────────
// Every page and every screen inside a page has its own readable address, named
// after the screen (and, as closely as possible, the .tsx file that draws it):
//
//   /                              home                     app/page.tsx
//   /get-started/<step>            the guest questionnaire  components/wizard/Wizard.tsx
//   /workspace/<service>           the signed-in workspace  components/workspace/Workspace.tsx
//   /workspace/settings/<section>  Settings (bottom left)   Workspace.tsx's account area
//   /signin, /signup               sign in / create account components/auth/SignInPanel.tsx
//
// Old addresses (/app, /start, /home, ...) forward to these: see `redirects` in
// web/next.config.mjs. The backend sends people back here too (Stripe in
// api/services/billing.py, QuickBooks / Xero / Supabase sign-in in
// api/services/oauth.py), so a change here may need a change there.
//
// WHY NOT ONE NEXT.JS PAGE PER STEP? The questionnaire and the workspace keep
// the client's files in the browser's memory (never stored on the server).
// Moving to a separate page would throw them away, so each of these is ONE
// page whose address changes as you move (window.history.pushState). The
// browser's Back / Forward buttons, bookmarks and refresh all use it.

export const HOME = "/";
export const GET_STARTED = "/get-started";
export const WORKSPACE = "/workspace";
export const SIGN_IN = "/signin";
export const SIGN_UP = "/signup";

// ── Get started (the wizard) ────────────────────────────────────────────────

/** The wizard's steps (Wizard.tsx `StepId`) and the address of each. */
export const WIZARD_STEP_SLUGS = {
  about: "about-you",       // StepAccountType.tsx
  data: "your-data",        // StepDataLocation.tsx
  upload: "upload",         // StepUpload.tsx
  clean: "clean",           // StepResults.tsx (files: clean & preview)
  account: "account",       // AuthForm.tsx, before connecting a cloud / business app
  connect: "connect",       // StepConnect.tsx
  preview: "preview",       // StepResults.tsx, read-only (cloud / business app data)
  quicklook: "quick-look",  // StepQuickLook.tsx
  needs: "your-needs",      // StepServices.tsx
  next: "next-steps",       // StepAccount.tsx
} as const;
export type WizardStep = keyof typeof WIZARD_STEP_SLUGS;

/** "/get-started/upload" for the "upload" step. */
export const wizardPath = (step: WizardStep) => `${GET_STARTED}/${WIZARD_STEP_SLUGS[step]}`;

/** The step named in an address ("/get-started/upload" -> "upload"); null if none or unknown. */
export function wizardStepFromPath(pathname: string): WizardStep | null {
  const slug = pathname.replace(/\/+$/, "").split("/")[2];
  const hit = Object.entries(WIZARD_STEP_SLUGS).find(([, s]) => s === slug);
  return hit ? (hit[0] as WizardStep) : null;
}

// ── The workspace ───────────────────────────────────────────────────────────

/** Settings' sections (the account area, bottom left of the workspace). */
export const SETTINGS_SLUGS = {
  account: "account",           // AccountPanel.tsx
  billing: "billing",           // billing/BillingPanel.tsx (Plan & billing)
  integrations: "integrations", // IntegrationsPanel.tsx
  consultant: "consultant",     // SettingsPanel.tsx (consultants only)
  admin: "admin",               // AdminPanel.tsx (admins only)
  help: "help",                 // components/FaqSection.tsx (Help & FAQ)
} as const;
export type SettingsSection = keyof typeof SETTINGS_SLUGS;

// A service's address is its id with dashes ("data_cleaning" -> "data-cleaning"),
// except the visualization service, which the workspace calls "Dashboard".
const SERVICE_SLUG_OVERRIDES: Record<string, string> = { data_visualization: "dashboard" };

/** "data_cleaning" -> "data-cleaning"; "data_visualization" -> "dashboard". */
export const serviceSlug = (serviceId: string) => SERVICE_SLUG_OVERRIDES[serviceId] ?? serviceId.replace(/_/g, "-");

/** The reverse of serviceSlug ("dashboard" -> "data_visualization"). */
export function serviceIdFromSlug(slug: string): string {
  const hit = Object.entries(SERVICE_SLUG_OVERRIDES).find(([, s]) => s === slug);
  return hit ? hit[0] : slug.replace(/-/g, "_");
}

/**
 * The workspace address for what's on screen: a Settings section wins over a
 * service; neither gives plain "/workspace" (the workspace picks its first page).
 */
export function workspacePath(view: { service?: string | null; settings?: SettingsSection | null }): string {
  if (view.settings) return `${WORKSPACE}/settings/${SETTINGS_SLUGS[view.settings]}`;
  if (view.service) return `${WORKSPACE}/${serviceSlug(view.service)}`;
  return WORKSPACE;
}

/** What a workspace address points at ("/workspace/settings/billing" -> settings "billing"). */
export function workspaceViewFromPath(pathname: string): { service: string | null; settings: SettingsSection | null } {
  const [, , first, second] = pathname.replace(/\/+$/, "").split("/");
  if (first === "settings") {
    const hit = Object.entries(SETTINGS_SLUGS).find(([, s]) => s === second);
    // "/workspace/settings" alone opens the Account section.
    return { service: null, settings: hit ? (hit[0] as SettingsSection) : "account" };
  }
  return { service: first ? serviceIdFromSlug(first) : null, settings: null };
}

/**
 * Change the address bar WITHOUT leaving the page (so in-memory files survive).
 * `replace` swaps the current history entry instead of adding one (used for
 * corrections, so Back doesn't land on a screen that can't be shown).
 * Any "?..." on the old address (e.g. ?checkout=success) is dropped: it has
 * been read by then, and a refresh shouldn't show its message again.
 * Next.js watches these calls and keeps its router in step.
 */
export function setAddress(path: string, replace = false) {
  if (typeof window === "undefined" || window.location.pathname === path) return;
  if (replace) window.history.replaceState(null, "", path);
  else window.history.pushState(null, "", path);
}

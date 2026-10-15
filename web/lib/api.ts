// ── FastAPI client ────────────────────────────────────────────────────────────
// Every call from the website to the backend goes through this file, so the
// base URL, error handling, and auth header live in ONE place.
//
// The backend URL comes from NEXT_PUBLIC_API_URL (see web/.env.example).
// "NEXT_PUBLIC_" means Next.js includes the value in the browser bundle — so
// never put secrets in a NEXT_PUBLIC_ variable.

import type {
  BillingInterval,
  CheckoutResult,
  ConsultantClient,
  ConnectionsInfo,
  DataConnection,
  RefreshFrequency,
  AnalyzeResponse,
  SkillInfo,
  SkillResult,
  AuthResponse,
  IntakeOptions,
  CleaningInput,
  Location,
  MyServices,
  Quote,
  SignupInput,
} from "./types";

// A trailing "/" is removed, so ".../" + "/api/..." never becomes "//api/...".
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

/** True when the site is online but still pointed at a local API (a missing Vercel setting). */
export const apiMisconfigured = () =>
  typeof window !== "undefined" && !/^(localhost|127\.0\.0\.1)$/.test(window.location.hostname) && /localhost|127\.0\.0\.1/.test(API_URL);

/** An error with the backend's friendly message (FastAPI's `detail`). */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Turn a non-2xx response into an ApiError with a readable message. */
async function raiseForStatus(res: Response): Promise<void> {
  if (res.ok) return;
  let message = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") message = body.detail;
    // Pydantic validation errors arrive as a list of {msg, loc}.
    else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join(" ");
  } catch {
    /* response was not JSON — keep the generic message */
  }
  throw new ApiError(res.status, message);
}

/**
 * fetch() with friendly messages when the API can't be reached at all:
 * a missing Vercel setting, or the free Render server waking up.
 */
async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    if (apiMisconfigured())
      throw new ApiError(0, "This site isn't connected to its API yet: set NEXT_PUBLIC_API_URL in Vercel to your Render URL, then redeploy.");
    throw new ApiError(0, "We couldn't reach the server. If it was asleep (free hosting naps when idle), wait a minute and try again.");
  }
}

/** Wake the API early (free Render servers sleep after 15 idle minutes). Fire and forget. */
export function warmUpApi() {
  fetch(`${API_URL}/health`).catch(() => {});
}

// ── Consultant mode ───────────────────────────────────────────────────────────
// When a consultant picks a client in the workspace's "Working on" menu, every
// signed-in request carries `X-Acting-As: <client id>`. The server checks that
// the client is assigned to this consultant (api/services/consultants.py).
let actingAs: string | null = null;
export function setActingAs(clientId: string | null) {
  actingAs = clientId;
}
/** Authorization (+ acting) headers for a signed-in request. */
const authHeaders = (token: string): Record<string, string> => ({
  Authorization: `Bearer ${token}`,
  ...(actingAs ? { "X-Acting-As": actingAs } : {}),
});

async function getJson<T>(path: string): Promise<T> {
  const res = await apiFetch(`${API_URL}${path}`);
  await raiseForStatus(res);
  return res.json();
}

async function postJson<T>(path: string, body: unknown, token?: string): Promise<T> {
  const res = await apiFetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? authHeaders(token) : {}),
    },
    body: JSON.stringify(body),
  });
  await raiseForStatus(res);
  return res.json();
}

/** Build the multipart form used by both file endpoints. */
function filesForm(files: File[], fields: Record<string, string>): FormData {
  const form = new FormData();
  files.forEach((f) => form.append("files", f, f.name));
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  return form;
}

// ── Public functions used by the components ───────────────────────────────────

export const getIntakeOptions = () => getJson<IntakeOptions>("/api/intake/options");

export const getQuote = (serviceIds: string[], runtimeSeconds?: number) =>
  postJson<Quote>("/api/quote", { service_ids: serviceIds, runtime_seconds: runtimeSeconds });

export const signup = (input: SignupInput) => postJson<AuthResponse>("/api/accounts/signup", input);

/** Sign in by email; consultants also send their access code. */
export const signin = (email: string, accessCode?: string) =>
  postJson<AuthResponse>("/api/accounts/signin", { email, ...(accessCode ? { access_code: accessCode } : {}) });

export const getSkills = () => getJson<SkillInfo[]>("/api/skills");

/** City search for the signup form's location picker (at least 2 letters). */
export const searchLocations = (q: string) => getJson<Location[]>(`/api/locations?q=${encodeURIComponent(q)}`);

/** Services on the signed-in account's plan (drives the workspace's left pane). */
export async function getMyServices(token: string): Promise<MyServices> {
  const res = await apiFetch(`${API_URL}/api/accounts/services`, { headers: authHeaders(token) });
  await raiseForStatus(res);
  return res.json();
}

// ── Billing (api/routers/billing.py) ──────────────────────────────────────────
// With Stripe on, these return Stripe's own page (`url`) to send the person to;
// in test mode (no Stripe key) `url` is null and the change is already made.

/** Subscribe to (or change) a package: the tools picked, billed monthly or yearly. */
export const startCheckout = (token: string, planIds: string[], interval: BillingInterval) =>
  postJson<CheckoutResult>("/api/billing/checkout", { plan_ids: planIds, interval }, token);

/** Prepay consulting hours (after the free 30-minute consultation). */
export const buyConsultingHours = (token: string, hours: number, serviceId?: string) =>
  postJson<CheckoutResult>("/api/billing/consulting-hours", { hours, ...(serviceId ? { service_id: serviceId } : {}) }, token);

/** Stripe's billing portal: change card, see invoices, cancel anytime. */
export const openBillingPortal = (token: string) => postJson<{ url: string }>("/api/billing/portal", {}, token);

/** Test mode only: switch the subscription off. */
export const cancelPlanTestMode = (token: string) => postJson<{ ok: boolean }>("/api/billing/cancel", {}, token);

// ── Connected data (api/routers/connections.py) ──────────────────────────────

/** Refresh choices, the client's connectors, and whether they can connect (Data Engineering). */
export async function getConnections(token: string): Promise<ConnectionsInfo> {
  const res = await apiFetch(`${API_URL}/api/connections`, { headers: authHeaders(token) });
  await raiseForStatus(res);
  return res.json();
}

/** Choose how often a connector pulls new data (manual, weekly, daily, hourly, live). */
export async function setRefresh(token: string, connector: string, frequency: RefreshFrequency): Promise<{ connectors: DataConnection[] }> {
  const res = await apiFetch(`${API_URL}/api/connections/${encodeURIComponent(connector)}/refresh`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ refresh_frequency: frequency }),
  });
  await raiseForStatus(res);
  return res.json();
}

/** Consultants: the clients assigned to me (the "Working on" picker). */
export async function getMyClients(token: string): Promise<{ consultant: { id: string; name: string }; clients: ConsultantClient[] }> {
  const res = await apiFetch(`${API_URL}/api/consultant/clients`, { headers: { Authorization: `Bearer ${token}` } });
  await raiseForStatus(res);
  return res.json();
}

/** Consultants: create an account for a client and assign it to me. */
export async function addClient(token: string, input: SignupInput): Promise<ConsultantClient> {
  const res = await apiFetch(`${API_URL}/api/consultant/clients`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(input),
  });
  await raiseForStatus(res);
  return res.json();
}

/** Add a service to the plan (placeholder until payments — see api/services/entitlements.py). */
export const addMyService = (token: string, serviceId: string) =>
  postJson<MyServices>("/api/accounts/services", { service_id: serviceId }, token);

/** The cleaning choices as form fields (same for analyze, download and skills). */
const choiceFields = (c: CleaningInput) => ({
  missing_strategy: c.strategy,
  column_actions: JSON.stringify(c.columnActions),
  column_names: JSON.stringify(c.columnNames ?? {}),
  transforms: JSON.stringify(c.transforms ?? {}),
});

/**
 * Run a data skill (data_skills/<id>/skill.py) on the files kept in memory,
 * cleaned with the user's choices. `groupId` picks which cleaned table to use.
 */
export async function runSkill(
  skillId: string,
  input: CleaningInput,
  params: Record<string, unknown>,
  groupId = 0,
): Promise<SkillResult> {
  const res = await apiFetch(`${API_URL}/api/skills/${encodeURIComponent(skillId)}/run`, {
    method: "POST",
    body: filesForm(input.files, { params: JSON.stringify(params), group_id: String(groupId), ...choiceFields(input) }),
  });
  await raiseForStatus(res);
  return res.json();
}

/** Save text (a report, SQL, an HTML dashboard) as a file in the browser. */
export function saveText(filename: string, text: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Guest step: upload files and get the cleaning report + preview. */
export async function analyzeFiles(input: CleaningInput): Promise<AnalyzeResponse> {
  const res = await apiFetch(`${API_URL}/api/files/analyze`, {
    method: "POST",
    body: filesForm(input.files, choiceFields(input)),
  });
  await raiseForStatus(res);
  return res.json();
}

/**
 * Signed-in step: send the same files again and save the cleaned result.
 * The file is never stored on the server — see api/routers/files.py.
 */
export async function downloadCleaned(opts: {
  input: CleaningInput;
  token: string;
  groupId: number;
  format: "csv" | "xlsx";
  /** The total the person confirmed; the server never charges more than this. */
  confirmedAmountUsd?: number;
}): Promise<{ amountUsd: string | null; repeat: boolean }> {
  const res = await apiFetch(`${API_URL}/api/files/download`, {
    method: "POST",
    headers: authHeaders(opts.token), // a consultant downloads for the client they are working on
    body: filesForm(opts.input.files, {
      group_id: String(opts.groupId),
      file_format: opts.format,
      ...(opts.confirmedAmountUsd !== undefined ? { confirmed_amount_usd: opts.confirmedAmountUsd.toFixed(2) } : {}),
      ...choiceFields(opts.input),
    }),
  });
  await raiseForStatus(res);

  // Pull the file name out of: Content-Disposition: attachment; filename="x.csv"
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `cleaned_data.${opts.format}`;

  // Trigger the browser's "save file" by clicking a temporary link.
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);

  // repeat = the exact same data was downloaded recently, so this copy was free.
  return { amountUsd: res.headers.get("X-Amount-USD"), repeat: res.headers.get("X-Repeat-Download") === "1" };
}

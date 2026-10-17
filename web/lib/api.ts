// ── FastAPI client ────────────────────────────────────────────────────────────
// Every call from the website to the backend goes through this file, so the
// base URL, error handling, and auth header live in ONE place.
//
// The backend URL comes from NEXT_PUBLIC_API_URL (see web/.env.example).
// "NEXT_PUBLIC_" means Next.js includes the value in the browser bundle — so
// never put secrets in a NEXT_PUBLIC_ variable.

import type {
  Account,
  AdminPanelData,
  BusinessContext,
  CloudSchema,
  CloudTable,
  CloudTableFile,
  AiAnswer,
  AiUsage,
  EntityMatch,
  LiveDataset,
  QuickLook,
  SyncResult,
  SupabaseProject,
  DashboardSuggestion,
  DashboardsInfo,
  BillingInterval,
  CheckoutResult,
  ConsultantClient,
  CombineSpec,
  ConsultantLoad,
  ConsultantPage,
  ConsultantPlan,
  ConsultantSettings,
  ConnectionsInfo,
  DataConnection,
  RefreshFrequency,
  AnalyzeResponse,
  SkillInfo,
  SkillResult,
  AuthResponse,
  IntakeOptions,
  CleaningInput,
  ConsultationStatus,
  ConsultationSummary,
  DeleteStoredResult,
  LoadChoicesBody,
  LoadPlan,
  LoadResult,
  Location,
  WarehouseSummary,
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

/**
 * Sign in with email + password (consultants too — no access code).
 * asConsultant: the "Consultant" choice at the top of the form; refused when
 * the account isn't an approved consultant.
 */
export const signin = (email: string, password: string, asConsultant = false) =>
  postJson<AuthResponse>("/api/accounts/signin", { email, password, as_consultant: asConsultant });

/** Who am I? The account as the server sees it now (e.g. just approved as a consultant). */
export async function getMe(token: string): Promise<Account> {
  const res = await apiFetch(`${API_URL}/api/accounts/me`, { headers: { Authorization: `Bearer ${token}` } });
  await raiseForStatus(res);
  return res.json();
}

/** One page of consultants and how full they are (public: the "pick your consultant" panel). */
export const getConsultants = (page = 1, pageSize = 8, q = "") =>
  getJson<ConsultantPage>(`/api/accounts/consultants?page=${page}&page_size=${pageSize}&q=${encodeURIComponent(q)}`);

/** Delete my account and EVERYTHING in it (type your own email to confirm). Cannot be undone. */
export async function deleteMyAccount(token: string, confirmEmail: string): Promise<{ deleted: number }> {
  const res = await apiFetch(`${API_URL}/api/accounts/me`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ confirm_email: confirmEmail }),
  });
  await raiseForStatus(res);
  return res.json();
}

/** A signed-in client asks for a consultant (empty id = keep mine, or the server picks one with room). */
export const chooseConsultant = (token: string, consultantId: string | null, plan: ConsultantPlan) =>
  postJson<MyServices>("/api/accounts/consultant", { consultant_id: consultantId, consultant_plan: plan }, token);

/** "I'd like to work as a consultant" — an admin approves or denies. */
export async function requestConsultantRole(token: string): Promise<Account> {
  const res = await apiFetch(`${API_URL}/api/accounts/consultant-request`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await raiseForStatus(res);
  return res.json();
}

// ── Admin (api/routers/admin.py) ──────────────────────────────────────────────

/** Admins: who asked to be a consultant, and every consultant's load. */
export async function getAdminPanel(token: string): Promise<AdminPanelData> {
  const res = await apiFetch(`${API_URL}/api/admin/consultant-requests`, { headers: { Authorization: `Bearer ${token}` } });
  await raiseForStatus(res);
  return res.json();
}

/** Admins: approve or deny one request; returns the refreshed panel. */
export async function decideConsultantRequest(token: string, accountId: string, decision: "approve" | "deny"): Promise<AdminPanelData> {
  const res = await apiFetch(`${API_URL}/api/admin/consultant-requests/${encodeURIComponent(accountId)}/${decision}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  await raiseForStatus(res);
  return res.json();
}

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

/** Consultants: the clients assigned to me (the "Working on" picker), and how full I am. */
export async function getMyClients(token: string): Promise<{ consultant: { id: string; name: string }; clients: ConsultantClient[]; load: ConsultantLoad }> {
  const res = await apiFetch(`${API_URL}/api/consultant/clients`, { headers: { Authorization: `Bearer ${token}` } });
  await raiseForStatus(res);
  return res.json();
}

/** Consultants: my Settings — capacity and load, Cal.com links, Cal.com webhook. */
export async function getConsultantSettings(token: string): Promise<ConsultantSettings> {
  const res = await apiFetch(`${API_URL}/api/consultant/settings`, { headers: { Authorization: `Bearer ${token}` } });
  await raiseForStatus(res);
  return res.json();
}

/** Consultants: change my capacity and/or booking links (send only what changes; "" clears a link). */
export async function saveConsultantSettings(
  token: string,
  change: { capacity?: number; free_booking_url?: string; paid_booking_url?: string },
): Promise<ConsultantSettings> {
  const res = await apiFetch(`${API_URL}/api/consultant/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(change),
  });
  await raiseForStatus(res);
  return res.json();
}

/** Consultants: make (or replace) my Cal.com webhook secret. */
export async function newWebhookSecret(token: string): Promise<ConsultantSettings> {
  const res = await apiFetch(`${API_URL}/api/consultant/settings/webhook-secret`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
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

// ── Consultations (api/routers/consultations.py) ──────────────────────────────

/** The client's consultations and which booking link to show (free or paid). */
export async function getConsultations(token: string): Promise<ConsultationSummary> {
  const res = await apiFetch(`${API_URL}/api/consultations`, { headers: authHeaders(token) });
  await raiseForStatus(res);
  return res.json();
}

/**
 * Consultants (working on a client): mark a consultation — "completed" uses up
 * the free call. Without consultationId a consultation is added by hand.
 */
export const markConsultation = (
  token: string,
  body: { status: ConsultationStatus; consultation_id?: string; kind?: "free" | "paid"; note?: string },
) => postJson<ConsultationSummary>("/api/consultations", body, token);

// ── Stored data (Data Engineering; api/routers/warehouse.py) ──────────────────
// Same files and cleaning choices as the cleaning report, so the load uses the
// data the client already checked. Nothing is stored until loadToWarehouse.

/** The event type and column changes picked in the preview. */
export interface LoadChoices {
  groupId: number;
  eventType: string; // "" = let the server guess
  choices: LoadChoicesBody; // column roles / metric descriptions changed by the client
  combine?: CombineSpec | null; // stack or join other tables first (null = this table alone)
  replaceSource?: string | null; // "Update my most recent upload": the stored source to replace
}

const loadFields = (input: CleaningInput, c: LoadChoices) => ({
  group_id: String(c.groupId),
  choices: JSON.stringify(c.choices),
  event_type: c.eventType,
  ...(c.combine ? { combine: JSON.stringify(c.combine) } : {}),
  ...(c.replaceSource ? { replace_source: c.replaceSource } : {}),
  ...choiceFields(input),
});

/** How the data will be stored, why, the first rows and the round-trip check. Stores nothing. */
export async function previewLoad(token: string, input: CleaningInput, c: LoadChoices): Promise<LoadPlan> {
  const res = await apiFetch(`${API_URL}/api/warehouse/preview`, {
    method: "POST",
    headers: authHeaders(token),
    body: filesForm(input.files, loadFields(input, c)),
  });
  await raiseForStatus(res);
  return res.json();
}

/** Accept the preview (its fingerprint) and load it. `consent` = "store my data" ticked. */
export async function loadToWarehouse(
  token: string,
  input: CleaningInput,
  c: LoadChoices,
  fingerprint: string,
  consent: boolean,
  /** "Load only the new records": skip what another source already stored (the preview's duplicates). */
  skipDuplicates = false,
): Promise<LoadResult> {
  const res = await apiFetch(`${API_URL}/api/warehouse/load`, {
    method: "POST",
    headers: authHeaders(token),
    body: filesForm(input.files, {
      ...loadFields(input, c), fingerprint, consent: String(consent), skip_duplicates: String(skipDuplicates),
    }),
  });
  await raiseForStatus(res);
  return res.json();
}

/**
 * The client's stored data as a CSV File — the same kind of object as a file
 * picked from the computer. The workspace feeds it into the normal cleaning
 * flow ("Use my stored data"), so every service works on stored data, and the
 * client and their consultant see the same thing. Also used to download it.
 */
export async function getStoredDataFile(token: string, source?: string): Promise<File> {
  const q = source ? `?source=${encodeURIComponent(source)}` : "";
  const res = await apiFetch(`${API_URL}/api/warehouse/export${q}`, { headers: authHeaders(token) });
  await raiseForStatus(res);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "stored_data.csv";
  return new File([await res.blob()], name, { type: "text/csv" });
}

/** "Delete my stored data": everything stored for this client; consent withdrawn. */
export const deleteStoredData = (token: string) =>
  postJson<DeleteStoredResult>("/api/warehouse/delete", { confirm: "DELETE" }, token);

/** What this client has stored (per source), and recent loads. */
export async function getWarehouseSummary(token: string): Promise<WarehouseSummary> {
  const res = await apiFetch(`${API_URL}/api/warehouse/summary`, { headers: authHeaders(token) });
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

/**
 * Beacon, the AI assistant: one plain-English question about the open table, counted
 * against this month's allowance (429 when it's used up). `simulate` is for
 * admins only ("free" | "premium").
 */
export async function askAi(token: string, input: CleaningInput, question: string, groupId = 0,
  simulate?: string): Promise<AiAnswer> {
  const res = await apiFetch(`${API_URL}/api/ai/ask`, {
    method: "POST",
    headers: authHeaders(token),
    body: filesForm(input.files, { question, group_id: String(groupId), simulate: simulate ?? "", ...choiceFields(input) }),
  });
  await raiseForStatus(res);
  return res.json();
}

/** This month's Beacon allowance (tier, used, limit, reset date). */
export async function getAiUsage(token: string, simulate?: string): Promise<AiUsage> {
  const q = simulate ? `?simulate=${encodeURIComponent(simulate)}` : "";
  const res = await apiFetch(`${API_URL}/api/ai/usage${q}`, { headers: authHeaders(token) });
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

// ── Passwords (api/routers/accounts.py, api/services/auth.py) ─────────────────

/** "Forgot password?": email a reset link (the reply is the same for any email). */
export const forgotPassword = (email: string) => postJson<{ message: string }>("/api/accounts/forgot-password", { email });

/** The reset page: the token from the emailed link + the new password. */
export const resetPassword = (token: string, password: string) =>
  postJson<{ message: string; email: string }>("/api/accounts/reset-password", { token, password });

/** Signed in (Account page): email myself a reset link. */
export const sendMyResetLink = (token: string) => postJson<{ message: string }>("/api/accounts/reset-link", {}, token);

// ── Business context and dashboards (api/routers/dashboards.py) ──────────────

/** Save "About your business" for the workspace's account. */
export async function saveBusinessContext(token: string, context: BusinessContext): Promise<{ context: BusinessContext }> {
  const res = await apiFetch(`${API_URL}/api/accounts/business-context`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify(context),
  });
  await raiseForStatus(res);
  return res.json();
}

/** The client's dashboard tabs, business context, industry and most-asked questions. */
export async function getDashboards(token: string): Promise<DashboardsInfo> {
  const res = await apiFetch(`${API_URL}/api/dashboards`, { headers: authHeaders(token) });
  await raiseForStatus(res);
  return res.json();
}

/** Questions worth a tab, for this table (its columns) and this business. */
export const getDashboardSuggestions = (token: string, columns: { name: string; type: string }[]) =>
  postJson<{ suggestions: DashboardSuggestion[] }>("/api/dashboards/suggestions", { columns }, token);

/** Add a tab (id null) or change one. Returns everything GET /api/dashboards returns. */
export async function saveDashboardTab(token: string, tab: { id: string | null; name: string; questions: string[] }): Promise<DashboardsInfo> {
  const res = await apiFetch(`${API_URL}/api/dashboards/tabs${tab.id ? `/${encodeURIComponent(tab.id)}` : ""}`, {
    method: tab.id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ name: tab.name, questions: tab.questions }),
  });
  await raiseForStatus(res);
  return res.json();
}

/** Remove a tab. */
export async function deleteDashboardTab(token: string, id: string): Promise<DashboardsInfo> {
  const res = await apiFetch(`${API_URL}/api/dashboards/tabs/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  await raiseForStatus(res);
  return res.json();
}

/** Remember a question asked of the data (text only) — repeated ones become suggested tabs. Fire and forget. */
export function logQuestion(token: string, question: string, answered: boolean) {
  postJson("/api/questions", { question, answered }, token).catch(() => {});
}

// ── Cloud databases (api/routers/connections.py) ──────────────────────────────

/** Connect the client's own Supabase / PostgreSQL database (read-only; the login is saved encrypted). */
export const connectSupabase = (token: string, connectionString: string) =>
  postJson<{ connectors: DataConnection[]; tables: CloudTable[]; schemas: CloudSchema[] }>(
    "/api/connections/supabase", { connection_string: connectionString }, token);

/** The connected database's tables and views (has_rows: false = empty), and a per-schema count. */
export async function getCloudTables(
  token: string,
): Promise<{ tables: CloudTable[]; schemas: CloudSchema[]; max_rows: number; needs_project: boolean }> {
  const res = await apiFetch(`${API_URL}/api/connections/supabase/tables`, { headers: authHeaders(token) });
  await raiseForStatus(res);
  return res.json();
}

/** A live CSV read (cloud table or QuickBooks / Xero records) as a File, with how many rows came. */
async function liveCsv(token: string, path: string, filename: string): Promise<CloudTableFile> {
  const res = await apiFetch(`${API_URL}${path}`, { headers: authHeaders(token) });
  await raiseForStatus(res);
  return {
    file: new File([await res.blob()], filename, { type: "text/csv" }),
    rows: Number(res.headers.get("X-Rows") ?? 0),
    truncated: res.headers.get("X-Truncated") === "true",
  };
}

/**
 * One table of the client's database as a CSV File (read-only, never stored) —
 * opened like any file. An empty table is refused by the server (422).
 */
export async function getCloudTableFile(token: string, schema: string, name: string): Promise<CloudTableFile> {
  const q = `schema=${encodeURIComponent(schema)}&name=${encodeURIComponent(name)}`;
  return liveCsv(token, `/api/connections/supabase/table?${q}`, `${schema}.${name}.csv`);
}

/**
 * "Connect QuickBooks / Xero / Supabase": the provider's sign-in address. The
 * page then goes there; the provider sends the person back to
 * /app?integration=... — or to /start?integration=... (returnTo "start", from Get Started).
 */
export async function startConnect(token: string, connector: string, returnTo: "app" | "start" = "app"): Promise<string> {
  const q = returnTo === "start" ? "?return_to=start" : "";
  const res = await apiFetch(`${API_URL}/api/connections/${encodeURIComponent(connector)}/authorize${q}`, {
    headers: authHeaders(token),
  });
  await raiseForStatus(res);
  return (await res.json()).url;
}

/** The free live quick look of a connected QuickBooks / Xero (start/end: ISO dates; default this year). */
export async function getQuickLook(token: string, connector: string, start?: string, end?: string): Promise<QuickLook> {
  const q = new URLSearchParams();
  if (start) q.set("start", start);
  if (end) q.set("end", end);
  const res = await apiFetch(`${API_URL}/api/connections/${encodeURIComponent(connector)}/quick-look?${q}`, {
    headers: authHeaders(token),
  });
  await raiseForStatus(res);
  return res.json();
}

/** The record types of a connected QuickBooks / Xero (has_rows false = empty). */
export async function getLiveDatasets(token: string, connector: string): Promise<{ datasets: LiveDataset[] }> {
  const res = await apiFetch(`${API_URL}/api/connections/${encodeURIComponent(connector)}/datasets`, {
    headers: authHeaders(token),
  });
  await raiseForStatus(res);
  return res.json();
}

/** One QuickBooks / Xero record type as a CSV File — live, never stored — opened like any file. */
export const getLiveDatasetFile = (token: string, connector: string, id: string) =>
  liveCsv(token, `/api/connections/${encodeURIComponent(connector)}/dataset?id=${encodeURIComponent(id)}`,
    `${connector}.${id}.csv`);

/**
 * The stored sync (Data Engineering): store a connected QuickBooks / Xero's
 * records now, then on its refresh schedule. `consent` = "store my data"
 * ticked (needed once, like any storing).
 */
export const startSync = (token: string, connector: string, consent: boolean) =>
  postJson<{ sync: SyncResult; connectors: DataConnection[] }>(
    `/api/connections/${encodeURIComponent(connector)}/sync`, { consent }, token);

/** Name matches between sources waiting for a person ("Is this the same customer?"). */
export async function getEntityReview(token: string): Promise<{ matches: EntityMatch[] }> {
  const res = await apiFetch(`${API_URL}/api/entities/review`, { headers: authHeaders(token) });
  await raiseForStatus(res);
  return res.json();
}

/** Answer one: same = merge them (no stored rows change), different = keep them apart. */
export const decideEntity = (token: string, linkId: number, same: boolean) =>
  postJson<{ matches: EntityMatch[] }>("/api/entities/review", { link_id: linkId, same }, token);

/** After "Sign in with Supabase": the client's projects and the one being read. */
export async function getSupabaseProjects(token: string): Promise<{ projects: SupabaseProject[]; selected: string | null }> {
  const res = await apiFetch(`${API_URL}/api/connections/supabase/projects`, { headers: authHeaders(token) });
  await raiseForStatus(res);
  return res.json();
}

/** Pick the Supabase project to read. */
export async function chooseSupabaseProject(token: string, ref: string): Promise<{ connectors: DataConnection[] }> {
  const res = await apiFetch(`${API_URL}/api/connections/supabase/project`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ ref }),
  });
  await raiseForStatus(res);
  return res.json();
}

/** Disconnect an integration (its saved login is deleted). */
export async function disconnectIntegration(token: string, connector: string): Promise<{ connectors: DataConnection[] }> {
  const res = await apiFetch(`${API_URL}/api/connections/${encodeURIComponent(connector)}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  await raiseForStatus(res);
  return res.json();
}


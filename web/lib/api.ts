// ── FastAPI client ────────────────────────────────────────────────────────────
// Every call from the website to the backend goes through this file, so the
// base URL, error handling, and auth header live in ONE place.
//
// The backend URL comes from NEXT_PUBLIC_API_URL (see web/.env.example).
// "NEXT_PUBLIC_" means Next.js includes the value in the browser bundle — so
// never put secrets in a NEXT_PUBLIC_ variable.

import type {
  AnalyzeResponse,
  AuthResponse,
  IntakeOptions,
  MissingStrategy,
  Quote,
  SignupInput,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  await raiseForStatus(res);
  return res.json();
}

async function postJson<T>(path: string, body: unknown, token?: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
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

export const signin = (email: string) => postJson<AuthResponse>("/api/accounts/signin", { email });

/** Guest step: upload files and get the cleaning report + preview. */
export async function analyzeFiles(files: File[], strategy: MissingStrategy): Promise<AnalyzeResponse> {
  const res = await fetch(`${API_URL}/api/files/analyze`, {
    method: "POST",
    body: filesForm(files, { missing_strategy: strategy }),
  });
  await raiseForStatus(res);
  return res.json();
}

/**
 * Signed-in step: send the same files again and save the cleaned result.
 * The file is never stored on the server — see api/routers/files.py.
 */
export async function downloadCleaned(opts: {
  files: File[];
  token: string;
  groupId: number;
  strategy: MissingStrategy;
  format: "csv" | "xlsx";
}): Promise<{ amountUsd: string | null }> {
  const res = await fetch(`${API_URL}/api/files/download`, {
    method: "POST",
    headers: { Authorization: `Bearer ${opts.token}` },
    body: filesForm(opts.files, {
      group_id: String(opts.groupId),
      missing_strategy: opts.strategy,
      file_format: opts.format,
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

  return { amountUsd: res.headers.get("X-Amount-USD") };
}

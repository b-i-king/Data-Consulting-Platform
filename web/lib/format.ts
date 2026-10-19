// Small display helpers (US formatting: 1,234 and $1,234.56).

export const fmtInt = (n: number) => n.toLocaleString("en-US");
/** A number in US format with up to 2 decimals: 1234.567 -> "1,234.57". */
export const fmtNumber = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });

export const fmtUsd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD" });

export const fmtPct = (n: number) => `${n.toFixed(n < 10 && n % 1 ? 1 : 0)}%`;

/**
 * A data set's name, tidied for a header: no file extension, no note in
 * parentheses, underscores / dashes / dots as spaces, each word capitalized.
 * Words already in capitals (KPI, Q3) stay as they are. Display only — never saved.
 *
 *   "sales_2026.csv"                       -> "Sales 2026"
 *   "CUSTOMER_LIST.xlsx"                   -> "Customer List"
 *   "monthlyKPI-report.xlsx"               -> "Monthly KPI Report"
 *   "Transactions (partitioned by year)"   -> "Transactions"
 */
export function prettyDataName(name: string | null | undefined): string {
  const raw = (name ?? "").trim();
  // Drop a folder path ("exports/sales.csv"), but keep a " / " between words ("Employees / Payroll").
  const base = /\s/.test(raw) ? raw : raw.split(/[\\/]/).pop() ?? "";
  const stem = base
    .replace(/\.[A-Za-z0-9]{1,8}$/, "")                            // the extension: .csv, .xlsx, .parquet
    .replace(/\s*\([^)]*\)\s*$/, "");                              // a trailing "(note)"
  const words = stem
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")                        // camelCase -> camel Case
    .split(/[\s._-]+/)
    .filter(Boolean);
  // A name typed ALL IN CAPITALS ("CUSTOMER_LIST") is capitalized normally; otherwise
  // words in capitals are short forms and stay (KPI). Words with a digit (Q3) always stay.
  const shouting = !/[a-z]/.test(stem);
  const keep = (w: string) => /\d/.test(w) || (!shouting && /[A-Z]/.test(w) && w === w.toUpperCase());
  const nice = words.map((w) => (keep(w) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1).toLowerCase()));
  return nice.join(" ") || "Your data";
}

/** An ISO time as US date + time in the visitor's time zone: "10/20/2026, 3:00 PM". */
export const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString("en-US", {
    month: "2-digit",
    day: "2-digit",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });

/** An ISO date ("2024-10-11") as MM/DD/YYYY, without time-zone shifts. */
export const fmtDate = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${m}/${d}/${y}`;
};

export const SITE_NAME = process.env.NEXT_PUBLIC_SITE_NAME ?? "Data Consulting Platform";

export const FALLBACK_BOOKING_URL = process.env.NEXT_PUBLIC_BOOKING_URL ?? "https://calendly.com/your-link";

/** The integrations' names, by connector id (one place, so a new tool is named everywhere). */
export const TOOL_NAMES: Record<string, string> = { supabase: "Supabase", quickbooks: "QuickBooks", xero: "Xero", wave: "Wave" };
export const toolName = (connector: string | null | undefined) => (connector && TOOL_NAMES[connector]) || connector || "your app";

// Small display helpers (US formatting: 1,234 and $1,234.56).

export const fmtInt = (n: number) => n.toLocaleString("en-US");

export const fmtUsd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD" });

export const fmtPct = (n: number) => `${n.toFixed(n < 10 && n % 1 ? 1 : 0)}%`;

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

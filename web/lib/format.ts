// Small display helpers (US formatting: 1,234 and $1,234.56).

export const fmtInt = (n: number) => n.toLocaleString("en-US");

export const fmtUsd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD" });

export const fmtPct = (n: number) => `${n.toFixed(n < 10 && n % 1 ? 1 : 0)}%`;

export const SITE_NAME = process.env.NEXT_PUBLIC_SITE_NAME ?? "Data Consulting Platform";

export const FALLBACK_BOOKING_URL = process.env.NEXT_PUBLIC_BOOKING_URL ?? "https://calendly.com/your-link";

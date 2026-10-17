// The free "quick look" of a connected QuickBooks or Xero: income, expenses,
// net income, what customers owe, what the business owes, month by month, and
// the top customers — for any dates (default: this year so far).
//
// LIVE and NEVER STORED: every view asks QuickBooks / Xero again
// (GET /api/connections/{connector}/quick-look, api/services/business_tools.py).
// QuickBooks numbers are its own reports; Xero's are added up from invoices
// and bank transactions, so they're labeled an estimate. FREE for the last 12
// months (the server moves an earlier start up); longer history = the stored sync.

"use client";

import { useEffect, useState } from "react";
import { getQuickLook } from "@/lib/api";
import { fmtDate, toolName } from "@/lib/format";
import type { QuickLook as QuickLookData } from "@/lib/types";
import { Button, ErrorBox, Stat } from "@/components/wizard/ui";

/** Money in the report's currency, US formatting: $1,234.56. */
function money(n: number | null, currency: string) {
  if (n === null) return "—"; // the tool doesn't share it (Wave: no expenses)
  try {
    return n.toLocaleString("en-US", { style: "currency", currency });
  } catch {
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
}

/** "2026-01" -> "Jan 2026". */
function monthLabel(ym: string) {
  const [y, m] = ym.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

export default function QuickLook(props: { token: string; connector: string }) {
  const thisYear = new Date().getFullYear();
  const [start, setStart] = useState(`${thisYear}-01-01`);
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [data, setData] = useState<QuickLookData | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // First load: this year so far. Later loads come from the "Update" button.
  useEffect(() => {
    getQuickLook(props.token, props.connector)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't read it."))
      .finally(() => setBusy(false));
  }, [props.token, props.connector]);

  async function update() {
    setBusy(true);
    setError(null);
    try {
      setData(await getQuickLook(props.token, props.connector, start, end));
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't read it.");
    } finally {
      setBusy(false);
    }
  }

  const cur = data?.currency ?? "USD";
  // Bars are scaled to the biggest month, so the tallest bar is full width.
  const biggest = Math.max(1, ...(data?.monthly ?? []).flatMap((m) => [m.income, m.expenses ?? 0]));

  return (
    <div className="space-y-4 rounded-xl border border-line p-4">
      <div className="flex flex-wrap items-end gap-2 text-sm">
        <label className="space-y-1">
          <span className="block text-xs text-ink-muted">From</span>
          <input type="date" value={start} min={data?.earliest} onChange={(e) => setStart(e.target.value)}
            className="rounded-xl border border-line bg-surface px-2 py-1" />
        </label>
        <label className="space-y-1">
          <span className="block text-xs text-ink-muted">To</span>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
            className="rounded-xl border border-line bg-surface px-2 py-1" />
        </label>
        <Button variant="secondary" disabled={busy || !start || !end} onClick={update}>
          {busy ? "Reading…" : "Update"}
        </Button>
      </div>
      <ErrorBox message={error} />
      {busy && !data && <p className="text-sm text-ink-muted">Reading from {toolName(props.connector)}…</p>}
      {data && (
        <>
          <p className="text-xs text-ink-muted">
            {data.company} · {fmtDate(data.start)} to {fmtDate(data.end)} · live, nothing stored
            {data.estimate && " · estimate"}
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Stat label="Income" value={money(data.totals.income, cur)} />
            <Stat label="Expenses" value={money(data.totals.expenses, cur)} />
            <Stat label="Net income" value={money(data.totals.net_income, cur)}
              tone={data.totals.net_income === null ? undefined : data.totals.net_income >= 0 ? "good" : "bad"} />
            <Stat label="Customers owe you" value={money(data.totals.owed_to_you, cur)} />
            <Stat label="You owe" value={money(data.totals.you_owe, cur)} />
          </div>

          {data.monthly.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-semibold">Month by month</h4>
              <div className="flex gap-3 text-xs text-ink-muted">
                <span><span className="mr-1 inline-block h-2 w-3 rounded-sm bg-good" />Income</span>
                <span><span className="mr-1 inline-block h-2 w-3 rounded-sm bg-bad" />Expenses</span>
              </div>
              <ul className="space-y-2">
                {data.monthly.map((m) => (
                  <li key={m.month} className="grid grid-cols-[5.5rem_minmax(0,1fr)_7rem] items-center gap-2 text-xs">
                    <span className="text-ink-muted">{monthLabel(m.month)}</span>
                    <span className="space-y-0.5">
                      <span className="block h-2 rounded-sm bg-good" style={{ width: `${(m.income / biggest) * 100}%` }} />
                      {m.expenses !== null && (
                        <span className="block h-2 rounded-sm bg-bad" style={{ width: `${(m.expenses / biggest) * 100}%` }} />
                      )}
                    </span>
                    <span className={`text-right font-mono ${m.net === null ? "text-ink-muted" : m.net >= 0 ? "text-good" : "text-bad"}`}>
                      {m.net === null ? money(m.income, cur) : money(m.net, cur)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.top_customers.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-semibold">Top customers</h4>
              <ol className="divide-y divide-line rounded-xl border border-line text-sm">
                {data.top_customers.map((c, i) => (
                  <li key={`${c.name}-${i}`} className="flex justify-between gap-3 px-3 py-1.5">
                    <span>{i + 1}. {c.name}</span>
                    <span className="font-mono">{money(c.amount, cur)}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
          <p className="text-xs text-ink-muted">{data.basis}</p>
          <p className={`text-xs ${data.history_limited ? "font-semibold text-warn" : "text-ink-muted"}`}>
            The free quick look covers the last 12 months (from {fmtDate(data.earliest)}). For your full history and
            trends over the years, store your data on a schedule with Data Engineering.
          </p>
        </>
      )}
    </div>
  );
}

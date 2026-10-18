// Admin panel -> "Prices": THE one place to change what each tool costs.
//
// Each row is one tool (or the All tools bundle) with its monthly and annual
// price. Saving a row calls PUT /api/admin/prices/{plan} (api/services/price_store.py):
//   1. Stripe first (when it's on): a NEW Stripe price under the same lookup
//      key; the old one is archived. People already subscribed keep their price.
//   2. Then the database table dim.plan_prices.
//   3. Then the running site: every page, quote and checkout shows the new price.
// If Stripe refuses, nothing changes anywhere, so the site never shows a price
// Stripe wouldn't charge. "Trial days" = a free trial on a FIRST package with
// this tool (0 = none; one trial per account; api/services/offers.py).

"use client";

import { useEffect, useState } from "react";
import { getAdminPrices, setPlanPrice } from "@/lib/api";
import { fmtDate, fmtUsd } from "@/lib/format";
import type { AdminPrices as AdminPricesData, PlanPriceRow } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const input =
  "w-24 rounded-lg border border-line bg-surface px-2 py-1 text-right font-mono text-sm outline-none focus:border-brand";

/** One editable row: monthly + annual inputs, "2 months free" hint, trial days, Save. */
function PriceRow(props: { row: PlanPriceRow; busy: boolean; onSave: (m: number, a: number, t: number) => void }) {
  const { row } = props;
  const [monthly, setMonthly] = useState(String(row.monthly_usd));
  const [annual, setAnnual] = useState(String(row.annual_usd));
  const [trial, setTrial] = useState(String(row.trial_days ?? 0));

  const m = Number(monthly);
  const a = Number(annual);
  const t = Number(trial);
  const valid = monthly !== "" && annual !== "" && trial !== "" && Number.isFinite(m) && Number.isFinite(a)
    && Number.isInteger(t);
  const changed = valid && (m !== row.monthly_usd || a !== row.annual_usd || t !== (row.trial_days ?? 0));
  // Annual price in "months of the monthly price": 10 = "2 months free".
  const months = valid && m > 0 ? a / m : null;

  return (
    <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 text-sm">
        <span className="font-medium">{row.name}</span>
        {row.kind === "bundle" && <span className="ml-2 text-xs text-ink-muted">(bundle)</span>}
        {!row.billable && <span className="ml-2 text-xs text-ink-muted">(not charged yet)</span>}
        <span className="block text-xs text-ink-muted">
          Default {fmtUsd(row.default_monthly_usd)} / {fmtUsd(row.default_annual_usd)}
          {row.updated_at && ` · changed ${fmtDate(row.updated_at)}${row.updated_by ? ` by ${row.updated_by}` : ""}`}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-1">
          <span className="text-xs text-ink-muted">$/mo</span>
          <input className={input} inputMode="decimal" value={monthly} onChange={(e) => setMonthly(e.target.value)}
            aria-label={`${row.name} monthly price`} />
        </label>
        <label className="flex items-center gap-1">
          <span className="text-xs text-ink-muted">$/yr</span>
          <input className={input} inputMode="decimal" value={annual} onChange={(e) => setAnnual(e.target.value)}
            aria-label={`${row.name} annual price`} />
        </label>
        <span className="w-24 text-xs text-ink-muted">
          {months !== null && (months < 12 ? `${(12 - months).toFixed(1).replace(/\.0$/, "")} mo free` : "no discount")}
        </span>
        <label className="flex items-center gap-1">
          <span className="text-xs text-ink-muted">trial days</span>
          <input className="w-14 rounded-lg border border-line bg-surface px-2 py-1 text-right font-mono text-sm outline-none focus:border-brand"
            inputMode="numeric" value={trial} onChange={(e) => setTrial(e.target.value)}
            aria-label={`${row.name} free trial days`} />
        </label>
        <Button className="px-4 py-1" disabled={!changed || props.busy} onClick={() => props.onSave(m, a, t)}>
          {props.busy ? "Saving…" : "Save"}
        </Button>
      </div>
    </li>
  );
}

export default function AdminPrices({ token }: { token: string }) {
  const [data, setData] = useState<AdminPricesData | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    getAdminPrices(token)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load the prices."));
  }, [token]);

  async function save(row: PlanPriceRow, monthly: number, annual: number, trial: number) {
    setBusy(row.id);
    setError(null);
    setNotice(null);
    try {
      const res = await setPlanPrice(token, row.id, monthly, annual, trial);
      setData((d) => (d ? { ...d, prices: res.prices } : d));
      setNotice(
        `${row.name}: now ${fmtUsd(monthly)} a month, ${fmtUsd(annual)} a year` +
          (trial ? `, with a ${trial}-day free trial.` : ", no free trial.") +
          (res.stripe.enabled ? " Stripe is updated; current subscribers keep their old price." : ""),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "That price wasn't saved.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">Prices</h2>
      <p className="mt-1 text-sm text-ink-muted">
        What each tool costs, everywhere on the site.{" "}
        {data?.stripe
          ? "Saving also updates Stripe: new checkouts use the new price, current subscribers keep theirs."
          : "Stripe isn't on yet (test mode), so only the site changes; Stripe gets these prices when you connect it."}
      </p>
      <ErrorBox message={error} />
      {notice && (
        <p role="status" className="mt-3 rounded-xl border border-good/40 bg-good/10 p-2 text-sm">
          {notice}
        </p>
      )}
      {!data && !error && <p className="mt-3 text-sm text-ink-muted">Loading…</p>}
      {data && (
        <ul className="mt-2 divide-y divide-line">
          {data.prices.map((row) => (
            // The key includes the prices, so after a save the row starts fresh from the saved numbers.
            <PriceRow key={`${row.id}-${row.monthly_usd}-${row.annual_usd}-${row.trial_days}`} row={row} busy={busy === row.id} onSave={(m, a, t) => save(row, m, a, t)} />
          ))}
        </ul>
      )}
    </Card>
  );
}

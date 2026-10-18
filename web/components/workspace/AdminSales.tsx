// Admin panel -> "Seasonal sales": discounts that come back EVERY YEAR by themselves.
//
// Each row is a calendar window (e.g. 11/24 to 12/31) and a percent off a new
// client's FIRST payment (the first month, or the first year on annual
// billing). Switch one on once and it runs every year in that window — no
// edits, and nothing to set up in Stripe (the API makes the coupon itself).
// They start switched off. A window may wrap the new year (12/15 to 01/05).
// A free trial, when someone qualifies for one, is used instead of the sale.
// Server side: api/services/offers.py, PUT /api/admin/sales/{id}.

"use client";

import { useEffect, useState } from "react";
import { getAdminSales, setSeasonalSale } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { AdminSales as AdminSalesData, SeasonalSale } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const input = "rounded-lg border border-line bg-surface px-2 py-1 text-sm outline-none focus:border-brand";

// The UI writes dates as MM/DD (US style); the database keeps "MM-DD".
const toUi = (md: string) => md.replace("-", "/");
const toDb = (ui: string) => ui.trim().replace("/", "-");

function SaleRow(props: { sale: SeasonalSale; busy: boolean; onSave: (s: SeasonalSale) => void }) {
  const { sale } = props;
  const [name, setName] = useState(sale.name);
  const [starts, setStarts] = useState(toUi(sale.starts_on));
  const [ends, setEnds] = useState(toUi(sale.ends_on));
  const [pct, setPct] = useState(String(sale.percent_off));
  const draft = { ...sale, name: name.trim(), starts_on: toDb(starts), ends_on: toDb(ends), percent_off: Number(pct) };
  const changed =
    draft.name !== sale.name || draft.starts_on !== sale.starts_on || draft.ends_on !== sale.ends_on ||
    draft.percent_off !== sale.percent_off;

  return (
    <li className="space-y-2 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <input className={`${input} min-w-0 flex-1`} value={name} onChange={(e) => setName(e.target.value)}
          aria-label="Sale name" maxLength={60} />
        <label className="flex items-center gap-1">
          <span className="text-xs text-ink-muted">from</span>
          <input className={`${input} w-16 font-mono`} value={starts} onChange={(e) => setStarts(e.target.value)}
            aria-label="Starts (MM/DD)" placeholder="MM/DD" />
        </label>
        <label className="flex items-center gap-1">
          <span className="text-xs text-ink-muted">to</span>
          <input className={`${input} w-16 font-mono`} value={ends} onChange={(e) => setEnds(e.target.value)}
            aria-label="Ends (MM/DD)" placeholder="MM/DD" />
        </label>
        <label className="flex items-center gap-1">
          <input className={`${input} w-14 text-right font-mono`} inputMode="numeric" value={pct}
            onChange={(e) => setPct(e.target.value)} aria-label="Percent off" />
          <span className="text-xs text-ink-muted">% off</span>
        </label>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-ink-muted">
          {sale.active ? "On — runs every year in this window." : "Off."}
          {sale.updated_at && ` Changed ${fmtDate(sale.updated_at)}${sale.updated_by ? ` by ${sale.updated_by}` : ""}.`}
        </span>
        <div className="flex gap-2">
          {changed && (
            <Button className="px-4 py-1" disabled={props.busy} onClick={() => props.onSave(draft)}>
              Save
            </Button>
          )}
          <Button variant={sale.active ? "secondary" : "primary"} className="px-4 py-1" disabled={props.busy}
            onClick={() => props.onSave({ ...draft, active: !sale.active })}>
            {props.busy ? "Saving…" : sale.active ? "Switch off" : "Switch on"}
          </Button>
        </div>
      </div>
    </li>
  );
}

export default function AdminSales({ token }: { token: string }) {
  const [data, setData] = useState<AdminSalesData | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAdminSales(token)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load the sales."));
  }, [token]);

  async function save(sale: SeasonalSale) {
    setBusy(sale.id);
    setError(null);
    try {
      setData(await setSeasonalSale(token, sale));
    } catch (e) {
      setError(e instanceof Error ? e.message : "That sale wasn't saved.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">Seasonal sales</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Percent off a new client&apos;s first payment, in the same window every year. Switch one on once and it comes
        back by itself — nothing to set up in Stripe. A free trial, when someone qualifies, is used instead.
      </p>
      {data?.running && (
        <p role="status" className="mt-3 rounded-xl border border-good/40 bg-good/10 p-2 text-sm">
          Running now: <strong>{data.running.name}</strong>, {data.running.percent_off}% off until{" "}
          {fmtDate(data.running.ends)}.
        </p>
      )}
      <ErrorBox message={error} />
      {!data && !error && <p className="mt-3 text-sm text-ink-muted">Loading…</p>}
      <ul className="mt-2 divide-y divide-line">
        {data?.sales.map((s) => (
          // The key includes the saved values, so a row starts fresh after a save.
          <SaleRow key={`${s.id}-${s.name}-${s.starts_on}-${s.ends_on}-${s.percent_off}-${s.active}`} sale={s}
            busy={busy === s.id} onSave={save} />
        ))}
      </ul>
    </Card>
  );
}

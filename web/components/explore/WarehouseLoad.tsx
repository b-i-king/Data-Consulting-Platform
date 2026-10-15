// Data Engineering -> "Load into database": PREVIEW the transformation, then
// ACCEPT (or not) before anything is stored.
//
// A spreadsheet doesn't fit the warehouse tables as-is (db/warehouse.sql):
// one sales file becomes a customers table, a products table, and one
// "event" row per sale. This screen shows, before anything is stored:
//   1. what the rows are (sales, expenses, feedback...) and which column is
//      which — guessed by the server, changeable here (each change re-previews),
//   2. every transformation step with WHY it's needed,
//   3. the first rows of each warehouse table exactly as they will look,
//   4. how many rows will load, and which are skipped and why.
// Then the client (or their consultant) ticks the storage consent and presses
// "Accept and load" — or "Don't load", which stores nothing. The server only
// loads if the data and choices still match this preview (its fingerprint).
//
// Server side: api/routers/warehouse.py, api/services/warehouse_loader.py.

"use client";

import { useCallback, useEffect, useState } from "react";
import { getWarehouseSummary, loadToWarehouse, previewLoad, type LoadChoices } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtInt, fmtUsd } from "@/lib/format";
import type { Cell, CleaningInput, LoadPlan, LoadResult, PreviewTable, WarehouseSummary } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const TABLE_NAMES: Record<string, string> = {
  fact_event: "Events (fact_event)",
  dim_customer: "Customers (dim_customer)",
  dim_product: "Products (dim_product)",
};

const show = (v: Cell) => (v === null || v === undefined ? "" : String(v));

function Preview({ name, table }: { name: string; table: PreviewTable }) {
  return (
    <div className="space-y-1">
      <p className="text-sm font-medium">
        {TABLE_NAMES[name] ?? name}{" "}
        <span className="font-normal text-ink-muted">
          — first {Math.min(table.rows.length, table.total)} of {fmtInt(table.total)} row(s)
        </span>
      </p>
      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="min-w-full text-xs">
          <thead className="bg-surface-2">
            <tr>
              {table.columns.map((c) => (
                <th key={c} className="whitespace-nowrap px-2 py-1.5 text-left font-semibold">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} className="border-t border-line">
                {table.columns.map((c) => (
                  <td key={c} className="max-w-[18rem] truncate whitespace-nowrap px-2 py-1.5 font-mono" title={show(r[c])}>
                    {show(r[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function WarehouseLoad(props: { token: string; input: CleaningInput; groupId: number; clientName: string }) {
  const [plan, setPlan] = useState<LoadPlan | null>(null);
  const [choices, setChoices] = useState<LoadChoices>({ groupId: props.groupId, mapping: {}, eventType: "" });
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [declined, setDeclined] = useState(false);
  const [result, setResult] = useState<LoadResult | null>(null);
  const [stored, setStored] = useState<WarehouseSummary | null>(null);

  const refreshSummary = useCallback(() => {
    getWarehouseSummary(props.token)
      .then(setStored)
      .catch(() => {});
  }, [props.token]);

  // Preview again whenever a choice changes (nothing is stored by a preview).
  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError(null);
    setResult(null); // a new preview needs a new decision
    setDeclined(false);
    previewLoad(props.token, props.input, choices)
      .then((p) => !cancelled && setPlan(p))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "We couldn't build the preview."))
      .finally(() => !cancelled && setBusy(false));
    return () => {
      cancelled = true;
    };
  }, [props.token, props.input, choices]);

  useEffect(refreshSummary, [refreshSummary]);

  async function accept() {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try {
      const r = await loadToWarehouse(props.token, props.input, choices, plan.fingerprint, consent);
      setResult(r);
      refreshSummary();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The load failed. Nothing was stored.");
    } finally {
      setBusy(false);
    }
  }

  const needsConsent = plan ? !plan.storage.consented : true;

  return (
    <div className="space-y-5">
      <Card className="space-y-2">
        <h2 className="font-semibold">Load into the database — check it first</h2>
        <p className="text-sm text-ink-muted">
          Your data needs a few changes to fit the warehouse. Below is exactly how it will be transformed, why, and how
          it will look. <strong>Nothing is stored until you press “Accept and load”.</strong>
        </p>
        {plan?.storage.mode === "memory" && (
          <p className="rounded-xl bg-warn/10 p-2 text-xs">
            Test mode: no database is connected, so loads are kept in the server&apos;s memory and forgotten when it
            restarts. Set DATABASE_URL to store them in Supabase.
          </p>
        )}
        {plan?.storage.mode === "unavailable" && (
          <p className="rounded-xl bg-bad/10 p-2 text-xs">The database isn&apos;t reachable right now, so loading is off.</p>
        )}
      </Card>

      {plan && (
        <Card className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">What are these rows?</span>
              <select
                className="w-full rounded-xl border border-line bg-surface px-3 py-2"
                value={plan.event_type}
                disabled={busy}
                onChange={(e) => setChoices((c) => ({ ...c, eventType: e.target.value }))}
              >
                {plan.event_types.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="self-end text-xs text-ink-muted">
              Table: <strong>{plan.source_name}</strong> · {fmtInt(plan.counts.rows_in)} cleaned row(s)
            </p>
          </div>
          <details>
            <summary className="cursor-pointer text-sm font-medium">Which column is which? (we guessed — change any)</summary>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {plan.roles.map((r) => (
                <label key={r.id} className="space-y-1 text-xs">
                  <span>{r.label}</span>
                  <select
                    className="w-full rounded-xl border border-line bg-surface px-2 py-1.5 text-sm"
                    value={plan.mapping[r.id] ?? ""}
                    disabled={busy}
                    onChange={(e) => setChoices((c) => ({ ...c, mapping: { ...c.mapping, [r.id]: e.target.value } }))}
                  >
                    <option value="">(not used)</option>
                    {plan.columns.map((col) => (
                      <option key={col} value={col}>
                        {col}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          </details>
        </Card>
      )}

      {busy && !plan && <p className="text-sm text-ink-muted">Working out the transformation…</p>}
      <ErrorBox message={error} />

      {plan && (
        <Card className="space-y-3">
          <h3 className="font-semibold">How your data will be transformed, and why</h3>
          <ol className="space-y-3">
            {plan.steps.map((s, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand-soft text-xs font-semibold">
                  {i + 1}
                </span>
                <span>
                  <span className="font-medium">{s.title}</span>
                  <span className="block text-ink-muted">Why: {s.why}</span>
                </span>
              </li>
            ))}
          </ol>
          {plan.problem && <p className="rounded-xl bg-bad/10 p-3 text-sm">{plan.problem}</p>}
        </Card>
      )}

      {plan?.ready && (
        <Card className="space-y-4">
          <h3 className="font-semibold">How it will look in the database</h3>
          <p className="text-sm">
            <strong>{fmtInt(plan.counts.rows_to_load)}</strong> event row(s) to load
            {plan.counts.customers > 0 && <>, {fmtInt(plan.counts.customers)} customer(s)</>}
            {plan.counts.products > 0 && <>, {fmtInt(plan.counts.products)} product(s)</>}
            {plan.counts.rows_skipped > 0 && (
              <span className="text-ink-muted">
                {" "}
                · {fmtInt(plan.counts.rows_skipped)} skipped (
                {Object.entries(plan.skipped)
                  .map(([why, n]) => `${fmtInt(n)} ${why}`)
                  .join(", ")}
                )
              </span>
            )}
          </p>
          {Object.entries(plan.tables).map(([name, t]) => (
            <Preview key={name} name={name} table={t} />
          ))}

          {!result && !declined && (
            <div className="space-y-3 border-t border-line pt-4">
              {needsConsent && (
                <label className="flex items-start gap-3 text-sm">
                  <input type="checkbox" className="mt-0.5" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                  <span>
                    Store this data for <strong>{props.clientName}</strong> in the managed warehouse. It&apos;s kept
                    private to this account and can be deleted on request.{" "}
                    <span className="text-ink-muted">(Storage terms {plan.storage.terms_version}.)</span>
                  </span>
                </label>
              )}
              <div className="flex flex-wrap gap-3">
                <Button
                  disabled={busy || plan.storage.mode === "unavailable" || (needsConsent && !consent)}
                  onClick={accept}
                >
                  {busy ? "Working…" : "Accept and load"}
                </Button>
                <Button variant="secondary" disabled={busy} onClick={() => setDeclined(true)}>
                  Don&apos;t load
                </Button>
              </div>
            </div>
          )}
          {declined && !result && (
            <p className="rounded-xl bg-surface-2 p-3 text-sm">
              Nothing was stored. Change the choices above to see a new preview, or{" "}
              <button type="button" className="text-brand underline" onClick={() => setDeclined(false)}>
                load after all
              </button>
              .
            </p>
          )}
          {result && (
            <p role="status" className="rounded-xl border border-good/40 bg-good/10 p-3 text-sm">
              Loaded <strong>{fmtInt(result.result.rows_loaded)}</strong> new row(s)
              {result.result.rows_already_stored > 0 && (
                <> — {fmtInt(result.result.rows_already_stored)} were already stored and skipped (no duplicates)</>
              )}
              .
            </p>
          )}
        </Card>
      )}

      {stored && (
        <Card className="space-y-2">
          <h3 className="font-semibold">Stored for {props.clientName}</h3>
          {!stored.warehouse.set_up ? (
            <p className="text-sm text-bad">The warehouse tables aren&apos;t in the database yet: run db/warehouse.sql.</p>
          ) : stored.warehouse.events === 0 ? (
            <p className="text-sm text-ink-muted">Nothing stored yet.</p>
          ) : (
            <>
              <p className="text-sm">
                {fmtInt(stored.warehouse.events)} event(s), {fmtInt(stored.warehouse.customers)} customer(s),{" "}
                {fmtInt(stored.warehouse.products)} product(s) from {fmtInt(stored.warehouse.sources)} source(s)
                {stored.warehouse.first_date && stored.warehouse.last_date && (
                  <>
                    , {fmtDate(stored.warehouse.first_date)} – {fmtDate(stored.warehouse.last_date)}
                  </>
                )}
                .
              </p>
              <ul className="text-xs text-ink-muted">
                {stored.warehouse.by_type.map((t) => (
                  <li key={t.event_type}>
                    {t.event_type}: {fmtInt(t.events)} event(s){t.amount ? `, ${fmtUsd(t.amount)}` : ""}
                  </li>
                ))}
              </ul>
            </>
          )}
          {stored.loads.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-ink-muted">Recent loads ({stored.loads.length})</summary>
              <ul className="mt-1 space-y-0.5">
                {stored.loads.map((l) => (
                  <li key={l.id}>
                    {fmtDateTime(l.started_at)} · {l.source_name} · {l.status} · {fmtInt(l.rows_loaded)} loaded
                    {l.rows_skipped > 0 && `, ${fmtInt(l.rows_skipped)} skipped`}
                    {l.error && ` · ${l.error}`}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </Card>
      )}
    </div>
  );
}

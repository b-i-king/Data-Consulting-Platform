"use client";

// ── "Summarize here": do the math in the client's own data source ─────────────
// Pick what to add up (or average, or count), how to group it and, optionally,
// a period (by month…). The source itself does the work — PostgreSQL /
// Supabase / Redshift, Snowflake, BigQuery or Salesforce — and only the small
// answer comes back, opened in the workspace like a file
// (api/services/pushdown.py, warehouses.py). Faster than pulling every row,
// and on BigQuery / Snowflake cheaper for the client too.

import { useEffect, useState } from "react";
import { getWarehouseColumns, summarizeWarehouse } from "@/lib/api";
import { Button, ErrorBox } from "@/components/wizard/ui";

const AGGS: [string, string][] = [["sum", "Total"], ["avg", "Average"], ["count", "Count of rows"], ["min", "Smallest"], ["max", "Largest"]];
const PERIODS = ["", "day", "week", "month", "quarter", "year"];
const field = "rounded-xl border border-line bg-surface px-2 py-1.5 text-sm";

export default function SummarizePanel({ token, connector, table, sourceName, onOpenFile }: {
  token: string;
  connector: string;
  table: string;
  sourceName: string;
  onOpenFile: (file: File, from: string, note: string | null) => void;
}) {
  const [cols, setCols] = useState<{ name: string; kind: string }[] | null>(null);
  const [agg, setAgg] = useState("sum");
  const [metric, setMetric] = useState("");
  const [groups, setGroups] = useState<string[]>(["", ""]);
  const [dateColumn, setDateColumn] = useState("");
  const [period, setPeriod] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getWarehouseColumns(token, connector, table)
      .then((r) => {
        setCols(r.columns);
        setMetric(r.columns.find((c) => c.kind === "number")?.name ?? "");
        setDateColumn(r.columns.find((c) => c.kind === "date")?.name ?? "");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't read the columns."));
  }, [token, connector, table]);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await summarizeWarehouse(token, connector, {
        table, agg, metric: agg === "count" ? null : metric, group_by: groups.filter(Boolean),
        date_column: period ? dateColumn : null, period: period || null,
      });
      onOpenFile(r.file, `Summary from ${sourceName} (the math ran there)`, `query: ${r.query}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That summary didn't run.");
    } finally {
      setBusy(false);
    }
  }

  if (!cols) return error ? <ErrorBox message={error} /> : <p className="text-xs text-ink-muted">Reading the columns…</p>;
  const numbers = cols.filter((c) => c.kind === "number");
  const dates = cols.filter((c) => c.kind === "date");
  return (
    <div className="w-full space-y-2 rounded-xl bg-surface-2 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <select className={field} value={agg} onChange={(e) => setAgg(e.target.value)} aria-label="Calculation">
          {AGGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {agg !== "count" && (
          <>
            <span>of</span>
            <select className={field} value={metric} onChange={(e) => setMetric(e.target.value)} aria-label="Number column">
              {numbers.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            </select>
          </>
        )}
        <span>by</span>
        {groups.map((g, i) => (
          <select key={i} className={field} value={g} aria-label={`Group by ${i + 1}`}
            onChange={(e) => setGroups((old) => old.map((x, j) => (j === i ? e.target.value : x)))}>
            <option value="">{i === 0 ? "(no grouping)" : "(and nothing else)"}</option>
            {cols.filter((c) => c.name !== metric).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        ))}
        {dates.length > 0 && (
          <>
            <span>per</span>
            <select className={field} value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Period">
              {PERIODS.map((p) => <option key={p} value={p}>{p || "(no period)"}</option>)}
            </select>
            {period && (
              <select className={field} value={dateColumn} onChange={(e) => setDateColumn(e.target.value)} aria-label="Date column">
                {dates.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            )}
          </>
        )}
      </div>
      <Button disabled={busy || (agg !== "count" && !metric)} onClick={run}>
        {busy ? "Summarizing…" : `Summarize in ${sourceName}`}
      </Button>
      <ErrorBox message={error} />
    </div>
  );
}

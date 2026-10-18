// "Compare over time" (BETA): two open tables that share nothing but time.
//
// Shown on the Dashboard page when two or more tables are open (for example a
// game tracker from Supabase next to Wave invoices). Pick a date and a number
// in each table, by week or by month; both are added up per period and drawn
// as two charts stacked on the SAME periods (each with its own scale — hours
// and dollars can't share one), with how closely they move together and the
// cautions: few periods can be chance, and moving together isn't cause.
// Server side: POST /api/compare/over-time (api/services/compare.py). Nothing is stored.
//
// To remove the feature: delete this file and its <CompareOverTime> in
// components/workspace/Workspace.tsx (and, if wanted, api/routers/compare.py).

"use client";

import { useState } from "react";
import { compareOverTime } from "@/lib/api";
import type { AnalyzeResponse, CleaningInput, CompareResult } from "@/lib/types";
import LineChart from "@/components/charts/LineChart";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

type Agg = "sum" | "mean" | "count";
interface Side {
  groupId: number;
  date: string;
  value: string;
  agg: Agg;
}

const field = "w-full rounded-lg border border-line bg-surface px-2 py-1 text-sm";

/** A table's usable columns: dates, and numbers (cleaned; dropped columns left out). */
function columnsOf(group: AnalyzeResponse["groups"][number]) {
  const kept = group.report.column_reports.filter((c) => c.action !== "dropped");
  return {
    dates: kept.filter((c) => c.inferred_type.startsWith("date")).map((c) => c.column),
    numbers: kept.filter((c) => c.inferred_type.startsWith("number")).map((c) => c.column),
  };
}

/** "2026-01-01" -> "Jan 2026" (months) or "01/05/2026" (weeks). */
function periodLabel(iso: string, period: "week" | "month") {
  const [y, m, d] = iso.split("-").map(Number);
  if (period === "month") {
    return new Date(Date.UTC(y, m - 1, 1)).toLocaleString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
  }
  return `${String(m).padStart(2, "0")}/${String(d).padStart(2, "0")}/${y}`;
}

export default function CompareOverTime(props: { input: CleaningInput; analysis: AnalyzeResponse }) {
  const groups = props.analysis.groups.filter((g) => columnsOf(g).dates.length > 0);
  const start = (i: number): Side => {
    const g = groups[Math.min(i, groups.length - 1)];
    if (!g) return { groupId: -1, date: "", value: "", agg: "count" }; // no table with dates: the card hides itself
    const cols = columnsOf(g);
    return { groupId: g.group_id, date: cols.dates[0] ?? "", value: cols.numbers[0] ?? "", agg: cols.numbers.length ? "sum" : "count" };
  };
  const [left, setLeft] = useState<Side>(() => start(0));
  const [right, setRight] = useState<Side>(() => start(1));
  const [period, setPeriod] = useState<"week" | "month">("month");
  const [result, setResult] = useState<CompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (groups.length < 2) return null; // needs two tables with dates

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const side = (s: Side) => ({ group_id: s.groupId, date: s.date, value: s.agg === "count" ? null : s.value, agg: s.agg });
      setResult(await compareOverTime(props.input, { period, left: side(left), right: side(right) }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't compare those.");
    } finally {
      setBusy(false);
    }
  }

  const picker = (s: Side, set: (s: Side) => void, label: string) => {
    const g = groups.find((x) => x.group_id === s.groupId) ?? groups[0];
    const cols = columnsOf(g);
    return (
      <div className="space-y-2 rounded-xl border border-line p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">{label}</p>
        <select aria-label={`${label}: table`} className={field} value={s.groupId}
          onChange={(e) => {
            const ng = groups.find((x) => x.group_id === Number(e.target.value)) ?? groups[0];
            const nc = columnsOf(ng);
            set({ groupId: ng.group_id, date: nc.dates[0] ?? "", value: nc.numbers[0] ?? "", agg: nc.numbers.length ? "sum" : "count" });
          }}>
          {groups.map((x) => <option key={x.group_id} value={x.group_id}>{x.label}</option>)}
        </select>
        <div className="grid grid-cols-2 gap-2">
          <select aria-label={`${label}: how`} className={field} value={s.agg} onChange={(e) => set({ ...s, agg: e.target.value as Agg })}>
            {cols.numbers.length > 0 && <option value="sum">Total of</option>}
            {cols.numbers.length > 0 && <option value="mean">Average of</option>}
            <option value="count">Number of rows</option>
          </select>
          {s.agg !== "count" ? (
            <select aria-label={`${label}: number`} className={field} value={s.value} onChange={(e) => set({ ...s, value: e.target.value })}>
              {cols.numbers.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : <span />}
        </div>
        <label className="block text-xs text-ink-muted">
          by date in{" "}
          <select aria-label={`${label}: date`} className={`${field} mt-1`} value={s.date} onChange={(e) => set({ ...s, date: e.target.value })}>
            {cols.dates.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
      </div>
    );
  };

  const r = result?.correlation;
  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-semibold">
            Compare over time <span className="ml-1 rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-semibold text-ink-muted">Beta</span>
          </h3>
          <p className="text-sm text-ink-muted">Two tables with nothing in common but dates — added up by period and lined up.</p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <select aria-label="Period" className="rounded-lg border border-line bg-surface px-2 py-1" value={period}
            onChange={(e) => setPeriod(e.target.value as "week" | "month")}>
            <option value="month">By month</option>
            <option value="week">By week</option>
          </select>
          <Button disabled={busy || !left.date || !right.date} onClick={run}>{busy ? "Comparing…" : "Compare"}</Button>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {picker(left, setLeft, "First")}
        {picker(right, setRight, "Second")}
      </div>
      <ErrorBox message={error} />
      {result && (
        <div className="space-y-3">
          <p className="text-sm">
            <strong>{result.summary}</strong>
            {r !== null && r !== undefined && (
              <span className={`ml-2 rounded-full px-2 py-0.5 font-mono text-xs ${Math.abs(r) >= 0.4 ? "bg-brand-soft text-brand-ink" : "bg-surface-2 text-ink-muted"}`}>
                r = {r >= 0 ? "+" : ""}{r.toFixed(2)}
              </span>
            )}
          </p>
          {/* Two charts on the same periods: each keeps its own scale. */}
          <LineChart x={result.periods} formatX={(x) => periodLabel(x, result.period)} height={170}
            ariaLabel={result.left.label}
            series={[{ name: result.left.label, values: result.left.values, color: "var(--color-chart-current)" }]} />
          <LineChart x={result.periods} formatX={(x) => periodLabel(x, result.period)} height={170}
            ariaLabel={result.right.label}
            series={[{ name: result.right.label, values: result.right.values, color: "var(--color-chart-previous)" }]} />
          <ul className="space-y-1 text-xs text-ink-muted">
            {result.cautions.map((c) => <li key={c}>• {c}</li>)}
          </ul>
        </div>
      )}
    </Card>
  );
}

// "How this was calculated": click a chart on the dashboard to see the query
// behind it — for checking the numbers (transparency), like a BI tool's
// "view data". Shown for line and bar charts (not the KPI cards). Two views:
// SQL, and a simplified PANDAS version (what the dashboard really runs, in
// short) for developers who check the numbers in Python.
//
// The dashboard is computed with pandas, not a database, so the SQL here is
// the EQUIVALENT query: the same table, date range, filters, grouping and
// math (build-dashboard's `lineage`: each metric's expression and the fields
// it reads). Alongside it: where the data came from (the files / stored
// table / connected app sent with the request), the fields used, and how many
// rows the table has after cleaning.

"use client";

import { useState } from "react";
import { fmtDate } from "@/lib/format";
import type { DashboardData, DashboardVisual } from "@/lib/types";
import { Button } from "@/components/wizard/ui";

/** SQL's name for a comparison's period: week over week -> week. */
const GRAIN: Record<string, string> = { dod: "day", wow: "week", mom: "month" };
/** pandas' name for the same period (data_skills/_time.py COMPARISONS). */
const FREQ: Record<string, string> = { dod: "D", wow: "W-MON", mom: "MS" };
/** A Python string literal. */
const py = (value: string) => `'${value.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;

const q = (name: string) => `"${name.replace(/"/g, '""')}"`;
const lit = (value: string) => `'${value.replace(/'/g, "''")}'`;

/** The equivalent SQL for one chart, with the range, filters and grouping on screen. */
export function chartSql(p: {
  visual: DashboardVisual;
  data: DashboardData;
  table: string;
  cmp: string;
  window: { start: string; end: string };
  filters: Record<string, string>;
}): string {
  const m = p.data.lineage?.metrics[p.visual.metric];
  if (!m) return "-- This chart's calculation isn't available.";
  const date = q(p.data.lineage!.date_column);
  const where = [
    `${date} BETWEEN ${lit(p.window.start)} AND ${lit(p.window.end)}`,
    ...(m.where ? [m.where] : []),
    ...Object.entries(p.filters).map(([col, value]) => `${q(col)} = ${lit(value)}`),
  ];
  const whereSql = `WHERE ${where.join("\n  AND ")}`;
  const label = q(m.label);
  if (p.visual.kind === "line") {
    const grain = GRAIN[p.cmp] ?? "month";
    return [
      `SELECT DATE_TRUNC('${grain}', ${date}) AS period,`,
      `       ${m.expression} AS ${label}`,
      `FROM ${q(p.table)}`,
      whereSql,
      "GROUP BY period",
      "ORDER BY period;",
    ].join("\n");
  }
  const dim = q(p.visual.dimension ?? "category");
  return [
    `SELECT ${dim},`,
    `       ${m.expression} AS ${label}`,
    `FROM ${q(p.table)}`,
    whereSql,
    `GROUP BY ${dim}`,
    "ORDER BY 2 DESC",
    "LIMIT 8;",
  ].join("\n");
}

/** The simplified pandas version for one chart (what the dashboard runs, in short). */
export function chartPandas(p: Parameters<typeof chartSql>[0]): string {
  const m = p.data.lineage?.metrics[p.visual.metric];
  if (!m) return "# This chart's calculation isn't available.";
  const date = py(p.data.lineage!.date_column);
  const lines = [
    "import pandas as pd",
    "",
    `# df = the cleaned ${py(p.table)} table (after your Data Cleaning choices)`,
    `df = df[df[${date}].between(${py(p.window.start)}, ${py(p.window.end)})]`,
    ...(m.pandas_where ? [`df = df[${m.pandas_where}]`] : []),
    ...Object.entries(p.filters).map(([col, value]) => `df = df[df[${py(col)}] == ${py(value)}]`),
  ];
  if (p.visual.kind === "line") {
    const freq = FREQ[p.cmp] ?? "MS";
    lines.push(`result = df.groupby(pd.Grouper(key=${date}, freq=${py(freq)})).apply(lambda g: ${m.pandas})`);
  } else {
    lines.push(`result = df.groupby(${py(p.visual.dimension ?? "category")}).apply(lambda g: ${m.pandas}).nlargest(8)`);
  }
  return lines.join("\n");
}

export default function QueryPanel(props: {
  visual: DashboardVisual;
  data: DashboardData;
  /** The table's name (the data's name, e.g. "sales"). */
  table: string;
  /** What the data came from: the file names (or stored / cloud / app record names) sent with the request. */
  sources: string[];
  cmp: string;
  cmpLabel: string;
  rangeLabel: string;
  window: { start: string; end: string };
  filters: Record<string, string>;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [view, setView] = useState<"sql" | "pandas">("sql");
  const m = props.data.lineage?.metrics[props.visual.metric];
  const sql = view === "sql" ? chartSql(props) : chartPandas(props);
  const fields = Array.from(new Set([
    props.data.lineage?.date_column ?? props.data.date_column,
    ...(props.visual.dimension ? [props.visual.dimension] : []),
    ...(m?.fields ?? []),
    ...Object.keys(props.filters),
  ]));

  async function copy() {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard blocked: the query can still be selected by hand.
    }
  }

  return (
    <div className="mt-3 space-y-3 rounded-2xl border border-line bg-surface-2 p-3 text-xs" role="region"
      aria-label={`How "${props.visual.title}" was calculated`}>
      <div className="flex items-center justify-between gap-2">
        <p className="font-semibold">How this was calculated</p>
        <button type="button" onClick={props.onClose} className="text-brand-ink hover:underline">Close</button>
      </div>
      <dl className="grid gap-x-3 gap-y-1 sm:grid-cols-[8rem_minmax(0,1fr)]">
        <dt className="text-ink-muted">Data source</dt>
        <dd>{props.sources.length ? props.sources.join(", ") : props.table}</dd>
        <dt className="text-ink-muted">Table</dt>
        <dd>
          <span className="font-mono">{props.table}</span> — <span className="font-mono">{props.data.rows.toLocaleString("en-US")}</span>{" "}
          rows after cleaning (with your Data Cleaning choices)
        </dd>
        <dt className="text-ink-muted">Fields used</dt>
        <dd className="font-mono">{fields.join(", ")}</dd>
        <dt className="text-ink-muted">Showing</dt>
        <dd>
          {props.rangeLabel} ({fmtDate(props.window.start)} to {fmtDate(props.window.end)})
          {props.visual.kind === "line" ? `, ${props.cmpLabel.toLowerCase()}` : ", top 8"}
          {Object.keys(props.filters).length > 0 &&
            `, filtered: ${Object.entries(props.filters).map(([k, v]) => `${k} = ${v}`).join(", ")}`}
        </dd>
        {m?.note && (
          <>
            <dt className="text-ink-muted">Note</dt>
            <dd>{m.note}</dd>
          </>
        )}
      </dl>
      <div role="tablist" aria-label="Query language" className="flex gap-1">
        {(["sql", "pandas"] as const).map((v) => (
          <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => setView(v)}
            className={`rounded-full px-3 py-0.5 font-mono ${view === v ? "bg-brand text-on-brand" : "border border-line hover:border-brand"}`}>
            {v === "sql" ? "SQL" : "pandas"}
          </button>
        ))}
      </div>
      <pre className="overflow-x-auto rounded-xl border border-line bg-surface p-3 font-mono text-[11px] leading-relaxed">{sql}</pre>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" className="px-3 py-1 text-xs" onClick={copy}>
          {copied ? "Copied ✓" : view === "sql" ? "Copy SQL" : "Copy pandas"}
        </Button>
        <span className="text-ink-muted">
          {view === "sql"
            ? "The dashboard is calculated in Python (pandas); this is the equivalent SQL, for checking the numbers."
            : "A simplified version of the pandas the dashboard runs (it also compares each period with the one before)."}
        </span>
      </div>
    </div>
  );
}

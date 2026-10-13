// Copilot Money-style time dashboard for one cleaned table.
//
// Calls the build-dashboard skill ONCE (data_skills/build-dashboard/skill.py),
// which returns every date range × every comparison. After that, every click
// (range, comparison, metric) is instant — no re-upload.
//
//   Ranges:       Last 4 weeks · Last 3 months · Last year · Month to date · Year to date · All time
//   Comparisons:  Day over day · Week over week · Month over month
//
// Layout follows the dataviz rules: one filter row above everything, KPI
// cards with ▲/▼ + text (never color alone), a line chart of this period vs.
// the previous period, a by-period table, and a category breakdown.

"use client";

import { useEffect, useState } from "react";
import { runSkill, saveText } from "@/lib/api";
import type { DashboardData, PeriodRow } from "@/lib/types";
import BarChart from "@/components/charts/BarChart";
import LineChart, { usDate } from "@/components/charts/LineChart";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const fmt = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 });
const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);

function Delta({ value, label = "vs. previous" }: { value: number | null | undefined; label?: string }) {
  if (value == null) return <span className="text-xs text-ink-muted">no previous period</span>;
  const up = value >= 0;
  return (
    <span className="text-xs text-ink-muted">
      <span className={`font-semibold ${up ? "text-good" : "text-bad"}`}>
        {up ? "▲" : "▼"} {pct(value)}
      </span>{" "}
      {label}
    </span>
  );
}

function Segmented<T extends string>(props: {
  label: string;
  options: Record<T, string>;
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={props.label} className="flex flex-wrap gap-1 rounded-full border border-line bg-surface p-1">
      {(Object.keys(props.options) as T[]).map((k) => (
        <button
          key={k}
          type="button"
          role="radio"
          aria-checked={props.value === k}
          onClick={() => props.onChange(k)}
          className={`rounded-full px-3 py-1 text-sm transition-colors ${
            props.value === k ? "bg-brand font-semibold text-on-brand" : "text-ink-muted hover:text-ink"
          }`}
        >
          {props.options[k]}
        </button>
      ))}
    </div>
  );
}

export default function Dashboard({ files, groupId }: { files: File[]; groupId: number }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [html, setHtml] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState("last_3_months");
  const [cmp, setCmp] = useState("wow");
  const [metric, setMetric] = useState("");
  const [showTable, setShowTable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    runSkill("build-dashboard", files, {}, groupId)
      .then((r) => {
        if (cancelled) return;
        const d = r.data as unknown as DashboardData;
        setData(d);
        setHtml(r.artifacts.html_dashboard ?? "");
        setRange(d.default_range);
        setCmp(d.default_comparison);
        setMetric(d.metrics[0]);
      })
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [files, groupId]);

  if (error) return <ErrorBox message={error} />;
  if (!data) return <p className="py-8 text-sm text-ink-muted">Building your dashboard…</p>;

  const R = data.ranges[range];
  // Day over day isn't calculated for very long ranges; fall back gracefully.
  const cmpOptions = Object.fromEntries(
    Object.entries(data.comparison_labels).filter(([k]) => R.series[k]),
  ) as Record<string, string>;
  const activeCmp = R.series[cmp] ? cmp : R.series.wow ? "wow" : "mom";
  const rows: PeriodRow[] = R.series[activeCmp][metric] ?? [];
  const partial = rows.length > 1 && rows[rows.length - 1].partial;
  const w = R.window;
  const breakdown = data.breakdown[range] ?? [];

  return (
    <div className="space-y-5">
      {/* One filter row above everything it controls. */}
      <div className="flex flex-wrap items-center gap-3">
        <Segmented label="Date range" options={data.range_labels} value={range} onChange={setRange} />
        <Segmented label="Comparison" options={cmpOptions} value={activeCmp} onChange={setCmp} />
        <label className="flex items-center gap-2 text-sm">
          <span className="text-ink-muted">Metric</span>
          <select
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
            className="rounded-xl border border-line bg-surface px-3 py-1.5 text-sm"
          >
            {data.metrics.map((m) => (
              <option key={m} value={m}>
                {data.metric_labels[m]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <p className="text-sm text-ink-muted">
        {data.range_labels[range]}: {usDate(w.start)} – {usDate(w.end)}
        {w.prev_start && ` · compared with ${usDate(w.prev_start)} – ${usDate(w.prev_end!)}`} · data as of{" "}
        {usDate(data.as_of)}
      </p>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {data.metrics.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMetric(m)}
            aria-pressed={metric === m}
            className={`rounded-2xl border bg-surface p-4 text-left shadow-[var(--shadow-card)] ${
              metric === m ? "border-brand" : "border-line hover:border-brand"
            }`}
          >
            <div className="text-xs uppercase tracking-wide text-ink-muted">{data.metric_labels[m]}</div>
            <div className="mt-1 font-mono text-2xl font-semibold tabular-nums">{fmt(R.kpis[m].current)}</div>
            <Delta value={R.kpis[m].pct_change} />
          </button>
        ))}
      </div>

      <Card>
        <h3 className="mb-2 font-semibold">
          {data.metric_labels[metric]} — {data.comparison_labels[activeCmp]}
        </h3>
        <LineChart
          ariaLabel={`${data.metric_labels[metric]}, ${data.comparison_labels[activeCmp]}, ${data.range_labels[range]}`}
          x={rows.map((r) => r.period)}
          series={[
            { name: "This period", values: rows.map((r) => r.value), color: "var(--color-chart-current)" },
            // The comparison line is the PREVIOUS RANGE lined up point by point
            // (e.g. the 4 weeks before), like Copilot Money's "vs. last month".
            ...(w.prev_start
              ? [{
                  name: `Previous period (${usDate(w.prev_start)} – ${usDate(w.prev_end!)})`,
                  values: rows.map((r) => r.prior_range),
                  color: "var(--color-chart-previous)",
                }]
              : []),
          ]}
          partialLast={partial}
          extraTooltip={(i) => {
            const r = rows[i];
            const lines = [];
            if (r.prior_period) lines.push(`Lined up with ${usDate(r.prior_period)}`);
            if (r.pct_change != null)
              lines.push(
                `${r.pct_change >= 0 ? "▲" : "▼"} ${pct(r.pct_change)} vs. previous ${
                  { dod: "day", wow: "week", mom: "month" }[activeCmp]
                } (${fmt(r.previous)})`,
              );
            return lines.length ? lines.join("\n") : null;
          }}
        />
        {partial && (
          <p className="mt-2 text-xs text-ink-muted">
            The last period is still in progress, so it&apos;s drawn faded. For a like-for-like comparison use Month to date
            or Year to date.
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setShowTable((s) => !s)}>
            {showTable ? "Hide" : "Show"} values by period
          </Button>
          {html && (
            <Button variant="secondary" onClick={() => saveText("dashboard.html", html, "text/html")}>
              Download dashboard (HTML)
            </Button>
          )}
        </div>
        {showTable && (
          <div className="mt-3 max-h-80 overflow-auto rounded-xl border border-line">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface-2 text-xs uppercase tracking-wide text-ink-muted">
                <tr>
                  {["Period", "Value", `Previous ${{ dod: "day", wow: "week", mom: "month" }[activeCmp]}`, "Change", "% change", "Previous range"].map((h) => (
                    <th key={h} className="px-3 py-2 text-right first:text-left">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line font-mono tabular-nums">
                {[...rows].reverse().map((r) => (
                  <tr key={r.period}>
                    <td className="px-3 py-1.5 text-left">
                      {usDate(r.period)}
                      {r.partial ? " (partial)" : ""}
                    </td>
                    <td className="px-3 py-1.5 text-right">{fmt(r.value)}</td>
                    <td className="px-3 py-1.5 text-right">{fmt(r.previous)}</td>
                    <td className="px-3 py-1.5 text-right">{fmt(r.change)}</td>
                    <td className="px-3 py-1.5 text-right">{pct(r.pct_change)}</td>
                    <td className="px-3 py-1.5 text-right">{fmt(r.prior_range)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {breakdown.length > 0 && data.dimension_column && (
        <Card>
          <h3 className="mb-3 font-semibold">
            {data.metric_labels[data.metrics[0]]} by {data.dimension_column} · {data.range_labels[range].toLowerCase()}
          </h3>
          <BarChart
            categories={breakdown.map((b) => b.category)}
            values={breakdown.map((b) => b.current)}
            extra={(i) => (breakdown[i].pct_change == null ? null : `${breakdown[i].pct_change! >= 0 ? "▲" : "▼"} ${pct(breakdown[i].pct_change)}`)}
          />
        </Card>
      )}
    </div>
  );
}

// Copilot Money-style dashboard for one cleaned table, built around KPIs.
//
// The build-dashboard skill (data_skills/build-dashboard/skill.py) decides:
//   * KPIs   — the 3–4 most common transaction TYPES (e.g. In-store, Online
//              pickup, Delivery, Catering), each with its own card + line chart
//   * Overview — total value and number of transactions, charted over time
//   * Filters  — customer / demographic columns (customer type, age group,
//              store...). Picking one asks the server again for just those rows.
//
// Every date range × comparison comes back in one response, so the time bar
// is instant. The time bar floats at the bottom of the screen while the
// dashboard is in view (CSS `sticky`), like Copilot Money:
//   Ranges:       Last 4 weeks · Last 3 months · Last year · Month to date · Year to date · All time
//   Comparisons:  Day over day · Week over week · Month over month

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { runSkill, saveText } from "@/lib/api";
import type { CleaningInput, DashboardData, PeriodRow } from "@/lib/types";
import BarChart from "@/components/charts/BarChart";
import LineChart, { usDate } from "@/components/charts/LineChart";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const fmt = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 });
const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);
const PERIOD: Record<string, string> = { dod: "day", wow: "week", mom: "month" };

function Delta({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-xs text-ink-muted">no previous period</span>;
  const up = value >= 0;
  return (
    <span className="text-xs text-ink-muted">
      <span className={`font-semibold ${up ? "text-good" : "text-bad"}`}>
        {up ? "▲" : "▼"} {pct(value)}
      </span>{" "}
      vs. previous
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
    // Phones: one row that scrolls sideways; wider screens: wraps and centers.
    <div role="radiogroup" aria-label={props.label} className="flex gap-1 overflow-x-auto sm:flex-wrap sm:justify-center">
      {(Object.keys(props.options) as T[]).map((k) => (
        <button
          key={k}
          type="button"
          role="radio"
          aria-checked={props.value === k}
          onClick={() => props.onChange(k)}
          className={`shrink-0 whitespace-nowrap rounded-full px-3 py-1 text-sm transition-colors ${
            props.value === k ? "bg-brand font-semibold text-on-brand" : "text-ink-muted hover:text-ink"
          }`}
        >
          {props.options[k]}
        </button>
      ))}
    </div>
  );
}

/** "This period" vs. "Previous period" line chart for one metric. */
function PeriodChart(props: {
  rows: PeriodRow[];
  cmp: string;
  label: string;
  prev: { start: string; end: string } | null;
  height?: number;
}) {
  const { rows, cmp } = props;
  const partial = rows.length > 1 && rows[rows.length - 1].partial;
  return (
    <LineChart
      ariaLabel={props.label}
      height={props.height}
      x={rows.map((r) => r.period)}
      series={[
        { name: "This period", values: rows.map((r) => r.value), color: "var(--color-chart-current)" },
        // The comparison line is the PREVIOUS RANGE lined up point by point
        // (e.g. the 4 weeks before), like Copilot Money's "vs. last month".
        ...(props.prev
          ? [{
              name: `Previous period (${usDate(props.prev.start)} – ${usDate(props.prev.end)})`,
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
          lines.push(`${r.pct_change >= 0 ? "▲" : "▼"} ${pct(r.pct_change)} vs. previous ${PERIOD[cmp]} (${fmt(r.previous)})`);
        return lines.length ? lines.join("\n") : null;
      }}
    />
  );
}

export default function Dashboard({ input, groupId }: { input: CleaningInput; groupId: number }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [range, setRange] = useState("last_3_months");
  const [cmp, setCmp] = useState("wow");
  const [overview, setOverview] = useState("__rows__");
  const [showTable, setShowTable] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const loadedOnce = useRef(false);

  // Only non-empty filters are sent: {"customer_type": "Wholesale"}.
  const activeFilters = useMemo(
    () => Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
    [filters],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    // include_html: false keeps the response small; the HTML file is built on Download.
    runSkill("build-dashboard", input, { filters: activeFilters, include_html: false }, groupId)
      .then((r) => {
        if (cancelled) return;
        const d = r.data as unknown as DashboardData;
        if (!loadedOnce.current) {
          // First load: start on the skill's suggested range and comparison.
          // (Later loads are filter changes — keep what the user picked.)
          loadedOnce.current = true;
          setRange(d.default_range);
          setCmp(d.default_comparison);
          setOverview(d.overview[d.overview.length - 1]);
        }
        setData(d);
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [input, groupId, activeFilters]);

  async function downloadHtml() {
    setDownloading(true);
    try {
      const r = await runSkill("build-dashboard", input, { filters: activeFilters, include_html: true }, groupId);
      saveText("dashboard.html", r.artifacts.html_dashboard ?? "", "text/html");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed.");
    } finally {
      setDownloading(false);
    }
  }

  if (!data) return error ? <ErrorBox message={error} /> : <p className="py-8 text-sm text-ink-muted">Building your dashboard…</p>;

  const R = data.ranges[range];
  // Day over day isn't calculated for very long ranges; fall back gracefully.
  const cmpOptions = Object.fromEntries(
    Object.entries(data.comparison_labels).filter(([k]) => R.series[k]),
  ) as Record<string, string>;
  const activeCmp = R.series[cmp] ? cmp : R.series.wow ? "wow" : "mom";
  const w = R.window;
  const prev = w.prev_start ? { start: w.prev_start, end: w.prev_end! } : null;
  const ovMetric = data.overview.includes(overview) ? overview : data.overview[0];
  const rows: PeriodRow[] = R.series[activeCmp][ovMetric] ?? [];
  const partial = rows.length > 1 && rows[rows.length - 1].partial;
  const breakdown = data.breakdown[range] ?? [];

  return (
    <div className="space-y-5">
      {/* Filters: WHO the numbers are about (customer type, demographics, place). */}
      <div className="flex flex-wrap items-end gap-3">
        {data.filter_columns.map((c) => (
          <label key={c} className="space-y-1 text-sm">
            <span className="block text-xs text-ink-muted">{data.filter_labels[c]}</span>
            <select
              value={filters[c] ?? ""}
              onChange={(e) => setFilters((f) => ({ ...f, [c]: e.target.value }))}
              className="rounded-xl border border-line bg-surface px-3 py-1.5 text-sm"
            >
              <option value="">All</option>
              {data.filter_options[c].map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        ))}
        {Object.keys(activeFilters).length > 0 && (
          <Button variant="secondary" onClick={() => setFilters({})}>
            Clear filters
          </Button>
        )}
        <div className="ml-auto flex items-center gap-3">
          {loading && <span className="text-xs text-ink-muted">Updating…</span>}
          <Button variant="secondary" onClick={downloadHtml} disabled={downloading}>
            {downloading ? "Preparing…" : "Download dashboard (HTML)"}
          </Button>
        </div>
      </div>
      <ErrorBox message={error} />

      <p className="text-sm text-ink-muted">
        {data.range_labels[range]}: {usDate(w.start)} – {usDate(w.end)}
        {prev && ` · compared with ${usDate(prev.start)} – ${usDate(prev.end)}`} · data as of {usDate(data.as_of)} ·{" "}
        {data.rows.toLocaleString("en-US")} {Object.keys(data.filters).length ? "matching rows" : "rows"}
      </p>

      {/* Overview: total value and transactions; click a card to chart it. */}
      <div className={`grid gap-3 ${data.overview.length > 1 ? "grid-cols-2" : "grid-cols-1 sm:max-w-xs"}`}>
        {data.overview.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setOverview(m)}
            aria-pressed={ovMetric === m}
            className={`rounded-2xl border bg-surface p-4 text-left shadow-[var(--shadow-card)] ${
              ovMetric === m ? "border-brand" : "border-line hover:border-brand"
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
          {data.metric_labels[ovMetric]} over time — {data.comparison_labels[activeCmp]}
        </h3>
        <PeriodChart
          rows={rows}
          cmp={activeCmp}
          prev={prev}
          label={`${data.metric_labels[ovMetric]}, ${data.comparison_labels[activeCmp]}, ${data.range_labels[range]}`}
        />
        {partial && (
          <p className="mt-2 text-xs text-ink-muted">
            The last period is still in progress, so it&apos;s drawn faded. For a like-for-like comparison use Month to date
            or Year to date.
          </p>
        )}
        <div className="mt-3">
          <Button variant="secondary" onClick={() => setShowTable((s) => !s)}>
            {showTable ? "Hide" : "Show"} values by period
          </Button>
        </div>
        {showTable && (
          <div className="mt-3 max-h-80 overflow-auto rounded-xl border border-line">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface-2 text-xs uppercase tracking-wide text-ink-muted">
                <tr>
                  {["Period", "Value", `Previous ${PERIOD[activeCmp]}`, "Change", "% change", "Previous range"].map((h) => (
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

      {/* One card + line chart per KPI (the most common transaction types). */}
      {data.kpis.length > 0 && (
        <div>
          <h3 className="mb-2 font-semibold">
            {data.kpi_column_label ? `Top ${data.kpi_column_label.toLowerCase()}s` : "Key metrics"}
            {data.kpi_column_label && <span className="font-normal text-ink-muted"> · {data.value_label.toLowerCase()}</span>}
          </h3>
          <div className="grid gap-3 lg:grid-cols-2">
            {data.kpis.map((m) => (
              <Card key={m}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="text-xs uppercase tracking-wide text-ink-muted">{data.metric_labels[m]}</div>
                  <Delta value={R.kpis[m].pct_change} />
                </div>
                <div className="mt-1 font-mono text-2xl font-semibold tabular-nums">{fmt(R.kpis[m].current)}</div>
                <PeriodChart
                  rows={R.series[activeCmp][m] ?? []}
                  cmp={activeCmp}
                  prev={prev}
                  height={200}
                  label={`${data.metric_labels[m]}, ${data.comparison_labels[activeCmp]}, ${data.range_labels[range]}`}
                />
              </Card>
            ))}
          </div>
        </div>
      )}

      {breakdown.length > 0 && data.dimension_column && (
        <Card>
          <h3 className="mb-3 font-semibold">
            {data.metric_labels[data.breakdown_metric]} by {data.dimension_column} · {data.range_labels[range].toLowerCase()}
          </h3>
          <BarChart
            categories={breakdown.map((b) => b.category)}
            values={breakdown.map((b) => b.current)}
            extra={(i) => (breakdown[i].pct_change == null ? null : `${breakdown[i].pct_change! >= 0 ? "▲" : "▼"} ${pct(breakdown[i].pct_change)}`)}
          />
        </Card>
      )}

      {/* Time bar: floats at the bottom of the screen while the dashboard is in view. */}
      <div className="sticky bottom-4 z-20 flex justify-center">
        <div
          role="group"
          aria-label="Date range and comparison"
          className="flex w-full flex-col gap-1 rounded-3xl border border-line bg-surface/95 p-1.5 shadow-[0_10px_30px_rgb(0_0_0/0.25)] backdrop-blur sm:w-auto sm:max-w-full sm:flex-row sm:flex-wrap sm:items-center sm:justify-center sm:gap-x-2 sm:rounded-full"
        >
          <Segmented label="Date range" options={data.range_labels} value={range} onChange={setRange} />
          <span aria-hidden className="hidden h-5 w-px bg-line sm:block" />
          <Segmented label="Comparison" options={cmpOptions} value={activeCmp} onChange={setCmp} />
        </div>
      </div>
    </div>
  );
}

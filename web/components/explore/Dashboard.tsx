// The dashboard for one cleaned table, laid out like the Canva design (a newspaper's "above the fold"):
//
//   title + subtitle, and the FILTER button: a panel opens next to it with the
//            customer / place filters (hidden until you want them, so the
//            dashboard stays clean)
//   KPI ROW  1–4 headline cards, the first one the hero (gold), all in ONE style
//            (KpiCard.tsx: number, change or gauge — the skill picks it). Each
//            change is colored by the metric's direction: green good, gray about
//            the same, red bad — a cost going DOWN is green; percentages change
//            in points.
//   ROWS     containers, mixed and matched for this data (build-dashboard's
//            layout.py): trends as line charts first, categories as horizontal
//            bar charts next, then complementary visuals — any number of rows.
//   time bar the date range and comparison, floating at the bottom (TimeBar.tsx)
//
// The skill decides WHAT to show (data_skills/build-dashboard/skill.py): the
// main total, the INDUSTRY KPIs the data supports (industry_metrics.json — the
// client's industry is sent as `industry`), transactions and the average per
// transaction; with no industry match, the generic ones. Every range ×
// comparison comes back in one response, so the time bar is instant.

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { runSkill, saveText } from "@/lib/api";
import type { CleaningInput, DashboardData, DashboardVisual, PeriodRow } from "@/lib/types";
import HBarChart from "@/components/charts/HBarChart";
import LineChart, { usDate } from "@/components/charts/LineChart";
import KpiCard, { ChangePill } from "./KpiCard";
import TimeBar from "./TimeBar";
import { Button, ErrorBox } from "@/components/wizard/ui";
import Tour, { type TourStep } from "@/components/ui/Tour";

// The first-time guide for the dashboard (components/ui/Tour.tsx).
const DASHBOARD_TOUR: TourStep[] = [
  { title: "Your dashboard builds itself",
    body: "It reads the dates and numbers in your table — and your industry — and picks the KPIs and charts that fit." },
  { target: "dash-filter", title: "Filter",
    body: "Narrow everything to one customer type, store or place. The panel opens next to the button." },
  { target: "dash-kpis", title: "Key metrics",
    body: "The headline numbers, with the change from the previous period: green is good, gray about the same, red needs a look." },
  { title: "Time range, at the bottom",
    body: "The bar at the bottom of the screen picks the range (week, month, year…) and what to compare with." },
];

const PERIOD: Record<string, string> = { dod: "day", wow: "week", mom: "month" };
// Column spans of the 6-column grid (templates/layouts.json), written out so Tailwind keeps them.
const SPAN: Record<number, string> = { 2: "lg:col-span-2", 3: "lg:col-span-3", 4: "lg:col-span-4", 6: "lg:col-span-6" };
const KPI_COLS: Record<number, string> = { 1: "lg:grid-cols-1", 2: "lg:grid-cols-2", 3: "lg:grid-cols-3", 4: "lg:grid-cols-4" };

/**
 * Lay out filters in rows of at most 3, as even as possible (rows differ by at
 * most one): 4 -> 2+2, 5 -> 3+2, 6 -> 3+3, 7 -> 3+2+2, 8 -> 3+3+2, 9 -> 3+3+3.
 * The dashboard skill offers at most 9 filters (MAX_FILTERS in skill.py).
 */
export function filterRows<T>(items: T[]): T[][] {
  const rows = Math.ceil(items.length / 3);
  if (!rows) return [];
  const small = Math.floor(items.length / rows);
  const big = items.length % rows;
  const out: T[][] = [];
  let at = 0;
  for (let r = 0; r < rows; r++) {
    const size = small + (r < big ? 1 : 0);
    out.push(items.slice(at, at + size));
    at += size;
  }
  return out;
}

/** "This period" vs. "Previous period" line chart for one metric. */
function PeriodChart(props: {
  rows: PeriodRow[];
  cmp: string;
  label: string;
  prev: { start: string; end: string } | null;
  format: (v: number | null | undefined) => string;
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
        // The comparison line is the PREVIOUS RANGE lined up point by point.
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
          lines.push(`${r.pct_change >= 0 ? "▲" : "▼"} ${(Math.abs(r.pct_change) * 100).toFixed(1)}% vs. previous ${PERIOD[cmp]} (${props.format(r.previous)})`);
        return lines.length ? lines.join("\n") : null;
      }}
    />
  );
}

export default function Dashboard(props: {
  input: CleaningInput;
  groupId: number;
  /** NAICS sector from sign-up ("72"): industry KPIs when the data has them. */
  industry?: string | null;
  businessName?: string | null;
  viewerName?: string | null;
  title?: string;
}) {
  const { input, groupId, industry, businessName, viewerName } = props;
  const title = props.title ?? "Dashboard";
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [filterOpen, setFilterOpen] = useState(false);
  const [range, setRange] = useState("last_3_months");
  const [cmp, setCmp] = useState("wow");
  const [downloading, setDownloading] = useState(false);
  const loadedOnce = useRef(false);
  const filterRef = useRef<HTMLDivElement>(null);

  // Only non-empty filters are sent: {"customer_type": "Wholesale"}.
  const activeFilters = useMemo(
    () => Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
    [filters],
  );
  const skillParams = useMemo(
    () => ({ filters: activeFilters, industry: industry ?? null, business_name: businessName ?? null,
             viewer_name: viewerName ?? null, title }),
    [activeFilters, industry, businessName, viewerName, title],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    // include_html: false keeps the response small; the HTML file is built on Download.
    runSkill("build-dashboard", input, { ...skillParams, include_html: false }, groupId)
      .then((r) => {
        if (cancelled) return;
        const d = r.data as unknown as DashboardData;
        if (!loadedOnce.current) {
          // First load: start on the skill's suggested range and comparison.
          loadedOnce.current = true;
          setRange(d.default_range);
          setCmp(d.default_comparison);
        }
        setData(d);
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [input, groupId, skillParams]);

  // The filter panel closes on a click outside it, or Esc.
  useEffect(() => {
    if (!filterOpen) return;
    const onDown = (e: MouseEvent) => !filterRef.current?.contains(e.target as Node) && setFilterOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setFilterOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [filterOpen]);

  async function downloadHtml() {
    setDownloading(true);
    try {
      const r = await runSkill("build-dashboard", input, { ...skillParams, include_html: true }, groupId);
      saveText("dashboard.html", r.artifacts.html_dashboard ?? "", "text/html");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed.");
    } finally {
      setDownloading(false);
    }
  }

  if (!data) return error ? <ErrorBox message={error} /> : <p className="py-8 text-sm text-ink-muted">Building your dashboard…</p>;

  // Numbers in US formats, by the metric's format (currency, percent, days, number).
  const fmt = (m: string) => (v: number | null | undefined) => {
    if (v == null || Number.isNaN(v)) return "—";
    const f = data.metric_formats?.[m] ?? "number";
    const digits = Math.abs(v) < 100 ? 2 : 0;
    if (f === "currency") return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits });
    if (f === "percent") return `${v.toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
    if (f === "days") return `${v.toLocaleString("en-US", { maximumFractionDigits: 1 })} days`;
    return v.toLocaleString("en-US", { maximumFractionDigits: digits });
  };
  const dir = (m: string) => data.metric_directions?.[m] ?? "higher";

  // Only ranges the data supports are offered (e.g. "All time" needs > 1 year).
  const rangeOptions = Object.fromEntries(
    Object.entries(data.range_labels).filter(([k]) => data.available_ranges.includes(k)),
  ) as Record<string, string>;
  const activeRange = data.ranges[range] && (data.available_ranges.includes(range) || !data.available_ranges.length) ? range : data.default_range;
  const R = data.ranges[activeRange];
  const cmpOptions = Object.fromEntries(
    Object.entries(data.comparison_labels).filter(([k]) => R.series[k]),
  ) as Record<string, string>;
  const cmpKeys = Object.keys(cmpOptions);
  const activeCmp = R.series[cmp] ? cmp : (cmpKeys.includes("wow") ? "wow" : cmpKeys[cmpKeys.length - 1] ?? "");
  const hasCharts = cmpKeys.length > 0;   // false with only a day or so of data
  const w = R.window;
  const prev = w.prev_start ? { start: w.prev_start, end: w.prev_end! } : null;
  const L = data.layout;
  const filterCount = Object.keys(activeFilters).length;

  // One container (a plain function, not a component, so nothing is re-created on each render).
  function container(v: DashboardVisual, span: number) {
    const rows: PeriodRow[] = R.series[activeCmp]?.[v.metric] ?? [];
    return (
      <section key={`${v.kind}-${v.metric}-${v.dimension ?? ""}`} className={`col-span-full min-w-0 rounded-3xl border border-line bg-surface p-4 shadow-[var(--shadow-card)] ${SPAN[span] ?? ""}`}>
        <h3 className="mb-2 font-mono text-sm font-semibold">{v.title}</h3>
        {v.kind === "line" ? (
          hasCharts ? (
            <>
              <PeriodChart rows={rows} cmp={activeCmp} prev={prev} format={fmt(v.metric)} height={span >= 4 ? 260 : 200}
                label={`${v.title}, ${data!.comparison_labels[activeCmp]}, ${data!.range_labels[activeRange]}`} />
              {rows.length > 1 && rows[rows.length - 1].partial && (
                <p className="mt-2 text-xs text-ink-muted">The last period is still in progress, so it&apos;s drawn faded.</p>
              )}
            </>
          ) : (
            <p className="text-sm text-ink-muted">Charts over time appear once the data covers at least two days.</p>
          )
        ) : (
          <HBarChart
            label={v.title}
            format={(x) => fmt(v.metric)(x)}
            rows={(data!.breakdowns?.[v.dimension ?? ""]?.[activeRange] ?? []).map((b) => ({
              category: b.category, value: b.current, pill: <ChangePill pct={b.pct_change} direction={dir(v.metric)} />,
            }))}
          />
        )}
      </section>
    );
  }

  return (
    <div className="space-y-4">
      <Tour id="dashboard" steps={DASHBOARD_TOUR} />

      {/* Title and subtitle on the left; the Filter button on the right (its panel opens beside it). */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-mono text-2xl font-bold sm:text-3xl">{title}</h2>
          <p className="mt-1 text-sm text-ink-muted">
            {data.subtitle ?? "How your business is doing over time"} · {data.range_labels[activeRange]}: {usDate(w.start)} –{" "}
            {usDate(w.end)}
            {Object.keys(data.filters).length ? ` · ${data.rows.toLocaleString("en-US")} matching rows` : ""}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {loading && <span className="text-xs text-ink-muted">Updating…</span>}
          <Button variant="secondary" onClick={downloadHtml} disabled={downloading}>
            {downloading ? "Preparing…" : "Download (HTML)"}
          </Button>
          {data.filter_columns.length > 0 && (
            <div className="relative" ref={filterRef} data-tour="dash-filter">
              <button
                type="button"
                onClick={() => setFilterOpen((o) => !o)}
                aria-expanded={filterOpen}
                aria-controls="dash-filter-panel"
                className="inline-flex items-center gap-2 rounded-full border border-brand bg-brand-soft py-1.5 pl-1.5 pr-4 text-sm font-semibold hover:bg-brand/20"
              >
                <span aria-hidden className="grid h-6 w-6 place-items-center rounded-full bg-brand text-on-brand">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M3 4h18l-7 9v6l-4 2v-8z" /></svg>
                </span>
                Filter{filterCount ? ` (${filterCount})` : ""}
              </button>
              {filterOpen && (
                <div id="dash-filter-panel" role="dialog" aria-label="Filters"
                  className="absolute right-0 top-[calc(100%+8px)] z-30 w-[min(36rem,calc(100vw-2rem))] space-y-3 rounded-2xl border border-line bg-surface p-4 shadow-[var(--shadow-card)]">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold">Filter the dashboard</h3>
                    {filterCount > 0 && (
                      <button type="button" className="text-xs text-brand hover:underline" onClick={() => setFilters({})}>
                        Clear filters
                      </button>
                    )}
                  </div>
                  {filterRows(data.filter_columns).map((row, i) => (
                    <div key={i} className={`grid gap-3 ${row.length > 1 ? "sm:grid-cols-2" : ""} ${row.length > 2 ? "lg:grid-cols-3" : ""}`}>
                      {row.map((c) => (
                        <label key={c} className="min-w-0 space-y-1 text-sm">
                          <span className="block text-xs text-ink-muted">{data.filter_labels[c]}</span>
                          <select
                            value={filters[c] ?? ""}
                            onChange={(e) => setFilters((f) => ({ ...f, [c]: e.target.value }))}
                            className="w-full rounded-xl border border-line bg-surface px-3 py-1.5 text-sm"
                          >
                            <option value="">All</option>
                            {data.filter_options[c].map((v) => (
                              <option key={v} value={v}>{v}</option>
                            ))}
                          </select>
                        </label>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      <ErrorBox message={error} />

      {/* ABOVE THE FOLD: the KPI row (1–4 cards; one card shares its row with a trend). */}
      <div data-tour="dash-kpis" className={`grid grid-cols-1 gap-3 ${L.kpi_side ? "lg:grid-cols-6" : `sm:grid-cols-2 ${KPI_COLS[L.kpi_row.length] ?? ""}`}`}>
        {L.kpi_row.map((m, i) => {
          const k = R.kpis[m];
          return (
            <KpiCard key={m} style={data.kpi_style ?? "change"} hero={i === 0} label={data.metric_labels[m]}
              value={fmt(m)(k?.current)} current={k?.current} pct={k?.pct_change} change={k?.change}
              format={data.metric_formats?.[m] ?? "number"} direction={dir(m)} benchmark={data.metric_benchmarks?.[m]}
              target={data.metric_targets?.[m] ?? null} className={L.kpi_side ? "lg:col-span-2" : ""}
              versus={`vs. previous ${data.range_labels[activeRange].toLowerCase()}`} />
          );
        })}
        {L.kpi_side && container(L.kpi_side, 4)}
      </div>

      {/* Container rows, mixed and matched for this data (layout.py). */}
      {L.rows.map((row, i) => (
        <div key={i} className="grid grid-cols-1 gap-3 lg:grid-cols-6">
          {row.items.map((v, j) => container(v, row.spans[j]))}
        </div>
      ))}

      {data.industry?.name && (
        <p className="text-xs text-ink-muted">
          {data.industry.generic
            ? `No ${data.industry.name} KPIs could be computed from this table's columns, so the dashboard shows the essentials.`
            : `Industry KPIs for ${data.industry.name}.`}
        </p>
      )}

      {/* Time bar: two floating panels fixed at the bottom of the screen while the dashboard is in view. */}
      <TimeBar
        ranges={rangeOptions}
        rangeShort={data.range_short_labels}
        range={activeRange}
        onRange={setRange}
        comparisons={cmpOptions}
        comparisonShort={data.comparison_short_labels}
        comparison={activeCmp}
        onComparison={setCmp}
      />
    </div>
  );
}

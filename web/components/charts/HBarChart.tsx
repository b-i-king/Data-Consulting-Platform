// Horizontal bar chart for dashboard containers (categories).
//
// Easy to read left to right: the NAME, then the bar, with the VALUE right at
// the end of the bar (and its change as a colored pill: green good, gray about
// the same, red bad — the caller's `pill`). Categories are text, never HTML.
//
// It sizes itself by its CONTAINER (CSS container queries), not the screen:
//   wide container (a ½, ⅔ or full row)  name on the left | bar · value · pill
//   narrow container (a ⅓ slot, a phone) name and pill on one line, the bar
//                                        and value below at full width
// The bar's length leaves room for the value (5rem), so the value always sits
// at the end of the bar and never spills out of the box.

"use client";

import type { ReactNode } from "react";

export default function HBarChart(props: {
  rows: { category: string; value: number; pill?: ReactNode }[];
  format: (v: number) => string;
  label: string;
}) {
  const max = Math.max(...props.rows.map((r) => Math.abs(r.value)), 1);
  if (!props.rows.length) return <p className="text-sm text-ink-muted">No categories in this range.</p>;
  return (
    <div className="@container">
      <ul className="space-y-2" aria-label={props.label}>
        {props.rows.map((r, i) => (
          <li key={`${r.category}-${i}`}
            className="grid grid-cols-1 items-center gap-x-3 gap-y-0.5 rounded-lg px-1 py-0.5 text-sm hover:bg-surface-2 @sm:grid-cols-[minmax(5rem,32%)_1fr]">
            <span className="flex min-w-0 items-center justify-between gap-2">
              <span className="truncate" title={r.category}>{r.category}</span>
              {/* narrow: the pill sits by the name, so the bar below gets the whole width */}
              <span className="shrink-0 @sm:hidden">{r.pill}</span>
            </span>
            <span className="flex min-w-0 items-center gap-2">
              <span className="flex min-w-0 flex-1 items-center gap-2">
                <span className="block h-3.5 shrink-0 rounded-r-md"
                  style={{ width: `max(2px, calc((100% - 5rem) * ${Math.abs(r.value) / max}))`, background: "var(--color-chart-current)" }} />
                <span className="whitespace-nowrap font-mono text-xs font-semibold tabular-nums">{props.format(r.value)}</span>
              </span>
              <span className="hidden shrink-0 @sm:inline">{r.pill}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Horizontal bar chart (HTML, not SVG): one row per category, sorted as given.
// Thin bars with a rounded data end, values in ink (not the bar color), and a
// hover/focus highlight per row. Category names are rendered as text, never HTML.

"use client";

export default function BarChart(props: {
  categories: string[];
  values: number[];
  color?: string;
  valueLabel?: string;
  extra?: (index: number) => string | null;
}) {
  const color = props.color ?? "var(--color-chart-current)";
  const max = Math.max(...props.values.map((v) => Math.abs(v)), 1);
  const fmt = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 });

  return (
    <ul className="space-y-1.5" aria-label={props.valueLabel ? `Values (${props.valueLabel})` : "Values"}>
      {props.categories.map((c, i) => (
        <li
          key={`${c}-${i}`}
         
          // Phones: name + value on one line, full-width bar underneath.
          // Wider screens: name | bar | value in one row.
          className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 rounded-lg px-1 py-0.5 text-sm hover:bg-surface-2 sm:grid-cols-[minmax(6rem,12rem)_1fr_auto]"
        >
          <span className="truncate" title={c}>
            {c}
          </span>
          <span className="order-last col-span-2 h-2.5 rounded-r bg-line/50 sm:order-none sm:col-span-1">
            <span
              className="block h-2.5 rounded-r"
              style={{ width: `${(Math.abs(props.values[i]) / max) * 100}%`, background: color }}
            />
          </span>
          <span className="text-right font-mono tabular-nums">
            {fmt(props.values[i])}
            {props.extra?.(i) && <span className="ml-2 text-xs text-ink-muted">{props.extra(i)}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

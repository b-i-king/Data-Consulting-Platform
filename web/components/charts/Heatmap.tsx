// Heatmap drawn with plain SVG: a grid of values in [-1, 1] (a correlation
// matrix), each cell colored on a DIVERGING scale.
//
// Colors: negative values blend --color-div-neg (cool) into the card color,
// positive values blend --color-div-pos (warm), using CSS color-mix. So 0 is
// the card color itself and only strong relationships stand out, in both the
// light and dark theme. Built from a HeatmapChartSpec (data_skills/_core.py heatmap_chart).

"use client";

import type { HeatmapChartSpec } from "@/lib/types";
import { ChartFrame, NoData, Tooltip, hoverProps, useTip } from "./chartKit";

/** CSS fill for one cell: a share of the cool or warm color mixed into the surface color. */
export function heatColor(v: number | null): string {
  if (v == null) return "var(--color-surface-2)";
  const pct = Math.round(Math.min(Math.abs(v), 1) * 100);
  return `color-mix(in srgb, var(${v >= 0 ? "--color-div-pos" : "--color-div-neg"}) ${pct}%, var(--color-surface))`;
}

const LEFT = 110; // room for row labels
const TOP = 70; // room for slanted column labels

export default function Heatmap({ spec }: { spec: HeatmapChartSpec }) {
  const t = useTip();
  const { rows, columns, values } = spec;
  if (!rows.length || !columns.length) return <NoData />;
  const cell = Math.min((480 - LEFT - 8) / columns.length, 44);
  const w = LEFT + cell * columns.length + 8;
  const h = TOP + cell * rows.length + 28;
  const cut = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

  return (
    <ChartFrame>
      <svg viewBox={`0 0 ${w} ${h}`} className="h-auto w-full max-w-xl" role="img" aria-label={spec.title}>
        {columns.map((c, j) => (
          <text
            key={c}
            transform={`translate(${LEFT + cell * (j + 0.5)} ${TOP - 6}) rotate(-45)`}
            fontSize={11}
            fill="var(--color-ink-muted)"
          >
            {cut(c, 14)}
          </text>
        ))}
        {rows.map((r, i) => (
          <g key={r}>
            <text x={LEFT - 6} y={TOP + cell * i + cell / 2 + 4} textAnchor="end" fontSize={11} fill="var(--color-ink-muted)">
              {cut(r, 16)}
            </text>
            {columns.map((c, j) => {
              const v = values[i]?.[j] ?? null;
              const x = LEFT + cell * j;
              const y = TOP + cell * i;
              const text = `${r} × ${c}\n${spec.value_label} = ${v == null ? "n/a" : (v >= 0 ? "+" : "") + v.toFixed(2)}`;
              return (
                <g key={c} {...hoverProps(t, x + cell / 2, y, text)}>
                  <rect x={x} y={y} width={cell - 2} height={cell - 2} rx={3} style={{ fill: heatColor(v) }} />
                  {v != null && cell >= 28 && (
                    <text x={x + cell / 2 - 1} y={y + cell / 2 + 3} textAnchor="middle" fontSize={10} fill="var(--color-ink)">
                      {v.toFixed(2)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        ))}
        {/* Legend: -1 (cool) through 0 (card color) to +1 (warm). */}
        {[-1, -0.5, 0, 0.5, 1].map((v, k) => (
          <g key={v}>
            <rect x={LEFT + k * 28} y={h - 18} width={26} height={10} rx={2} style={{ fill: heatColor(v) }} stroke="var(--color-line)" strokeWidth={0.5} />
            {(k === 0 || k === 2 || k === 4) && (
              <text x={LEFT + k * 28 + 13} y={h - 22} textAnchor="middle" fontSize={9} fill="var(--color-ink-muted)">
                {v > 0 ? `+${v}` : v}
              </text>
            )}
          </g>
        ))}
      </svg>
      <Tooltip tip={t.tip} width={w} height={h} />
    </ChartFrame>
  );
}

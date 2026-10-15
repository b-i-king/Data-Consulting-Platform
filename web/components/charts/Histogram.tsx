// Histogram drawn with plain SVG: one bar per value range (bin), touching,
// height = number of rows in that range. Built from a HistogramChartSpec
// (data_skills/_core.py histogram_chart). Hover or focus a bar to see its
// exact range and row count.

"use client";

import type { HistogramChartSpec } from "@/lib/types";
import { AxisLabels, ChartFrame, H, NoData, PAD, Tooltip, W, YAxis, fmtNum, hoverProps, scale, shortNum, useTip } from "./chartKit";

export default function Histogram({ spec }: { spec: HistogramChartSpec }) {
  const t = useTip();
  const { bin_edges: edges, counts } = spec;
  if (!counts.length) return <NoData />;
  const cmax = Math.max(...counts, 1);
  const xs = scale(edges[0], edges[edges.length - 1], PAD.l, W - PAD.r);
  const ys = scale(0, cmax, H - PAD.b, PAD.t);
  const mid = (edges[0] + edges[edges.length - 1]) / 2;

  return (
    <ChartFrame>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={spec.title}>
        <YAxis lo={0} hi={cmax} ys={ys} />
        {counts.map((c, i) => {
          const x0 = xs(edges[i]);
          const x1 = xs(edges[i + 1]);
          const text = `${fmtNum(edges[i])} to ${fmtNum(edges[i + 1])}\n${c.toLocaleString("en-US")} rows`;
          const active = t.tip?.text === text;
          return (
            <rect
              key={i}
              x={x0 + 0.5}
              y={ys(c)}
              width={Math.max(x1 - x0 - 1, 1)}
              height={ys(0) - ys(c)}
              fill="var(--color-chart-current)"
              opacity={t.tip && !active ? 0.55 : 1}
              {...hoverProps(t, (x0 + x1) / 2, ys(c), text)}
            />
          );
        })}
        {[edges[0], mid, edges[edges.length - 1]].map((v, i) => (
          <text key={i} x={xs(v)} y={H - PAD.b + 16} textAnchor="middle" fontSize={11} fill="var(--color-ink-muted)">
            {shortNum(v)}
          </text>
        ))}
        <AxisLabels x={spec.x_label} y={spec.y_label} />
      </svg>
      <Tooltip tip={t.tip} />
    </ChartFrame>
  );
}

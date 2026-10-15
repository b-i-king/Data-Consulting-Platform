// Scatter plot drawn with plain SVG: one small see-through dot per row, so
// dense areas look darker. Built from a ScatterChartSpec (data_skills/_core.py
// scatter_chart; the EDA report sends at most 500 sampled points). Hover or
// focus a dot to see its two values.

"use client";

import type { ScatterChartSpec } from "@/lib/types";
import { AxisLabels, ChartFrame, H, NoData, PAD, Tooltip, W, YAxis, fmtNum, hoverProps, scale, shortNum, useTip } from "./chartKit";

export default function ScatterPlot({ spec }: { spec: ScatterChartSpec }) {
  const t = useTip();
  const pts = spec.points;
  if (!pts.length) return <NoData />;
  const xlo = Math.min(...pts.map((p) => p.x));
  const xhi = Math.max(...pts.map((p) => p.x));
  const ylo = Math.min(...pts.map((p) => p.y));
  const yhi = Math.max(...pts.map((p) => p.y));
  // Inset by 4px so dots on the edge are not cut off.
  const xs = scale(xlo, xhi, PAD.l + 4, W - PAD.r - 4);
  const ys = scale(ylo, yhi, H - PAD.b - 4, PAD.t + 4);

  return (
    <ChartFrame>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={spec.title}>
        <YAxis lo={ylo} hi={yhi} ys={ys} />
        {pts.map((p, i) => (
          <circle
            key={i}
            cx={xs(p.x)}
            cy={ys(p.y)}
            r={3}
            fill="var(--color-chart-current)"
            fillOpacity={0.5}
            {...hoverProps(t, xs(p.x), ys(p.y), `${spec.x_label || "x"}: ${fmtNum(p.x)}\n${spec.y_label || "y"}: ${fmtNum(p.y)}`)}
          />
        ))}
        {[xlo, (xlo + xhi) / 2, xhi].map((v, i) => (
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

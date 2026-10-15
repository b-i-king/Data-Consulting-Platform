// Box plot drawn with plain SVG, one vertical box per group (or per column).
//
// How to read it: the box spans the middle half of the values (25th to 75th
// percentile), the thick line is the median, the whiskers reach the last
// values inside the 1.5 × IQR fences, and hollow dots are outliers beyond
// them. Built from a BoxChartSpec (data_skills/_core.py box_stats/box_chart).

"use client";

import type { BoxChartSpec } from "@/lib/types";
import { AxisLabels, ChartFrame, H, NoData, PAD, Tooltip, W, YAxis, fmtNum, hoverProps, scale, useTip } from "./chartKit";

export default function BoxPlot({ spec }: { spec: BoxChartSpec }) {
  const t = useTip();
  const boxes = spec.boxes;
  if (!boxes.length) return <NoData />;
  const vals = boxes.flatMap((b) => [b.whisker_low, b.whisker_high, ...b.outliers]);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const ys = scale(lo, hi, H - PAD.b, PAD.t);
  const slot = (W - PAD.l - PAD.r) / boxes.length;
  const bw = Math.min(slot * 0.5, 48);

  return (
    <ChartFrame>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={spec.title}>
        <YAxis lo={lo} hi={hi} ys={ys} />
        {boxes.map((b, i) => {
          const cx = PAD.l + slot * (i + 0.5);
          const text =
            `${b.name} (${b.n.toLocaleString("en-US")} values)\nmedian ${fmtNum(b.median)}\n` +
            `middle half ${fmtNum(b.q1)} to ${fmtNum(b.q3)}\n${b.outlier_count.toLocaleString("en-US")} outliers`;
          const label = b.name.length > 12 ? `${b.name.slice(0, 11)}…` : b.name;
          return (
            <g key={`${b.name}-${i}`}>
              <g {...hoverProps(t, cx, ys(b.q3), text)}>
                {/* Whisker line, then the box, then the median on top. */}
                <line x1={cx} x2={cx} y1={ys(b.whisker_low)} y2={ys(b.whisker_high)} stroke="var(--color-ink-muted)" strokeWidth={1} />
                <line x1={cx - bw / 4} x2={cx + bw / 4} y1={ys(b.whisker_low)} y2={ys(b.whisker_low)} stroke="var(--color-ink-muted)" strokeWidth={1} />
                <line x1={cx - bw / 4} x2={cx + bw / 4} y1={ys(b.whisker_high)} y2={ys(b.whisker_high)} stroke="var(--color-ink-muted)" strokeWidth={1} />
                <rect
                  x={cx - bw / 2}
                  y={ys(b.q3)}
                  width={bw}
                  height={Math.max(ys(b.q1) - ys(b.q3), 1)}
                  rx={3}
                  style={{ fill: "color-mix(in srgb, var(--color-chart-current) 28%, var(--color-surface))" }}
                  stroke="var(--color-chart-current)"
                  strokeWidth={1.5}
                />
                <line x1={cx - bw / 2} x2={cx + bw / 2} y1={ys(b.median)} y2={ys(b.median)} stroke="var(--color-ink)" strokeWidth={2} />
              </g>
              {b.outliers.map((v, j) => (
                <circle
                  key={j}
                  cx={cx}
                  cy={ys(v)}
                  r={2.5}
                  fill="none"
                  stroke="var(--color-chart-previous)"
                  strokeWidth={1}
                  {...hoverProps(t, cx, ys(v), `${b.name}: outlier ${fmtNum(v)}`)}
                />
              ))}
              <text x={cx} y={H - PAD.b + 16} textAnchor="middle" fontSize={11} fill="var(--color-ink-muted)">
                {label}
              </text>
            </g>
          );
        })}
        <AxisLabels x={spec.group_label} y={spec.value_label} />
      </svg>
      <Tooltip tip={t.tip} />
    </ChartFrame>
  );
}

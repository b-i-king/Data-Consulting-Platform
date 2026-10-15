// Shared pieces for the small SVG charts (Histogram, BoxPlot, ScatterPlot, Heatmap).
//
// Each chart is drawn on a fixed 480 × 260 "drawing board" (the viewBox) and
// scaled to the card width with CSS (width: 100%), so it is responsive without
// measuring the screen. This file holds what they all need: the drawing size,
// a linear scale (data value -> pixel), short number labels, the value-axis
// grid, axis titles, and one hover tooltip. All colors are CSS variables from
// app/globals.css, so the charts follow the light/dark theme automatically.

"use client";

import { useState, type ReactNode } from "react";

/** Drawing size and margins (room for axis labels on the left and bottom). */
export const W = 480;
export const H = 260;
export const PAD = { l: 56, r: 14, t: 12, b: 40 };

/**
 * A linear scale: maps data values in [lo, hi] onto pixels in [a, b].
 * A zero-width range (a constant column) maps everything to the middle.
 * Example: scale(0, 10, 0, 100)(5) === 50
 */
export function scale(lo: number, hi: number, a: number, b: number) {
  if (hi === lo) return () => (a + b) / 2;
  return (v: number) => a + ((v - lo) / (hi - lo)) * (b - a);
}

/** Full number in US format: 1234.5 -> "1,234.5". */
export const fmtNum = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 });

/** Compact axis label: 1234 -> "1.2K", 2500000 -> "2.5M". */
export const shortNum = (v: number) =>
  v.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: Math.abs(v) < 10 ? 2 : 1 });

/** Five horizontal hairlines with their values on the left (the value axis). */
export function YAxis({ lo, hi, ys }: { lo: number; hi: number; ys: (v: number) => number }) {
  return (
    <g>
      {[0, 1, 2, 3, 4].map((g) => {
        const v = lo + ((hi - lo) * g) / 4;
        return (
          <g key={g}>
            <line x1={PAD.l} x2={W - PAD.r} y1={ys(v)} y2={ys(v)} stroke="var(--color-line)" strokeWidth={1} />
            <text x={PAD.l - 6} y={ys(v) + 4} textAnchor="end" fontSize={11} fill="var(--color-ink-muted)">
              {shortNum(v)}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/** Axis titles: x under the chart, y rotated along the left edge. */
export function AxisLabels({ x, y }: { x?: string; y?: string }) {
  return (
    <g fontSize={11} fill="var(--color-ink-muted)">
      {x && (
        <text x={(PAD.l + W - PAD.r) / 2} y={H - 4} textAnchor="middle">
          {x}
        </text>
      )}
      {y && (
        <text transform={`translate(12 ${(PAD.t + H - PAD.b) / 2}) rotate(-90)`} textAnchor="middle">
          {y}
        </text>
      )}
    </g>
  );
}

/** Where the tooltip sits (in drawing units) and what it says. */
export interface Tip {
  x: number;
  y: number;
  text: string;
}

/**
 * Hover tooltip state for one chart. `show(x, y, text)` on pointer enter / focus,
 * `hide()` on leave / blur; render `<Tooltip tip={tip} />` next to the SVG.
 */
export function useTip() {
  const [tip, setTip] = useState<Tip | null>(null);
  return {
    tip,
    show: (x: number, y: number, text: string) => setTip({ x, y, text }),
    hide: () => setTip(null),
  };
}

/** Props that make one mark (bar, dot, cell) show the tooltip on hover and keyboard focus. */
export function hoverProps(t: ReturnType<typeof useTip>, x: number, y: number, text: string) {
  return {
    tabIndex: 0,
    "aria-label": text,
    onPointerEnter: () => t.show(x, y, text),
    onPointerLeave: t.hide,
    onFocus: () => t.show(x, y, text),
    onBlur: t.hide,
    style: { outline: "none" },
  };
}

/** The tooltip box, positioned over the chart as a percentage of the drawing size. */
export function Tooltip({ tip, width = W, height = H }: { tip: Tip | null; width?: number; height?: number }) {
  if (!tip) return null;
  return (
    <div
      role="status"
      className="pointer-events-none absolute z-10 max-w-56 -translate-x-1/2 -translate-y-full whitespace-pre-line rounded-xl border border-line bg-surface px-2.5 py-1.5 text-xs shadow-[var(--shadow-card)]"
      style={{
        left: `clamp(5rem, ${(tip.x / width) * 100}%, calc(100% - 5rem))`,
        top: `calc(${(tip.y / height) * 100}% - 6px)`,
      }}
    >
      {tip.text}
    </div>
  );
}

/** Wrapper: a relatively positioned box holding the SVG and its tooltip. */
export function ChartFrame({ children }: { children: ReactNode }) {
  return <div className="relative">{children}</div>;
}

/** Shown instead of a chart when there is nothing to draw. */
export function NoData() {
  return <p className="py-8 text-center text-sm text-ink-muted">No data to chart.</p>;
}

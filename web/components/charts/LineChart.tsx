// Line chart drawn with plain SVG (no chart library), in the site's colors.
//
// Built to the dataviz rules: 2px lines, recessive solid hairline grid, a
// vertical crosshair that snaps to the nearest point, ONE tooltip listing
// every series at that point, a legend whenever there are 2+ series, and
// keyboard focus support. If `partialLast` is true, the final segment is
// faded with a hollow end point — the period is unfinished, not a real drop.

"use client";

import { useEffect, useId, useRef, useState } from "react";

export interface Series {
  name: string;
  values: (number | null)[];
  color: string; // a CSS color or var(--color-…)
}

// The chart is drawn at its real on-screen width (measured below), so text
// stays readable on phones instead of shrinking with a fixed-size drawing.
const PAD = { l: 56, r: 12, t: 12, b: 28 };

const fmt = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 });

/** "2026-03-01" -> "03/01/2026" (US format for display). */
export const usDate = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return d ? `${m}/${d}/${y}` : iso;
};

export default function LineChart(props: {
  x: string[];
  series: Series[];
  partialLast?: boolean;
  formatX?: (x: string) => string;
  extraTooltip?: (index: number) => string | null;
  ariaLabel: string;
}) {
  const { x, series, partialLast = false } = props;
  const formatX = props.formatX ?? usDate;
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [W, setW] = useState(900);
  const H = W < 520 ? 220 : 300;

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setW(Math.max(280, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!x.length) {
    return <p className="py-10 text-center text-sm text-ink-muted">No data in this range.</p>;
  }

  const all = series.flatMap((s) => s.values).filter((v): v is number => v != null);
  const max = Math.max(...all, 0);
  const min = Math.min(...all, 0);
  const xs = (i: number) =>
    PAD.l + (x.length === 1 ? (W - PAD.l - PAD.r) / 2 : (i / (x.length - 1)) * (W - PAD.l - PAD.r));
  const ys = (v: number) => PAD.t + (1 - (v - min) / (max - min || 1)) * (H - PAD.t - PAD.b);
  const last = x.length - 1;

  const points = (vals: (number | null)[], upTo: number) =>
    vals
      .slice(0, upTo + 1)
      .map((v, i) => (v == null ? null : `${xs(i)},${ys(v)}`))
      .filter(Boolean)
      .join(" ");

  function onMove(e: React.PointerEvent) {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return;
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * last);
    setHover(Math.max(0, Math.min(last, i)));
  }

  const ticks = [0, 1, 2, 3, 4].map((g) => min + ((max - min) * g) / 4);
  const xLabels = Array.from(new Set(W < 520 ? [0, last] : [0, Math.floor(last / 2), last]));

  return (
    <div className="relative" ref={boxRef}>
      {series.length > 1 && (
        <div className="mb-2 flex flex-wrap gap-4 text-xs text-ink-muted">
          {series.map((s) => (
            <span key={s.name} className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-3.5 rounded" style={{ background: s.color }} />
              {s.name}
            </span>
          ))}
        </div>
      )}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full overflow-visible"
        role="img"
        aria-labelledby={titleId}
      >
        <title id={titleId}>{props.ariaLabel}</title>
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={ys(v)} y2={ys(v)} stroke="var(--color-line)" strokeWidth={1} />
            <text x={PAD.l - 8} y={ys(v) + 4} textAnchor="end" fontSize={11} fill="var(--color-ink-muted)">
              {fmt(v)}
            </text>
          </g>
        ))}
        {xLabels.map((i) => (
          <text
            key={i}
            x={xs(i)}
            y={H - 8}
            fontSize={11}
            fill="var(--color-ink-muted)"
            textAnchor={i === 0 ? "start" : i === last ? "end" : "middle"}
          >
            {formatX(x[i])}
          </text>
        ))}

        {/* Draw the main (first) series last so it sits on top. */}
        {[...series].reverse().map((s, idx) => {
          const isMain = idx === series.length - 1;
          const fade = isMain && partialLast && x.length > 1;
          return (
            <g key={s.name}>
              <polyline
                points={points(s.values, fade ? last - 1 : last)}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinejoin="round"
              />
              {fade && s.values[last] != null && s.values[last - 1] != null && (
                <>
                  <line
                    x1={xs(last - 1)}
                    y1={ys(s.values[last - 1] as number)}
                    x2={xs(last)}
                    y2={ys(s.values[last] as number)}
                    stroke={s.color}
                    strokeWidth={2}
                    opacity={0.4}
                  />
                  <circle cx={xs(last)} cy={ys(s.values[last] as number)} r={4} fill="var(--color-surface)" stroke={s.color} strokeWidth={2} />
                </>
              )}
            </g>
          );
        })}

        {hover != null && (
          <g pointerEvents="none">
            <line x1={xs(hover)} x2={xs(hover)} y1={PAD.t} y2={H - PAD.b} stroke="var(--color-ink-muted)" strokeWidth={1} />
            {series.map((s) =>
              s.values[hover] == null ? null : (
                <circle key={s.name} cx={xs(hover)} cy={ys(s.values[hover] as number)} r={4} fill={s.color} stroke="var(--color-surface)" strokeWidth={2} />
              ),
            )}
          </g>
        )}
        <rect
          x={PAD.l}
          y={PAD.t}
          width={W - PAD.l - PAD.r}
          height={H - PAD.t - PAD.b}
          fill="transparent"
          tabIndex={0}
          aria-label="Chart values: hover or use left and right arrow keys"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
          onFocus={() => setHover(last)}
          onBlur={() => setHover(null)}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? last) - 1));
            if (e.key === "ArrowRight") setHover((h) => Math.min(last, (h ?? 0) + 1));
          }}
        />
      </svg>

      {hover != null && (
        <div
          className="pointer-events-none absolute top-8 rounded-xl border border-line bg-surface px-3 py-2 text-xs shadow-[var(--shadow-card)]"
          style={{ left: `min(calc(${(xs(hover) / W) * 100}% + 12px), calc(100% - 190px))` }}
          role="status"
        >
          <div className="mb-1 text-ink-muted">
            {formatX(x[hover])}
            {partialLast && hover === last ? " (in progress)" : ""}
          </div>
          {series.map((s) => (
            <div key={s.name} className="flex items-center gap-2">
              <span className="inline-block h-0.5 w-3 rounded" style={{ background: s.color }} />
              <span className="font-mono font-semibold">{fmt(s.values[hover])}</span>
              <span className="text-ink-muted">{s.name}</span>
            </div>
          ))}
          {props.extraTooltip?.(hover) && (
            <div className="mt-1 whitespace-pre-line text-ink-muted">{props.extraTooltip(hover)}</div>
          )}
        </div>
      )}
    </div>
  );
}

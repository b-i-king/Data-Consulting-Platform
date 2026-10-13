// The dashboard's time controls, fixed at the bottom of the screen
// (Copilot Money style), as TWO separate floating panels, one above the other:
//
//   [ Last 4 weeks | Last 3 months | Last year | Month to date | Year to date | All time ]
//                [ Day over day | Week over week | Month over month ]
//
// The panels are slightly see-through (frosted), so the chart behind them stays
// visible. Only the ranges and comparisons the data supports are passed in; a
// panel with nothing to offer is left out, and with neither the bar is hidden.
//
// When the full labels don't fit the width available — a phone, a narrow
// window, a zoomed-in browser — both panels switch to short labels:
//
//   [ 4W | 3M | 1Y | MTD | YTD | All ]
//        [ DoD | WoW | MoM ]
//
// "Fits" is measured, not guessed from a screen-size breakpoint: an invisible
// copy of the full-label panel is compared with the space available every
// time the space changes (ResizeObserver). Screen readers always hear the full
// label (aria-label), and hovering shows it too (title).

"use client";

import { useEffect, useRef, useState } from "react";

function Panel(props: {
  label: string;
  options: Record<string, string>;
  short: Record<string, string>;
  compact: boolean;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={props.label}
      className="flex max-w-full gap-1 rounded-full border border-line/70 bg-surface/60 p-1 shadow-[0_10px_30px_rgb(0_0_0/0.18)] backdrop-blur-md"
    >
      {Object.keys(props.options).map((k) => (
        <button
          key={k}
          type="button"
          role="radio"
          aria-checked={props.value === k}
          aria-label={props.options[k]}
          title={props.compact ? props.options[k] : undefined}
          onClick={() => props.onChange(k)}
          className={`whitespace-nowrap rounded-full py-1 text-sm transition-colors ${props.compact ? "px-2.5" : "px-3"} ${
            props.value === k ? "bg-brand font-semibold text-on-brand" : "text-ink-muted hover:text-ink"
          }`}
        >
          {props.compact ? (props.short[k] ?? props.options[k]) : props.options[k]}
        </button>
      ))}
    </div>
  );
}

export default function TimeBar(props: {
  ranges: Record<string, string>;
  rangeShort: Record<string, string>;
  range: string;
  onRange: (v: string) => void;
  comparisons: Record<string, string>;
  comparisonShort: Record<string, string>;
  comparison: string;
  onComparison: (v: string) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const measure = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false);

  useEffect(() => {
    const w = wrap.current;
    const m = measure.current;
    if (!w || !m) return;
    // Compact when the widest full-label panel is wider than the space we have.
    const check = () => setCompact(m.scrollWidth > w.clientWidth);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(w);
    return () => ro.disconnect();
  }, [props.ranges, props.comparisons]);

  const hasRanges = Object.keys(props.ranges).length > 0;
  const hasComparisons = Object.keys(props.comparisons).length > 0;
  if (!hasRanges && !hasComparisons) return null;   // not enough data for any time view

  return (
    <div ref={wrap} className="pointer-events-none sticky bottom-4 z-20 flex flex-col items-center gap-2">
      {/* Invisible full-label copy, only used to measure the width needed. It sits
          in a zero-size box so it can never widen the page; scrollWidth still
          reports the width its content needs. */}
      <div ref={measure} aria-hidden className="invisible absolute left-0 top-0 h-0 w-0 overflow-hidden">
        <div className="flex w-max gap-1 whitespace-nowrap border p-1 pr-3">
          {Object.values(props.ranges).map((l) => (
            <span key={l} className="rounded-full px-3 py-1 text-sm font-semibold">
              {l}
            </span>
          ))}
        </div>
      </div>
      {hasRanges && (
      <div className="pointer-events-auto max-w-full">
        <Panel label="Date range" options={props.ranges} short={props.rangeShort} compact={compact} value={props.range} onChange={props.onRange} />
      </div>
      )}
      {hasComparisons && (
      <div className="pointer-events-auto max-w-full">
        <Panel
          label="Comparison"
          options={props.comparisons}
          short={props.comparisonShort}
          compact={compact}
          value={props.comparison}
          onChange={props.onComparison}
        />
      </div>
      )}
    </div>
  );
}

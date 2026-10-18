// One KPI card, in the style the whole KPI row uses (build-dashboard picks ONE
// style per dashboard so the row looks consistent — layout.py kpi_style):
//
//   number  the title and the big number (no earlier period to compare with)
//   change  the title, the number, and the change vs. the previous range as a
//           pill: green good, gray about the same, red bad (the metric's
//           direction decides: a cost going down is green)
//   gauge   a half-circle dial from 0 to 100% with the healthy target band
//           marked (industry_metrics.json "target"), the value in the middle,
//           "On target / Above / Below" and the change — only when every KPI
//           on the row is a percentage
//
// Percentages show their change in percentage POINTS: 60% -> 62% is "▲ 2.0 pts",
// not "▲ 3.3%". The first card is the hero (gold).

"use client";

export type KpiStyle = "number" | "change" | "gauge";
export type Tone = "good" | "neutral" | "bad";

const NEUTRAL_BAND = 0.005;    // a change under 0.5% reads as "about the same"
const NEUTRAL_POINTS = 0.5;    // ...and for percentages, under half a point

export const TONE_CLS: Record<Tone, string> = {
  good: "bg-good/15 text-good",
  neutral: "bg-ink-muted/15 text-ink-muted",
  bad: "bg-bad/15 text-bad",
};

/** good / neutral / bad for a relative change (0.05 = 5%), by the metric's direction. */
export function tone(pct: number | null | undefined, direction: "higher" | "lower" = "higher"): Tone {
  if (pct == null || Math.abs(pct) < NEUTRAL_BAND) return "neutral";
  return pct > 0 === (direction === "higher") ? "good" : "bad";
}

/** The same for a change in percentage points (2.0 = two points). */
export function tonePoints(points: number | null | undefined, direction: "higher" | "lower" = "higher"): Tone {
  if (points == null || Math.abs(points) < NEUTRAL_POINTS) return "neutral";
  return points > 0 === (direction === "higher") ? "good" : "bad";
}

/** The colored change pill: "▲ 3.3%" — or "▲ 2.0 pts" for a percentage metric. */
export function ChangePill(props: { pct?: number | null; points?: number | null; direction?: "higher" | "lower" }) {
  const isPoints = props.points !== undefined;
  const v = isPoints ? props.points : props.pct;
  if (v == null) return <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${TONE_CLS.neutral}`}>—</span>;
  const t = isPoints ? tonePoints(v, props.direction) : tone(v, props.direction);
  const text = isPoints ? `${Math.abs(v).toFixed(1)} pts` : `${Math.abs(v * 100).toFixed(1)}%`;
  return (
    <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${TONE_CLS[t]}`}>
      {v >= 0 ? "▲" : "▼"} {text}
    </span>
  );
}

/** A point on the dial: 0% at the left, 100% at the right, along a half circle. */
function dial(v: number, r = 48) {
  const a = Math.PI * (1 - Math.min(100, Math.max(0, v)) / 100);
  return { x: 60 + r * Math.cos(a), y: 60 - r * Math.sin(a) };
}
const arc = (from: number, to: number) => {
  const a = dial(from), b = dial(to);
  return `M ${a.x.toFixed(2)} ${a.y.toFixed(2)} A 48 48 0 0 1 ${b.x.toFixed(2)} ${b.y.toFixed(2)}`;
};

/** A half-circle gauge, 0–100%, with the target band and the value. Colors are CSS variables. */
export function Gauge(props: { value: number | null | undefined; target?: [number, number] | null; label: string; hero?: boolean }) {
  const v = props.value ?? 0;
  const stroke = props.hero ? "var(--color-on-brand)" : "var(--color-brand)";
  return (
    <svg viewBox="0 0 120 68" className="mx-auto block w-full max-w-[13rem]" role="img"
      aria-label={`${props.label}: ${v.toFixed(1)}%${props.target ? `, target ${props.target[0]}–${props.target[1]}%` : ""}`}>
      <path d={arc(0, 100)} fill="none" stroke={props.hero ? "color-mix(in srgb, var(--color-on-brand) 25%, transparent)" : "var(--color-line)"}
        strokeWidth="10" strokeLinecap="round" />
      {props.target && (
        <path d={arc(props.target[0], props.target[1])} fill="none" stroke="var(--color-good)" strokeOpacity="0.45" strokeWidth="10" />
      )}
      {props.value != null && v > 0 && <path d={arc(0, v)} fill="none" stroke={stroke} strokeWidth="6" strokeLinecap="round" />}
      <text x="60" y="56" textAnchor="middle" className="font-mono" fontSize="17" fontWeight="700" fill="currentColor">
        {props.value == null ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: 1 })}%`}
      </text>
    </svg>
  );
}

/** "On target" / "Above target" / "Below target", good or bad by the metric's direction. */
function targetStatus(v: number, target: [number, number], direction: "higher" | "lower"): { text: string; tone: Tone } {
  if (v >= target[0] && v <= target[1]) return { text: "On target", tone: "good" };
  const above = v > target[1];
  return { text: above ? "Above target" : "Below target", tone: above === (direction === "higher") ? "good" : "bad" };
}

export default function KpiCard(props: {
  style: KpiStyle;
  label: string;
  /** The number, formatted ("$24,874", "60.6%"). */
  value: string;
  current: number | null | undefined;
  pct: number | null | undefined;
  /** current − previous, in the metric's units (points for a percentage). */
  change: number | null | undefined;
  format: "number" | "currency" | "percent" | "days";
  direction: "higher" | "lower";
  benchmark?: string;
  target?: [number, number] | null;
  hero?: boolean;
  /** "vs. previous last 3 months" */
  versus: string;
  className?: string;
}) {
  const { hero } = props;
  const muted = hero ? "opacity-80" : "text-ink-muted";
  const percent = props.format === "percent";
  const pill = (
    <span className={hero ? "rounded-full bg-surface/80" : ""}>
      {percent ? <ChangePill points={props.pct == null ? null : props.change} direction={props.direction} />
        : <ChangePill pct={props.pct} direction={props.direction} />}
    </span>
  );
  const status = props.style === "gauge" && props.target && props.current != null
    ? targetStatus(props.current, props.target, props.direction) : null;

  return (
    <article className={`min-w-0 rounded-3xl p-4 shadow-[var(--shadow-card)] ${
      hero ? "bg-gradient-to-br from-brand to-brand-strong text-on-brand" : "border border-line bg-surface"} ${props.className ?? ""}`}>
      <div className={`text-xs uppercase tracking-wide ${muted}`}>{props.label}</div>

      {props.style === "gauge" ? (
        <>
          <Gauge value={props.current} target={props.target} label={props.label} hero={hero} />
          <div className="flex flex-wrap items-center justify-center gap-1.5 text-xs">
            {status && (
              <span className={hero ? "rounded-full bg-surface/80" : ""}>
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${TONE_CLS[status.tone]}`}>{status.text}</span>
              </span>
            )}
            {pill}
          </div>
          {props.target && <p className={`mt-1 text-center text-[11px] ${muted}`}>Target {props.target[0]}–{props.target[1]}%</p>}
        </>
      ) : (
        <>
          <div className="mt-1 font-mono text-2xl font-bold tabular-nums sm:text-3xl">{props.value}</div>
          {props.style === "change" && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
              {pill}
              <span className={muted}>{props.pct == null ? "no previous period" : props.versus}</span>
            </div>
          )}
          {props.benchmark && <p className={`mt-2 text-[11px] ${muted}`}>{props.benchmark}</p>}
        </>
      )}
    </article>
  );
}

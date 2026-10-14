// "Fix wrong data" — the wrong-format and wrong-data checks, with one-click fixes.
//
// The cleaning report (api/services/transforms.py) flags, without changing anything:
//   * WRONG FORMAT: a text column that is really numbers or dates ("$1,200",
//     "2020/12/26"). Fix: "Convert to date" — like pd.to_datetime(). Values that
//     don't convert become blank; then "Remove rows where this is blank" in the
//     missing-values table (like df.dropna(subset=[...])).
//   * WRONG DATA (outliers): numbers far outside the usual range (3 × the
//     interquartile range), and dates in the future or before 1900. For each
//     column the client picks: keep (default — a very large order may be real),
//     cap to the range ("over 120 becomes 120"), remove those rows, or make them
//     blank. One wrong value (450 instead of 45) is fixed with "Replace values"
//     in Transform data.
// Every fix is added as a step to the table's transform steps, so it's listed
// (and can be removed) under "Transform data".

"use client";

import { useState } from "react";
import { fmtInt } from "@/lib/format";
import type { CleaningReport, ColumnReport, TransformStep } from "@/lib/types";
import { Button, ErrorBox } from "./ui";

type Method = "keep" | "cap" | "remove" | "blank";

/** The steps without any earlier step of this kind for this column (a new choice replaces the old one). */
function without(steps: TransformStep[], op: TransformStep["op"], column: string) {
  return steps.filter((s) => !(s.op === op && "column" in s && s.column === column));
}

export default function DataQuality(props: {
  report: CleaningReport;
  steps: TransformStep[];
  onApplySteps: (steps: TransformStep[]) => Promise<void>;
}) {
  const cols = props.report.column_reports.filter((c) => c.action !== "dropped");
  const wrongFormat = cols.filter((c) => c.format_issue);
  const unusual = cols.filter((c) => c.outliers);
  const [choice, setChoice] = useState<Record<string, { method: Method; low: string; high: string }>>(() =>
    Object.fromEntries(
      unusual.map((c) => [c.key, { method: "keep" as Method, low: String(c.outliers!.low), high: String(c.outliers!.high) }]),
    ),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!wrongFormat.length && !unusual.length) return null;

  async function run(steps: TransformStep[]) {
    setBusy(true);
    setError(null);
    try {
      await props.onApplySteps(steps);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't apply that.");
    } finally {
      setBusy(false);
    }
  }

  const convert = (c: ColumnReport) =>
    run([...without(props.steps, "change_type", c.key), { op: "change_type", column: c.key, to: c.format_issue!.to }]);

  function applyUnusual() {
    let steps = props.steps;
    unusual.forEach((c) => {
      const pick = choice[c.key];
      steps = without(steps, "outliers", c.key);
      if (pick && pick.method !== "keep") {
        steps = [...steps, { op: "outliers", column: c.key, method: pick.method, low: pick.low || null, high: pick.high || null }];
      }
    });
    run(steps);
  }
  const anyPicked = unusual.some((c) => choice[c.key]?.method !== "keep");

  return (
    <div className="space-y-6">
      {wrongFormat.length > 0 && (
        <div>
          <h3 className="font-semibold">Wrong format</h3>
          <p className="mb-2 text-xs text-ink-muted">
            These columns hold numbers or dates saved as text, so they can&apos;t be added up or put on a timeline yet.
          </p>
          <div className="space-y-2">
            {wrongFormat.map((c) => {
              const f = c.format_issue!;
              return (
                <div key={c.key} className="flex flex-col gap-2 rounded-2xl border border-line bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div className="text-sm">
                    <span className="font-mono font-medium">{c.column}</span> looks like {f.to}s stored as text.{" "}
                    <span className="text-ink-muted">
                      {fmtInt(f.convertible)} values convert
                      {f.failing > 0 && (
                        <>
                          ; <span className="text-warn">{fmtInt(f.failing)} won&apos;t</span> (e.g. {f.examples.map((v) => `"${v}"`).join(", ")}) and
                          will become blank — then pick &ldquo;Remove rows where this is blank&rdquo; below
                        </>
                      )}
                      .
                    </span>
                  </div>
                  <Button variant="secondary" onClick={() => convert(c)} disabled={busy} className="shrink-0">
                    Convert to {f.to}
                  </Button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {unusual.length > 0 && (
        <div>
          <h3 className="font-semibold">Unusual values (possible wrong data)</h3>
          <p className="mb-2 text-xs text-ink-muted">
            Values far outside the usual range — often a typo (450 instead of 45), sometimes real (a very large order).
            Nothing changes unless you choose. To fix one value, use <strong>Replace values</strong> in Transform data.
          </p>
          <div className="overflow-x-auto rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
            <table className="w-full text-sm">
              <thead className="border-b border-line bg-surface-2 text-left text-xs uppercase tracking-wide text-ink-muted">
                <tr>
                  <th className="px-3 py-2">Column</th>
                  <th className="px-3 py-2">Unusual</th>
                  <th className="px-3 py-2">Expected range</th>
                  <th className="px-3 py-2">What to do</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {unusual.map((c) => {
                  const o = c.outliers!;
                  const pick = choice[c.key] ?? { method: "keep", low: String(o.low), high: String(o.high) };
                  const set = (patch: Partial<typeof pick>) => setChoice((all) => ({ ...all, [c.key]: { ...pick, ...patch } }));
                  return (
                    <tr key={c.key} className="align-top">
                      <td className="px-3 py-2 font-mono font-medium">{c.column}</td>
                      <td className="px-3 py-2">
                        <div className="tabular-nums">
                          {fmtInt(o.count)} value{o.count === 1 ? "" : "s"}
                        </div>
                        <div className="text-xs text-ink-muted">e.g. {o.examples.join(", ")}</div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1">
                          <input
                            aria-label={`Lowest expected ${c.column}`}
                            value={pick.low}
                            onChange={(e) => set({ low: e.target.value })}
                            className="w-28 rounded-lg border border-line bg-surface px-2 py-1 font-mono text-xs"
                          />
                          <span className="text-ink-muted">to</span>
                          <input
                            aria-label={`Highest expected ${c.column}`}
                            value={pick.high}
                            onChange={(e) => set({ high: e.target.value })}
                            className="w-28 rounded-lg border border-line bg-surface px-2 py-1 font-mono text-xs"
                          />
                        </div>
                        <div className="mt-0.5 text-xs text-ink-muted">
                          Data runs {o.min} to {o.max}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <select
                          aria-label={`What to do with unusual values in ${c.column}`}
                          value={pick.method}
                          onChange={(e) => set({ method: e.target.value as Method })}
                          className="w-full min-w-44 rounded-xl border border-line bg-surface px-2 py-1 text-sm"
                        >
                          <option value="keep">Keep them (they&apos;re real)</option>
                          {o.kind === "number" && <option value="cap">Cap to the range</option>}
                          <option value="remove">Remove those rows</option>
                          <option value="blank">Make them blank</option>
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button onClick={applyUnusual} disabled={busy || !anyPicked}>
              {busy ? "Applying…" : "Apply"}
            </Button>
            <span className="text-xs text-ink-muted">
              Cap: anything above the range becomes the highest expected value (and below, the lowest).
            </span>
          </div>
        </div>
      )}
      <ErrorBox message={error} />
    </div>
  );
}

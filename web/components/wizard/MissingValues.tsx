// "Missing values by column" — the report plus what to do about each gap.
//
// For every column the backend (api/services/cleaning.py) sends:
//   * how much is missing, and a level from the rules of thumb:
//       under 20%  -> fine to keep or fill
//       20% +      -> "Review": think about removing the column
//       90% +      -> "Remove": too little left to trust
//   * the values a fill would use (mean / median / mode) and a suggestion:
//       mean for roughly symmetric numbers, median for skewed numbers,
//       mode (most common value) for text
//   * for ID columns (order_id, customer_id …): no fills — an ID should never
//     be blank, so the suggestion is "Remove rows with no ID". Those rows go
//     on the "rows_missing_id" sheet of the Excel download for the client to fix.
//   * every column can also "Remove rows where this is blank" (df.dropna(subset=[col])),
//     e.g. after converting a date column whose bad values became blank
//   * the column's original name, when cleaning renamed it ("Order Date" -> order_date)
// Choices are keyed by the column's stable `key`, so they survive a rename.
//
// Long tables are shown 10 columns per page (First / Previous / Next / Last);
// choices made on one page are kept when you move to another.
//
// The user picks an action per column, then "Apply" re-runs the cleaning on
// the server with those choices (the files are re-sent from browser memory —
// nothing is stored). The same choices are used by the dashboard, the skills
// and the download, so everything matches what's on screen.

"use client";

import { useState } from "react";
import { fmtInt, fmtPct } from "@/lib/format";
import type { CleaningReport, ColumnAction, ColumnReport, FillMethod } from "@/lib/types";
import { Button, ErrorBox, Pager } from "./ui";

/** Columns shown per page. */
export const COLUMNS_PER_PAGE = 10;

const FILL_LABEL: Record<FillMethod, string> = { mean: "mean (average)", median: "median (middle value)", mode: "mode (most common)" };

/** Bar/badge color from the 20% / 90% rules of thumb. */
const levelTone = (c: ColumnReport) =>
  c.missing_level === "remove" ? "bg-bad" : c.missing_level === "review" ? "bg-warn" : "bg-good";

function skewNote(c: ColumnReport): string | null {
  if (c.skewness == null) return null;
  const shape = Math.abs(c.skewness) < 0.5 ? "roughly symmetric" : c.skewness > 0 ? "long tail of high values" : "long tail of low values";
  return `Skewness ${c.skewness.toFixed(2)} (${shape})`;
}

export default function MissingValues(props: {
  report: CleaningReport;
  /** The choices currently applied on the server for this table. */
  applied: Record<string, ColumnAction>;
  onApply: (actions: Record<string, ColumnAction>) => Promise<void>;
}) {
  const { report } = props;
  const [draft, setDraft] = useState<Record<string, ColumnAction>>(props.applied);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const all = report.column_reports;
  const pages = Math.max(1, Math.ceil(all.length / COLUMNS_PER_PAGE));
  const current = Math.min(page, pages - 1); // a removed column can shrink the list
  const shown = all.slice(current * COLUMNS_PER_PAGE, (current + 1) * COLUMNS_PER_PAGE);

  const withGaps = report.column_reports.filter((c) => c.missing_count > 0);
  const valueOf = (key: string) => draft[key] ?? "keep";
  // Compare ignoring "keep" (the default) so an untouched form isn't "changed".
  const clean = (a: Record<string, ColumnAction>) =>
    JSON.stringify(Object.entries(a).filter(([, v]) => v !== "keep").sort());
  const changed = clean(draft) !== clean(props.applied);

  // Our suggestions: remove 90%+ columns, remove rows with a blank ID, fill
  // small gaps (under 20%) with the suggested method. Other 20–90% columns are left as they are for the user to
  // decide — filling a third of a column with one value changes the data a lot.
  function useSuggestions() {
    const next: Record<string, ColumnAction> = { ...draft };
    withGaps.forEach((c) => {
      if (c.missing_level === "remove") next[c.key] = "drop";
      // IDs must not be blank: remove those rows at any level below 90%.
      else if (c.suggested_fill === "drop_rows") next[c.key] = "drop_rows";
      else if (c.missing_level === "low" && c.suggested_fill) next[c.key] = c.suggested_fill;
    });
    setDraft(next);
  }

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      await props.onApply(Object.fromEntries(Object.entries(draft).filter(([, v]) => v !== "keep")));
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't apply those changes.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">Missing values by column</h3>
        <span className="text-xs text-ink-muted">
          Rules of thumb: <span className="font-medium">20%+</span> missing — think about removing the column ·{" "}
          <span className="font-medium">90%+</span> — remove it
        </span>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
        <table className="w-full text-sm">
          <thead className="border-b border-line bg-surface-2 text-left text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th className="px-3 py-2">Column</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2 text-right">Missing</th>
              <th className="w-1/5 px-3 py-2">% missing</th>
              <th className="px-3 py-2">What to do</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {shown.map((c) => (
              <tr key={c.column} className="align-top">
                <td className="px-3 py-2">
                  <div className="flex items-center gap-2 font-medium">
                    {c.column}
                    {c.missing_level === "review" && (
                      <span className="rounded-full bg-warn/15 px-2 py-0.5 text-[11px] font-semibold text-warn">Review</span>
                    )}
                    {c.missing_level === "remove" && (
                      <span className="rounded-full bg-bad/15 px-2 py-0.5 text-[11px] font-semibold text-bad">Remove</span>
                    )}
                    {c.action === "dropped" && <span className="text-xs text-ink-muted">(removed)</span>}
                  </div>
                  {c.original_name === "" ? (
                    <div className="text-xs text-warn">no name in your file</div>
                  ) : (
                    c.original_name !== c.column && (
                      <div className="text-xs text-ink-muted">was &ldquo;{c.original_name}&rdquo;</div>
                    )
                  )}
                  {c.recommendation && <div className="mt-0.5 max-w-sm text-xs text-ink-muted">{c.recommendation}</div>}
                </td>
                <td className="px-3 py-2 text-ink-muted">{c.inferred_type}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {fmtInt(c.missing_count)}
                  {c.filled_count > 0 && <div className="text-xs text-good">{fmtInt(c.filled_count)} filled</div>}
                  {c.rows_dropped > 0 && <div className="text-xs text-good">{fmtInt(c.rows_dropped)} rows removed</div>}
                </td>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-2">
                    <div className="h-2 flex-1 rounded-full bg-line">
                      <div
                        className={`h-2 rounded-full ${levelTone(c)}`}
                        style={{ width: `${Math.max(c.missing_pct, c.missing_pct > 0 ? 2 : 0)}%` }}
                      />
                    </div>
                    <span className="w-12 text-right tabular-nums">{fmtPct(c.missing_pct)}</span>
                  </div>
                </td>
                <td className="px-3 py-2">
                  {c.missing_count === 0 ? (
                    <span className="text-xs text-ink-muted">Nothing missing</span>
                  ) : (
                    <>
                      <select
                        aria-label={`What to do with missing values in ${c.column}`}
                        value={valueOf(c.key)}
                        onChange={(e) => setDraft((d) => ({ ...d, [c.key]: e.target.value as ColumnAction }))}
                        className="w-full min-w-48 rounded-xl border border-line bg-surface px-2 py-1 text-sm"
                      >
                        <option value="keep">Keep the gaps</option>
                        {/* IDs and dates get no fill options (an "average ID" means nothing). */}
                        {(Object.keys(c.fill_options) as FillMethod[]).map((m) => (
                          <option key={m} value={m}>
                            Fill with {FILL_LABEL[m]}: {c.fill_options[m]}
                            {c.suggested_fill === m ? "  ★ suggested" : ""}
                          </option>
                        ))}
                        {/* Like df.dropna(subset=[column]); for IDs this is the suggestion. */}
                        <option value="drop_rows">
                          {c.is_id ? "Remove rows with no ID" : "Remove rows where this is blank"} ({fmtInt(c.missing_count)})
                          {c.suggested_fill === "drop_rows" ? "  ★ suggested" : ""}
                        </option>
                        <option value="drop">Remove this column{c.missing_level === "remove" ? "  ★ suggested" : ""}</option>
                      </select>
                      {skewNote(c) && <div className="mt-0.5 text-xs text-ink-muted">{skewNote(c)}</div>}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Pager
        label="Missing values pages"
        page={current}
        pages={pages}
        onPage={setPage}
        summary={`Columns ${fmtInt(current * COLUMNS_PER_PAGE + 1)}–${fmtInt(current * COLUMNS_PER_PAGE + shown.length)} of ${fmtInt(all.length)}`}
      />

      {withGaps.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={useSuggestions} disabled={busy}>
            Use our suggestions
          </Button>
          <Button onClick={apply} disabled={busy || !changed}>
            {busy ? "Applying…" : "Apply changes"}
          </Button>
          <span className="text-xs text-ink-muted" aria-live="polite">
            {changed
              ? "You have changes that aren't applied yet."
              : "Nothing changes unless you choose to. Suggestions (for every page) fill small gaps (under 20%), remove rows with no ID, and remove 90%+ columns; the rest is your call."}
          </span>
        </div>
      )}
      <div className="mt-2">
        <ErrorBox message={error} />
      </div>
    </div>
  );
}

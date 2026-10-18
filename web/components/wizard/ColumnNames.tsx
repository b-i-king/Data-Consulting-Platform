// "Column names" — header problems and renaming, shown above the missing values.
//
// The cleaning (api/services/cleaning.py, Step 0) turns every column name into
// lowercase snake_case and flags two header problems:
//   * a BLANK header ("unnamed_column_4"): the client can name it, remove it
//     (one button removes every unnamed column), or fix the header row in the
//     file and upload again
//   * a REPEATED header ("amount" and "amount_2"): the client should rename
//     them so the analysis can tell them apart (amount_paid, amount_refunded)
// "Edit column names" lets the client rename ANY column (10 per page, the
// columns that need fixing first). Names are sent to the
// server as {column key: new name} (`column_names`), cleaned the same way
// (lowercase, underscores) and must be unique. Like the missing-value choices,
// nothing is stored: the files are re-sent from browser memory each time.

"use client";

import { useState } from "react";
import type { CleaningReport, ColumnAction } from "@/lib/types";
import { COLUMNS_PER_PAGE } from "./MissingValues";
import { Button, ErrorBox, Pager } from "./ui";

/** Roughly what the server will save a typed name as (it has the final say). */
const preview = (name: string) =>
  name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");

export default function ColumnNames(props: {
  report: CleaningReport;
  /** Column choices in effect (removing an unnamed column is one of them). */
  applied: Record<string, ColumnAction>;
  /** The client's names in effect: column key -> name. */
  appliedNames: Record<string, string>;
  onApplyNames: (names: Record<string, string>, actions?: Record<string, ColumnAction>) => Promise<void>;
}) {
  const { report } = props;
  const cols = report.column_reports;
  const unnamed = cols.filter((c) => c.name_problem === "unnamed");
  const duplicates = report.duplicate_columns ?? [];
  const hasProblems = unnamed.length > 0 || duplicates.length > 0;

  // Opens by itself when there's something to fix.
  const [editing, setEditing] = useState(hasProblems);
  // Draft: column key -> the name typed in the box (starts as the current name).
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    Object.fromEntries(cols.map((c) => [c.key, c.column])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 10 columns per page, like the other column tables; problem columns first.
  const [page, setPage] = useState(0);
  const ordered = [...cols.filter((c) => c.name_problem), ...cols.filter((c) => !c.name_problem)];
  const pages = Math.max(1, Math.ceil(ordered.length / COLUMNS_PER_PAGE));
  const current = Math.min(page, pages - 1);
  const shown = ordered.slice(current * COLUMNS_PER_PAGE, (current + 1) * COLUMNS_PER_PAGE);

  // Only names that differ from the automatic one are sent.
  const wanted = Object.fromEntries(
    cols.filter((c) => draft[c.key]?.trim() && preview(draft[c.key]) !== c.key).map((c) => [c.key, draft[c.key].trim()]),
  );
  const changed = cols.some((c) => preview(draft[c.key] ?? "") !== c.column);
  const blankBox = cols.some((c) => !preview(draft[c.key] ?? ""));
  const typedTwice = (() => {
    const names = cols.filter((c) => c.action !== "dropped").map((c) => preview(draft[c.key] ?? ""));
    return [...new Set(names.filter((n, i) => n && names.indexOf(n) !== i))];
  })();

  async function run(names: Record<string, string>, actions?: Record<string, ColumnAction>) {
    setBusy(true);
    setError(null);
    try {
      await props.onApplyNames(names, actions);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't rename those columns.");
    } finally {
      setBusy(false);
    }
  }

  const removeUnnamed = () =>
    run(props.appliedNames, { ...props.applied, ...Object.fromEntries(unnamed.map((c) => [c.key, "drop" as const])) });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">Column names</h3>
        <button type="button" className="text-sm text-brand-ink hover:underline" onClick={() => setEditing(!editing)} aria-expanded={editing}>
          {editing ? "Hide column names" : "Edit column names"}
        </button>
      </div>
      <p className="text-xs text-ink-muted">
        Cleaned to lowercase with underscores (<span className="font-mono">Order Date → order_date</span>) so they work in
        Excel, SQL and Python.
      </p>

      {unnamed.length > 0 && (
        <div role="alert" className="rounded-2xl border border-warn/40 bg-warn/10 p-4 text-sm">
          <p>
            <strong>
              {unnamed.length === 1 ? "1 column has" : `${unnamed.length} columns have`} no name in your file
            </strong>{" "}
            ({unnamed.map((c) => c.column).join(", ")} — the number is its position from the left). Analysis needs every
            column named. You can:
          </p>
          <ul className="mt-2 list-inside list-disc space-y-1">
            <li>type a name for each one below,</li>
            <li>remove them (best when they&apos;re empty or not needed), or</li>
            <li>fix the header row in your file (Excel or CSV) and upload it again.</li>
          </ul>
          <div className="mt-3">
            <Button variant="secondary" onClick={removeUnnamed} disabled={busy}>
              Remove {unnamed.length === 1 ? "the unnamed column" : `all ${unnamed.length} unnamed columns`}
            </Button>
          </div>
        </div>
      )}

      {duplicates.length > 0 && (
        <div role="alert" className="rounded-2xl border border-warn/40 bg-warn/10 p-4 text-sm">
          <strong>Some columns had the same name:</strong>{" "}
          {duplicates.map((g) => g.join(" and ")).join("; ")}. Rename them below so the analysis can tell them apart —
          for example <span className="font-mono">amount_paid</span> and <span className="font-mono">amount_refunded</span>.
        </div>
      )}

      {editing && (
        <div className="overflow-x-auto rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
          <table className="w-full text-sm">
            <thead className="border-b border-line bg-surface-2 text-left text-xs uppercase tracking-wide text-ink-muted">
              <tr>
                <th className="px-3 py-2">In your file</th>
                <th className="px-3 py-2">Column name</th>
                <th className="px-3 py-2">Saved as</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {shown.map((c) => {
                const typed = draft[c.key] ?? "";
                const saved = preview(typed);
                const problem = !saved || typedTwice.includes(saved);
                return (
                  <tr key={c.key} className={c.action === "dropped" ? "opacity-50" : ""}>
                    <td className="px-3 py-2">
                      {c.original_name === "" ? <span className="text-warn">(no name)</span> : c.original_name}
                      {c.action === "dropped" && <span className="ml-1 text-xs text-ink-muted">(removed)</span>}
                    </td>
                    <td className="px-3 py-2">
                      <input
                        aria-label={`New name for ${c.column}`}
                        value={typed}
                        disabled={c.action === "dropped"}
                        onChange={(e) => setDraft((d) => ({ ...d, [c.key]: e.target.value }))}
                        className={`w-full min-w-40 rounded-xl border bg-surface px-2 py-1 font-mono text-sm ${
                          problem || c.name_problem ? "border-warn" : "border-line"
                        }`}
                      />
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-ink-muted">
                      {saved || <span className="text-bad">needs letters or numbers</span>}
                      {saved && typedTwice.includes(saved) && <span className="ml-1 text-bad">used twice</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <Pager
          label="Column name pages"
          page={current}
          pages={pages}
          onPage={setPage}
          summary={`Columns ${current * COLUMNS_PER_PAGE + 1}–${current * COLUMNS_PER_PAGE + shown.length} of ${ordered.length} (ones to fix first)`}
        />
      )}

      {editing && (
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => run(wanted)} disabled={busy || !changed || blankBox || typedTwice.length > 0}>
            {busy ? "Saving…" : "Save column names"}
          </Button>
          {Object.keys(props.appliedNames).length > 0 && (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setDraft(Object.fromEntries(cols.map((c) => [c.key, c.key])));
                run({});
              }}
            >
              Undo my names
            </Button>
          )}
          <span className="text-xs text-ink-muted">Your names are used in the preview, the analysis and the download.</span>
        </div>
      )}
      <ErrorBox message={error} />
    </div>
  );
}

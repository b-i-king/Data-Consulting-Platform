// Table showing the first rows of the cleaned data (like pandas' df.head()).
// The backend sends the first PREVIEW_MAX_ROWS rows (5 by default).
//
// Wide tables are shown 10 fields (columns) at a time, with First / Previous /
// Next / Last buttons — like df.iloc[:, 0:10], then 10:20, and so on. If
// PREVIEW_MAX_ROWS is raised, rows are paged 50 at a time the same way.
// Headers show the real (cleaned, lowercase snake_case) column names as-is.

"use client";

import { useState } from "react";
import { fmtInt } from "@/lib/format";
import type { GroupOutput } from "@/lib/types";
import { Pager } from "./ui";
import { COLUMNS_PER_PAGE } from "./MissingValues";

const PAGE_SIZE = 50;

export default function DataPreview({ preview }: { preview: GroupOutput["preview"] }) {
  const [page, setPage] = useState(0);
  const [colPage, setColPage] = useState(0);
  const colPages = Math.max(1, Math.ceil(preview.columns.length / COLUMNS_PER_PAGE));
  const colCurrent = Math.min(colPage, colPages - 1);
  const columns = preview.columns.slice(colCurrent * COLUMNS_PER_PAGE, (colCurrent + 1) * COLUMNS_PER_PAGE);
  const pages = Math.max(1, Math.ceil(preview.rows.length / PAGE_SIZE));
  const rows = preview.rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const first = page * PAGE_SIZE + 1;
  const last = Math.min((page + 1) * PAGE_SIZE, preview.rows.length);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">Preview of your cleaned data</h3>
        <span className="text-sm text-ink-muted">
          {preview.rows.length
            ? `Rows ${fmtInt(first)}–${fmtInt(last)} of ${fmtInt(preview.total_rows)} · ${fmtInt(preview.columns.length)} fields`
            : "No rows"}
        </span>
      </div>
      {preview.truncated && (
        <p className="mb-2 text-xs text-ink-muted">
          Showing the first {fmtInt(preview.rows.length)} rows. Your download includes all {fmtInt(preview.total_rows)} rows.
        </p>
      )}
      <div className="max-h-[32rem] overflow-auto rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-surface-2 text-left font-mono text-xs text-ink-muted shadow-[0_1px_0_var(--color-line)]">
            <tr>
              <th className="px-3 py-2 text-right">#</th>
              {columns.map((c) => (
                <th key={c} className="whitespace-nowrap px-3 py-2">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((row, i) => (
              <tr key={first + i}>
                <td className="px-3 py-1.5 text-right text-ink-muted tabular-nums">{fmtInt(first + i)}</td>
                {columns.map((c) => {
                  const v = row[c];
                  return (
                    <td key={c} className="whitespace-nowrap px-3 py-1.5">
                      {v === null || v === undefined ? (
                        <span className="text-ink-muted/60 italic">missing</span>
                      ) : (
                        String(v)
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager
        label="Field pages"
        page={colCurrent}
        pages={colPages}
        onPage={setColPage}
        summary={`Fields ${fmtInt(colCurrent * COLUMNS_PER_PAGE + 1)}–${fmtInt(colCurrent * COLUMNS_PER_PAGE + columns.length)} of ${fmtInt(preview.columns.length)}`}
      />
      <Pager
        label="Row pages"
        page={page}
        pages={pages}
        onPage={setPage}
        summary={`Rows ${fmtInt(first)}–${fmtInt(last)}`}
      />
    </div>
  );
}

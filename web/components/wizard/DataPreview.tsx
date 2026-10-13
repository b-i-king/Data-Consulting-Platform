// Paginated table showing the cleaned data.
// The backend sends every row (up to PREVIEW_MAX_ROWS), and this component
// shows 50 at a time so the page stays fast even with tens of thousands of rows.

"use client";

import { useState } from "react";
import { fmtInt } from "@/lib/format";
import type { GroupOutput } from "@/lib/types";
import { Button } from "./ui";

const PAGE_SIZE = 50;

export default function DataPreview({ preview }: { preview: GroupOutput["preview"] }) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(preview.rows.length / PAGE_SIZE));
  const rows = preview.rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const first = page * PAGE_SIZE + 1;
  const last = Math.min((page + 1) * PAGE_SIZE, preview.rows.length);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">Preview of your cleaned data</h3>
        <span className="text-sm text-ink-muted">
          {preview.rows.length ? `Rows ${fmtInt(first)}–${fmtInt(last)} of ${fmtInt(preview.total_rows)}` : "No rows"}
        </span>
      </div>
      {preview.truncated && (
        <p className="mb-2 text-xs text-warn">
          Showing the first {fmtInt(preview.rows.length)} rows on screen. Your download includes all{" "}
          {fmtInt(preview.total_rows)} rows.
        </p>
      )}
      <div className="max-h-[32rem] overflow-auto rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-surface-2 text-left text-xs uppercase tracking-wide text-ink-muted shadow-[0_1px_0_var(--color-line)]">
            <tr>
              <th className="px-3 py-2 text-right">#</th>
              {preview.columns.map((c) => (
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
                {preview.columns.map((c) => {
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
      {pages > 1 && (
        <div className="mt-3 flex items-center justify-end gap-2 text-sm">
          <Button variant="secondary" onClick={() => setPage((p) => p - 1)} disabled={page === 0}>
            Previous
          </Button>
          <span className="text-ink-muted">
            Page {fmtInt(page + 1)} of {fmtInt(pages)}
          </span>
          <Button variant="secondary" onClick={() => setPage((p) => p + 1)} disabled={page >= pages - 1}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}

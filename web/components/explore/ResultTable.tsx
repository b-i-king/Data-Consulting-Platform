// A skill's result table (SkillResult.tables[name]), scrollable, first 200 rows.
// Shared by the Skills panel and the EDA report view. Numbers are shown in US
// format (1,234.5); empty cells as "—". Long text is cut, with the full value
// on hover.

import type { Cell } from "@/lib/types";

const show = (v: Cell) =>
  v == null ? "—" : typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: 4 }) : String(v);

export default function ResultTable({ rows }: { rows: Record<string, Cell>[] }) {
  if (!rows.length) return <p className="text-sm text-ink-muted">No rows.</p>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="max-h-72 overflow-auto rounded-xl border border-line">
      <table className="w-full text-sm">
        {/* Column names exactly as in the cleaned data (lowercase snake_case). */}
        <thead className="sticky top-0 bg-surface-2 text-left font-mono text-xs text-ink-muted">
          <tr>
            {cols.map((c) => (
              <th key={c} className="whitespace-nowrap px-3 py-2">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.slice(0, 200).map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} className="max-w-xs truncate whitespace-nowrap px-3 py-1.5" title={show(r[c])}>
                  {show(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

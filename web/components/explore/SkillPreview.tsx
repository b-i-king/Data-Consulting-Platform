// A PREVIEW of a paid tool: what the server sends when the tool isn't in the
// package (api/services/skill_access.py). One per tool per visit, and small on
// purpose: the summary, the first finding and — for the dashboard — the
// headline KPIs. No charts, tables or downloads; the card says how much the
// full result has, and how to get it.

"use client";

import { fmtNumber } from "@/lib/format";
import type { SkillResult } from "@/lib/types";
import { Card } from "@/components/wizard/ui";

/** "3 charts, 2 tables and 1 file" — what the full result has that the preview doesn't show. */
function hiddenLine(h: NonNullable<SkillResult["preview_hidden"]>): string | null {
  const parts = [
    h.charts && `${h.charts} chart${h.charts === 1 ? "" : "s"}`,
    h.tables && `${h.tables} table${h.tables === 1 ? "" : "s"}`,
    h.findings && `${h.findings} more finding${h.findings === 1 ? "" : "s"}`,
    h.files && `${h.files} download${h.files === 1 ? "" : "s"}`,
  ].filter(Boolean) as string[];
  if (!parts.length) return null;
  const last = parts.pop();
  return `The full result also has ${parts.length ? `${parts.join(", ")} and ${last}` : last}.`;
}

export default function SkillPreview({ result }: { result: SkillResult }) {
  // The dashboard's headline KPIs: rows of {kpi, current, previous, pct_change}.
  const kpis = Object.entries(result.tables).find(([k]) => k.startsWith("kpis_"))?.[1] ?? [];
  const hidden = result.preview_hidden ? hiddenLine(result.preview_hidden) : null;

  return (
    <Card className="space-y-3">
      <span className="inline-block rounded-full bg-brand-soft px-2.5 py-0.5 text-xs font-semibold text-brand-ink">
        Preview
      </span>
      <p className="font-medium">{result.summary}</p>
      {kpis.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {kpis.map((k, i) => {
            const pct = typeof k.pct_change === "number" ? k.pct_change : null;
            return (
              <div key={i} className="rounded-xl border border-line bg-surface-2 p-3">
                <p className="text-xs text-ink-muted">{String(k.kpi)}</p>
                <p className="font-mono text-xl font-semibold">
                  {typeof k.current === "number" ? fmtNumber(k.current) : "—"}
                </p>
                {pct !== null && (
                  <p className="text-xs text-ink-muted">
                    {pct >= 0 ? "▲" : "▼"} {fmtNumber(Math.abs(pct * 100))}% vs. the period before
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
      {result.findings.length > 0 && (
        <ul className="list-inside list-disc space-y-1 text-sm">
          {result.findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
      <p className="rounded-xl border border-brand/40 bg-brand-soft p-3 text-sm">
        {result.preview_note}
        {hidden && <span className="block text-ink-muted">{hidden}</span>}
      </p>
    </Card>
  );
}

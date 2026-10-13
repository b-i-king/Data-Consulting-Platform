// Step 3 — show what we found and what the cleaning did.
//
// Sections:
//   1. "How your files fit together" — only for multi-file uploads
//   2. One tab per dataset group, each with:
//        a. cleaning summary tiles (rows before/after, duplicates, missing)
//        b. missing values by column (table with bars)
//        c. "Is there enough data?" sample-size snapshot
//        d. full data preview (paginated)

"use client";

import { useState } from "react";
import { fmtInt, fmtPct } from "@/lib/format";
import type { AnalyzeResponse, GroupOutput } from "@/lib/types";
import { Button, Card, ConsultantCta, NavRow, Stat, StepHeader } from "./ui";
import DataPreview from "./DataPreview";

export default function StepResults(props: {
  analysis: AnalyzeResponse;
  bookingUrl: string;
  onBack: () => void;
  onNext: () => void;
}) {
  const { categorization, groups } = props.analysis;
  const [active, setActive] = useState(0);
  const group = groups[active];

  return (
    <section className="space-y-8">
      <StepHeader title="Your data, cleaned" subtitle={categorization.next_step} />

      {categorization.mode === "multiple_files" && (
        <div className="space-y-3">
          <h2 className="font-semibold">How your files fit together</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {categorization.groups.map((g) => (
              <Card key={g.group_id}>
                <div className="font-medium">{g.label}</div>
                <ul className="mt-1 list-inside list-disc text-sm text-ink-muted">
                  {g.members.map((m) => (
                    <li key={m} className="truncate">
                      {m}
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-sm">{g.recommendation}</p>
              </Card>
            ))}
          </div>
          {categorization.relationships.map((r) => (
            <p key={`${r.left_group}-${r.right_group}`} className="text-sm">
              🔗 {r.description}
            </p>
          ))}
          <p className="text-xs text-ink-muted">
            Categories are a best guess from column names. A consultant can confirm how your data relates.
          </p>
        </div>
      )}

      {groups.length > 1 && (
        <div role="tablist" className="flex flex-wrap gap-2">
          {groups.map((g, i) => (
            <button
              key={g.group_id}
              role="tab"
              aria-selected={i === active}
              onClick={() => setActive(i)}
              className={`rounded-full border px-3 py-1.5 text-sm ${
                i === active ? "border-brand bg-brand-soft font-medium" : "border-line bg-surface hover:border-brand"
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>
      )}

      <GroupDetails key={group.group_id} group={group} />

      <ConsultantCta bookingUrl={props.bookingUrl}>
        <strong>Not sure what these numbers mean for your business?</strong> Talk it through with a consultant.
      </ConsultantCta>

      <NavRow onBack={props.onBack}>
        <Button onClick={props.onNext}>Continue</Button>
      </NavRow>
    </section>
  );
}

function GroupDetails({ group }: { group: GroupOutput }) {
  const r = group.report;
  const s = group.sample_size;

  return (
    <div className="space-y-8">
      {/* a. Summary tiles */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Rows before" value={fmtInt(r.rows_before)} />
        <Stat label="Rows after" value={fmtInt(r.rows_after)} />
        <Stat label="Duplicates removed" value={fmtInt(r.duplicate_rows_removed)} tone={r.duplicate_rows_removed ? "good" : undefined} />
        <Stat
          label="Cells missing"
          value={fmtPct(r.total_missing_pct)}
          tone={r.total_missing_pct > 20 ? "bad" : r.total_missing_pct > 5 ? "warn" : "good"}
        />
      </div>

      {r.notes.length > 0 && (
        <ul className="list-inside list-disc space-y-1 text-sm">
          {r.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}

      {/* b. Missing values by column */}
      <div>
        <h3 className="mb-2 font-semibold">Missing values by column</h3>
        <div className="overflow-x-auto rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
          <table className="w-full text-sm">
            <thead className="border-b border-line bg-surface-2 text-left text-xs uppercase tracking-wide text-ink-muted">
              <tr>
                <th className="px-3 py-2">Column</th>
                <th className="px-3 py-2">Type</th>
                <th className="px-3 py-2 text-right">Missing</th>
                <th className="w-1/3 px-3 py-2">% missing</th>
                <th className="px-3 py-2 text-right">Unique</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {r.column_reports.map((c) => (
                <tr key={c.column}>
                  <td className="px-3 py-2 font-medium">{c.column}</td>
                  <td className="px-3 py-2 text-ink-muted">{c.inferred_type}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtInt(c.missing_count)}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <div className="h-2 flex-1 rounded-full bg-line">
                        <div
                          className={`h-2 rounded-full ${c.missing_pct > 20 ? "bg-bad" : c.missing_pct > 5 ? "bg-warn" : "bg-good"}`}
                          style={{ width: `${Math.max(c.missing_pct, c.missing_pct > 0 ? 2 : 0)}%` }}
                        />
                      </div>
                      <span className="w-12 text-right tabular-nums">{fmtPct(c.missing_pct)}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtInt(c.unique_count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* c. Sample-size snapshot (adapted from the sample-size skill) */}
      <div>
        <h3 className="mb-1 font-semibold">Is there enough data?</h3>
        <p className="mb-3 text-sm text-ink-muted">
          {fmtInt(s.complete_rows)} of {fmtInt(s.rows)} rows have no missing values.{" "}
          {s.time_span &&
            `Dates run from ${s.time_span.first.slice(0, 10)} to ${s.time_span.last.slice(0, 10)} (${fmtInt(s.time_span.days_covered)} days).`}
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          {s.checks.map((c) => (
            <Card key={c.question} className="p-4">
              <div className="flex items-start gap-2">
                <span
                  aria-label={c.verdict}
                  className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${
                    c.verdict === "green" ? "bg-good" : c.verdict === "yellow" ? "bg-warn" : "bg-bad"
                  }`}
                />
                <div>
                  <div className="text-sm font-medium">{c.question}</div>
                  <div className="text-xs text-ink-muted">
                    Needs about {fmtInt(c.rows_needed)} rows · {c.note}
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
        <p className="mt-2 text-xs text-ink-muted">{s.regression_predictor_guidance}</p>
      </div>

      {/* d. Data preview */}
      <DataPreview preview={group.preview} />
    </div>
  );
}

// Step 3 — show what we found and what the cleaning did.
//
// Sections:
//   1. "How your files fit together" — only for multi-file uploads
//   2. One tab per dataset group, each with:
//        a. cleaning summary tiles (rows, fields, duplicates, missing)
//        b. missing values by column, with advice and fill/remove choices
//           (MissingValues.tsx); a consultation banner if 90%+ is missing
//        c. "Is there enough data?" sample-size snapshot
//        d. preview of the first rows
//   3. Explore your data — dashboard + skills, limited to the services chosen

"use client";

import { useState } from "react";
import { fmtInt, fmtPct } from "@/lib/format";
import type { AnalyzeResponse, CleaningInput, ColumnAction, GroupOutput, Service } from "@/lib/types";
import { Button, Card, ConsultantCta, NavRow, Stat, StepHeader } from "./ui";
import DataPreview from "./DataPreview";
import MissingValues from "./MissingValues";
import ExplorePanel from "@/components/explore/ExplorePanel";

export default function StepResults(props: {
  analysis: AnalyzeResponse;
  input: CleaningInput; // files in memory + missing-value choices, re-sent by the dashboard and skills
  /** Re-run the cleaning with new choices for one table (group). */
  onApplyActions: (groupId: number, actions: Record<string, ColumnAction>) => Promise<void>;
  services: Service[];
  selectedServices: string[];
  onAddService: (id: string) => void;
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
              {/* 🔗 exact shared ID · ≈ fuzzy match on names / emails / phones */}
              <span aria-hidden>{r.method === "fuzzy" ? "≈" : "🔗"}</span> {r.description}
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

      <GroupDetails
        key={group.group_id}
        group={group}
        bookingUrl={props.bookingUrl}
        applied={props.input.columnActions[String(group.group_id)] ?? {}}
        onApply={(actions) => props.onApplyActions(group.group_id, actions)}
      />

      {/* Dashboard (when there are dates) and the data skills, on this table. */}
      <ExplorePanel
        key={`explore-${group.group_id}`}
        input={props.input}
        group={group}
        services={props.services}
        selectedServices={props.selectedServices}
        onAddService={props.onAddService}
      />

      <ConsultantCta bookingUrl={props.bookingUrl}>
        <strong>Not sure what these numbers mean for your business?</strong> Talk it through with a consultant.
      </ConsultantCta>

      <NavRow onBack={props.onBack}>
        <Button onClick={props.onNext}>Continue</Button>
      </NavRow>
    </section>
  );
}

/** The cleaning report for one table (also used by the signed-in workspace). */
export function GroupDetails(props: {
  group: GroupOutput;
  bookingUrl: string;
  applied: Record<string, ColumnAction>;
  onApply: (actions: Record<string, ColumnAction>) => Promise<void>;
}) {
  const { group } = props;
  const r = group.report;
  const s = group.sample_size;

  return (
    <div className="space-y-8">
      {/* a. Summary tiles */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label={`Rows (${fmtInt(r.rows_before)} before)`} value={fmtInt(r.rows_after)} />
        <Stat
          label={r.columns_dropped.length ? `Fields (${fmtInt(r.columns_dropped.length)} removed)` : "Fields (columns)"}
          value={fmtInt(r.columns)}
        />
        <Stat label="Duplicates removed" value={fmtInt(r.duplicate_rows_removed)} tone={r.duplicate_rows_removed ? "good" : undefined} />
        <Stat
          label={r.cells_filled ? `Cells missing (${fmtInt(r.cells_filled)} filled)` : "Cells missing"}
          value={fmtPct(r.total_missing_pct)}
          tone={r.total_missing_pct >= 90 ? "bad" : r.total_missing_pct >= 20 ? "warn" : "good"}
        />
      </div>

      {r.consultation_recommended && (
        <ConsultantCta bookingUrl={props.bookingUrl}>
          <strong>{fmtPct(r.missing_pct_before)} of this data is missing.</strong> That&apos;s more than cleaning can fix —
          book a free consultation and we&apos;ll help you find the missing data.
        </ConsultantCta>
      )}

      {r.notes.length > 0 && (
        <ul className="list-inside list-disc space-y-1 text-sm">
          {r.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}

      {/* b. Missing values by column — advice + fill / remove choices */}
      <MissingValues report={r} applied={props.applied} onApply={props.onApply} />

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

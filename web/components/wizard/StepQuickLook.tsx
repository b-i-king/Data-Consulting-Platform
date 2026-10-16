// Get Started — "Quick look" (the business app path: QuickBooks, Xero).
//
// The FREE live summary (components/workspace/QuickLook.tsx: income,
// expenses, net income, what customers owe, what you owe, month by month, top
// customers; the last 12 months) and, optionally, a preview of one record type
// (invoices, payments...): it opens on the next screen exactly like the
// workspace shows live data — read-only, a report instead of fixes, never stored.
// Listing the record types costs a few API calls, so it happens only when asked.

"use client";

import { useState } from "react";
import { getLiveDatasets } from "@/lib/api";
import { fmtInt } from "@/lib/format";
import type { LiveDataset } from "@/lib/types";
import QuickLook from "@/components/workspace/QuickLook";
import { Button, Card, ErrorBox, NavRow, StepHeader } from "./ui";

export default function StepQuickLook(props: {
  token: string;
  connector: string; // "quickbooks" | "xero"
  name: string; // "QuickBooks"
  /** A record type was picked to preview (read-only). */
  onPreview: (dataset: LiveDataset) => void;
  busy: boolean;
  error: string | null;
  onBack: () => void;
  onNext: () => void;
}) {
  const [datasets, setDatasets] = useState<LiveDataset[] | null>(null);
  const [listing, setListing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function list() {
    setListing(true);
    setError(null);
    try {
      setDatasets((await getLiveDatasets(props.token, props.connector)).datasets);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't read your records.");
    } finally {
      setListing(false);
    }
  }

  const withRecords = (datasets ?? []).filter((d) => d.has_rows);
  const empty = (datasets ?? []).length - withRecords.length;

  return (
    <section className="space-y-6">
      <StepHeader title={`Your ${props.name} at a glance`}
        subtitle="Free and live: read from your books when you open it. Nothing is stored." />
      <QuickLook token={props.token} connector={props.connector} />

      <Card className="space-y-3">
        <div>
          <h2 className="font-semibold">Preview your records (optional)</h2>
          <p className="text-sm text-ink-muted">
            See invoices, payments or expenses as a table, with a data-quality report and a dashboard preview — the same
            view the workspace gives you.
          </p>
        </div>
        {!datasets ? (
          <Button variant="secondary" disabled={listing} onClick={list}>
            {listing ? "Checking…" : "Show my record types"}
          </Button>
        ) : withRecords.length === 0 ? (
          <p className="text-sm text-ink-muted">No records yet, so there&apos;s nothing to preview.</p>
        ) : (
          <ul className="divide-y divide-line rounded-xl border border-line">
            {withRecords.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span>{d.name}</span>
                <Button variant="secondary" disabled={props.busy} onClick={() => props.onPreview(d)}>
                  {props.busy ? "Reading…" : "Preview"}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {datasets && empty > 0 && <p className="text-xs text-ink-muted">{fmtInt(empty)} empty record types hidden.</p>}
        <ErrorBox message={error ?? props.error} />
      </Card>

      <NavRow onBack={props.onBack}>
        <Button onClick={props.onNext}>Continue</Button>
      </NavRow>
    </section>
  );
}

// Consultant-only: the client's consultations, and the buttons that decide
// whether the FREE first call has been used (api/services/consultations.py).
//
// Shown under the lavender "You're working on ..." banner. It answers:
//   * Has this client had their free 30-minute consultation?
//   * Is something booked (from the Cal.com webhook)? Did a meeting end and
//     need my confirmation?
// THE RULE: the free call is used up only when YOU press "Confirm it happened"
// on a free consultation. After that the client's booking links switch to
// the paid ($80/hour) Cal.com link. "No-show" and "Canceled" keep it free.
// "Add a call by hand" covers calls booked before the webhook was set up.

"use client";

import { useState } from "react";
import { markConsultation } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type { Consultation, ConsultationStatus, ConsultationSummary } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const STATUS_TEXT: Record<ConsultationStatus, string> = {
  booked: "Booked",
  ended: "Ended — please confirm",
  completed: "Completed",
  no_show: "No-show",
  canceled: "Canceled",
};

function when(c: Consultation): string {
  return c.starts_at ? fmtDateTime(c.starts_at) : c.confirmed_at ? fmtDateTime(c.confirmed_at) : "no time on file";
}

export default function ConsultationPanel(props: {
  token: string;
  summary: ConsultationSummary;
  freeMinutes: number;
  onChanged: () => void;
}) {
  const { summary } = props;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  async function mark(status: ConsultationStatus, extra: { consultation_id?: string; kind?: "free" | "paid" } = {}) {
    setBusy(true);
    setError(null);
    try {
      await markConsultation(props.token, { status, ...extra });
      setAdding(false);
      props.onChanged(); // reload the plan, which carries the new summary
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  }

  // Calls that still need a decision from you.
  const open = summary.history.filter((c) => c.status === "booked" || c.status === "ended");

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Consultations</h2>
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            summary.free_used ? "bg-surface-2 text-ink-muted" : "bg-good/15 text-good"
          }`}
        >
          {summary.free_used ? "Free consultation used — paid link shown" : `Free ${props.freeMinutes}-minute consultation available`}
        </span>
      </div>

      {open.map((c) => (
        <div key={c.id} className="rounded-xl border border-line p-3 text-sm">
          <p>
            <strong>{c.kind === "free" ? "Free consultation" : "Paid session"}</strong> · {when(c)} ·{" "}
            <span className={c.status === "ended" ? "font-semibold text-warn" : ""}>{STATUS_TEXT[c.status]}</span>
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button disabled={busy} onClick={() => mark("completed", { consultation_id: c.id })}>
              Confirm it happened
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => mark("no_show", { consultation_id: c.id })}>
              No-show
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => mark("canceled", { consultation_id: c.id })}>
              Canceled
            </Button>
          </div>
        </div>
      ))}

      {!adding ? (
        <button type="button" className="text-sm text-brand hover:underline" onClick={() => setAdding(true)}>
          Add a call by hand
        </button>
      ) : (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-dashed border-line p-3 text-sm">
          <span>Record a call that already happened:</span>
          <Button disabled={busy} onClick={() => mark("completed", { kind: "free" })}>
            Free consultation done
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => mark("completed", { kind: "paid" })}>
            Paid session done
          </Button>
          <button type="button" className="text-ink-muted hover:text-ink" onClick={() => setAdding(false)}>
            Cancel
          </button>
        </div>
      )}

      {summary.history.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-ink-muted">History ({summary.history.length})</summary>
          <ul className="mt-2 space-y-1">
            {summary.history.map((c) => (
              <li key={c.id}>
                {c.kind === "free" ? "Free" : "Paid"} · {when(c)} · {STATUS_TEXT[c.status]}
                <span className="text-ink-muted"> ({c.source === "calcom" ? "Cal.com" : "entered by hand"})</span>
                {c.status === "completed" && c.kind === "free" && (
                  <button
                    type="button"
                    className="ml-2 text-xs text-ink-muted underline hover:text-ink"
                    disabled={busy}
                    onClick={() => mark("booked", { consultation_id: c.id })}
                  >
                    undo
                  </button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="text-xs text-ink-muted">
        {summary.webhook_configured
          ? "Bookings, reschedules and cancellations arrive from Cal.com automatically."
          : "Cal.com webhook not set up yet (CALCOM_WEBHOOK_SECRET), so bookings don't appear here automatically — use “Add a call by hand”."}
        {!summary.paid_link_configured && " PAID_BOOKING_URL isn't set, so clients keep seeing your free link."}
      </p>
      <ErrorBox message={error} />
    </Card>
  );
}

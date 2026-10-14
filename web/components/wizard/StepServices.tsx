// Step 4 — "What does your company need help with?"
// Pick one or more services. Data cleaning is always included (it's the
// automatic first step). The price estimate refreshes whenever the selection
// changes; services that need a person show the booking link.
// "Continue to download" first asks the person to confirm the selection and
// the estimated total they can expect to pay.

"use client";

import { useEffect, useState } from "react";
import { getQuote } from "@/lib/api";
import { categoryColor } from "@/lib/categories";
import { fmtUsd } from "@/lib/format";
import type { Quote, Service } from "@/lib/types";
import { QuoteSummary } from "./ServiceConfirm";
import { Button, Card, ConfirmDialog, ConsultantCta, ErrorBox, NavRow, StepHeader } from "./ui";

export default function StepServices(props: {
  services: Service[];
  selected: string[];
  onChange: (ids: string[]) => void;
  runtimeSeconds: number;
  bookingUrl: string;
  onBack: () => void;
  onNext: () => void;
}) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  // Re-price whenever the selection changes.
  useEffect(() => {
    let cancelled = false;
    getQuote(props.selected, props.runtimeSeconds)
      .then((q) => !cancelled && setQuote(q))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true; // ignore a slow response if the user clicked again
    };
  }, [props.selected, props.runtimeSeconds]);

  const toggle = (id: string) => {
    if (id === "data_cleaning") return; // always included
    props.onChange(props.selected.includes(id) ? props.selected.filter((s) => s !== id) : [...props.selected, id]);
  };

  return (
    <section>
      <StepHeader
        title="What would you like help with next?"
        subtitle="Choose as many as you like — or none. Your cleaned data is ready to download either way."
      />

      <div className="grid gap-3 sm:grid-cols-2">
        {props.services.map((s) => {
          const on = props.selected.includes(s.id);
          const price =
            s.pricing_model === "runtime"
              ? "Pay per minute of processing"
              : s.pricing_model === "consultation"
                ? "Free discovery call"
                : s.pricing_model === "early_access"
                  ? "Early access · no charge"
                  : `From ${fmtUsd(s.starting_price_usd)}`;
          const color = categoryColor(s.id);
          return (
            <button
              key={s.id}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(s.id)}
              // The left border carries the category color (like a budget category).
              style={{ borderLeftColor: color }}
              className={`relative rounded-2xl border border-l-4 p-4 text-left shadow-[var(--shadow-card)] transition-colors ${
                on ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand"
              } ${s.id === "data_cleaning" ? "cursor-default" : ""}`}
            >
              <div className="flex items-start justify-between gap-2">
                <span className="flex flex-wrap items-center gap-2 font-semibold">
                  {s.name}
                  {!s.available && (
                    <span
                      className="rounded-full px-2 py-0.5 text-[11px] font-medium"
                      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 15%, transparent)` }}
                    >
                      Early access
                    </span>
                  )}
                </span>
                <input type="checkbox" readOnly checked={on} tabIndex={-1} className="mt-1 accent-[var(--color-brand)]" />
              </div>
              <p className="mt-1 text-sm text-ink-muted">{s.description}</p>
              {s.capabilities.length > 0 && (
                <ul aria-label="What's included" className="mt-3 flex flex-wrap gap-1.5">
                  {s.capabilities.map((c) => (
                    <li
                      key={c}
                      className="rounded-full px-2 py-0.5 text-[11px]"
                      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 12%, transparent)` }}
                    >
                      {c}
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 font-mono text-xs font-medium">
                {price}
                {s.id === "data_cleaning" && " · included"}
              </p>
            </button>
          );
        })}
      </div>

      {quote && (
        <Card className="mt-6">
          <h2 className="mb-3 font-semibold">Estimate</h2>
          <ul className="divide-y divide-line text-sm">
            {quote.lines.map((l) => (
              <li key={l.service_id} className="flex justify-between gap-4 py-2">
                <span>
                  {l.name}
                  <span className="block text-xs text-ink-muted">{l.note}</span>
                </span>
                <span className="font-mono tabular-nums">
                  {l.pricing_model === "consultation" || l.pricing_model === "early_access" ? "—" : fmtUsd(l.amount_usd)}
                </span>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex justify-between border-t border-line pt-3 font-semibold">
            <span>Estimated starting total</span>
            <span className="font-mono tabular-nums">{fmtUsd(quote.estimated_total_usd)}</span>
          </div>
        </Card>
      )}

      {quote?.requires_consultant && (
        <div className="mt-4">
          <ConsultantCta bookingUrl={quote.booking_url ?? props.bookingUrl}>
            <strong>Some of these start with a conversation.</strong> Book a time and we&apos;ll scope it together.
          </ConsultantCta>
        </div>
      )}

      <ErrorBox message={error} />

      <NavRow onBack={props.onBack}>
        {/* If pricing failed to load, don't trap the person here: go on without the box. */}
        <Button onClick={() => (quote ? setConfirming(true) : props.onNext())} disabled={!quote && !error}>
          Continue to download
        </Button>
      </NavRow>

      {confirming && quote && (
        <ConfirmDialog
          title="Confirm your selection"
          confirmLabel={`Confirm · ${fmtUsd(quote.estimated_total_usd)}`}
          onConfirm={() => {
            setConfirming(false);
            props.onNext();
          }}
          onCancel={() => setConfirming(false)}
        >
          <p>Here&apos;s what you&apos;ve chosen and what you can expect to pay:</p>
          <QuoteSummary quote={quote} />
          <p className="text-xs text-ink-muted">
            Data Cleaning is charged when you download (you&apos;ll confirm that amount too). Package prices are starting
            prices — we confirm the final price before any work begins.
            {quote.requires_consultant && " Some services start with a free call to scope the work."} Nothing is
            charged on this step.
          </p>
        </ConfirmDialog>
      )}
    </section>
  );
}

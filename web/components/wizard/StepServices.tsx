// Step 4 — "What does your company need help with?"
// Pick one or more services. Data cleaning is always included (it's the
// automatic first step). The price estimate refreshes whenever the selection
// changes; services that need a person show the booking link.

"use client";

import { useEffect, useState } from "react";
import { getQuote } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import type { Quote, Service } from "@/lib/types";
import { Button, Card, ConsultantCta, ErrorBox, NavRow, StepHeader } from "./ui";

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
                : `From ${fmtUsd(s.starting_price_usd)}`;
          return (
            <button
              key={s.id}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(s.id)}
              className={`rounded-lg border p-4 text-left transition-colors ${
                on ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand"
              } ${s.id === "data_cleaning" ? "cursor-default" : ""}`}
            >
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium">{s.name}</span>
                <input type="checkbox" readOnly checked={on} tabIndex={-1} className="mt-1 accent-[var(--color-brand)]" />
              </div>
              <p className="mt-1 text-sm text-ink-muted">{s.description}</p>
              <p className="mt-2 text-xs font-medium">
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
                <span className="tabular-nums">{l.pricing_model === "consultation" ? "—" : fmtUsd(l.amount_usd)}</span>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex justify-between border-t border-line pt-3 font-semibold">
            <span>Estimated starting total</span>
            <span className="tabular-nums">{fmtUsd(quote.estimated_total_usd)}</span>
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
        <Button onClick={props.onNext}>Continue to download</Button>
      </NavRow>
    </section>
  );
}

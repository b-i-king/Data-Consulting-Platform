// Confirmations for choosing services, so nobody adds something without
// seeing what it will cost.
//
//   QuoteSummary       the price lines + estimated total from POST /api/quote
//                      (api/services/pricing.py build_quote)
//   AddServiceConfirm  "Add Data Visualization?" — shown when a locked
//                      service's Add button is clicked (wizard and
//                      workspace). It prices the plan WITH the new service
//                      so the person sees the new total before confirming.
//
// Package prices are starting prices (the final price is set once the work is
// scoped); consultation services start with a free call; Data Cleaning is
// billed by processing time when you download.

"use client";

import { useEffect, useState } from "react";
import { getQuote } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import type { Quote, Service } from "@/lib/types";
import { priceLabel } from "@/components/explore/LockedService";
import { ConfirmDialog, TotalRow } from "./ui";

/** The price list for a quote, ending in the estimated total. */
export function QuoteSummary({ quote, highlight }: { quote: Quote; highlight?: string }) {
  return (
    <div className="space-y-1 rounded-xl bg-surface-2 p-3">
      {quote.lines.map((l) => (
        <TotalRow
          key={l.service_id}
          label={
            <span className={l.service_id === highlight ? "font-semibold" : ""}>
              {l.name}
              {l.service_id === highlight && " (new)"}
              <span className="block text-xs font-normal text-ink-muted">{l.note}</span>
            </span>
          }
          value={l.pricing_model === "consultation" || l.pricing_model === "early_access" ? "—" : fmtUsd(l.amount_usd)}
        />
      ))}
      <TotalRow label="Estimated total" value={fmtUsd(quote.estimated_total_usd)} strong />
    </div>
  );
}

export function AddServiceConfirm(props: {
  service: Service;
  /** Services already chosen (Data Cleaning is always added by the server). */
  currentIds: string[];
  /** Processing time measured on the cleaning, used to price Data Cleaning. */
  runtimeSeconds: number;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { service } = props;
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ids = props.currentIds.includes(service.id) ? props.currentIds : [...props.currentIds, service.id];
    getQuote(ids, props.runtimeSeconds)
      .then((q) => !cancelled && setQuote(q))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "We couldn't price that."));
    return () => {
      cancelled = true;
    };
  }, [service.id, props.currentIds, props.runtimeSeconds]);

  return (
    <ConfirmDialog
      title={`Add ${service.name}?`}
      confirmLabel={`Add · ${priceLabel(service)}`}
      onConfirm={props.onConfirm}
      onCancel={props.onCancel}
    >
      <p>{service.description}</p>
      {quote ? (
        <>
          <p className="font-medium">Your plan with {service.name}:</p>
          <QuoteSummary quote={quote} highlight={service.id} />
        </>
      ) : (
        <p className="text-ink-muted">{error ?? "Working out your new total…"}</p>
      )}
      <p className="text-xs text-ink-muted">
        Package prices are starting prices; we confirm the final price with you before any work begins. Nothing is
        charged when you add a service.
      </p>
    </ConfirmDialog>
  );
}

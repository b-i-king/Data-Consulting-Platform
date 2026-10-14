// Confirmations for choosing services, so nobody adds something without
// seeing what it will cost.
//
//   QuoteSummary       the price lines + estimated total from POST /api/quote
//                      (api/services/pricing.py build_quote)
//   AddServiceConfirm  "Add Dashboards to your package?" — shown when a locked
//                      tool's Add button is clicked in the guest wizard. It
//                      prices the PACKAGE with the new tool (each tool's fixed
//                      price, added up). For a non-tool (Data Strategy &
//                      Discovery) it just records interest for the consultant.
//
// Package prices are starting prices (the final price is set once the work is
// scoped); consulting starts with a free 30-minute call, then the hourly rate
// (Data Strategy & Discovery is paid from the first hour); Data Cleaning is
// billed by processing time when you download.

"use client";

import { fmtUsd } from "@/lib/format";
import type { BillingInterval, Quote, Service, Subscription } from "@/lib/types";
import { normalizePackage, packageTotal } from "@/components/billing/PackageBuilder";
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
  /** Every priced item (tools + bundle), from /api/intake/options. */
  plans: Subscription[];
  /** The package being built on "Your needs" so far. */
  currentPlans: string[];
  interval: BillingInterval;
  onConfirm: (nextPackage: string[]) => void;
  onCancel: () => void;
}) {
  const { service, plans, interval } = props;
  const isTool = plans.some((p) => p.id === service.id);
  // The package with this tool (and anything it needs, e.g. AI -> Data Engineering).
  const next = isTool ? normalizePackage(plans, [...props.currentPlans, ...service.tool_requires, service.id]) : props.currentPlans;
  const per = interval === "month" ? "month" : "year";

  return (
    <ConfirmDialog
      title={isTool ? `Add ${service.name} to your package?` : `Interested in ${service.name}?`}
      confirmLabel={isTool ? `Add · ${priceLabel(service)}` : "Yes, I'm interested"}
      onConfirm={() => props.onConfirm(next)}
      onCancel={props.onCancel}
    >
      <p>{service.description}</p>
      {isTool ? (
        <>
          <div className="space-y-1 rounded-xl bg-surface-2 p-3">
            {next.map((id) => {
              const p = plans.find((x) => x.id === id)!;
              return (
                <TotalRow
                  key={id}
                  label={<span className={id === service.id ? "font-semibold" : ""}>{p.name}{id === service.id ? " (new)" : ""}</span>}
                  value={fmtUsd(p.billable ? (interval === "month" ? p.monthly_usd : p.annual_usd) : 0)}
                />
              );
            })}
            <TotalRow strong label={`Package total per ${per}`} value={fmtUsd(packageTotal(plans, next, interval))} />
          </div>
          <p className="text-xs text-ink-muted">
            Try it now in this preview; you&apos;ll start the package after creating your account. Cancel anytime.
          </p>
        </>
      ) : (
        <p className="text-xs text-ink-muted">{service.consultation.text}. We&apos;ll note your interest for the consultant.</p>
      )}
    </ConfirmDialog>
  );
}

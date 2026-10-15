// Step 4 — "What does your company need help with?"
// Pick one or more services. Data cleaning is always included (it's the
// automatic first step). The price estimate refreshes whenever the selection
// changes; services that need a person show the booking link.
// "Continue to download" first asks the person to confirm the selection and
// the estimated total they can expect to pay.
//
// Two ways to work, above the services:
//   * BUILD YOUR OWN PACKAGE — pick tools (each has a fixed monthly / yearly
//     price; the total is the sum) or the All tools bundle; cancel anytime
//     (components/billing/PackageBuilder.tsx). It starts after signup, on the
//     download step (Stripe Checkout).
//   * DONE FOR YOU — a consultant (lavender card) signs in and does the work.

"use client";

import { useEffect, useState } from "react";
import { getQuote } from "@/lib/api";
import { categoryColor } from "@/lib/categories";
import { fmtUsd } from "@/lib/format";
import type { BillingInterval, Quote, Service, Subscription, SubscriptionChoice } from "@/lib/types";
import PackageBuilder, { packageTotal } from "@/components/billing/PackageBuilder";
import { QuoteSummary } from "./ServiceConfirm";
import { Button, Card, ConfirmDialog, ConsultantCta, ErrorBox, NavRow, StepHeader, TotalRow } from "./ui";

export default function StepServices(props: {
  services: Service[];
  selected: string[];
  onChange: (ids: string[]) => void;
  runtimeSeconds: number;
  bookingUrl: string;
  subscriptions: Subscription[];
  subscription: SubscriptionChoice | null;
  onSubscription: (s: SubscriptionChoice | null) => void;
  consultantName: string | null;
  consultingRate: number;
  wantsConsultant: boolean;
  onWantsConsultant: (v: boolean) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const picked = props.subscription?.plans ?? [];
  const pickedNames = picked.map((id) => props.subscriptions.find((p) => p.id === id)?.name).filter(Boolean);
  const [interval, setBilling] = useState<BillingInterval>(props.subscription?.interval ?? "year");
  const pickInterval = (i: BillingInterval) => {
    setBilling(i);
    if (props.subscription) props.onSubscription({ ...props.subscription, interval: i });
  };
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

      <div className="mb-8">
        <PackageBuilder
          plans={props.subscriptions}
          interval={interval}
          onInterval={pickInterval}
          value={picked}
          onChange={(plans) => props.onSubscription(plans.length ? { plans, interval } : null)}
          // A new account has no stored data yet, and AI works on stored data.
          locked={{ ai: "Add it after your data is stored (Data Engineering)" }}
          consultantSlot={
            // "Done for you": wide, in lavender, right under All tools.
            <div
              className={`flex flex-col gap-3 rounded-2xl border-2 p-5 shadow-[var(--shadow-card)] sm:flex-row sm:items-center sm:justify-between ${
                props.wantsConsultant ? "border-consultant bg-consultant-soft" : "border-consultant/40 bg-surface"
              }`}
            >
              <div>
                <span className="text-lg font-semibold">Done for you</span>
                <p className="mt-1 text-sm text-ink-muted">
                  Rather not learn a new platform? {props.consultantName ?? "A consultant"} signs in to your account and
                  does the cleaning, dashboards and analysis for you — every tool included. Start with a free 30-minute
                  consultation; after that, consulting is {fmtUsd(props.consultingRate)}/hour.
                </p>
              </div>
              <button
                type="button"
                aria-pressed={props.wantsConsultant}
                onClick={() => props.onWantsConsultant(!props.wantsConsultant)}
                className={`shrink-0 rounded-full px-5 py-2 text-sm font-semibold ${
                  props.wantsConsultant ? "border border-line bg-surface" : "bg-consultant text-on-consultant hover:opacity-90"
                }`}
              >
                {props.wantsConsultant ? "I'll do it myself" : `Have ${props.consultantName ?? "a consultant"} do it`}
              </button>
            </div>
          }
        />
      </div>

      <h2 className="font-semibold">Projects with a consultant (optional)</h2>
      <p className="mb-2 text-sm text-ink-muted">
        One-time work done for you — tell us what you&apos;d like help with. Priced after your free 30-minute consultation.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {props.services.map((s) => {
          const on = props.selected.includes(s.id);
          const price =
            s.pricing_model === "runtime"
              ? "Pay per minute of processing"
              : s.pricing_model === "consultation" || s.pricing_model === "hourly"
                ? s.consultation.text
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
              {/* Every service can start with a consultant: the free 30 minutes, then hourly. */}
              {s.pricing_model !== "consultation" && s.pricing_model !== "hourly" && s.consultation.text && (
                <p className="mt-0.5 text-xs text-ink-muted">{s.consultation.text}</p>
              )}
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
          {props.subscription && pickedNames.length > 0 && (
            <TotalRow
              strong
              label={`Your package: ${pickedNames.join(" + ")} (${props.subscription.interval === "month" ? "monthly" : "yearly"}, cancel anytime — you'll pay after creating your account)`}
              value={`${fmtUsd(packageTotal(props.subscriptions, picked, props.subscription.interval))}/${props.subscription.interval === "month" ? "mo" : "yr"}`}
            />
          )}
          {props.wantsConsultant && (
            <p className="rounded-xl bg-consultant-soft p-3">
              <strong>Done for you:</strong> {props.consultantName ?? "a consultant"} will reach out for your free
              30-minute consultation; after that, consulting is {fmtUsd(props.consultingRate)}/hour.
            </p>
          )}
          <QuoteSummary quote={quote} />
          <p className="text-xs text-ink-muted">
            Data Cleaning is charged when you download (you&apos;ll confirm that amount too). Package prices are starting
            prices — we confirm the final price before any work begins.
            {quote.requires_consultant && " Consulting starts with a free 30-minute call (Data Strategy & Discovery is paid from the first hour)."} Nothing is
            charged on this step.
          </p>
        </ConfirmDialog>
      )}
    </section>
  );
}

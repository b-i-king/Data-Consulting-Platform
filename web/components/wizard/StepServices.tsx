// Step 4 — "Your needs": what would you like to do next?
//
// THREE PATHS (one card each; the choice decides what "Next steps" offers):
//   1. JUST DOWNLOAD     — the cleaned data, nothing else. Data Cleaning is
//                          charged by the minute when you download.
//   2. DO IT MYSELF      — build a monthly / yearly package of tools (or All
//                          tools), cancel anytime (components/billing/PackageBuilder.tsx).
//                          It starts after signup, on "Next steps" (Stripe Checkout;
//                          switched on at once in test mode).
//   3. HELP FROM A CONSULTANT (lavender) — a ONE-TIME analysis of everything you
//                          sent, or MONTHLY help. Pick a consultant by name: the fill
//                          bars show how full each one is (components/consultants/
//                          ConsultantPicker.tsx). Optional: the projects you'd like help
//                          with. Starts with a free 30-minute consultation.
// "Continue" first asks the person to confirm the choice and what to expect to pay.
//
// THE SAME RULES AS THE WORKSPACE, by where the data is (`source`):
//   files     every option.
//   cloud     "Just download" is grayed out (cleaning only REPORTS on data that
//             lives in their database — the database stays the source), and the
//             package notes that storing a copy is off (Data Engineering's
//             diagrams and dictionaries still work on it).
//   business  "Just download" grayed out the same way; Data Engineering is
//             where the stored sync lives (history, trends, combining sources).

"use client";

import { useEffect, useState } from "react";
import { getQuote } from "@/lib/api";
import { categoryColor } from "@/lib/categories";
import { fmtUsd } from "@/lib/format";
import type { BillingInterval, ConsultantLoad, ConsultantPlan, Quote, Service, Subscription, SubscriptionChoice } from "@/lib/types";
import PackageBuilder, { packageTotal } from "@/components/billing/PackageBuilder";
import ConsultantPicker, { PLAN_LABELS } from "@/components/consultants/ConsultantPicker";
import { QuoteSummary } from "./ServiceConfirm";
import type { DataSource } from "./StepDataLocation";
import { Button, Card, ConfirmDialog, ConsultantCta, ErrorBox, NavRow, StepHeader, TotalRow } from "./ui";

/** The three ways forward from "Your needs". */
export type NextPath = "download" | "package" | "consultant";

const PATHS: { id: NextPath; emoji: string; title: string; note: string }[] = [
  { id: "download", emoji: "⬇️", title: "Just download", note: "Get your cleaned data as CSV or Excel. Pay only for the processing minutes." },
  { id: "package", emoji: "🧰", title: "Do it myself", note: "A monthly or yearly package of tools — dashboards, analysis, storage. Cancel anytime." },
  { id: "consultant", emoji: "🤝", title: "Help from a consultant", note: "A one-time analysis, or monthly help. Pick who, by name. Starts with a free call." },
];

export default function StepServices(props: {
  services: Service[];
  selected: string[];
  onChange: (ids: string[]) => void;
  runtimeSeconds: number;
  bookingUrl: string;
  subscriptions: Subscription[];
  subscription: SubscriptionChoice | null;
  onSubscription: (s: SubscriptionChoice | null) => void;
  consultingRate: number;
  path: NextPath | null;
  onPath: (p: NextPath) => void;
  consultants: ConsultantLoad[];
  consultantId: string | null;
  onConsultantId: (id: string | null) => void;
  consultantPlan: ConsultantPlan;
  onConsultantPlan: (p: ConsultantPlan) => void;
  onBack: () => void;
  onNext: () => void;
  /** Where the data is: decides which options apply (see the top of this file). */
  source?: DataSource;
}) {
  const source = props.source ?? "files";
  // Why an option doesn't apply on this path (shown on the grayed-out card).
  const offWhy: Partial<Record<NextPath, string>> =
    source === "files"
      ? {}
      : {
          download:
            source === "cloud"
              ? "Cleaning fixes need files from your computer. Your database stays the source."
              : "Cleaning fixes need files. Export a file from your app to use this.",
        };
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
  const consultantName = props.consultants.find((c) => c.id === props.consultantId)?.name ?? null;

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
  // "Do it myself" needs at least one tool picked before going on; a grayed-out path can't be chosen.
  const ready = props.path !== null && !offWhy[props.path] && (props.path !== "package" || picked.length > 0);

  return (
    <section>
      <StepHeader
        title="What would you like to do next?"
        subtitle={
          source === "files"
            ? "Your cleaned data is ready. Choose one — you can change your mind later."
            : "Your data is connected. Choose one — you can change your mind later."
        }
      />

      <div role="radiogroup" aria-label="Next step" className="grid gap-3 sm:grid-cols-3">
        {PATHS.map((p) => {
          const on = props.path === p.id;
          const lavender = p.id === "consultant";
          const off = offWhy[p.id];
          return (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={on}
              aria-disabled={!!off}
              disabled={!!off}
              onClick={() => props.onPath(p.id)}
              className={`rounded-2xl border-2 p-4 text-left shadow-[var(--shadow-card)] transition-colors ${
                off
                  ? "cursor-not-allowed border-dashed border-line bg-surface opacity-60"
                  : on
                  ? lavender
                    ? "border-consultant bg-consultant-soft"
                    : "border-brand bg-brand-soft"
                  : lavender
                    ? "border-consultant/40 bg-surface hover:border-consultant"
                    : "border-line bg-surface hover:border-brand"
              }`}
            >
              <span aria-hidden className="text-2xl">
                {p.emoji}
              </span>
              <span className="mt-1 block font-semibold">{p.title}</span>
              <span className="mt-1 block text-sm text-ink-muted">{off ?? p.note}</span>
            </button>
          );
        })}
      </div>

      {props.path === "download" && (
        <Card className="mt-6 text-sm">
          <p>
            <strong>Next:</strong> create a free account and download. Estimated processing charge:{" "}
            <span className="font-mono">{quote ? fmtUsd(quote.estimated_total_usd) : "…"}</span> — you&apos;ll confirm
            it before anything is charged.
          </p>
        </Card>
      )}

      {props.path === "package" && source !== "files" && (
        <Card className="mt-6 text-sm">
          {source === "cloud" ? (
            <p>
              <strong>Your data stays in your database.</strong> Every tool works on it there; storing a copy with us is
              off for cloud data. Data Engineering&apos;s diagrams and data dictionaries still work on it.
            </p>
          ) : (
            <p>
              <strong>The quick look is free.</strong> Add <strong>Data Engineering</strong> to store your records on a
              schedule — your full history, trends over the years, and combining them with your other sources.
            </p>
          )}
        </Card>
      )}

      {props.path === "package" && (
        <div className="mt-6">
          <PackageBuilder
            plans={props.subscriptions}
            interval={interval}
            onInterval={pickInterval}
            value={picked}
            onChange={(plans) => props.onSubscription(plans.length ? { plans, interval } : null)}
            // A new account has no stored data yet, and AI works on stored data.
            locked={{ ai: "Add it after your data is stored (Data Engineering)" }}
          />
        </div>
      )}

      {props.path === "consultant" && (
        <div className="mt-6 space-y-6">
          <Card className="border-consultant/40">
            <h2 className="mb-1 font-semibold">Your consultant</h2>
            <p className="mb-4 text-sm text-ink-muted">
              No new platform to learn: your consultant signs in to your account and does the cleaning, dashboards and
              analysis for you — every tool included. It starts with a free 30-minute consultation; after that,
              consulting is {fmtUsd(props.consultingRate)}/hour.
            </p>
            <ConsultantPicker
              consultants={props.consultants}
              value={props.consultantId}
              onChange={props.onConsultantId}
              plan={props.consultantPlan}
              onPlan={props.onConsultantPlan}
              // Choosing "Help from a consultant" slides the consultant panel in.
              autoOpen={!props.consultantId}
            />
          </Card>

          <div>
            <h2 className="font-semibold">What would you like help with? (optional)</h2>
            <p className="mb-2 text-sm text-ink-muted">Tell your consultant where to start. Priced after your free consultation.</p>
            <div className="grid gap-3 sm:grid-cols-2">
              {props.services.map((s) => {
                const on = props.selected.includes(s.id);
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
                    {s.id === "data_cleaning" && <p className="mt-2 font-mono text-xs font-medium">Included</p>}
                  </button>
                );
              })}
            </div>
          </div>

          {quote?.requires_consultant && (
            <ConsultantCta bookingUrl={quote.booking_url ?? props.bookingUrl}>
              <strong>Want to talk first?</strong> Book your free 30-minute consultation now, or after you sign up.
            </ConsultantCta>
          )}
        </div>
      )}

      <ErrorBox message={error} />

      <NavRow onBack={props.onBack}>
        {/* If pricing failed to load, don't trap the person here: go on without the box. */}
        <Button onClick={() => (quote ? setConfirming(true) : props.onNext())} disabled={!ready || (!quote && !error)}>
          Continue to next steps
        </Button>
      </NavRow>

      {confirming && quote && (
        <ConfirmDialog
          title="Confirm your choice"
          confirmLabel="Confirm"
          onConfirm={() => {
            setConfirming(false);
            props.onNext();
          }}
          onCancel={() => setConfirming(false)}
        >
          {props.path === "download" && <p>You&apos;ll create a free account, then download your cleaned data.</p>}
          {props.path === "package" && props.subscription && pickedNames.length > 0 && (
            <TotalRow
              strong
              label={`Your package: ${pickedNames.join(" + ")} (${props.subscription.interval === "month" ? "monthly" : "yearly"}, cancel anytime — you'll pay after creating your account)`}
              value={`${fmtUsd(packageTotal(props.subscriptions, picked, props.subscription.interval))}/${props.subscription.interval === "month" ? "mo" : "yr"}`}
            />
          )}
          {props.path === "consultant" && (
            <p className="rounded-xl bg-consultant-soft p-3">
              <strong>{PLAN_LABELS[props.consultantPlan].title}</strong> with {consultantName ?? "whoever has room"}. Your
              consultant will reach out for your free 30-minute consultation; after that, consulting is{" "}
              {fmtUsd(props.consultingRate)}/hour. On the next page you can share your cleaned data with them.
            </p>
          )}
          <QuoteSummary quote={quote} />
          <p className="text-xs text-ink-muted">
            Data Cleaning is charged when you download (you&apos;ll confirm that amount too). Nothing is charged on this step.
          </p>
        </ConfirmDialog>
      )}
    </section>
  );
}

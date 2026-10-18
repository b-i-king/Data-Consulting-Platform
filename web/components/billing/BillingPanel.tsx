// "Plan & billing" in the workspace: subscriptions, cancel anytime, booking a consultant.
//
//   Your package      the live subscription and its tools (dim.subscriptions,
//                     its tools in `items`, kept in sync by Stripe's webhook),
//                     with "Manage billing" — Stripe's own portal to change the
//                     card, see invoices or CANCEL ANYTIME.
//   Build / change    PackageBuilder: pick tools (prices add up) or All tools,
//                     monthly or yearly -> Stripe Checkout, or — if they already
//                     subscribe — the subscription's tools change in place
//                     (Stripe prorates the difference).
//   (Connected data and its refresh schedule moved to Settings -> Integrations.)
//   Consulting        ONE booking button: the free 30-minute first consultation
//                     until a consultant confirms it happened, then the paid
//                     Cal.com link. Paid sessions are booked AND paid on Cal.com
//                     (usually one hour at a time), so nothing about them is
//                     bought or stored here (api/services/consultations.py).
//
// OFFERS on a FIRST package (api/services/offers.py): a free-trial banner
// (trial days set per tool in Admin -> Prices; one trial per account), or —
// when no trial applies — the seasonal sale running today, shown in the
// banner and as a line in the confirmation.
// Everything that costs money asks for confirmation first, with the total.
// TEST MODE (no Stripe key on the server): choices take effect at once with no
// charge, and a banner says so. A consultant working on a client (X-Acting-As)
// sees and changes the CLIENT's billing.

"use client";

import { useState } from "react";
import { cancelPlanTestMode, openBillingPortal, startCheckout } from "@/lib/api";
import { fmtDate, fmtUsd } from "@/lib/format";
import type { BillingInterval, MyServices } from "@/lib/types";
import { Card, ConfirmDialog, ErrorBox, TotalRow } from "@/components/wizard/ui";
import PackageBuilder, { normalizePackage, packageTotal } from "./PackageBuilder";

export default function BillingPanel(props: {
  token: string;
  plan: MyServices;
  bookingUrl: string;
  /** Re-load the plan after a change made here. */
  onChanged: () => Promise<void>;
  /** A tool to start ticked (from an "Add … to my package" button elsewhere). */
  suggestTool?: string | null;
}) {
  const { plan } = props;
  const stripe = plan.billing.stripe;
  const live = plan.subscriptions[0] ?? null;
  // The package being built: starts as the current one.
  const [interval, setBilling] = useState<BillingInterval>(live?.interval ?? "year");
  const [picked, setPicked] = useState<string[]>(() =>
    normalizePackage(plan.plans, [...(live?.plan_ids ?? []), ...(props.suggestTool ? [props.suggestTool] : [])]),
  );
  const [asking, setAsking] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rate = plan.consulting.rate_per_hour_usd;
  const next = normalizePackage(plan.plans, picked);
  const nextTotal = packageTotal(plan.plans, next, interval);
  const unchanged = !!live && live.interval === interval && [...live.plan_ids].sort().join() === [...next].sort().join();
  const nextNames = next.map((id) => plan.plans.find((p) => p.id === id)?.name).join(" + ");
  // A FIRST package's offer (api/services/offers.py): a free trial if they qualify
  // (the longest trial among the tools picked), otherwise today's seasonal sale.
  const offer = plan.offer;
  const trialDays = !live && offer?.trial_eligible
    ? Math.max(0, ...next.map((id) => plan.plans.find((p) => p.id === id)?.trial_days ?? 0))
    : 0;
  const sale = !live && !trialDays ? offer?.sale ?? null : null;
  const bestTrial = offer?.trial_eligible ? Math.max(0, ...plan.plans.map((p) => p.trial_days ?? 0)) : 0;

  /** Run a billing action: go to Stripe's page, or (test mode) refresh. */
  async function act(run: () => Promise<{ url: string | null }>, done: string) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const res = await run();
      if (res.url) {
        window.location.assign(res.url); // Stripe's secure page; it sends them back to /workspace/settings/billing
        return;
      }
      await props.onChanged();
      setNote(done);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  // An admin's own workspace: every tool is free, so there's nothing to buy.
  // (Working on a client, an admin sees and manages the CLIENT's billing.)
  if (plan.admin && !plan.acting) {
    return (
      <Card>
        <h2 className="font-semibold">Admin: every tool is included, free</h2>
        <p className="mt-1 text-sm text-ink-muted">
          As an admin you have all seven services — AI included — with free downloads, in your own workspace and in any
          client&apos;s. Consultants and clients pay for their own packages.
        </p>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {!stripe && (
        <p role="status" className="rounded-2xl border border-warn/40 bg-warn/10 p-3 text-sm">
          {plan.billing.tester ? (
            <>
              <strong>Tester account:</strong> you can try any package for free — it takes effect right away and
              nothing is charged. Downloads are free too.
            </>
          ) : (
            <>
              <strong>Test mode:</strong> payments aren&apos;t switched on yet, so packages take effect right away
              and nothing is charged.
            </>
          )}
        </p>
      )}

      {!live && (bestTrial > 0 || offer?.sale) && (
        <p role="status" className="rounded-2xl border border-brand/40 bg-brand-soft p-3 text-sm">
          {bestTrial > 0 ? (
            <>
              <strong>Free trial:</strong> your first package starts with up to {bestTrial} days free — nothing is charged
              until the trial ends, and you can cancel before then.
            </>
          ) : (
            offer?.sale && (
              <>
                <strong>{offer.sale.name}:</strong> {offer.sale.percent_off}% off your first payment (the first month, or
                the first year on annual billing) until {fmtDate(offer.sale.ends)}.
              </>
            )
          )}
        </p>
      )}

      <Card>
        <h2 className="font-semibold">Your package</h2>
        {!live ? (
          <p className="mt-1 text-sm text-ink-muted">No package yet — you pay per minute for cleaning downloads.</p>
        ) : (
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>
              <strong>{live.names.join(" + ")}</strong> · {fmtUsd(live.total_usd)}/{live.interval === "year" ? "year" : "month"}
              <span className="block text-xs text-ink-muted">
                {live.cancel_at_period_end
                  ? `Canceled — active until ${live.current_period_end ?? "the end of the period"}`
                  : live.status === "past_due"
                    ? "Payment failed — update your card"
                    : live.current_period_end
                      ? `Renews ${live.current_period_end}`
                      : "Active"}
              </span>
            </span>
            {!stripe && plan.can_add && (
              <button type="button" className="text-xs text-ink-muted underline hover:text-bad" onClick={() => setCanceling(true)}>
                Cancel anytime
              </button>
            )}
          </div>
        )}
        {plan.downloads_included && <p className="mt-2 text-xs text-good">Cleaning downloads are included in your package.</p>}
        {stripe && plan.can_add && live && (
          <button
            type="button"
            disabled={busy}
            onClick={() => act(() => openBillingPortal(props.token), "")}
            className="mt-3 rounded-full border border-line px-4 py-1.5 text-sm font-semibold hover:border-brand"
          >
            Manage billing · cancel anytime
          </button>
        )}
      </Card>

      {plan.can_add ? (
        <div>
          <h2 className="mb-2 font-semibold">{live ? "Change your package" : "Build your package"}</h2>
          <PackageBuilder
            plans={plan.plans}
            interval={interval}
            onInterval={setBilling}
            value={picked}
            onChange={setPicked}
            actionLabel={live ? "Update package" : "Subscribe"}
            actionDisabled={busy || unchanged}
            onAction={() => setAsking(true)}
            // AI answers questions about STORED data, so it unlocks once data is loaded.
            locked={plan.ai_ready ? undefined : { ai: "Load your data first (Data Engineering → Load into database)" }}
          />
        </div>
      ) : (
        <p className="text-sm text-ink-muted">Your team&apos;s owner manages the package.</p>
      )}



      <Card>
        <h2 className="font-semibold">Talk to a consultant</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Your first {plan.consulting.free_minutes}-minute consultation is free (Data Strategy &amp; Discovery is paid
          from the start). After that, sessions are {fmtUsd(rate)}/hour — you book and pay on the booking page.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <a
            href={props.bookingUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-full border border-line px-4 py-1.5 text-sm font-semibold hover:border-brand"
          >
            {plan.consulting.free_used
              ? `Book a consulting session (${fmtUsd(rate)}/hour)`
              : `Book the free ${plan.consulting.free_minutes}-minute call`}
          </a>
        </div>
      </Card>

      {note && (
        <p role="status" className="text-sm text-good">
          {note}
        </p>
      )}
      <ErrorBox message={error} />

      {asking && next.length > 0 && (
        <ConfirmDialog
          title={live ? "Update your package?" : "Subscribe to this package?"}
          confirmLabel={stripe && !live ? "Continue to secure payment" : live ? "Update package" : "Subscribe (test mode)"}
          onCancel={() => setAsking(false)}
          onConfirm={() => {
            setAsking(false);
            act(() => startCheckout(props.token, next, interval), live ? "Your package is updated." : "Your package is on.");
          }}
        >
          <div className="space-y-1 rounded-xl bg-surface-2 p-3">
            {next.map((id) => {
              const p = plan.plans.find((x) => x.id === id)!;
              return (
                <TotalRow
                  key={id}
                  label={p.name + (p.billable ? "" : " (free until it launches)")}
                  value={fmtUsd(p.billable ? (interval === "month" ? p.monthly_usd : p.annual_usd) : 0)}
                />
              );
            })}
            <TotalRow strong label={`Total ${interval === "month" ? "per month" : "per year"}`} value={fmtUsd(nextTotal)} />
            {sale && (
              <TotalRow
                label={`${sale.name}: ${sale.percent_off}% off your first payment`}
                value={`−${fmtUsd(Math.round(nextTotal * sale.percent_off) / 100)}`}
              />
            )}
          </div>
          {trialDays > 0 && (
            <p className="text-sm">
              <strong>Starts with a {trialDays}-day free trial.</strong> Nothing is charged for the first {trialDays} days;
              cancel before then and you pay nothing.
            </p>
          )}
          <p className="text-xs text-ink-muted">
            {nextNames}. Renews automatically; cancel anytime — you keep access until the end of the period you paid for.
            {live && stripe && " Changes are prorated: you're charged (or credited) the difference right away."}
            {!live && stripe && " You'll pay on Stripe's secure page; your card details never reach our server."}
          </p>
        </ConfirmDialog>
      )}

      {canceling && (
        <ConfirmDialog
          title="Cancel your package?"
          confirmLabel="Cancel the package"
          onCancel={() => setCanceling(false)}
          onConfirm={() => {
            setCanceling(false);
            setPicked([]);
            act(async () => {
              await cancelPlanTestMode(props.token);
              return { url: null };
            }, "Package canceled.");
          }}
        >
          <p>No fees and no questions. Your downloaded files are yours to keep.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

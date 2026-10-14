// "Plan & billing" in the workspace: subscriptions, cancel anytime, consulting hours.
//
//   Your plans        live subscriptions (table `subscriptions`, kept in sync by
//                     Stripe's webhook), with "Manage billing" — Stripe's own
//                     portal to change the card, see invoices or CANCEL ANYTIME.
//   Choose a plan     the plans, monthly or yearly -> Stripe Checkout.
//   Consulting        the free 30-minute first consultation (booking link), and
//                     prepaid hours at the hourly rate -> Stripe Checkout.
//
// Everything that costs money asks for confirmation first, with the total.
// TEST MODE (no Stripe key on the server): choices take effect at once with no
// charge, and a banner says so. A consultant working on a client (X-Acting-As)
// sees and changes the CLIENT's billing.

"use client";

import { useState } from "react";
import { buyConsultingHours, cancelPlanTestMode, openBillingPortal, startCheckout } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import type { BillingInterval, MyServices, SubscriptionChoice } from "@/lib/types";
import { Card, ConfirmDialog, ErrorBox, TotalRow } from "@/components/wizard/ui";
import PlanPicker from "./PlanPicker";

export default function BillingPanel(props: {
  token: string;
  plan: MyServices;
  bookingUrl: string;
  /** Re-load the plan after a change made here. */
  onChanged: () => Promise<void>;
}) {
  const { plan } = props;
  const stripe = plan.billing.stripe;
  const [interval, setBilling] = useState<BillingInterval>("year");
  const [asking, setAsking] = useState<SubscriptionChoice | null>(null);
  const [canceling, setCanceling] = useState<string | null>(null);
  const [hours, setHours] = useState("1");
  const [askHours, setAskHours] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rate = plan.consulting.rate_per_hour_usd;
  const asked = asking && plan.plans.find((p) => p.id === asking.plan);
  const n = Math.max(1, Math.min(40, Number(hours) || 1));

  /** Run a billing action: go to Stripe's page, or (test mode) refresh. */
  async function act(run: () => Promise<{ url: string | null }>, done: string) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const res = await run();
      if (res.url) {
        window.location.assign(res.url); // Stripe's secure page; it sends them back to /app
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

  return (
    <div className="space-y-6">
      {!stripe && (
        <p role="status" className="rounded-2xl border border-warn/40 bg-warn/10 p-3 text-sm">
          <strong>Test mode:</strong> payments aren&apos;t switched on yet, so plans and hours take effect right away and
          nothing is charged.
        </p>
      )}

      <Card>
        <h2 className="font-semibold">Your plans</h2>
        {plan.subscriptions.length === 0 ? (
          <p className="mt-1 text-sm text-ink-muted">No plan yet — you pay per minute for cleaning downloads.</p>
        ) : (
          <ul className="mt-2 divide-y divide-line text-sm">
            {plan.subscriptions.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <strong>{s.name}</strong> · {s.interval === "year" ? "yearly" : "monthly"}
                  <span className="block text-xs text-ink-muted">
                    {s.cancel_at_period_end
                      ? `Canceled — active until ${s.current_period_end ?? "the end of the period"}`
                      : s.status === "past_due"
                        ? "Payment failed — update your card"
                        : s.current_period_end
                          ? `Renews ${s.current_period_end}`
                          : "Active"}
                  </span>
                </span>
                {!stripe && (
                  <button type="button" className="text-xs text-ink-muted underline hover:text-bad" onClick={() => setCanceling(s.plan_id)}>
                    Cancel anytime
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
        {plan.downloads_included && <p className="mt-2 text-xs text-good">Cleaning downloads are included in your plan.</p>}
        {stripe && plan.can_add && (
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
          <h2 className="mb-2 font-semibold">Choose a plan</h2>
          <PlanPicker
            plans={plan.plans}
            interval={interval}
            onInterval={setBilling}
            owned={plan.subscriptions.map((s) => s.plan_id)}
            onPick={setAsking}
            actionLabel="Subscribe"
            busy={busy}
          />
        </div>
      ) : (
        <p className="text-sm text-ink-muted">Your team&apos;s owner manages the plan.</p>
      )}

      <Card>
        <h2 className="font-semibold">Talk to a consultant</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Your first {plan.consulting.free_minutes}-minute consultation is free (Data Strategy &amp; Discovery is paid
          from the start). After that, consulting is {fmtUsd(rate)}/hour, prepaid here.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <a
            href={props.bookingUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-full border border-line px-4 py-1.5 text-sm font-semibold hover:border-brand"
          >
            Book the free {plan.consulting.free_minutes}-minute call
          </a>
          {plan.can_add && (
            <>
              <label className="text-sm">
                <span className="block text-xs text-ink-muted">Hours</span>
                <input
                  inputMode="numeric"
                  value={hours}
                  onChange={(e) => setHours(e.target.value.replace(/\D/g, "").slice(0, 2))}
                  className="w-20 rounded-xl border border-line bg-surface px-2 py-1.5 text-sm"
                />
              </label>
              <button
                type="button"
                disabled={busy}
                onClick={() => setAskHours(true)}
                className="rounded-full bg-brand px-4 py-1.5 text-sm font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50"
              >
                Buy {n} hour{n === 1 ? "" : "s"} · {fmtUsd(n * rate)}
              </button>
            </>
          )}
        </div>
        {plan.consulting.hours_bought > 0 && (
          <p className="mt-2 text-xs text-ink-muted">Hours bought so far: {plan.consulting.hours_bought}</p>
        )}
      </Card>

      {note && (
        <p role="status" className="text-sm text-good">
          {note}
        </p>
      )}
      <ErrorBox message={error} />

      {asking && asked && (
        <ConfirmDialog
          title={`Subscribe to ${asked.name}?`}
          confirmLabel={stripe ? "Continue to secure payment" : "Subscribe (test mode)"}
          onCancel={() => setAsking(null)}
          onConfirm={() => {
            const choice = asking;
            setAsking(null);
            act(() => startCheckout(props.token, choice.plan, choice.interval), `${asked.name} is on.`);
          }}
        >
          <div className="space-y-1 rounded-xl bg-surface-2 p-3">
            <TotalRow
              strong
              label={`${asked.name} (${asking.interval === "month" ? "monthly" : "yearly"})`}
              value={asking.interval === "month" ? `${fmtUsd(asked.monthly_usd)}/month` : `${fmtUsd(asked.annual_usd)}/year`}
            />
          </div>
          <p className="text-xs text-ink-muted">
            Renews automatically. Cancel anytime — you keep access until the end of the period you paid for.
            {stripe && " You'll pay on Stripe's secure page; your card details never reach our server."}
          </p>
        </ConfirmDialog>
      )}

      {askHours && (
        <ConfirmDialog
          title="Buy consulting hours?"
          confirmLabel={stripe ? "Continue to secure payment" : "Buy (test mode)"}
          onCancel={() => setAskHours(false)}
          onConfirm={() => {
            setAskHours(false);
            act(() => buyConsultingHours(props.token, n), `${n} consulting hour${n === 1 ? "" : "s"} added.`);
          }}
        >
          <div className="space-y-1 rounded-xl bg-surface-2 p-3">
            <TotalRow label={`${n} hour${n === 1 ? "" : "s"} × ${fmtUsd(rate)}`} value={fmtUsd(n * rate)} />
            <TotalRow strong label="Total you'll pay" value={fmtUsd(n * rate)} />
          </div>
        </ConfirmDialog>
      )}

      {canceling && (
        <ConfirmDialog
          title="Cancel this plan?"
          confirmLabel="Cancel the plan"
          onCancel={() => setCanceling(null)}
          onConfirm={() => {
            const id = canceling;
            setCanceling(null);
            act(async () => {
              await cancelPlanTestMode(props.token, id);
              return { url: null };
            }, "Plan canceled.");
          }}
        >
          <p>No fees and no questions. Your downloaded files are yours to keep.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

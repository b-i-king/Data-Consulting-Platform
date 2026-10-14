// The subscription plans, side by side, with a Monthly / Yearly switch.
//
// Each plan is a Stripe product with two prices (api/services/pricing.py
// SUBSCRIPTIONS; scripts/sync_stripe.py puts them in Stripe). Yearly is
// cheaper ("2 months free"), and every plan can be canceled anytime.
//
// Used on the wizard's "Your needs" step (pick one to start after signup) and
// in the workspace's billing panel (pick one to go to checkout).

"use client";

import { fmtUsd } from "@/lib/format";
import type { BillingInterval, Subscription, SubscriptionChoice } from "@/lib/types";

export default function PlanPicker(props: {
  plans: Subscription[];
  interval: BillingInterval;
  onInterval: (i: BillingInterval) => void;
  /** The chosen plan (wizard), if any. */
  selected?: string | null;
  /** Plans the account already has (shown as "Your plan"). */
  owned?: string[];
  onPick: (choice: SubscriptionChoice) => void;
  /** Button text, e.g. "Choose" or "Subscribe". */
  actionLabel?: string;
  busy?: boolean;
}) {
  const { plans, interval } = props;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink-muted">Do it yourself with a plan — cancel anytime, no contract.</p>
        <div role="radiogroup" aria-label="Billing" className="flex gap-1 rounded-full border border-line bg-surface p-0.5 text-xs">
          {(["month", "year"] as const).map((i) => (
            <button
              key={i}
              type="button"
              role="radio"
              aria-checked={interval === i}
              onClick={() => props.onInterval(i)}
              className={`rounded-full px-3 py-1 ${interval === i ? "bg-brand font-semibold text-on-brand" : "text-ink-muted"}`}
            >
              {i === "month" ? "Monthly" : `Yearly · save ${plans[0]?.annual_savings_pct ?? 17}%`}
            </button>
          ))}
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {plans.map((p) => {
          const owned = props.owned?.includes(p.id);
          const chosen = props.selected === p.id;
          return (
            <div
              key={p.id}
              className={`flex flex-col rounded-2xl border p-4 shadow-[var(--shadow-card)] ${
                chosen || owned ? "border-brand bg-brand-soft" : "border-line bg-surface"
              }`}
            >
              <span className="font-semibold">{p.name}</span>
              <p className="mt-1 flex-1 text-xs text-ink-muted">{p.description}</p>
              <p className="mt-3 font-mono text-lg font-semibold tabular-nums">
                {interval === "month" ? fmtUsd(p.monthly_usd) : fmtUsd(p.annual_usd)}
                <span className="text-xs font-normal text-ink-muted">/{interval === "month" ? "month" : "year"}</span>
              </p>
              <p className="text-xs text-ink-muted">
                {interval === "year"
                  ? `${fmtUsd(p.annual_per_month_usd)}/month · save ${fmtUsd(p.annual_savings_usd)}`
                  : `or ${fmtUsd(p.annual_usd)}/year`}
              </p>
              <ul className="mt-2 space-y-0.5 text-xs">
                {p.includes.map((i) => (
                  <li key={i}>✓ {i}</li>
                ))}
              </ul>
              {owned ? (
                <p className="mt-3 text-xs font-semibold text-brand">Your plan</p>
              ) : (
                <button
                  type="button"
                  disabled={props.busy}
                  aria-pressed={chosen}
                  onClick={() => props.onPick({ plan: p.id, interval })}
                  className={`mt-3 rounded-full px-4 py-1.5 text-sm font-semibold disabled:opacity-50 ${
                    chosen ? "border border-line bg-surface" : "bg-brand text-on-brand hover:bg-brand-strong"
                  }`}
                >
                  {chosen ? "Remove" : (props.actionLabel ?? "Choose")}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

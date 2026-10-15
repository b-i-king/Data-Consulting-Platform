// "Plan & billing" in the workspace: subscriptions, cancel anytime, consulting hours.
//
//   Your package      the live subscription and its tools (tables `subscriptions`
//                     + `subscription_items`, kept in sync by Stripe's webhook),
//                     with "Manage billing" — Stripe's own portal to change the
//                     card, see invoices or CANCEL ANYTIME.
//   Build / change    PackageBuilder: pick tools (prices add up) or All tools,
//                     monthly or yearly -> Stripe Checkout, or — if they already
//                     subscribe — the subscription's tools change in place
//                     (Stripe prorates the difference).
//   Connected data    DataRefresh: how often each connector (QuickBooks) pulls
//                     new data — daily by default, hourly/live are add-ons.
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
import type { BillingInterval, MyServices } from "@/lib/types";
import { Card, ConfirmDialog, ErrorBox, TotalRow } from "@/components/wizard/ui";
import DataRefresh from "./DataRefresh";
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
  const [hours, setHours] = useState("1");
  const [askHours, setAskHours] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rate = plan.consulting.rate_per_hour_usd;
  const next = normalizePackage(plan.plans, picked);
  const nextTotal = packageTotal(plan.plans, next, interval);
  const unchanged = !!live && live.interval === interval && [...live.plan_ids].sort().join() === [...next].sort().join();
  const nextNames = next.map((id) => plan.plans.find((p) => p.id === id)?.name).join(" + ");
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
          <strong>Test mode:</strong> payments aren&apos;t switched on yet, so packages and hours take effect right away
          and nothing is charged.
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
          />
        </div>
      ) : (
        <p className="text-sm text-ink-muted">Your team&apos;s owner manages the package.</p>
      )}

      <DataRefresh token={props.token} canChange={plan.can_add} />

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
          </div>
          <p className="text-xs text-ink-muted">
            {nextNames}. Renews automatically; cancel anytime — you keep access until the end of the period you paid for.
            {live && stripe && " Changes are prorated: you're charged (or credited) the difference on your next invoice."}
            {!live && stripe && " You'll pay on Stripe's secure page; your card details never reach our server."}
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

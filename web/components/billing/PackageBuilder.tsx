// "Build your own package" — pick a bundle and/or tools, see the price add up.
//
//   [ Monthly | Yearly ]
//   [ Starter $19 ] [ Growth $49 ] [ Pro (all tools) $99 ]     <- bundles, cheaper than one by one
//   ┌──────────────────────────── Done for you (consultant, optional slot) ───┐
//   └──────────────────────────────────────────────────────────────────────────┘
//   [ Data Cleaning ] [ Exploration & Analysis ] [ Dashboards ]
//   [ Data Engineering ] [ Data Science & ML ]  [ AI (needs Data Engineering) ]
//   Your package: Starter + Data Science & ML ............ $58.00/month [Subscribe]
//
// Rules (the server checks them too: api/services/pricing.py normalize_selection):
//   * each item has a fixed monthly and annual price; the total is the sum
//   * at most ONE bundle (picking another switches to it); a bundle's tools are
//     never bought twice — they show "Included in <bundle>"; tools it doesn't
//     cover can be added on (Pro covers everything)
//   * without a bundle, Data Cleaning is the base: in every package
//   * AI is grayed out until Data Engineering is picked or covered; a tool whose
//     service isn't launched yet is shown "free until it launches"
//   * when what's picked costs more than a bundle that covers it, a hint offers the bundle

"use client";

import type { ReactNode } from "react";
import { fmtUsd } from "@/lib/format";
import type { BillingInterval, Subscription } from "@/lib/types";

/** The tools a picked item gives: a bundle's `covers`, or the tool itself. */
const gives = (plans: Subscription[], id: string): string[] => {
  const p = plans.find((x) => x.id === id);
  return p?.kind === "bundle" ? p.covers ?? [] : [id];
};

/** Same rules as the server: one bundle at most, its tools dropped, add-ons kept, the base without a bundle. */
export function normalizePackage(plans: Subscription[], picked: string[]): string[] {
  if (!picked.length) return [];
  const bundles = plans.filter((p) => p.kind === "bundle" && picked.includes(p.id));
  // The biggest bundle wins (most tools covered, then the higher price).
  const bundle = bundles.sort((a, b) => (b.covers?.length ?? 0) - (a.covers?.length ?? 0) || b.monthly_usd - a.monthly_usd)[0];
  let set: Set<string>;
  if (bundle) {
    const covered = new Set(bundle.covers ?? []);
    set = new Set([bundle.id, ...picked.filter((id) => plans.find((p) => p.id === id)?.kind === "tool" && !covered.has(id))]);
  } else {
    const base = plans.find((p) => p.base)?.id;
    set = new Set([...picked, ...(base ? [base] : [])]);
  }
  // Drop anything whose requirement isn't picked or covered (e.g. AI without Data Engineering).
  const have = new Set([...set].flatMap((id) => gives(plans, id)));
  for (const p of plans) if (set.has(p.id) && p.requires.some((r) => !have.has(r))) set.delete(p.id);
  return plans.filter((p) => set.has(p.id)).map((p) => p.id);
}

/** The total for a package (items not billable yet count $0). */
export function packageTotal(plans: Subscription[], picked: string[], interval: BillingInterval): number {
  return normalizePackage(plans, picked).reduce((sum, id) => {
    const p = plans.find((x) => x.id === id);
    return sum + (p && p.billable ? (interval === "month" ? p.monthly_usd : p.annual_usd) : 0);
  }, 0);
}

const per = (i: BillingInterval) => (i === "month" ? "/month" : "/year");

export default function PackageBuilder(props: {
  plans: Subscription[];
  interval: BillingInterval;
  onInterval: (i: BillingInterval) => void;
  /** What's picked ([] = no package; a bundle id, tools, or a bundle + add-on tools). */
  value: string[];
  onChange: (picked: string[]) => void;
  /** The "Done for you" consultant card, shown wide under the bundles. */
  consultantSlot?: ReactNode;
  /** Button on the summary bar (e.g. "Subscribe"); omitted = no button. */
  actionLabel?: string;
  onAction?: () => void;
  actionDisabled?: boolean;
  /**
   * Tools that can't be picked yet, with the reason shown on the card, e.g.
   * {ai: "Load your data first"} — AI needs stored data (the server checks it
   * too: billing.ai_ready). A tool already in the package stays picked.
   */
  locked?: Record<string, string>;
}) {
  const { plans, interval } = props;
  const bundles = plans.filter((p) => p.kind === "bundle");
  const tools = plans.filter((p) => p.kind === "tool");
  const picked = normalizePackage(plans, props.value);
  const bundle = bundles.find((b) => picked.includes(b.id)) ?? null;
  const covered = new Set(bundle?.covers ?? []);
  const have = new Set(picked.flatMap((id) => gives(plans, id)));
  const total = packageTotal(plans, picked, interval);
  const price = (p: Subscription) => (interval === "month" ? p.monthly_usd : p.annual_usd);
  // The cheapest bundle that covers everything picked, if it costs less (same as the server's hint).
  const cheaper = bundles
    .filter((b) => !picked.includes(b.id) && [...have].every((t) => b.covers?.includes(t)) && price(b) < total)
    .sort((a, b) => price(a) - price(b))[0];

  function chooseBundle(b: Subscription) {
    if (picked.includes(b.id)) return props.onChange(picked.filter((x) => x !== b.id));
    // Switching bundles keeps the add-ons the new bundle doesn't cover.
    props.onChange(normalizePackage(plans, [b.id, ...picked.filter((x) => plans.find((p) => p.id === x)?.kind === "tool")]));
  }

  function toggle(p: Subscription) {
    if (covered.has(p.id)) return;                                  // already in the bundle: never twice
    const on = picked.includes(p.id);
    if (!bundle && p.base && on) {
      // The base can only go when it's the only thing picked (= no package).
      return props.onChange(picked.length === 1 ? [] : picked);
    }
    props.onChange(normalizePackage(plans, on ? picked.filter((x) => x !== p.id) : [...picked, p.id]));
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink-muted">Pick a bundle, or build your own from the tools below. Cancel anytime.</p>
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
              {i === "month" ? "Monthly" : `Yearly · save ${bundles[0]?.annual_savings_pct ?? 17}%`}
            </button>
          ))}
        </div>
      </div>

      {/* The bundles: cheaper than the same tools one by one. */}
      <div role="group" aria-label="Bundles" className="grid gap-3 lg:grid-cols-3">
        {bundles.map((b, i) => {
          const on = picked.includes(b.id);
          return (
            <div key={b.id} className={`flex flex-col rounded-2xl border-2 p-4 shadow-[var(--shadow-card)] ${
              on ? "border-brand bg-brand-soft" : "border-brand/40 bg-surface"}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-lg font-semibold">{b.name}</span>
                {i === bundles.length - 1 && (
                  <span className="rounded-full bg-brand px-2 py-0.5 text-[11px] font-semibold text-on-brand">Best value</span>
                )}
              </div>
              <p className="mt-1 flex-1 text-sm text-ink-muted">{b.description}</p>
              <p className="mt-1 text-xs text-ink-muted">{b.includes.join(" · ")}</p>
              <p className="mt-3 font-mono text-2xl font-semibold tabular-nums">
                {fmtUsd(price(b))}
                <span className="text-xs font-normal text-ink-muted">{per(interval)}</span>
              </p>
              {b.bundle_savings_month_usd > 0 && (
                <p className="text-xs text-good">
                  Save {fmtUsd(interval === "month" ? b.bundle_savings_month_usd : b.bundle_savings_month_usd * 10)}
                  {per(interval)} vs. one by one
                </p>
              )}
              <button
                type="button"
                aria-pressed={on}
                onClick={() => chooseBundle(b)}
                className={`mt-2 rounded-full px-5 py-1.5 text-sm font-semibold ${
                  on ? "border border-line bg-surface" : "bg-brand text-on-brand hover:bg-brand-strong"}`}
              >
                {on ? "Remove" : `Choose ${b.name}`}
              </button>
            </div>
          );
        })}
      </div>

      {props.consultantSlot}

      {/* The six tools: 3 × 2 on wide screens. Covered by the bundle = shown included, not clickable. */}
      <div role="group" aria-label="Tools" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {tools.map((p) => {
          const inBundle = covered.has(p.id);
          const on = inBundle || picked.includes(p.id);
          const lockedWhy = !on ? props.locked?.[p.id] : undefined;
          const blocked = inBundle || (p.requires.some((r) => !have.has(r))) || !!lockedWhy;
          const needs = p.requires.map((r) => plans.find((x) => x.id === r)?.name ?? r).join(", ");
          return (
            <button
              key={p.id}
              type="button"
              role="checkbox"
              aria-checked={on}
              aria-disabled={blocked}
              disabled={blocked}
              onClick={() => toggle(p)}
              className={`flex flex-col rounded-2xl border p-4 text-left shadow-[var(--shadow-card)] transition-colors disabled:cursor-not-allowed ${
                inBundle ? "border-brand bg-brand-soft" : blocked ? "border-line bg-surface opacity-50"
                  : on ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand"
              }`}
            >
              <span className="flex items-start justify-between gap-2">
                <span className="font-semibold">{p.name}</span>
                <span
                  aria-hidden
                  className={`grid h-5 w-5 shrink-0 place-items-center rounded-md border text-xs ${
                    on ? "border-brand bg-brand text-on-brand" : "border-line"
                  }`}
                >
                  {on ? "✓" : blocked ? "🔒" : ""}
                </span>
              </span>
              <span className="mt-1 flex-1 text-xs text-ink-muted">{p.description}</span>
              <span className="mt-3 font-mono font-semibold tabular-nums">
                {inBundle ? "Included" : fmtUsd(price(p))}
                {!inBundle && <span className="text-xs font-normal text-ink-muted">{per(interval)}</span>}
              </span>
              <span className="text-xs text-ink-muted">
                {inBundle
                  ? `Included in ${bundle?.name}`
                  : p.base && !bundle
                    ? "Always included in a package"
                    : lockedWhy
                      ? lockedWhy
                      : blocked
                        ? `Needs ${needs}`
                        : !p.billable
                          ? "Coming soon — free until it launches"
                          : bundle
                            ? `Add-on to ${bundle.name}`
                            : ""}
              </span>
            </button>
          );
        })}
      </div>

      {/* Running total. */}
      <div className="flex flex-col gap-3 rounded-2xl border border-line bg-surface-2 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-sm">
          {picked.length ? (
            <>
              <span className="text-ink-muted">Your package: </span>
              <strong>{picked.map((id) => plans.find((p) => p.id === id)?.name).join(" + ")}</strong>
              {cheaper && (
                <span className="mt-1 block text-xs text-warn">
                  {cheaper.name} covers all of this for less ({fmtUsd(price(cheaper))}
                  {per(interval)}) —{" "}
                  <button type="button" className="underline" onClick={() => props.onChange([cheaper.id])}>
                    switch to {cheaper.name}
                  </button>
                </span>
              )}
            </>
          ) : (
            <span className="text-ink-muted">No package — cleaning downloads are pay per minute.</span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-xl font-semibold tabular-nums">
            {fmtUsd(total)}
            <span className="text-xs font-normal text-ink-muted">{per(interval)}</span>
          </span>
          {props.actionLabel && (
            <button
              type="button"
              disabled={props.actionDisabled || !picked.length}
              onClick={props.onAction}
              className="rounded-full bg-brand px-5 py-2 text-sm font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50"
            >
              {props.actionLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

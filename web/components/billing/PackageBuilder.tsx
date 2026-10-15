// "Build your own package" — pick tools, see the price add up.
//
//   [ Monthly | Yearly ]
//   ┌──────────────────────────── All tools (wide, stands out) ───────────────┐
//   └──────────────────────────────────────────────────────────────────────────┘
//   ┌──────────────────────────── Done for you (consultant, optional slot) ───┐
//   └──────────────────────────────────────────────────────────────────────────┘
//   [ Data Cleaning ] [ Exploration & Analysis ] [ Dashboards ]
//   [ Data Engineering ] [ Data Science & ML ]  [ AI (needs Data Engineering) ]
//   Your package: Data Cleaning + Dashboards ................ $38.00/month [Subscribe]
//
// Rules (the server checks them too: api/services/pricing.py normalize_selection):
//   * each tool has a fixed monthly and annual price; the total is the sum
//   * Data Cleaning is the base: in every package (it can't be removed while
//     other tools are picked)
//   * AI is grayed out until Data Engineering is picked (it works on stored data),
//     and is free until it launches
//   * All tools is a cheaper bundle; when the picked tools cost more, a hint says so

"use client";

import type { ReactNode } from "react";
import { fmtUsd } from "@/lib/format";
import type { BillingInterval, Subscription } from "@/lib/types";

const BUNDLE = "all_tools";

/** Same rules as the server: bundle alone, or the base + picks (requirements kept). */
export function normalizePackage(plans: Subscription[], picked: string[]): string[] {
  if (picked.includes(BUNDLE)) return [BUNDLE];
  if (!picked.length) return [];
  const base = plans.find((p) => p.base)?.id;
  const set = new Set([...picked, ...(base ? [base] : [])]);
  // Drop anything whose requirement isn't picked (e.g. AI without Data Engineering).
  for (const p of plans) if (set.has(p.id) && p.requires.some((r) => !set.has(r))) set.delete(p.id);
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
  /** The tools picked ([] = no package; ["all_tools"] = the bundle). */
  value: string[];
  onChange: (picked: string[]) => void;
  /** The "Done for you" consultant card, shown wide under All tools. */
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
  const bundle = plans.find((p) => p.kind === "bundle");
  const tools = plans.filter((p) => p.kind === "tool");
  const picked = normalizePackage(plans, props.value);
  const isBundle = picked.includes(BUNDLE);
  const total = packageTotal(plans, picked, interval);
  const bundlePrice = bundle ? (interval === "month" ? bundle.monthly_usd : bundle.annual_usd) : 0;
  const price = (p: Subscription) => (interval === "month" ? p.monthly_usd : p.annual_usd);
  // What every tool would cost picked separately, minus the bundle.
  const bundleSavings = tools.reduce((s, p) => s + price(p), 0) - bundlePrice;

  function toggle(p: Subscription) {
    if (isBundle) return props.onChange(normalizePackage(plans, [p.id]));
    const on = picked.includes(p.id);
    if (p.base && on) {
      // The base can only go when it's the only thing picked (= no package).
      return props.onChange(picked.length === 1 ? [] : picked);
    }
    props.onChange(normalizePackage(plans, on ? picked.filter((x) => x !== p.id) : [...picked, p.id]));
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink-muted">Build your own package — pick the tools you need. Cancel anytime.</p>
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
              {i === "month" ? "Monthly" : `Yearly · save ${bundle?.annual_savings_pct ?? 17}%`}
            </button>
          ))}
        </div>
      </div>

      {/* All tools: wide, so it stands out. */}
      {bundle && (
        <div
          className={`flex flex-col gap-3 rounded-2xl border-2 p-5 shadow-[var(--shadow-card)] sm:flex-row sm:items-center sm:justify-between ${
            isBundle ? "border-brand bg-brand-soft" : "border-brand/50 bg-surface"
          }`}
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-lg font-semibold">{bundle.name}</span>
              <span className="rounded-full bg-brand px-2 py-0.5 text-[11px] font-semibold text-on-brand">Best value</span>
            </div>
            <p className="mt-1 text-sm text-ink-muted">{bundle.description}</p>
            <p className="mt-1 text-xs text-ink-muted">{bundle.includes.join(" · ")}</p>
          </div>
          <div className="shrink-0 sm:text-right">
            <p className="font-mono text-2xl font-semibold tabular-nums">
              {fmtUsd(bundlePrice)}
              <span className="text-xs font-normal text-ink-muted">{per(interval)}</span>
            </p>
            <p className="text-xs text-good">
              Save {fmtUsd(bundleSavings)}
              {per(interval)} vs. picking every tool
            </p>
            <button
              type="button"
              aria-pressed={isBundle}
              onClick={() => props.onChange(isBundle ? [] : [BUNDLE])}
              className={`mt-2 rounded-full px-5 py-1.5 text-sm font-semibold ${
                isBundle ? "border border-line bg-surface" : "bg-brand text-on-brand hover:bg-brand-strong"
              }`}
            >
              {isBundle ? "Remove" : "Choose All tools"}
            </button>
          </div>
        </div>
      )}

      {props.consultantSlot}

      {/* The six tools: 3 × 2 on wide screens. */}
      <div role="group" aria-label="Tools" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {tools.map((p) => {
          const on = isBundle || picked.includes(p.id);
          const lockedWhy = !on ? props.locked?.[p.id] : undefined;
          const blocked = (!isBundle && p.requires.some((r) => !picked.includes(r))) || !!lockedWhy;
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
                blocked ? "border-line bg-surface opacity-50" : on ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand"
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
                  {blocked ? "🔒" : on ? "✓" : ""}
                </span>
              </span>
              <span className="mt-1 flex-1 text-xs text-ink-muted">{p.description}</span>
              <span className="mt-3 font-mono font-semibold tabular-nums">
                {fmtUsd(price(p))}
                <span className="text-xs font-normal text-ink-muted">{per(interval)}</span>
              </span>
              <span className="text-xs text-ink-muted">
                {p.base
                  ? "Always included in a package"
                  : lockedWhy
                    ? lockedWhy
                    : blocked
                      ? `Needs ${needs}`
                      : !p.billable
                        ? "Coming soon — free until it launches"
                        : isBundle
                          ? "Included in All tools"
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
              {!isBundle && total > bundlePrice && (
                <span className="mt-1 block text-xs text-warn">
                  All tools is cheaper ({fmtUsd(bundlePrice)}
                  {per(interval)}) —{" "}
                  <button type="button" className="underline" onClick={() => props.onChange([BUNDLE])}>
                    switch to All tools
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

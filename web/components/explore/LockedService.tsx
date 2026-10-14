// A locked service, shown as a badge the user can choose to add.
//
// Explore only offers the skills (and the dashboard) of services the user
// has chosen — Data Cleaning is always included. Every other service shows up
// as one of these badges instead: its category color, name and starting
// monthly price as a package tool, and a button to add it: in the guest wizard
// it joins the package being built on "Your needs" (and unlocks the preview);
// in the workspace it opens Plan & billing with the tool ticked.

"use client";

import { fmtUsd } from "@/lib/format";
import { categoryColor, categoryEmoji } from "@/lib/categories";
import type { Service } from "@/lib/types";

/** "$19/month", "$80/hour", "Free 30-min call", "Early access", "Included". */
export function priceLabel(s: Service): string {
  if (s.id === "data_cleaning") return "Included";
  if (s.tool_monthly_usd != null) return `${fmtUsd(s.tool_monthly_usd)}/month`;
  if (s.pricing_model === "package") return `from ${fmtUsd(s.starting_price_usd)}`;
  if (s.pricing_model === "consultation") return `Free ${s.consultation.free_minutes}-min call`;
  if (s.pricing_model === "hourly") return `${fmtUsd(s.consultation.rate_per_hour_usd)}/hour`;
  if (s.pricing_model === "early_access") return "Early access";
  return "Included";
}

export function LockedBadge({ service, onAdd }: { service: Service; onAdd: (id: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onAdd(service.id)}
      className="group flex items-center gap-2 rounded-full border border-line bg-surface py-1 pl-2 pr-3 text-sm hover:border-brand"
      title={`Add ${service.name} to unlock its skills`}
    >
      <span aria-hidden className="h-2.5 w-2.5 rounded-full" style={{ background: categoryColor(service.id) }} />
      <span aria-hidden>🔒</span>
      <span className="font-medium">
        <span aria-hidden>{categoryEmoji(service.id)} </span>
        {service.name}
      </span>
      <span className="text-xs text-ink-muted">· {priceLabel(service)}</span>
      <span className="text-xs font-semibold text-brand group-hover:underline">Add</span>
    </button>
  );
}

/** A full-width card for a locked feature (e.g. the dashboard). */
export function LockedCard(props: { service: Service; feature: string; onAdd: (id: string) => void }) {
  const { service } = props;
  return (
    <div className="rounded-2xl border border-dashed border-line bg-surface p-6 text-center">
      <p className="text-2xl" aria-hidden>
        🔒
      </p>
      <p className="mt-2 font-semibold">
        {props.feature} is part of <span style={{ color: categoryColor(service.id) }}>{service.name}</span>
      </p>
      <p className="mx-auto mt-1 max-w-md text-sm text-ink-muted">{service.description}</p>
      <button
        type="button"
        onClick={() => props.onAdd(service.id)}
        className="mt-4 rounded-full bg-brand px-4 py-2 text-sm font-semibold text-on-brand"
      >
        Add {service.name} · {priceLabel(service)}
      </button>
    </div>
  );
}

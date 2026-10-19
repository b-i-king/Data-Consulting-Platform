// "Pick your consultant": how they help (one-time or monthly), and WHO — chosen
// from a PANEL THAT SLIDES IN FROM THE RIGHT with a paginated, scrollable list
// of consultants (first name + a fill bar of how full they are), searchable by
// name. The panel opens on its own when "I need a consultant" is chosen
// (`autoOpen`), and again from "Browse consultants".
//
// The list comes one page at a time from GET /api/accounts/consultants
// (consultants with room first), so it scales to any number of consultants.
//
// The fill bar is clients ÷ capacity (each consultant sets their capacity in
// their workspace; default 3, at most 10):
//   green   under half full
//   yellow  half full or more
//   red     full — can't be picked (the server refuses it too)
// "Whoever has room" leaves the choice to the server (api/services/consultants.py
// pick): the default consultant if they have room, else whoever has the most room.
//
// Used by "Your needs" (wizard), the sign-up form and "Next steps".

"use client";

import { useEffect, useState } from "react";
import { getConsultants } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import type { ConsultantLoad, ConsultantPage, ConsultantPlan, ConsultingCatalog } from "@/lib/types";

const PAGE_SIZE = 8;

/** Fill-bar color for how full a consultant is. */
export function loadColor(c: Pick<ConsultantLoad, "percent" | "full">): string {
  if (c.full) return "bg-bad";
  return c.percent >= 50 ? "bg-warn" : "bg-good";
}

/** A fill bar: "2 of 3 clients", colored by how full. */
export function LoadBar({ load }: { load: Pick<ConsultantLoad, "percent" | "full" | "clients" | "capacity"> }) {
  return (
    <span className="flex items-center gap-2">
      <span
        role="meter"
        aria-valuemin={0}
        aria-valuemax={load.capacity}
        aria-valuenow={load.clients}
        aria-label={`${load.clients} of ${load.capacity} clients`}
        className="h-2 w-24 overflow-hidden rounded-full bg-surface-2"
      >
        <span className={`block h-full rounded-full ${loadColor(load)}`} style={{ width: `${Math.max(load.percent, 4)}%` }} />
      </span>
      <span className="font-mono text-xs tabular-nums text-ink-muted">
        {load.clients}/{load.capacity}
        {load.full ? " · full" : ""}
      </span>
    </span>
  );
}

export const PLAN_LABELS: Record<ConsultantPlan, { title: string; note: string }> = {
  one_time: { title: "One-time setup", note: "Your consultant connects, cleans and loads your data and builds your first dashboard." },
  monthly: { title: "Ongoing help", note: "Your consultant keeps your data, dashboards and answers up to date, every week." },
};

/** The slide-in panel: a paginated, searchable list of consultants. */
export function ConsultantDrawer(props: {
  value: string | null;
  onPick: (c: ConsultantLoad | null) => void;
  onClose: () => void;
}) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [data, setData] = useState<ConsultantPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      getConsultants(page, PAGE_SIZE, q)
        .then((d) => live && setData(d))
        .catch((e) => live && setError(e instanceof Error ? e.message : "We couldn't load the consultants."));
    }, q ? 250 : 0); // wait for a pause in typing before searching
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [page, q]);

  // Escape closes the panel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Choose your consultant">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/40" onClick={props.onClose} />
      <aside className="relative flex h-full w-full max-w-md flex-col border-l border-line bg-surface shadow-[var(--shadow-card)]">
        <div className="flex items-center justify-between border-b border-line p-4">
          <h2 className="text-lg font-semibold">Choose your consultant</h2>
          <button type="button" onClick={props.onClose} className="rounded-full px-3 py-1 text-sm text-ink-muted hover:bg-surface-2">
            Close
          </button>
        </div>
        <div className="border-b border-line p-4">
          <input
            type="search"
            placeholder="Search by name"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
            className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-consultant"
          />
          <p className="mt-2 text-xs text-ink-muted">
            The bar shows how full each consultant is: green has room, yellow is filling up, red is fully booked.
          </p>
        </div>
        {/* The list scrolls on its own; the page buttons stay put at the bottom. */}
        <ul className="flex-1 space-y-2 overflow-y-auto p-4">
          <li>
            <button
              type="button"
              onClick={() => props.onPick(null)}
              className={`flex w-full items-center justify-between rounded-xl border p-3 text-left text-sm ${
                props.value === null ? "border-consultant bg-consultant-soft" : "border-line hover:border-consultant"
              }`}
            >
              <span className="font-semibold">Whoever has room</span>
              <span className="text-xs text-ink-muted">We&apos;ll pick for you</span>
            </button>
          </li>
          {data?.consultants.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                disabled={c.full}
                onClick={() => props.onPick(c)}
                className={`flex w-full items-center justify-between gap-3 rounded-xl border p-3 text-left text-sm ${
                  c.full ? "cursor-not-allowed opacity-60" : "hover:border-consultant"
                } ${props.value === c.id ? "border-consultant bg-consultant-soft" : "border-line"}`}
              >
                <span className="font-semibold">{c.name}</span>
                <LoadBar load={c} />
              </button>
            </li>
          ))}
          {data && data.total === 0 && <li className="text-sm text-ink-muted">No consultants match.</li>}
          {!data && !error && <li className="text-sm text-ink-muted">Loading…</li>}
          {error && <li className="text-sm text-bad">{error}</li>}
        </ul>
        {data && data.pages > 1 && (
          <div className="flex items-center justify-between border-t border-line p-4 text-sm">
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}
              className="rounded-full border border-line px-4 py-1.5 disabled:opacity-40">
              Previous
            </button>
            <span className="text-ink-muted">
              Page {data.page} of {data.pages}
            </span>
            <button type="button" disabled={page >= data.pages} onClick={() => setPage(page + 1)}
              className="rounded-full border border-line px-4 py-1.5 disabled:opacity-40">
              Next
            </button>
          </div>
        )}
      </aside>
    </div>
  );
}

export default function ConsultantPicker(props: {
  consultants: ConsultantLoad[]; // known consultants (to show the chosen one's name)
  /** The consultant picked (null = whoever has room). */
  value: string | null;
  onChange: (id: string | null) => void;
  plan: ConsultantPlan;
  onPlan: (p: ConsultantPlan) => void;
  /** Prices to show under each choice (pricing.consulting_catalog); none = no prices shown. */
  catalog?: ConsultingCatalog;
  /** Open the consultant panel straight away (when "I need a consultant" was just chosen). */
  autoOpen?: boolean;
}) {
  const [open, setOpen] = useState(!!props.autoOpen);
  const [chosen, setChosen] = useState<ConsultantLoad | null>(
    props.consultants.find((c) => c.id === props.value) ?? null,
  );
  const shown = chosen ?? props.consultants.find((c) => c.id === props.value) ?? null;

  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="mb-2 text-sm font-medium">How should they help?</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {(Object.keys(PLAN_LABELS) as ConsultantPlan[]).map((p) => (
            <label
              key={p}
              className={`flex cursor-pointer items-start gap-2 rounded-xl border p-3 text-sm ${
                props.plan === p ? "border-consultant bg-consultant-soft" : "border-line bg-surface"
              }`}
            >
              <input
                type="radio"
                name="consultant-plan"
                className="mt-0.5 accent-[var(--color-consultant)]"
                checked={props.plan === p}
                onChange={() => props.onPlan(p)}
              />
              <span>
                <span className="font-semibold">{PLAN_LABELS[p].title}</span>
                <span className="block text-xs text-ink-muted">{PLAN_LABELS[p].note}</span>
                {props.catalog && (
                  <span className="mt-1 block font-mono text-xs">
                    {p === "one_time"
                      ? `${fmtUsd(props.catalog.setup_usd)} once`
                      : `from ${fmtUsd(props.catalog.min_monthly_usd)}/month, by what you need help with`}
                  </span>
                )}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div>
        <p className="mb-2 text-sm font-medium">Who?</p>
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-consultant/40 bg-surface p-3 text-sm">
          <span className="flex items-center gap-3">
            <span className="font-semibold">{props.value && shown ? shown.name : "Whoever has room"}</span>
            {props.value && shown && <LoadBar load={shown} />}
          </span>
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="rounded-full bg-consultant px-4 py-1.5 text-xs font-semibold text-on-consultant hover:opacity-90"
          >
            Browse consultants
          </button>
        </div>
      </div>

      {open && (
        <ConsultantDrawer
          value={props.value}
          onClose={() => setOpen(false)}
          onPick={(c) => {
            setChosen(c);
            props.onChange(c ? c.id : null);
            setOpen(false);
          }}
        />
      )}
    </div>
  );
}

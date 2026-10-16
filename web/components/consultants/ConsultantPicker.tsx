// "Pick your consultant": every consultant by first name with a FILL BAR of
// how full they are, plus how they help (one-time or monthly).
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

import type { ConsultantLoad, ConsultantPlan } from "@/lib/types";

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
  one_time: { title: "One-time analysis", note: "Your consultant works through everything you sent, once." },
  monthly: { title: "Monthly help", note: "Ongoing: your consultant keeps your data, dashboards and analysis up to date." },
};

export default function ConsultantPicker(props: {
  consultants: ConsultantLoad[];
  /** The consultant picked (null = whoever has room). */
  value: string | null;
  onChange: (id: string | null) => void;
  plan: ConsultantPlan;
  onPlan: (p: ConsultantPlan) => void;
}) {
  const anyRoom = props.consultants.some((c) => !c.full);
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
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-sm font-medium">Who?</legend>
        <ul className="space-y-2">
          <li>
            <label
              className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl border p-3 text-sm ${
                props.value === null ? "border-consultant bg-consultant-soft" : "border-line bg-surface"
              }`}
            >
              <span className="flex items-center gap-2">
                <input
                  type="radio"
                  name="consultant"
                  className="accent-[var(--color-consultant)]"
                  checked={props.value === null}
                  onChange={() => props.onChange(null)}
                />
                <span className="font-semibold">Whoever has room</span>
              </span>
              <span className="text-xs text-ink-muted">{anyRoom ? "We'll pick for you" : "You'll be first in line"}</span>
            </label>
          </li>
          {props.consultants.map((c) => (
            <li key={c.id}>
              <label
                className={`flex items-center justify-between gap-3 rounded-xl border p-3 text-sm ${
                  c.full ? "cursor-not-allowed opacity-60" : "cursor-pointer"
                } ${props.value === c.id ? "border-consultant bg-consultant-soft" : "border-line bg-surface"}`}
              >
                <span className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="consultant"
                    className="accent-[var(--color-consultant)]"
                    checked={props.value === c.id}
                    disabled={c.full}
                    onChange={() => props.onChange(c.id)}
                  />
                  <span className="font-semibold">{c.name}</span>
                </span>
                <LoadBar load={c} />
              </label>
            </li>
          ))}
        </ul>
        {props.consultants.length === 0 && (
          <p className="mt-2 text-xs text-ink-muted">No consultants are set up yet — you&apos;ll be assigned as soon as one is.</p>
        )}
      </fieldset>
    </div>
  );
}

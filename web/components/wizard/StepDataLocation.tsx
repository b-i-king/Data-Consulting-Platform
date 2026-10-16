// Step 2 — "Where is your data stored?"
//
// The answer decides the PATH through Get Started (Wizard.tsx):
//   * Files on my computer                  -> Upload, Clean & preview (as always)
//   * Cloud storage: Supabase / PostgreSQL  -> Account, Connect & choose a table, Preview (read-only)
//   * Business apps: QuickBooks, Xero       -> Account, Connect, Quick look
// An option with a `connector` that works today (api/services/intake.py
// data_locations(): QuickBooks / Xero need the platform's sign-in keys) takes
// the connect path. Everything else (Snowflake, BigQuery, Square...) is
// "coming soon": people can still pick it — that tells you what to build next —
// and are offered a consultation, or a file export (the files path).

"use client";

import type { DataLocation } from "@/lib/types";
import { Button, ChoiceCard, ConsultantCta, NavRow, StepHeader } from "./ui";

/** Which path the answer leads to. */
export type DataSource = "files" | "cloud" | "business";

/** The path for a location + option: connect when the option is a live integration, else files. */
export function sourceFor(locations: DataLocation[], location: string | null, option: string | null): DataSource {
  const loc = locations.find((l) => l.id === location);
  const opt = loc?.options.find((o) => o.id === option);
  if (!opt?.connector || !opt.available) return "files";
  return loc?.id === "business_apps" ? "business" : "cloud";
}

export default function StepDataLocation(props: {
  locations: DataLocation[];
  location: string | null;
  option: string | null;
  bookingUrl: string;
  onChange: (location: string, option: string | null) => void;
  onBack: () => void;
  /** Go on: "files" (upload) or the connect path for the chosen integration. */
  onNext: (source: DataSource) => void;
}) {
  const current = props.locations.find((l) => l.id === props.location);
  const isFlat = current?.id === "flat_files";
  const opt = current?.options.find((o) => o.id === props.option);
  const source = sourceFor(props.locations, props.location, props.option);
  const live = source !== "files";

  return (
    <section>
      <StepHeader title="Where is your data stored today?" subtitle="Pick the closest match — there are no wrong answers." />

      <div className="grid gap-3 sm:grid-cols-2">
        {props.locations.map((loc) => (
          <ChoiceCard
            key={loc.id}
            label={loc.label}
            description={loc.description}
            badge={loc.available ? undefined : "Coming soon"}
            selected={props.location === loc.id}
            onClick={() => props.onChange(loc.id, null)}
          />
        ))}
      </div>

      {/* Second-level choices, e.g. Supabase / Snowflake, QuickBooks / Xero. */}
      {current && current.options.length > 0 && (
        <div className="mt-6">
          <h2 className="mb-2 text-sm font-medium">Which one?</h2>
          <div className="flex flex-wrap gap-2">
            {current.options.map((o) => (
              <button
                key={o.id}
                type="button"
                aria-pressed={props.option === o.id}
                onClick={() => props.onChange(current.id, o.id)}
                className={`rounded-full border px-3 py-1.5 text-sm ${
                  props.option === o.id ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand"
                }`}
              >
                {o.label}
                {o.connector && o.available ? (
                  <span className="ml-1.5 text-xs font-semibold text-good">connect now</span>
                ) : (
                  <span className="ml-1.5 text-xs text-ink-muted">coming soon</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {live && opt && (
        <p className="mt-6 rounded-2xl border border-good/40 bg-good/10 p-3 text-sm">
          <strong>{opt.label} connects directly.</strong> Next you&apos;ll create your account (the connection is saved,
          encrypted, on it), connect read-only, and{" "}
          {source === "business" ? "see a free quick look at your numbers" : "preview a table"}. Nothing is copied.
        </p>
      )}

      {current && !isFlat && !live && (
        <div className="mt-6 space-y-3">
          <ConsultantCta bookingUrl={props.bookingUrl}>
            <strong>{opt ? `${opt.label} is coming soon.` : "Pick the one you use."}</strong> In the meantime, a
            consultant can connect to your data for you.
          </ConsultantCta>
          <p className="text-sm text-ink-muted">Or export a CSV or Excel file from that system and continue on your own.</p>
        </div>
      )}

      <NavRow onBack={props.onBack}>
        <Button onClick={() => props.onNext(source)} disabled={!current}>
          {isFlat || live ? "Continue" : "Continue with a file export"}
        </Button>
      </NavRow>
    </section>
  );
}

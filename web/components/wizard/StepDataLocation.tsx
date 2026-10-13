// Step 1 — "Where is your data stored?"
// Flat files work today. Cloud storage and business-app integrations are
// shown as "coming soon": people can still pick them (that tells you which
// connectors to build next) and are offered a consultation, or they can
// export a file and continue on the flat-file path.

"use client";

import type { DataLocation } from "@/lib/types";
import { Button, ChoiceCard, ConsultantCta, NavRow, StepHeader } from "./ui";

export default function StepDataLocation(props: {
  locations: DataLocation[];
  location: string | null;
  option: string | null;
  bookingUrl: string;
  onChange: (location: string, option: string | null) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const current = props.locations.find((l) => l.id === props.location);
  const isFlat = current?.id === "flat_files";

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

      {/* Second-level choices, e.g. Snowflake / BigQuery, QuickBooks / Gusto. */}
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
              </button>
            ))}
          </div>
        </div>
      )}

      {current && !isFlat && (
        <div className="mt-6 space-y-3">
          <ConsultantCta bookingUrl={props.bookingUrl}>
            <strong>Direct connections are on the way.</strong> In the meantime, a consultant can connect to
            your data for you.
          </ConsultantCta>
          <p className="text-sm text-ink-muted">
            Or export a CSV or Excel file from that system and continue on your own.
          </p>
        </div>
      )}

      <NavRow onBack={props.onBack}>
        <Button onClick={props.onNext} disabled={!current}>
          {isFlat ? "Continue" : "Continue with a file export"}
        </Button>
      </NavRow>
    </section>
  );
}

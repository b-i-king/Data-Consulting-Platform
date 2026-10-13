// Step 0 — "Are you an individual or a business?"
// The answer decides which signup fields we show later (business accounts
// get a company name and team members). Businesses also answer the optional
// industry questions here, up front in the funnel (see IndustryPicker).

"use client";

import type { AccountType, Choice, Industry, IntakeOptions } from "@/lib/types";
import IndustryPicker from "./IndustryPicker";
import { Button, ChoiceCard, NavRow, StepHeader } from "./ui";

export default function StepAccountType(props: {
  choices: Choice[];
  value: AccountType | null;
  onChange: (v: AccountType) => void;
  industries: IntakeOptions["industries"];
  industry: Industry;
  onIndustryChange: (v: Industry) => void;
  onNext: () => void;
}) {
  return (
    <section>
      <StepHeader
        title="First, who are we helping?"
        subtitle="No account needed yet. You can do everything up to the download as a guest."
      />
      <div className="grid gap-3 sm:grid-cols-2">
        {props.choices.map((c) => (
          <ChoiceCard
            key={c.id}
            label={c.label}
            description={c.description}
            selected={props.value === c.id}
            onClick={() => props.onChange(c.id as AccountType)}
          />
        ))}
      </div>

      {props.value === "business" && (
        <div className="mt-8">
          <IndustryPicker industries={props.industries} value={props.industry} onChange={props.onIndustryChange} />
        </div>
      )}

      <NavRow>
        <Button onClick={props.onNext} disabled={!props.value}>
          Continue
        </Button>
      </NavRow>
    </section>
  );
}

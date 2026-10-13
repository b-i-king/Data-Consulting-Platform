// Step 0 — "Are you an individual or a business?"
// The answer decides which signup fields we show later (business accounts
// get a company name and team members). Businesses also answer two optional
// questions here, up front in the funnel:
//   * company size — LinkedIn's employee-count bands (codes A–I), so the
//     answers line up with LinkedIn data and feel familiar
//   * industry     — goods/services -> NAICS sector -> subsector (IndustryPicker)

"use client";

import type { AccountType, Choice, CompanySize, Industry, IntakeOptions } from "@/lib/types";
import IndustryPicker from "./IndustryPicker";
import { Button, ChoiceCard, NavRow, StepHeader } from "./ui";

export default function StepAccountType(props: {
  choices: Choice[];
  value: AccountType | null;
  onChange: (v: AccountType) => void;
  industries: IntakeOptions["industries"];
  companySizes: CompanySize[];
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
        <div className="mt-8 space-y-8">
          <div>
            <h2 className="mb-1 font-semibold">How big is the business?</h2>
            <p className="mb-3 text-sm text-ink-muted">Optional. Count everyone on the payroll, including you.</p>
            {/* Radio buttons styled as pills; clicking the selected one again clears it. */}
            <div role="radiogroup" aria-label="Company size" className="flex flex-wrap gap-2">
              {props.companySizes.map((s) => {
                const on = props.industry.company_size === s.id;
                return (
                  <button
                    key={s.id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => props.onIndustryChange({ ...props.industry, company_size: on ? null : s.id })}
                    className={`rounded-full border px-3 py-1.5 text-sm transition-colors ${
                      on ? "border-brand bg-brand-soft font-medium" : "border-line bg-surface hover:border-brand"
                    }`}
                  >
                    {s.label}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-xs text-ink-muted">Size bands follow LinkedIn&apos;s company size codes.</p>
          </div>
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

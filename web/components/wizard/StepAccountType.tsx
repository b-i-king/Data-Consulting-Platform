// Step 0 — "Are you an individual or a business?"
// The answer decides which signup fields we show later (business accounts
// get a company name and team members). Businesses also answer two optional
// questions here, up front in the funnel:
//   * company size — LinkedIn's employee-count bands (codes A–I), so the
//     answers line up with LinkedIn data and feel familiar; a searchable
//     dropdown (type "50" or "small")
//   * industry     — one search box over NAICS sectors and subsectors, or "Other" (IndustryPicker)

"use client";

import type { AccountType, Choice, CompanySize, Industry, IntakeOptions } from "@/lib/types";
import SearchSelect from "@/components/ui/SearchSelect";
import IndustryPicker from "./IndustryPicker";

/** A plain-English size word for each LinkedIn band, shown under the band. */
const SIZE_WORD: Record<string, string> = {
  A: "Just me — freelancer or solo owner",
  B: "Micro business",
  C: "Small business",
  D: "Mid-size business",
  E: "Mid-size business",
  F: "Large business",
  G: "Large business",
  H: "Enterprise",
  I: "Enterprise",
};
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
            <div className="max-w-xl">
              <SearchSelect
                label="Company size"
                placeholder="Search sizes, e.g. 50 or small…"
                value={props.industry.company_size}
                onChange={(v) => props.onIndustryChange({ ...props.industry, company_size: v })}
                options={props.companySizes.map((s) => ({
                  value: s.id,
                  label: s.label,
                  detail: SIZE_WORD[s.id],
                  keywords: `${s.employees} ${s.id === "A" ? "solo self freelancer one 1" : ""}`,
                }))}
              />
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

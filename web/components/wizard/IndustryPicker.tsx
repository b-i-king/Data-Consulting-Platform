// Industry questions for business accounts (asked on the first step).
//
//   1. Goods-producing or service-providing?  (two cards)
//   2. Sector     — NAICS 2-digit, filtered by answer 1   (dropdown)
//   3. Subsector  — NAICS 3-digit, filtered by answer 2   (dropdown)
//
// We stop at the subsector on purpose; deeper NAICS levels have 1,000+ options.
// The list comes from GET /api/intake/options (built from the SBA workbook by
// scripts/build_naics_reference.py). All three answers are optional.
// Changing an earlier answer clears the later ones so they can't disagree.

"use client";

import type { Industry, IntakeOptions, Supersector } from "@/lib/types";
import { ChoiceCard } from "./ui";

const selectCls =
  "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20 disabled:opacity-50";

export default function IndustryPicker({
  industries,
  value,
  onChange,
}: {
  industries: IntakeOptions["industries"];
  value: Industry;
  onChange: (v: Industry) => void;
}) {
  const sectors = industries.sectors.filter((s) => s.supersector === value.industry_supersector);
  const sector = industries.sectors.find((s) => s.code === value.naics_sector);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="mb-1 font-semibold">What kind of business is it?</h2>
        <p className="mb-3 text-sm text-ink-muted">
          Optional, but it helps us suggest the right dashboards and benchmarks for your industry.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          {industries.supersectors.map((s) => (
            <ChoiceCard
              key={s.id}
              label={s.label}
              description={s.description}
              selected={value.industry_supersector === s.id}
              onClick={() =>
                onChange({ industry_supersector: s.id as Supersector, naics_sector: null, naics_subsector: null })
              }
            />
          ))}
        </div>
      </div>

      {value.industry_supersector && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span>Sector</span>
            <select
              className={selectCls}
              value={value.naics_sector ?? ""}
              onChange={(e) => onChange({ ...value, naics_sector: e.target.value || null, naics_subsector: null })}
            >
              <option value="">Choose a sector…</option>
              {sectors.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.name} ({s.code})
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-1 text-sm">
            <span>Subsector</span>
            <select
              className={selectCls}
              value={value.naics_subsector ?? ""}
              disabled={!sector || sector.subsectors.length === 0}
              onChange={(e) => onChange({ ...value, naics_subsector: e.target.value || null })}
            >
              <option value="">
                {sector && sector.subsectors.length === 0 ? "No subsectors for this sector" : "Choose a subsector…"}
              </option>
              {sector?.subsectors.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.name} ({s.code})
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      <p className="text-xs text-ink-muted">Industry codes: North American Industry Classification System (NAICS).</p>
    </div>
  );
}

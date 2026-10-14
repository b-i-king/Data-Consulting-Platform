// Industry for business accounts — one search box, like the location picker.
//
//   [ bakery                              ✕ ]
//     Food Manufacturing          Manufacturing · NAICS 311
//     Food and Beverage Stores    Retail Trade · NAICS 445
//     Other: “bakery”             not in the list — we'll save what you typed
//
// Type any word: it searches the NAICS sector and subsector names (from GET
// /api/intake/options, built from the SBA workbook by
// scripts/build_naics_reference.py), plus a few everyday words below
// ("restaurant", "salon", "church"…) that NAICS names differently.
// Picking a subsector also fills its sector and goods/services group; picking
// a sector leaves the subsector empty. If nothing fits, "Other" saves the
// typed text (industry_other). Leaving the box empty saves nothing (NULL).
// Keyboard: ↑/↓ to move, Enter to choose, Escape to close.

"use client";

import { useId, useMemo, useRef, useState } from "react";
import type { Industry, IntakeOptions, Supersector } from "@/lib/types";

const inputCls =
  "w-full rounded-xl border border-line bg-surface px-3 py-2 pr-9 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20";

/** Everyday words -> NAICS codes, for businesses NAICS names differently. */
const EVERYDAY: Record<string, string[]> = {
  restaurant: ["722"], cafe: ["722"], coffee: ["722", "445"], bar: ["722"], catering: ["722"], food: ["722", "311", "445"],
  bakery: ["311", "445"], grocery: ["445"], store: ["44-45"], shop: ["44-45"], boutique: ["458"], ecommerce: ["44-45"],
  salon: ["812"], barber: ["812"], spa: ["812"], beauty: ["812", "456"], laundry: ["812"], fitness: ["713"], gym: ["713"],
  church: ["813"], nonprofit: ["813"], charity: ["813"], association: ["813"],
  consulting: ["541"], accounting: ["541"], law: ["541"], legal: ["541"], marketing: ["541"], design: ["541"],
  software: ["513", "541"], tech: ["518", "541"], it: ["541", "518"], agency: ["541"], engineering: ["541"],
  doctor: ["621"], clinic: ["621"], dental: ["621"], therapy: ["621"], hospital: ["622"], daycare: ["624"], childcare: ["624"],
  school: ["611"], tutoring: ["611"], training: ["611"], realty: ["531"], realtor: ["531"], property: ["531"],
  trucking: ["484"], delivery: ["492"], logistics: ["488", "493"], moving: ["484"], cleaning: ["561"], staffing: ["561"],
  landscaping: ["561"], security: ["561"], contractor: ["238"], plumbing: ["238"], electrician: ["238"], roofing: ["238"],
  builder: ["236"], hotel: ["721"], airbnb: ["721"], farm: ["111", "112"], auto: ["811", "441"], mechanic: ["811"],
  repair: ["811"], bank: ["522"], insurance: ["524"], investing: ["523"], media: ["512", "516"], music: ["512", "711"],
  events: ["711"], photography: ["541"], printing: ["323"], clothing: ["315", "458"], apparel: ["315", "458"],
};

interface Option {
  key: string;
  label: string;
  detail: string;
  value: Industry;
}

export default function IndustryPicker({
  industries,
  value,
  onChange,
}: {
  industries: IntakeOptions["industries"];
  value: Industry;
  onChange: (v: Industry) => void;
}) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  // Every sector and subsector as one searchable list.
  const all: (Option & { words: string; codes: string[] })[] = useMemo(
    () =>
      industries.sectors.flatMap((s) => [
        {
          key: s.code,
          label: s.name,
          detail: `Sector · NAICS ${s.code}`,
          codes: [s.code],
          words: s.name.toLowerCase(),
          value: { ...value, industry_supersector: s.supersector as Supersector, naics_sector: s.code, naics_subsector: null, industry_other: null },
        },
        ...s.subsectors.map((sub) => ({
          key: sub.code,
          label: sub.name,
          detail: `${s.name} · NAICS ${sub.code}`,
          codes: [sub.code, s.code],
          words: `${sub.name} ${s.name}`.toLowerCase(),
          value: { ...value, industry_supersector: s.supersector as Supersector, naics_sector: s.code, naics_subsector: sub.code, industry_other: null },
        })),
      ]),
    // `value` only carries company_size through; the industry fields are replaced.
    [industries, value],
  );

  const query = text.trim().toLowerCase();
  const options: Option[] = useMemo(() => {
    if (!query) return [];
    const words = query.split(/\s+/);
    const everyday = words.flatMap((w) => EVERYDAY[w] ?? []);
    const hits = all
      .map((o) => {
        const nameMatch = words.every((w) => o.words.includes(w));
        const wordMatch = o.codes.some((c) => everyday.includes(c));
        return { o, score: (wordMatch ? 2 : 0) + (nameMatch ? 1 : 0) + (o.key.length === 3 ? 0.1 : 0) };
      })
      .filter((h) => h.score >= 1)
      .sort((a, b) => b.score - a.score)
      .slice(0, 7)
      .map((h) => h.o);
    // "Other" is always last: saves exactly what they typed.
    return [
      ...hits,
      {
        key: "__other",
        label: `Other: “${text.trim()}”`,
        detail: "Not in the list — we'll save what you typed",
        value: { ...value, industry_supersector: null, naics_sector: null, naics_subsector: null, industry_other: text.trim() },
      },
    ];
  }, [all, query, text, value]);

  // What's chosen right now, shown in place of the search text.
  const sector = industries.sectors.find((s) => s.code === value.naics_sector);
  const sub = sector?.subsectors.find((s) => s.code === value.naics_subsector);
  const chosen = sub ? `${sub.name} (${sector!.name})` : sector ? sector.name : value.industry_other ? `Other: ${value.industry_other}` : "";

  function pick(o: Option) {
    onChange(o.value);
    setText("");
    setOpen(false);
  }
  function clear() {
    onChange({ ...value, industry_supersector: null, naics_sector: null, naics_subsector: null, industry_other: null });
    setText("");
    inputRef.current?.focus();
  }

  return (
    <div className="space-y-2">
      <div>
        <h2 className="mb-1 font-semibold">What kind of business is it?</h2>
        <p className="text-sm text-ink-muted">
          Optional — type a word like <em>bakery</em>, <em>salon</em> or <em>consulting</em>. It helps us suggest the
          right dashboards and benchmarks.
        </p>
      </div>
      <div className="relative max-w-xl">
        <input
          ref={inputRef}
          role="combobox"
          aria-expanded={open && options.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-label="Industry"
          aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
          className={inputCls}
          placeholder={chosen || "Search industries…"}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => text && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (!open || !options.length) return;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, options.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              pick(options[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
        {(chosen || text) && (
          <button
            type="button"
            aria-label="Clear industry"
            onClick={clear}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full px-2 text-ink-muted hover:text-ink"
          >
            ✕
          </button>
        )}
        {open && options.length > 0 && (
          <ul
            id={listId}
            role="listbox"
            className="absolute z-20 mt-1 max-h-80 w-full overflow-auto rounded-xl border border-line bg-surface py-1 shadow-[var(--shadow-card)]"
          >
            {options.map((o, i) => (
              <li
                key={o.key}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault(); // keep focus so the click lands
                  pick(o);
                }}
                onMouseEnter={() => setActive(i)}
                className={`cursor-pointer px-3 py-2 text-sm ${i === active ? "bg-brand-soft" : ""} ${
                  o.key === "__other" ? "border-t border-line" : ""
                }`}
              >
                <div className="font-medium">{o.label}</div>
                <div className="text-xs text-ink-muted">{o.detail}</div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {chosen && !text && (
        <p className="text-xs text-ink-muted">
          Chosen: <span className="font-medium text-ink">{chosen}</span>
        </p>
      )}
      <p className="text-xs text-ink-muted">Industry codes: North American Industry Classification System (NAICS).</p>
    </div>
  );
}

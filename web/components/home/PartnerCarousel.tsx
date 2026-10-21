"use client";

// ── Partners carousel (home page, between the start and "How it works") ──────
// Logos of partner businesses (api/services/partners.py: paid plan for 30+
// days, their own website, a logo a person approved). Each logo links to the
// partner's website in a new tab (rel="sponsored": it's a paid relationship,
// which search engines ask sites to mark).
//
// How it looks: each logo (only its shape — the API removes backgrounds) is
// drawn in the theme's text color on a rounded card, and turns gold when you
// point at it (ThemedLogo.tsx), so a row of different brands looks like part
// of this site in light and dark. The row slides slowly and pauses
// on hover or keyboard focus; with "reduce motion" on, it stays still and
// scrolls sideways instead (app/globals.css, "Partners carousel").
//
// Size: the API sends at most 30 partners (a new order each day so everyone
// gets time at the front), each logo a small WebP, loaded lazily.
//
// Until partners are approved, the row shows "Partner spots opening soon"
// cards and how to become one — honest, and still business-friendly.

import { useEffect, useState } from "react";
import { getPartners, partnerLogoSrc } from "@/lib/api";
import type { PartnerCarouselData } from "@/lib/types";
import ThemedLogo from "./ThemedLogo";

const PLACEHOLDERS = 4;

export default function PartnerCarousel() {
  const [data, setData] = useState<PartnerCarouselData | null>(null);
  const [showRules, setShowRules] = useState(false);

  useEffect(() => {
    getPartners()
      .then(setData)
      .catch(() => setData({ partners: [], requirements: [] }));   // API asleep or down: show the placeholders
  }, []);

  const partners = data?.partners ?? [];
  // A few logos can't fill a sliding row: repeat them so the loop looks full.
  const base = partners.length ? Array.from({ length: Math.ceil(8 / partners.length) }, () => partners).flat() : [];
  // Slower with more logos, so each one stays readable (about 4 seconds a logo).
  const speed = `${Math.max(30, base.length * 4)}s`;

  return (
    <section id="partners" aria-labelledby="partners-title" className="scroll-mt-20 space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="partners-title" className="text-xl font-semibold">
          Partnered with
        </h2>
        <button type="button" onClick={() => setShowRules((v) => !v)} aria-expanded={showRules}
          className="text-sm font-medium text-brand-ink hover:underline">
          {showRules ? "Hide" : "How to become a partner"}
        </button>
      </div>

      {partners.length > 0 ? (
        <div className="partner-strip relative overflow-hidden rounded-3xl border border-line bg-surface-2 py-4"
          style={{ ["--partner-speed" as string]: speed }}>
          {/* Drawn twice so the slide loops without a jump; the copy is hidden from screen readers. */}
          <ul className="partner-track flex w-max gap-4 px-2">
            {[...base, ...base].map((p, i) => (
              <li key={`${p.id}-${i}`} aria-hidden={i >= base.length || undefined}>
                <a href={p.url} target="_blank" rel="noopener noreferrer sponsored" title={`${p.name} — opens their website`}
                  tabIndex={i >= base.length ? -1 : undefined}
                  className="group flex h-20 w-44 items-center justify-center rounded-2xl border border-line bg-surface px-4 shadow-[var(--shadow-card)] transition-colors hover:border-brand">
                  <ThemedLogo src={partnerLogoSrc(p.logo_url)} name={p.name} width={p.width} height={p.height} />
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <ul className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {Array.from({ length: PLACEHOLDERS }, (_, i) => (
            <li key={i} className="flex h-20 items-center justify-center rounded-2xl border border-dashed border-line bg-surface px-4 text-center text-sm text-ink-muted">
              Partner spot opening soon
            </li>
          ))}
        </ul>
      )}

      {showRules && (
        <div className="rounded-2xl border border-line bg-surface p-5 text-sm shadow-[var(--shadow-card)]">
          <p className="font-semibold">Show your business here</p>
          <p className="mt-1 text-ink-muted">
            Partners get their logo on this page, linked to their website. To keep the list trustworthy:
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-ink-muted">
            {(data?.requirements ?? []).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          <p className="mt-2 text-ink-muted">Already a client? Apply in Settings → Account → Partner program.</p>
        </div>
      )}
    </section>
  );
}

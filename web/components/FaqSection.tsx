// ── Frequently asked questions ────────────────────────────────────────────────
// The questions from lib/faq.ts as fold-out answers. Uses the browser's own
// <details>/<summary> (no JavaScript needed), so the answers are in the page's
// HTML for search engines too. With `withSchema` (the home page) it also adds
// schema.org "FAQPage" markup, which lets Google show the answers in results;
// Settings -> Help & FAQ shows the same list without it.

import { FAQS } from "@/lib/faq";

export default function FaqSection({ withSchema = false, title = "Frequently asked questions" }: {
  withSchema?: boolean;
  title?: string;
}) {
  return (
    <section id="faq" aria-labelledby="faq-title" className="scroll-mt-20 space-y-4">
      <h2 id="faq-title" className="text-xl font-semibold">
        {title}
      </h2>
      <div className="space-y-2">
        {FAQS.map(({ q, a }) => (
          <details key={q} className="group rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)]">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 font-medium hover:text-brand-ink">
              {q}
              <span aria-hidden className="shrink-0 text-ink-muted transition-transform group-open:rotate-180">
                ▾
              </span>
            </summary>
            <p className="px-5 pb-4 text-sm leading-relaxed text-ink-muted">{a}</p>
          </details>
        ))}
      </div>
      {withSchema && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "FAQPage",
              mainEntity: FAQS.map(({ q, a }) => ({
                "@type": "Question",
                name: q,
                acceptedAnswer: { "@type": "Answer", text: a },
              })),
            }),
          }}
        />
      )}
    </section>
  );
}

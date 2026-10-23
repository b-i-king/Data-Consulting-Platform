// ── Enterprise plan card (home page, after Services) ─────────────────────────
// A plan card in the style of a pricing page: the plan's name and "Custom"
// price with a contact button on one side, what it includes on the other.
// The numbers match api/services/enterprise.py (LIMITS ceilings) — change both
// together. "Talk to us" opens the booking page (NEXT_PUBLIC_BOOKING_URL).

import Icon from "@/components/ui/Icon";
import { FALLBACK_BOOKING_URL } from "@/lib/format";

// What an enterprise contract includes (api/services/enterprise.py; docs/PRICING.md "Enterprise contracts").
const INCLUDES = [
  "Your own contract: monthly, yearly or one-time pricing, invoiced to you",
  "Uploads up to 200 MB per file (50 MB on standard plans)",
  "Up to 500 team seats (5 on standard plans)",
  "Up to 1,000,000 rows per database table (100,000 on standard plans)",
  "Summaries run in your warehouse: Snowflake, BigQuery, Redshift, Salesforce",
  "The services you need, chosen for your contract",
  "A dedicated consultant, onboarding and custom dashboards",
  "An Enterprise Agreement you accept online; security reviews and data agreements on request",
];

export default function EnterpriseOffer() {
  return (
    <section id="enterprise" aria-labelledby="enterprise-title" className="scroll-mt-20">
      <div className="grid gap-6 rounded-3xl border border-line bg-surface p-6 shadow-[var(--shadow-card)] sm:p-8 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-brand-soft text-brand-ink">
              <Icon name="shield" className="h-5 w-5" />
            </span>
            <h2 id="enterprise-title" className="text-xl font-semibold">Enterprise</h2>
          </div>
          <p>
            <span className="font-mono text-3xl font-semibold">Custom</span>
          </p>
          <p className="text-sm text-ink-muted">
            For larger organizations with more data, more people and their own security and procurement needs. We
            agree the services, limits and price with you, and your team works on the same platform.
          </p>
          <div className="mt-auto">
            <a href={FALLBACK_BOOKING_URL} target="_blank" rel="noopener noreferrer"
              className="inline-block rounded-full bg-brand px-6 py-2.5 font-semibold text-on-brand hover:bg-brand-strong">
              Talk to us
            </a>
          </div>
        </div>
        <ul className="grid gap-3 sm:grid-cols-2 lg:border-l lg:border-line lg:pl-6">
          {INCLUDES.map((item) => (
            <li key={item} className="flex items-start gap-2 text-sm">
              <span className="mt-0.5 text-brand-ink">
                <Icon name="check" className="h-4 w-4" />
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

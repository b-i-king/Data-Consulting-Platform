// Landing page ("/"). Explains the offer in plain language. The main button
// starts the guest questionnaire (/get-started, no sign-in needed); under it,
// "Sign in" and "Create an account" (by email) lead to /signin or /signup and
// then the signed-in workspace (/workspace). "/home" forwards here too.
//
// The category list below is short marketing copy for this page only. Full
// descriptions and prices live in the backend (api/services/pricing.py) and
// are shown in the wizard.

import Link from "next/link";
import { categoryColor } from "@/lib/categories";
import Tour, { TourButton, type TourStep } from "@/components/ui/Tour";
import FaqSection from "@/components/FaqSection";
import PartnerCarousel from "@/components/home/PartnerCarousel";
import { GET_STARTED, SIGN_IN, SIGN_UP } from "@/lib/routes";
import { FALLBACK_BOOKING_URL } from "@/lib/format";
import Icon from "@/components/ui/Icon";

// The first-visit guide for this page (components/ui/Tour.tsx).
const HOME_TOUR: TourStep[] = [
  { target: "home-start", title: "Start here — no account needed",
    body: "Start as a guest: tell us a little about you, upload a file, and see it cleaned in minutes. You only make an account when you want to download or keep going." },
  { target: "home-how", title: "How it works",
    body: "Four steps: about you, upload, clean, then choose what's next — just download, do it yourself with a monthly package, or have a consultant do it." },
  { target: "home-services", title: "The seven services",
    body: "Data Cleaning is always included. The others (dashboards, analysis, storage, ML, AI) open with a package or with a consultant." },
  { target: "home-signin", title: "Coming back?",
    body: "Sign in with your email and password to open your workspace: your tools, your stored data and your dashboards." },
];

const STEPS = [
  { title: "Tell us about you", body: "Individual or business, and where your data lives today." },
  { title: "Upload your files", body: "CSV, Excel, JSON, or Parquet. One file or many — we sort them out." },
  { title: "Get clean data", body: "Duplicates removed, missing values measured with advice, gaps filled if you choose." },
  { title: "Go further, your way", body: "Analysis, dashboards, ML, AI, or a call with a consultant — only if you want." },
];

const CATEGORIES = [
  { id: "data_cleaning", name: "Data Cleaning", body: "Automatic. Duplicates, blanks, and data types handled first." },
  { id: "data_strategy_discovery", name: "Data Strategy & Discovery", body: "Find your data sources and build a roadmap tied to your goals." },
  { id: "data_exploration_analysis", name: "Data Exploration & Analysis", body: "Answers to your critical questions and the key trends behind them." },
  { id: "data_visualization", name: "Data Visualization", body: "Interactive dashboards built from your data, right here. Tableau or Power BI builds are a consultant project." },
  { id: "data_engineering", name: "Data Engineering", body: "ER diagrams, data dictionaries, and data built to integrate." },
  { id: "data_science_ml", name: "Data Science & ML", body: "Forecasts, predictive models, and advanced analytics." },
  { id: "ai", name: "AI", body: "Ask Beacon questions about your data in plain English. Free questions every month; more with the AI add-on." },
];

// "Why choose us": how the platform differs from data-science tools
// (software only) and data consulting firms (people only, quotes on request).
const DIFFERENCES = [
  { icon: "bolt", title: "Self-serve, in minutes",
    body: "Start as a guest — no account, no sales call. Upload a file or connect your tools and get it cleaned, " +
      "summarized and on a dashboard yourself. Most consulting firms start with a meeting and a quote." },
  { icon: "tag", title: "Transparent pricing",
    body: "Prices are published up front, and every step that costs money shows the amount before you agree. " +
      "Monthly or yearly, cancel any time — no surprise invoices." },
  { icon: "users", title: "Screened consultants, when you want one",
    body: "Need a person? Every consultant applies, is reviewed by us and agrees to our terms before working " +
      "with clients. Most services start with a free 30-minute call, and you pay them through the platform." },
];

export default function Home() {
  return (
    <div className="space-y-16">
      <Tour id="home" steps={HOME_TOUR} />
      <section className="hero-glow -mx-4 space-y-6 rounded-3xl px-4 pb-10 pt-10 sm:px-8">
        <span className="inline-block rounded-full border border-line bg-surface px-3 py-1 font-mono text-xs text-ink-muted">
          Start free as a guest · no account needed
        </span>
        <h1 className="max-w-3xl text-4xl font-semibold sm:text-5xl">
          Meet your data <span className="text-brand-ink">where it already lives.</span>
        </h1>
        <p className="max-w-2xl text-lg text-ink-muted">
          A data consultant&apos;s first questions, answered in minutes. Clean your spreadsheets automatically, pay only
          for the processing time you use, and bring in a consultant when you&apos;re ready.
        </p>
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3" data-tour="home-start">
            <Link
              href={GET_STARTED}
              className="rounded-full bg-brand px-6 py-2.5 font-semibold text-on-brand hover:bg-brand-strong"
            >
              Start as a guest
            </Link>
            <a
              href="#services"
              className="rounded-full border border-line bg-surface px-6 py-2.5 font-semibold hover:border-brand hover:text-brand-ink"
            >
              See services
            </a>
            <TourButton id="home" />
          </div>
          {/* Returning visitors and people ready for an account: sign in by email. */}
          <div className="flex flex-wrap items-center gap-3 text-sm" data-tour="home-signin">
            <span className="text-ink-muted">Or, with your email address:</span>
            <Link href={SIGN_IN} className="rounded-full border border-line bg-surface px-4 py-1.5 font-semibold hover:border-brand hover:text-brand-ink">
              Sign in
            </Link>
            <Link
              href={SIGN_UP}
              className="rounded-full border border-line bg-surface px-4 py-1.5 font-semibold hover:border-brand hover:text-brand-ink"
            >
              Create an account
            </Link>
          </div>
        </div>
      </section>

      {/* Partner businesses' logos, linked to their websites (components/home/PartnerCarousel.tsx). */}
      <PartnerCarousel />

      <section aria-labelledby="how" className="space-y-4" data-tour="home-how">
        <h2 id="how" className="text-xl font-semibold">
          How it works
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <div key={s.title} className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-card)]">
              <div className="mb-2 font-mono text-sm text-brand-ink">0{i + 1}</div>
              <h3 className="font-semibold">{s.title}</h3>
              <p className="mt-1 text-sm text-ink-muted">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* What sets the platform apart from data-science tools (do-it-yourself only) and
          consulting firms (people only, prices on request): three reasons, in plain words. */}
      <section id="why-us" aria-labelledby="why-us-title" className="scroll-mt-20 space-y-4">
        <h2 id="why-us-title" className="text-xl font-semibold">
          Why choose us
        </h2>
        <div className="grid gap-4 sm:grid-cols-3">
          {DIFFERENCES.map((d) => (
            <div key={d.title} className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-card)]">
              <div className="mb-3 grid h-10 w-10 place-items-center rounded-xl bg-brand-soft text-brand-ink">
                <Icon name={d.icon} className="h-5 w-5" />
              </div>
              <h3 className="font-semibold">{d.title}</h3>
              <p className="mt-1 text-sm text-ink-muted">{d.body}</p>
            </div>
          ))}
        </div>
        <p className="text-sm text-ink-muted">
          A larger organization? Ask about an <strong className="text-ink">Enterprise plan</strong> — your own contract, higher limits and a
          dedicated consultant.{" "}
          <a href={FALLBACK_BOOKING_URL} target="_blank" rel="noopener noreferrer" className="font-semibold text-brand-ink underline">
            Talk to us
          </a>
        </p>
      </section>

      <section id="services" aria-labelledby="services-title" className="space-y-4" data-tour="home-services">
        <h2 id="services-title" className="text-xl font-semibold">
          Services
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {CATEGORIES.map((c) => (
            <div
              key={c.id}
              style={{ borderLeftColor: categoryColor(c.id) }}
              className="rounded-2xl border border-l-4 border-line bg-surface p-5 shadow-[var(--shadow-card)]"
            >
              <h3 className="font-semibold" style={{ color: categoryColor(c.id) }}>
                {c.name}
              </h3>
              <p className="mt-1 text-sm text-ink-muted">{c.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Questions people ask before signing up (lib/faq.ts), with search-engine markup. */}
      <FaqSection withSchema />
    </div>
  );
}

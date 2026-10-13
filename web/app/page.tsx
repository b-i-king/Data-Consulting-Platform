// Landing page ("/"). Explains the offer in plain language and sends visitors
// to the guest questionnaire at /start. No sign-in needed.
//
// The category list below is short marketing copy for this page only. Full
// descriptions and prices live in the backend (api/services/pricing.py) and
// are shown in the wizard.

import Link from "next/link";
import { categoryColor } from "@/lib/categories";

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
  { id: "data_visualization", name: "Data Visualization", body: "Dashboards and reports in Tableau, Power BI, or custom builds." },
  { id: "data_engineering", name: "Data Engineering", body: "ER diagrams, data dictionaries, and data built to integrate." },
  { id: "data_science_ml", name: "Data Science & ML", body: "Forecasts, predictive models, and advanced analytics." },
  { id: "ai", name: "AI", body: "Ask questions about your data in plain English. Early access." },
];

export default function Home() {
  return (
    <div className="space-y-16">
      <section className="hero-glow -mx-4 space-y-6 rounded-3xl px-4 pb-10 pt-10 sm:px-8">
        <span className="inline-block rounded-full border border-line bg-surface px-3 py-1 font-mono text-xs text-ink-muted">
          Start free as a guest · no account needed
        </span>
        <h1 className="max-w-3xl text-4xl font-semibold sm:text-5xl">
          Meet your data <span className="text-brand">where it already lives.</span>
        </h1>
        <p className="max-w-2xl text-lg text-ink-muted">
          A data consultant&apos;s first questions, answered in minutes. Clean your spreadsheets automatically, pay only
          for the processing time you use, and bring in a consultant when you&apos;re ready.
        </p>
        <div className="flex flex-wrap gap-3">
          <Link
            href="/start"
            className="rounded-full bg-brand px-6 py-2.5 font-semibold text-on-brand hover:bg-brand-strong"
          >
            Start as a guest
          </Link>
          <a
            href="#services"
            className="rounded-full border border-line bg-surface px-6 py-2.5 font-semibold hover:border-brand hover:text-brand"
          >
            See services
          </a>
        </div>
      </section>

      <section aria-labelledby="how" className="space-y-4">
        <h2 id="how" className="text-xl font-semibold">
          How it works
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <div key={s.title} className="rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-card)]">
              <div className="mb-2 font-mono text-sm text-brand">0{i + 1}</div>
              <h3 className="font-semibold">{s.title}</h3>
              <p className="mt-1 text-sm text-ink-muted">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="services" aria-labelledby="services-title" className="space-y-4">
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
    </div>
  );
}

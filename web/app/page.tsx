// Landing page ("/"). Explains the offer in plain language and sends visitors
// to the guest questionnaire at /start. No sign-in needed.

import Link from "next/link";

const STEPS = [
  { title: "Tell us about you", body: "Individual or business, and where your data lives today." },
  { title: "Upload your files", body: "CSV, Excel, JSON, or Parquet. One file or many — we sort them out." },
  { title: "Get clean data", body: "Duplicates removed, missing values measured, a full preview on screen." },
  { title: "Go further, your way", body: "Analysis, dashboards, ML, or a call with a consultant — only if you want." },
];

export default function Home() {
  return (
    <div className="space-y-12">
      <section className="space-y-5 pt-6">
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight sm:text-5xl">
          Meet your data where it already lives.
        </h1>
        <p className="max-w-2xl text-lg text-ink-muted">
          A data consultant&apos;s first questions, answered in minutes. Start as a guest, clean your
          spreadsheets automatically, and pay only for the processing time you use.
        </p>
        <div className="flex flex-wrap gap-3">
          <Link href="/start" className="rounded-md bg-brand px-5 py-2.5 font-medium text-white hover:bg-brand-strong">
            Start as a guest
          </Link>
          <a href="#how" className="rounded-md border border-line bg-surface px-5 py-2.5 font-medium hover:border-brand">
            How it works
          </a>
        </div>
      </section>

      <section id="how" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((s, i) => (
          <div key={s.title} className="rounded-lg border border-line bg-surface p-5">
            <div className="mb-2 text-sm font-medium text-brand">Step {i + 1}</div>
            <h2 className="font-semibold">{s.title}</h2>
            <p className="mt-1 text-sm text-ink-muted">{s.body}</p>
          </div>
        ))}
      </section>
    </div>
  );
}

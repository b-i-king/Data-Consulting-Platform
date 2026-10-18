// The "page not found" (404) page: shown for any address the site doesn't have
// (a typo, an old bookmark). Instead of a dead end, it offers the main places
// to go. Next.js shows this file automatically; the address map is lib/routes.ts.

import type { Metadata } from "next";
import Link from "next/link";
import { GET_STARTED, HOME, SIGN_IN, WORKSPACE } from "@/lib/routes";

export const metadata: Metadata = { title: "Page not found" };

const PLACES = [
  { href: HOME, label: "Home", body: "What we do and how it works." },
  { href: GET_STARTED, label: "Get started", body: "Try it as a guest: upload a file and see it cleaned." },
  { href: WORKSPACE, label: "My workspace", body: "Your tools, data and dashboards (signed in)." },
  { href: SIGN_IN, label: "Sign in", body: "Use the email address you signed up with." },
];

export default function NotFound() {
  return (
    <section className="mx-auto max-w-2xl space-y-6 py-10">
      <div className="space-y-2">
        <p className="font-mono text-sm text-ink-muted">404</p>
        <h1 className="text-3xl font-semibold">We couldn&apos;t find that page</h1>
        <p className="text-ink-muted">The address may be mistyped, or the page has moved. Here are the main places to go:</p>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2">
        {PLACES.map((p) => (
          <li key={p.href}>
            <Link href={p.href} className="block rounded-2xl border border-line bg-surface p-4 hover:border-brand">
              <span className="font-semibold text-brand-ink">{p.label} →</span>
              <span className="mt-1 block text-sm text-ink-muted">{p.body}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

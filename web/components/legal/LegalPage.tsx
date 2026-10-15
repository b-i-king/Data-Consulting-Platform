// Shared layout for the legal pages (/privacy, /terms, /storage-terms,
// /data-deletion), adapted from the Video_Game_Stats_Tracker template's pages.
// Plain server components: no sign-in, no data, just text. Colors come from
// the theme variables in app/globals.css, so they work in light and dark.

import type { ReactNode } from "react";

/** Who runs the platform and how to reach them — used on every legal page. */
export const COMPANY = "BOL Group LLC";
export const CONTACT_EMAIL = "thebolgroup.llc@gmail.com";

export function LegalPage(props: { title: string; updated: string; children: ReactNode }) {
  return (
    <article className="mx-auto max-w-3xl space-y-6 py-2">
      <header className="space-y-1">
        <h1 className="font-mono text-3xl font-semibold text-brand">{props.title}</h1>
        <p className="text-sm text-ink-muted">Last updated: {props.updated}</p>
      </header>
      {props.children}
    </article>
  );
}

export function Section(props: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 text-sm leading-relaxed">
      <h2 className="text-xl font-semibold">{props.title}</h2>
      {props.children}
    </section>
  );
}

/** A bulleted list in the muted style; put <Strong> at the start of an item for its label. */
export function List({ children, ordered = false }: { children: ReactNode; ordered?: boolean }) {
  const cls = `${ordered ? "list-decimal" : "list-disc"} space-y-2 pl-6 text-ink-muted`;
  return ordered ? <ol className={cls}>{children}</ol> : <ul className={cls}>{children}</ul>;
}

export function Strong({ children }: { children: ReactNode }) {
  return <strong className="text-ink">{children}</strong>;
}

export function Email({ subject }: { subject?: string }) {
  const href = `mailto:${CONTACT_EMAIL}${subject ? `?subject=${encodeURIComponent(subject)}` : ""}`;
  return (
    <a href={href} className="text-brand hover:underline">
      {CONTACT_EMAIL}
    </a>
  );
}

"use client";

// ── The header menu (the ☰ button next to "Get started") ─────────────────────
// Quick links to the home page's sections, the legal pages (also in the
// footer) and Sign in — Sign in lives here instead of as its own header link,
// so it isn't shown twice next to the home page's own Sign in button.
// Signed in, "Sign in" becomes "Help & FAQ" (Settings).
//
// Closes on a click outside, on Escape (focus goes back to the button) and
// after choosing a link.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { SIGN_IN, workspacePath } from "@/lib/routes";

const SECTIONS = [
  { href: "/#how", label: "How it works" },
  { href: "/#why-us", label: "Why us" },
  { href: "/#services", label: "Services" },
  { href: "/#enterprise", label: "Enterprise" },
  { href: "/#trusted-by", label: "Trusted by" },
  { href: "/#faq", label: "FAQ" },
];
const LEGAL = [
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "/storage-terms", label: "Storage terms" },
  { href: "/data-deletion", label: "Data deletion" },
];

export default function SiteMenu({ signedIn }: { signedIn: boolean }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const item = "block rounded-xl px-3 py-2 text-sm hover:bg-surface-2 hover:text-brand-ink";
  const close = () => setOpen(false);

  return (
    <div ref={box} className="relative">
      <button ref={button} type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-controls="site-menu"
        aria-label="Menu" className="grid h-8 w-8 place-items-center rounded-full border border-line bg-surface hover:border-brand">
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <nav id="site-menu" aria-label="Site"
          className="absolute right-0 top-[calc(100%+8px)] z-30 w-56 rounded-2xl border border-line bg-surface p-2 shadow-[var(--shadow-card)]">
          {signedIn ? (
            <Link href={workspacePath({ settings: "help" })} onClick={close} className={`${item} font-semibold`}>
              Help &amp; FAQ
            </Link>
          ) : (
            <Link href={SIGN_IN} onClick={close} className={`${item} font-semibold`}>
              Sign in
            </Link>
          )}
          <div className="my-1 border-t border-line" />
          {SECTIONS.map((l) => (
            <Link key={l.href} href={l.href} onClick={close} className={item}>
              {l.label}
            </Link>
          ))}
          <div className="my-1 border-t border-line" />
          {LEGAL.map((l) => (
            <Link key={l.href} href={l.href} onClick={close} className={`${item} text-ink-muted`}>
              {l.label}
            </Link>
          ))}
        </nav>
      )}
    </div>
  );
}

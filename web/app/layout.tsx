// Root layout: wraps every page with the header and footer.
// Server component (no "use client"), so it renders fast with no JavaScript.

import "./globals.css";
import type { Metadata, Viewport } from "next";
import Link from "next/link";
import { SITE_NAME } from "@/lib/format";

export const metadata: Metadata = {
  title: { default: SITE_NAME, template: `%s · ${SITE_NAME}` },
  description:
    "Start with the data you already have. Upload a spreadsheet, get it cleaned automatically, and talk to a consultant only when you want to.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-US">
      <body className="min-h-screen antialiased">
        <header className="border-b border-line bg-surface">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              {SITE_NAME}
            </Link>
            <Link
              href="/start"
              className="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-strong"
            >
              Get started
            </Link>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
        <footer className="mx-auto max-w-5xl px-4 pb-8 text-xs text-ink-muted">
          Your files are processed in memory and are not stored on our servers.
        </footer>
      </body>
    </html>
  );
}

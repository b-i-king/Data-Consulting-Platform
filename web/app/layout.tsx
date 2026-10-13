// Root layout: wraps every page with the header (logo, theme switch, sign-in links) and footer.
//
// Theme: THEME_INIT_SCRIPT runs before the page paints and applies a saved
// Light/Dark choice; with no saved choice the CSS follows the computer's
// setting. `suppressHydrationWarning` on <html> is needed because that
// script may add data-theme before React loads.

import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Fira_Code, Inter } from "next/font/google";
import Link from "next/link";
import ThemeToggle, { THEME_INIT_SCRIPT } from "@/components/ThemeToggle";
import HeaderAuth from "@/components/auth/HeaderAuth";
import { SITE_NAME } from "@/lib/format";

// Fira Code = headings and numbers (VGST signature). Inter = body text.
// next/font downloads them at build time and serves them from our own domain.
const firaCode = Fira_Code({ subsets: ["latin"], variable: "--font-fira-code", display: "swap" });
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: { default: SITE_NAME, template: `%s · ${SITE_NAME}` },
  description:
    "Start with the data you already have. Upload a spreadsheet, get it cleaned automatically, and talk to a consultant only when you want to.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Browser UI color (mobile address bar) for each theme.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f0f2f5" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1117" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-US" suppressHydrationWarning className={`${firaCode.variable} ${inter.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen antialiased">
        <header className="sticky top-0 z-10 border-b border-line bg-canvas/80 backdrop-blur">
          <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-3">
            <Link href="/" className="flex items-center gap-2 font-mono font-semibold tracking-tight">
              <span aria-hidden className="grid h-7 w-7 place-items-center rounded-lg bg-brand text-on-brand">
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
                  <rect x="4" y="13" width="4" height="7" rx="1" />
                  <rect x="10" y="9" width="4" height="11" rx="1" />
                  <rect x="16" y="5" width="4" height="15" rx="1" />
                </svg>
              </span>
              <span className="hidden sm:inline">{SITE_NAME}</span>
            </Link>
            <div className="flex items-center gap-3">
              <ThemeToggle />
              <HeaderAuth />
            </div>
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

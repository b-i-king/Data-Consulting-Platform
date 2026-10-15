// Root layout: wraps every page with the header (logo, theme switch, sign-in links)
// and footer (social media icons, privacy note, links to the legal pages).
//
// SEO (how people find the site): the `metadata` below — title, description,
// keywords, link previews, robots rules, Google verification — plus a JSON-LD
// block Google reads to understand the business. The values live in
// lib/seo.ts; app/sitemap.ts, app/robots.ts and app/opengraph-image.tsx
// complete the set (same pieces as the Video_Game_Stats_Tracker template).
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
import SocialLinks from "@/components/SocialLinks";
import { SITE_NAME } from "@/lib/format";
import { COMPANY, DESCRIPTION, KEYWORDS, SITE_URL, TAGLINE, X_HANDLE, jsonLd } from "@/lib/seo";

// Fira Code = headings and numbers (VGST signature). Inter = body text.
// next/font downloads them at build time and serves them from our own domain.
const firaCode = Fira_Code({ subsets: ["latin"], variable: "--font-fira-code", display: "swap" });
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

const GOOGLE_VERIFICATION = process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION;

export const metadata: Metadata = {
  // Makes every relative URL below (images, canonical) absolute.
  metadataBase: new URL(SITE_URL),
  // Each page sets its own short title ("Sign in"); the template adds the brand.
  title: { default: `${SITE_NAME} — Data Consulting for Small Businesses`, template: `%s · ${SITE_NAME}` },
  description: DESCRIPTION,
  keywords: KEYWORDS,
  authors: [{ name: COMPANY, url: SITE_URL }],
  applicationName: SITE_NAME,
  category: "Business, Data Analytics, Consulting",
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-snippet": -1, "max-image-preview": "large", "max-video-preview": -1 },
  },
  alternates: { canonical: "/" },
  // Google Search Console ownership check — only added once the code is set.
  ...(GOOGLE_VERIFICATION ? { verification: { google: GOOGLE_VERIFICATION } } : {}),
  // Link previews (LinkedIn, Facebook, iMessage...). The image comes from app/opengraph-image.tsx.
  openGraph: {
    title: `${SITE_NAME} — Data Consulting for Small Businesses`,
    description: TAGLINE,
    siteName: SITE_NAME,
    url: SITE_URL,
    locale: "en_US",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: `${SITE_NAME} — Data Consulting for Small Businesses`,
    description: TAGLINE,
    site: X_HANDLE,
    creator: X_HANDLE,
  },
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
        {/* Structured data for search engines (lib/seo.ts). */}
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd()) }} />
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
        <footer className="mx-auto max-w-5xl border-t border-line px-4 pb-8 pt-2 text-xs text-ink-muted">
          {/* Social media icons (components/SocialLinks.tsx). */}
          <SocialLinks />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>Your files are processed in memory and not stored, unless you choose managed storage.</span>
            {/* Legal pages (app/privacy, terms, storage-terms, data-deletion). */}
            <nav aria-label="Legal" className="flex flex-wrap gap-4">
              <Link href="/privacy" className="hover:text-ink">
                Privacy
              </Link>
              <Link href="/terms" className="hover:text-ink">
                Terms
              </Link>
              <Link href="/storage-terms" className="hover:text-ink">
                Storage terms
              </Link>
              <Link href="/data-deletion" className="hover:text-ink">
                Data deletion
              </Link>
            </nav>
          </div>
          <p className="mt-3 text-center">
            Made with 💡 by{" "}
            <a
              href="https://www.linkedin.com/company/thebolgroup"
              target="_blank"
              rel="noopener noreferrer"
              className="text-brand hover:underline"
            >
              {COMPANY}
            </a>
          </p>
        </footer>
      </body>
    </html>
  );
}

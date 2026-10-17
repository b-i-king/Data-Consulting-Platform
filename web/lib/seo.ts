// ── Search-engine settings (SEO) ──────────────────────────────────────────────
// Everything that helps people find the site on Google and makes link previews
// look good, in ONE place. Same approach as the Video_Game_Stats_Tracker
// template (its web/app/layout.tsx + app/sitemap.ts), adapted to a data
// consulting business. Used by:
//   app/layout.tsx            page title, description, keywords, link previews,
//                             robots rules, Google verification, JSON-LD
//   app/sitemap.ts            the list of public pages for search engines
//   app/robots.ts             what crawlers may visit (not the private workspace)
//   app/opengraph-image.tsx   the 1200×630 preview image for shared links
//
// SET IN VERCEL (Project → Settings → Environment Variables):
//   NEXT_PUBLIC_SITE_URL                  your public address, e.g. https://yourdomain.com
//   NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION  the code from Google Search Console
//                                         (Add property → HTML tag → content="...")

import { SOCIAL_LINKS } from "@/components/SocialLinks";
import { SITE_NAME } from "./format";

/** The site's public address (no trailing "/"). Local fallback for development. */
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000").replace(/\/+$/, "");

export const COMPANY = "The BOL Group LLC";
export const X_HANDLE = "@BOL_Biz"; // from SOCIAL_LINKS (x.com/BOL_Biz)

/** Shown in Google results: ~150–160 characters, main keywords first. */
export const DESCRIPTION =
  "Data consulting for small and growing businesses: upload a spreadsheet, get it cleaned automatically, " +
  "see dashboards and answers, and book a free 30-minute call with a consultant.";

/** Short line for link previews (Open Graph / X cards). */
export const TAGLINE = "Your data, cleaned and explained — with a consultant when you want one.";

/** The seven services, used as search keywords and in the JSON-LD "offers". */
export const SERVICES = [
  "Data Cleaning",
  "Data Strategy & Discovery",
  "Data Exploration & Analysis",
  "Data Visualization",
  "Data Engineering",
  "Data Science & Machine Learning",
  "AI",
];

// Grouped like the template's list. Google gives meta keywords little weight
// today, but other search engines and AI assistants still read them.
export const KEYWORDS = [
  // Brand
  SITE_NAME, "The BOL Group", "BOL Group data consulting",
  // Primary
  "data consulting", "data consultant", "small business data consultant", "data analytics consulting",
  "business data analysis", "data consulting services",
  // Who it's for
  "minority-owned business data", "underrepresented business owners", "small business analytics",
  "data help for small businesses", "nonprofit data analysis",
  // What it does
  "data cleaning service", "clean my spreadsheet", "CSV cleaning", "Excel data cleaning",
  "business dashboards", "sales dashboard", "KPI dashboard", "data visualization services",
  "data engineering services", "data warehouse for small business", "predictive analytics",
  "machine learning consulting", "AI for small business", "ask questions about your data",
  // Integrations (index now, rank later)
  "QuickBooks data analysis", "QuickBooks reporting", "Shopify sales analysis",
  // Offer
  "free data consultation", "hourly data consultant",
];

/**
 * Structured data (JSON-LD) Google reads to understand the business: one
 * "@graph" block (Google's recommended pattern for several types on a page).
 *   WebSite              the site and its name
 *   ProfessionalService  the business: services, prices, social profiles
 */
export function jsonLd(rate = 80) {
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `${SITE_URL}/#website`,
        name: SITE_NAME,
        alternateName: [COMPANY, "BOL Data Consulting"],
        url: SITE_URL,
        description: DESCRIPTION,
        inLanguage: "en-US",
        publisher: { "@id": `${SITE_URL}/#business` },
      },
      // The logo Google can show for the business (512 x 512 PNG, app/logo.png/route.tsx).
      {
        "@type": "ImageObject",
        "@id": `${SITE_URL}/#logo`,
        url: `${SITE_URL}/logo.png`,
        contentUrl: `${SITE_URL}/logo.png`,
        width: 512,
        height: 512,
        caption: COMPANY,
      },
      {
        "@type": "ProfessionalService",
        "@id": `${SITE_URL}/#business`,
        name: COMPANY,
        url: SITE_URL,
        logo: { "@id": `${SITE_URL}/#logo` },
        image: `${SITE_URL}/logo.png`,
        email: "thebolgroup.llc@gmail.com",
        description: DESCRIPTION,
        areaServed: { "@type": "Country", name: "United States" },
        knowsAbout: SERVICES,
        // Your social accounts: tells Google these profiles belong to this business.
        sameAs: SOCIAL_LINKS.map((l) => l.href),
        makesOffer: [
          {
            "@type": "Offer",
            name: "Free 30-minute data consultation",
            price: "0",
            priceCurrency: "USD",
          },
          {
            "@type": "Offer",
            name: "Data consulting session",
            priceSpecification: {
              "@type": "UnitPriceSpecification",
              price: String(rate),
              priceCurrency: "USD",
              unitText: "hour",
            },
          },
          ...SERVICES.map((name) => ({ "@type": "Offer", itemOffered: { "@type": "Service", name } })),
        ],
      },
    ],
  };
}

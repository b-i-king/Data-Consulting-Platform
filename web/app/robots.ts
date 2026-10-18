// /robots.txt — tells search-engine crawlers what they may visit. Everything
// public is allowed; the private workspace (/workspace) is not, and the sitemap's
// address is listed so crawlers find every public page.

import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/seo";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: ["/workspace"] }],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}

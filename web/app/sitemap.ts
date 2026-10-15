// /sitemap.xml — the public pages search engines should know about (from the
// Video_Game_Stats_Tracker template's app/sitemap.ts). The signed-in workspace
// (/app) is private, so it isn't listed. Submit
// <your site>/sitemap.xml in Google Search Console once the site is live.

import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/seo";

export default function sitemap(): MetadataRoute.Sitemap {
  const page = (path: string, changeFrequency: "weekly" | "monthly" | "yearly", priority: number) => ({
    url: `${SITE_URL}${path}`,
    lastModified: new Date(),
    changeFrequency,
    priority,
  });
  return [
    page("", "weekly", 1.0), // home
    page("/start", "monthly", 0.9), // try it as a guest
    page("/signin", "monthly", 0.5),
    page("/privacy", "yearly", 0.3),
    page("/terms", "yearly", 0.3),
    page("/storage-terms", "yearly", 0.3),
    page("/data-deletion", "yearly", 0.3),
  ];
}

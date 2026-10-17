// The web app manifest (/manifest.webmanifest): the name, colors and icons a
// phone or browser uses when someone installs or pins the site, and one more
// place search engines read the logo from. Next.js links it from every page.

import type { MetadataRoute } from "next";
import { SITE_NAME } from "@/lib/format";
import { TAGLINE } from "@/lib/seo";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: SITE_NAME,
    short_name: SITE_NAME,
    description: TAGLINE,
    start_url: "/",
    display: "standalone",
    background_color: "#0e1117",
    theme_color: "#c8ac44",
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml" },
      { src: "/logo.png", sizes: "512x512", type: "image/png" },
      { src: "/apple-icon.png", sizes: "180x180", type: "image/png" },
    ],
  };
}

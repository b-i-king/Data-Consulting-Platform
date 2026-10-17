// GET /logo.png — the logo as a 512 x 512 PNG.
//
// Used by Google (the "logo" in the structured data, lib/seo.ts), the web app
// manifest (app/manifest.ts), and as the file to UPLOAD as your app's logo in
// the QuickBooks (developer.intuit.com), Xero (developer.xero.com) and
// Supabase (OAuth Apps) developer settings — that is what their sign-in
// screens show next to the tool's own logo. Drawn from lib/logo.tsx, so it
// always matches the site's icon.

import { ImageResponse } from "next/og";
import { LogoMark } from "@/lib/logo";

export const dynamic = "force-static"; // drawn once at build time

export function GET() {
  return new ImageResponse(<LogoMark size={512} />, { width: 512, height: 512 });
}

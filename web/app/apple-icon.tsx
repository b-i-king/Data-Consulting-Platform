// The home-screen icon for iPhones and iPads ("Add to Home Screen"): 180 x 180
// pixels. Next.js turns this file into /apple-icon.png and adds the
// <link rel="apple-touch-icon"> tag to every page. Drawn from lib/logo.tsx.

import { ImageResponse } from "next/og";
import { LogoMark } from "@/lib/logo";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(<LogoMark size={180} />, size);
}

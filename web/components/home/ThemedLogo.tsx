// ── A partner logo drawn in the site's colors ────────────────────────────────
// Every partner logo is only its shape on a transparent background (the API
// makes sure of it: api/services/partners.py). Here that shape is used as a
// STENCIL (CSS mask-image) and filled with a theme color:
//   * light theme -> the muted text color (dark gray), dark theme -> light gray;
//   * pointing at it (or keyboard focus on its link) -> the brand gold.
// So ten different brands read as one designed row, and a black logo never
// vanishes on the dark theme. The colors are the CSS variables in
// app/globals.css, so they follow the Light / Dark / System switch.
//
// The original colors are still visible to the business (Settings -> Account)
// and to admins reviewing it (`original`), where it matters what it really is.

import type { CSSProperties } from "react";

export default function ThemedLogo({ src, name, width, height, maxHeight = 48, maxWidth = 150, original = false }: {
  src: string;          // the logo's address (a URL, or a blob: address)
  name: string;         // read aloud by screen readers
  width: number;        // the logo's real size, for its shape
  height: number;
  maxHeight?: number;   // the box it's fitted into (px)
  maxWidth?: number;
  original?: boolean;   // true = its own colors (review screens)
}) {
  // Fit the logo's shape into the box without stretching it.
  const scale = Math.min(maxHeight / height, maxWidth / width, 1);
  const box = { width: Math.round(width * scale), height: Math.round(height * scale) };
  if (original) {
    // eslint-disable-next-line @next/next/no-img-element -- a small, already-sized WebP (or blob:)
    return <img src={src} alt={name} width={box.width} height={box.height} className="object-contain" />;
  }
  const mask: CSSProperties = {
    ...box,
    WebkitMaskImage: `url("${src}")`,
    maskImage: `url("${src}")`,
    WebkitMaskSize: "contain",
    maskSize: "contain",
    WebkitMaskRepeat: "no-repeat",
    maskRepeat: "no-repeat",
    WebkitMaskPosition: "center",
    maskPosition: "center",
  };
  return (
    <span role="img" aria-label={name} style={mask}
      className="block bg-ink-muted transition-colors group-hover:bg-brand group-focus-visible:bg-brand" />
  );
}

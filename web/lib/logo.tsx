// The logo, drawn as a picture (PNG) for places that can't use the SVG icon
// (app/icon.svg): Google's logo in search results (via the structured data in
// lib/seo.ts), phones' home-screen icons (app/apple-icon.tsx,
// app/manifest.ts), and the logo you upload to QuickBooks, Xero and Supabase
// for their sign-in screens (download it from /logo.png).
//
// Same design as app/icon.svg: a gold rounded square with three navy bars
// rising left to right. Colors are written out because CSS variables don't
// exist inside a generated image.

const GOLD = "#c8ac44";
const NAVY = "#0e1117";

/** The logo at any size (a square `size` pixels wide), for next/og's ImageResponse. */
export function LogoMark({ size }: { size: number }) {
  const u = size / 32; // app/icon.svg is drawn on a 32 x 32 grid
  const bar = (x: number, y: number, h: number) => (
    <div style={{ position: "absolute", left: x * u, top: y * u, width: 4 * u, height: h * u, background: NAVY,
      borderRadius: u }} />
  );
  return (
    <div style={{ width: size, height: size, display: "flex", position: "relative", background: GOLD,
      borderRadius: 7 * u }}>
      {bar(7, 17, 8)}
      {bar(14, 12, 13)}
      {bar(21, 7, 18)}
    </div>
  );
}

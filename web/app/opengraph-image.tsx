// The preview image shown when someone shares the site's link (Google, LinkedIn,
// Facebook, X, Discord, text messages): 1200×630 pixels. The template used a
// hand-made /public/og-image.png; here Next.js DRAWS it from this code at build
// time (next/og), so it always shows the current name and tagline. Colors are
// the VGST palette (navy-black + gold), written out because CSS variables
// don't exist inside a generated image. Replace with a designed PNG anytime:
// delete this file and put og-image.png in web/public/ instead.

import { ImageResponse } from "next/og";
import { SITE_NAME } from "@/lib/format";
import { TAGLINE } from "@/lib/seo";

export const alt = `${SITE_NAME} — data consulting for small businesses`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: 80,
          background: "#0e1117",
          color: "#e8e8e8",
        }}
      >
        {/* The logo's three bars */}
        <div style={{ display: "flex", alignItems: "flex-end", gap: 12, marginBottom: 40 }}>
          {[70, 110, 150].map((h) => (
            <div key={h} style={{ width: 40, height: h, background: "#c8ac44", borderRadius: 8 }} />
          ))}
        </div>
        <div style={{ fontSize: 72, fontWeight: 700, color: "#c8ac44" }}>{SITE_NAME}</div>
        <div style={{ fontSize: 36, marginTop: 24, maxWidth: 980, lineHeight: 1.3 }}>{TAGLINE}</div>
        <div style={{ fontSize: 28, marginTop: 40, color: "#9aa0ae" }}>
          Free 30-minute consultation · Cleaning · Dashboards · Analysis · AI
        </div>
      </div>
    ),
    size,
  );
}

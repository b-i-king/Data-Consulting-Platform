// ── Icons (instead of emoji) ──────────────────────────────────────────────────
// Thin line icons drawn with the CURRENT text color (stroke="currentColor"), so
// they follow the Light / Dark theme and any color a parent sets (a service's
// category color, the brand gold) — emoji look different on every device and
// never match the theme. 24 x 24 drawings, 1.75px lines with round ends.
//
// Decorative by default (aria-hidden): the text next to an icon says what it
// is. Pass `label` when an icon stands alone, so screen readers read it.

import type { ReactNode } from "react";
import { CATEGORY_ICON } from "@/lib/categories";

const PATHS: Record<string, ReactNode> = {
  // Services
  sparkles: <><path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6z" /><path d="M19 14l.8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8z" /></>,
  compass: <><circle cx="12" cy="12" r="9" /><path d="M15.5 8.5l-2 5-5 2 2-5z" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></>,
  chart: <><path d="M4 20h16" /><rect x="6" y="11" width="3" height="6" rx="1" /><rect x="11" y="7" width="3" height="10" rx="1" /><rect x="16" y="13" width="3" height="4" rx="1" /></>,
  database: <><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6" /><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" /></>,
  trend: <><path d="M4 17l5-5 4 3 7-8" /><path d="M15 7h5v5" /></>,
  beacon: <><circle cx="12" cy="12" r="2.5" /><path d="M7.5 7.5a6.5 6.5 0 000 9" /><path d="M16.5 7.5a6.5 6.5 0 010 9" /><path d="M4.5 4.5a10.5 10.5 0 000 15" /><path d="M19.5 4.5a10.5 10.5 0 010 15" /></>,
  // Settings and people
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5" /></>,
  users: <><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c0-3.5 2.9-5.5 6.5-5.5s6.5 2 6.5 5.5" /><path d="M16 4.6a3.5 3.5 0 010 6.8" /><path d="M18 14.8c2.2.6 3.5 2.4 3.5 5.2" /></>,
  card: <><rect x="3" y="5" width="18" height="14" rx="2.5" /><path d="M3 10h18" /><path d="M7 15h3" /></>,
  plug: <><path d="M9 3v5" /><path d="M15 3v5" /><path d="M6 8h12v3a6 6 0 01-12 0z" /><path d="M12 17v4" /></>,
  link: <><path d="M10 14a4.5 4.5 0 006.4 0l3-3a4.5 4.5 0 00-6.4-6.4l-1 1" /><path d="M14 10a4.5 4.5 0 00-6.4 0l-3 3a4.5 4.5 0 006.4 6.4l1-1" /></>,
  shield: <><path d="M12 3l7 3v6c0 4.5-3 7.8-7 9-4-1.2-7-4.5-7-9V6z" /><path d="M9 12l2 2 4-4" /></>,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.5 9.3a2.6 2.6 0 015 .9c0 1.8-2.5 2.3-2.5 3.8" /><path d="M12 17.2v.1" /></>,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M12 2.5v3M12 18.5v3M4.6 4.6l2.1 2.1M17.3 17.3l2.1 2.1M2.5 12h3M18.5 12h3M4.6 19.4l2.1-2.1M17.3 6.7l2.1-2.1" /></>,
  handshake: <><path d="M3 11l4-4 4 2 3-2 7 5" /><path d="M7 7l-4 4 6 6c.8.8 2 .8 2.8 0l5.2-5" /><path d="M10 14l1.5 1.5M12.5 11.5L15 14" /></>,
  wrench: <><path d="M14.5 6.5a4 4 0 00-5.3 5.3L3.5 17.5a2 2 0 002.8 2.8l5.7-5.7a4 4 0 005.3-5.3l-2.6 2.6-2.4-.4-.4-2.4z" /></>,
  // Small marks
  lock: <><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V8a4 4 0 018 0v3" /></>,
  bulb: <><path d="M9 18h6" /><path d="M10 21h4" /><path d="M12 3a6 6 0 00-3.6 10.8c.6.5 1 1.2 1 2V16h5.2v-.2c0-.8.4-1.5 1-2A6 6 0 0012 3z" /></>,
  pin: <><path d="M12 21s-6.5-6.1-6.5-11a6.5 6.5 0 0113 0c0 4.9-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></>,
  check: <><path d="M5 12.5l4.5 4.5L19 7" /></>,
  bolt: <><path d="M13 2L4.5 13.5H11L10 22l8.5-11.5H12z" /></>,
  download: <><path d="M12 4v11" /><path d="M7 10.5l5 5 5-5" /><path d="M5 20h14" /></>,
  tag: <><path d="M3 12V4a1 1 0 011-1h8l9 9-9 9z" /><circle cx="7.5" cy="7.5" r="1.5" /></>,
};

export type IconName = keyof typeof PATHS;

export default function Icon({ name, className = "h-5 w-5", label }: { name: string; className?: string; label?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round"
      strokeLinejoin="round" className={`inline-block shrink-0 ${className}`}
      role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      {PATHS[name] ?? <circle cx="12" cy="12" r="3" />}
    </svg>
  );
}

/** A service's icon (lib/categories.ts CATEGORY_ICON), in the current text color. */
export function CategoryIcon({ serviceId, className }: { serviceId: string; className?: string }) {
  return <Icon name={CATEGORY_ICON[serviceId] ?? "sparkles"} className={className} />;
}

// Header links that depend on whether someone is signed in (and the API
// wake-up call, since the header is on every page):
//   signed out -> "Sign in" + "Get started"
//   signed in  -> "My workspace"
// The session lives in this browser tab only (lib/session.ts), so this has to
// run in the browser; it shows the signed-out links until it has checked.

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { warmUpApi } from "@/lib/api";
import { loadSession, SESSION_EVENT } from "@/lib/session";

let warmed = false;   // wake the API once per page load, not on every navigation

export default function HeaderAuth() {
  const [signedIn, setSignedIn] = useState(false);
  // Re-check on every page change: signing in or out navigates to another page.
  const pathname = usePathname();
  useEffect(() => setSignedIn(loadSession() !== null), [pathname]);
  // ...and right away when someone signs in or out on the current page.
  useEffect(() => {
    const update = () => setSignedIn(loadSession() !== null);
    window.addEventListener(SESSION_EVENT, update);
    return () => window.removeEventListener(SESSION_EVENT, update);
  }, []);
  // The header is on every page, so this wakes a sleeping free-plan API while
  // the visitor is still reading — by the time they upload, it's awake.
  useEffect(() => {
    if (!warmed) {
      warmed = true;
      warmUpApi();
    }
  }, []);

  if (signedIn) {
    return (
      <Link href="/app" className="rounded-full bg-brand px-4 py-1.5 text-sm font-semibold text-on-brand hover:bg-brand-strong">
        My workspace
      </Link>
    );
  }
  return (
    <>
      <Link href="/signin" className="text-sm font-medium text-ink-muted hover:text-ink">
        Sign in
      </Link>
      <Link href="/start" className="rounded-full bg-brand px-4 py-1.5 text-sm font-semibold text-on-brand hover:bg-brand-strong">
        Get started
      </Link>
    </>
  );
}

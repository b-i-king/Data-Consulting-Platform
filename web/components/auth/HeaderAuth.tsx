// Header links that depend on whether someone is signed in (and the API
// wake-up call, since the header is on every page):
//   signed out -> "Sign in" + "Get started"
//   signed in  -> "My workspace" (consultants also get a lavender
//                 "Consultant" badge, so it's always clear which mode you're in)
// The session lives in this browser tab only (lib/session.ts), so this has to
// run in the browser; it shows the signed-out links until it has checked.

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { warmUpApi } from "@/lib/api";
import { loadSession, SESSION_EVENT, type Session } from "@/lib/session";
import { GET_STARTED, WORKSPACE } from "@/lib/routes";
import SiteMenu from "@/components/SiteMenu";

let warmed = false;   // wake the API once per page load, not on every navigation

export default function HeaderAuth() {
  const [session, setSession] = useState<Session | null>(null);
  const signedIn = session !== null;
  // Re-check on every page change: signing in or out navigates to another page.
  const pathname = usePathname();
  useEffect(() => setSession(loadSession()), [pathname]);
  // ...and right away when someone signs in or out on the current page.
  useEffect(() => {
    const update = () => setSession(loadSession());
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
      <>
        {session.account.role === "consultant" && (
          <Link
            href={WORKSPACE}
            title={`Signed in as a consultant (${session.account.email})`}
            aria-label="Consultant: open my workspace"
            className="flex items-center gap-1.5 whitespace-nowrap rounded-full bg-consultant px-3 py-1.5 text-sm font-semibold text-on-consultant"
          >
            {/* On a phone only the ◆ shows, so the header fits a 375px screen. */}
            <span aria-hidden>◆</span> <span className="hidden sm:inline">Consultant</span>
          </Link>
        )}
        <Link href={WORKSPACE} className="whitespace-nowrap rounded-full bg-brand px-3 py-1.5 text-sm font-semibold text-on-brand hover:bg-brand-strong sm:px-4">
          My workspace
        </Link>
        <SiteMenu signedIn />
      </>
    );
  }
  return (
    <>
      {/* Sign in is in the menu (and on the home page), so it isn't shown twice. */}
      <Link href={GET_STARTED} className="whitespace-nowrap rounded-full bg-brand px-3 py-1.5 sm:px-4 text-sm font-semibold text-on-brand hover:bg-brand-strong">
        Get started
      </Link>
      <SiteMenu signedIn={false} />
    </>
  );
}

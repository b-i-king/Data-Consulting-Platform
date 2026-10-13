// Header links that depend on whether someone is signed in:
//   signed out -> "Sign in" + "Get started"
//   signed in  -> "My workspace"
// The session lives in this browser tab only (lib/session.ts), so this has to
// run in the browser; it shows the signed-out links until it has checked.

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { loadSession } from "@/lib/session";

export default function HeaderAuth() {
  const [signedIn, setSignedIn] = useState(false);
  // Re-check on every page change: signing in or out navigates to another page.
  const pathname = usePathname();
  useEffect(() => setSignedIn(loadSession() !== null), [pathname]);

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

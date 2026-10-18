// The /signin and /signup pages' form: saves the session, then opens the
// workspace (/workspace) — or the workspace page the visitor was trying to
// reach (`next`, e.g. a bookmarked /workspace/settings/billing).
// Kept as a small client component so the page itself can stay a server component.
// The consultant picker inside the form loads its own list (fill bars).

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { saveSession } from "@/lib/session";
import { GET_STARTED, WORKSPACE } from "@/lib/routes";
import AuthForm from "./AuthForm";

/** Only workspace addresses on this site are followed (never another website). */
export const safeNext = (next?: string | null) =>
  next && /^\/workspace(\/[\w-]+)*\/?$/.test(next) ? next : WORKSPACE;

export default function SignInPanel({ initialMode, next }: { initialMode: "signup" | "signin"; next?: string | null }) {
  const router = useRouter();
  return (
    <section className="mx-auto max-w-xl">
      <AuthForm
        initialMode={initialMode}
        title={{ signup: "Create your account", signin: "Sign in" }}
        subtitle="Use your email address and password. Your workspace shows the services on your plan."
        onSignedIn={(s) => {
          saveSession(s);
          router.push(safeNext(next));
        }}
      />
      <p className="mt-6 text-sm text-ink-muted">
        Just looking?{" "}
        <Link href={GET_STARTED} className="text-brand-ink hover:underline">
          Start as a guest
        </Link>{" "}
        — no account needed until you download.
      </p>
    </section>
  );
}

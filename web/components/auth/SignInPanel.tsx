// The /signin page's form: saves the session, then opens the workspace (/app).
// Kept as a small client component so the page itself can stay a server component.
// The consultant picker inside the form loads its own list (fill bars).

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { saveSession } from "@/lib/session";
import AuthForm from "./AuthForm";

export default function SignInPanel({ initialMode }: { initialMode: "signup" | "signin" }) {
  const router = useRouter();
  return (
    <section className="mx-auto max-w-xl">
      <AuthForm
        initialMode={initialMode}
        title={{ signup: "Create your account", signin: "Sign in" }}
        subtitle="Use your email address and password. Your workspace shows the services on your plan."
        onSignedIn={(s) => {
          saveSession(s);
          router.push("/app");
        }}
      />
      <p className="mt-6 text-sm text-ink-muted">
        Just looking?{" "}
        <Link href="/start" className="text-brand hover:underline">
          Start as a guest
        </Link>{" "}
        — no account needed until you download.
      </p>
    </section>
  );
}

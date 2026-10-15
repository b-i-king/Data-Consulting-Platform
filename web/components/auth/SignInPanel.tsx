// The /signin page's form: saves the session, then opens the workspace (/app).
// Kept as a small client component so the page itself can stay a server component.
// Loads the default consultant's name for "I'd like Brandon to do this for me".

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getIntakeOptions } from "@/lib/api";
import { saveSession } from "@/lib/session";
import AuthForm from "./AuthForm";

export default function SignInPanel({ initialMode }: { initialMode: "signup" | "signin" }) {
  const router = useRouter();
  const [consultantName, setConsultantName] = useState<string | null>(null);
  useEffect(() => {
    getIntakeOptions()
      .then((o) => setConsultantName(o.consultant?.name ?? null))
      .catch(() => {});
  }, []);
  return (
    <section className="mx-auto max-w-xl">
      <AuthForm
        initialMode={initialMode}
        consultantName={consultantName}
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

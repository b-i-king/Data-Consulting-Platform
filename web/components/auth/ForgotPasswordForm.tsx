// "Forgot password?" form: type your email, get a reset link.
// POST /api/accounts/forgot-password — always the same answer, so it never
// reveals whether an email has an account.

"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { forgotPassword } from "@/lib/api";
import { emailError } from "@/lib/validation";
import { Button, ErrorBox, StepHeader } from "@/components/wizard/ui";

export default function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const problem = emailError(email);
    if (problem) return setError(problem);
    setBusy(true);
    setError(null);
    try {
      setSent((await forgotPassword(email.trim())).message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto max-w-md">
      <StepHeader title="Forgot your password?" subtitle="Type the email you signed up with. We'll send you a link to choose a new password." />
      {sent ? (
        <p role="status" className="rounded-2xl border border-good/40 bg-good/10 p-4 text-sm">
          {sent}
        </p>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          <label className="block space-y-1 text-sm">
            <span>Email address</span>
            <input
              type="email"
              inputMode="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
            />
          </label>
          <ErrorBox message={error} />
          <Button type="submit" disabled={busy}>
            {busy ? "Sending…" : "Send me a reset link"}
          </Button>
        </form>
      )}
      <p className="mt-6 text-sm text-ink-muted">
        Remembered it?{" "}
        <Link href="/signin" className="text-brand hover:underline">
          Back to sign in
        </Link>
      </p>
    </section>
  );
}

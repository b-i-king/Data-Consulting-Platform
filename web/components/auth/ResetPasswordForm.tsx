// The reset page's form: choose a new password (and confirm it).
// The token comes from the emailed link: Supabase puts it after "#"
// (#access_token=...&type=recovery; the part after # never reaches any server
// log), local mode as ?token=... Then POST /api/accounts/reset-password.

"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { resetPassword } from "@/lib/api";
import { Button, ErrorBox, StepHeader } from "@/components/wizard/ui";

const MIN_PASSWORD = 8; // same rule as api/services/auth.py
const field = "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20";

/** The token in the address: Supabase's #access_token, or ?token= in local mode. */
function tokenFromAddress(): { token: string | null; problem: string | null } {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  if (hash.get("error_description")) return { token: null, problem: hash.get("error_description") };
  const token = hash.get("access_token") ?? new URLSearchParams(window.location.search).get("token");
  return { token, problem: token ? null : "This page needs the link from your reset email. Ask for a new one below." };
}

export default function ResetPasswordForm() {
  const [token, setToken] = useState<string | null>(null);
  const [linkProblem, setLinkProblem] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const found = tokenFromAddress();
    // Read once the page is in the browser (the address isn't known on the server).
    const t = setTimeout(() => {
      setToken(found.token);
      setLinkProblem(found.problem);
    }, 0);
    // Take the token out of the address bar, so it isn't left in the history.
    if (found.token) window.history.replaceState(null, "", window.location.pathname);
    return () => clearTimeout(t);
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password.length < MIN_PASSWORD) return setError(`Use at least ${MIN_PASSWORD} characters.`);
    if (password !== confirm) return setError("The two passwords don't match.");
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      setDone((await resetPassword(token, password)).message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto max-w-md">
      <StepHeader title="Choose a new password" subtitle={`At least ${MIN_PASSWORD} characters.`} />
      {done ? (
        <div className="space-y-4">
          <p role="status" className="rounded-2xl border border-good/40 bg-good/10 p-4 text-sm">
            {done}
          </p>
          <Link href="/signin" className="inline-block rounded-full bg-brand px-5 py-2 text-sm font-semibold text-on-brand">
            Sign in
          </Link>
        </div>
      ) : linkProblem ? (
        <div className="space-y-4">
          <ErrorBox message={linkProblem} />
          <Link href="/forgot-password" className="text-sm text-brand-ink hover:underline">
            Send me a new link
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          <label className="block space-y-1 text-sm">
            <span>New password</span>
            <input type="password" autoComplete="new-password" className={field} value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label className="block space-y-1 text-sm">
            <span>Confirm password</span>
            <input type="password" autoComplete="new-password" className={field} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </label>
          <ErrorBox message={error} />
          <Button type="submit" disabled={busy || !token}>
            {busy ? "Saving…" : "Save my new password"}
          </Button>
        </form>
      )}
    </section>
  );
}

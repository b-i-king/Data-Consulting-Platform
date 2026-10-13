// Sign-up / sign-in form, by email address.
//
// Used in two places:
//   * the wizard's last step (create an account to download), and
//   * the /signin page (reached from the home page), which then opens the
//     signed-in workspace at /app.
//
// Signup asks: individual -> first name, last name, email; business -> plus
// company name and optional team members. Sign-in asks only for the email.
// ⚠️ Email-only sign-in is a placeholder — see api/routers/accounts.py.

"use client";

import { useState, type FormEvent } from "react";
import { signin, signup } from "@/lib/api";
import type { Session } from "@/lib/session";
import type { AccountType, Industry, TeamMemberInput } from "@/lib/types";
import { Button, Card, ChoiceCard, ErrorBox, StepHeader } from "@/components/wizard/ui";

const inputCls =
  "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20";

export default function AuthForm({
  defaultType = "individual",
  industry,
  serviceIds = [],
  onSignedIn,
  initialMode = "signup",
  title,
  subtitle,
}: {
  defaultType?: AccountType;
  industry?: Industry; // answered on the wizard's first step; sent only for business signups
  serviceIds?: string[]; // services chosen in the wizard; put on the new account's plan
  onSignedIn: (s: Session) => void;
  initialMode?: "signup" | "signin";
  title: { signup: string; signin: string };
  subtitle?: string;
}) {
  const [mode, setMode] = useState<"signup" | "signin">(initialMode);
  const [type, setType] = useState<AccountType>(defaultType);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [team, setTeam] = useState<TeamMemberInput[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateMember = (i: number, field: keyof TeamMemberInput, value: string) =>
    setTeam((t) => t.map((m, j) => (j === i ? { ...m, [field]: value } : m)));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res =
        mode === "signin"
          ? await signin(email)
          : await signup({
              account_type: type,
              first_name: first,
              last_name: last,
              email,
              ...(serviceIds.length ? { service_ids: serviceIds } : {}),
              ...(type === "business"
                ? { company_name: company, team_members: team.filter((m) => m.email.trim()), ...(industry ?? {}) }
                : {}),
            });
      onSignedIn({ token: res.access_token, account: res.account });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <StepHeader title={mode === "signup" ? title.signup : title.signin} subtitle={subtitle} />
      <form onSubmit={submit} className="max-w-xl space-y-4">
        {mode === "signup" && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <ChoiceCard label="Individual" selected={type === "individual"} onClick={() => setType("individual")} />
              <ChoiceCard label="Business" selected={type === "business"} onClick={() => setType("business")} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm">
                <span>First name</span>
                <input required className={inputCls} value={first} onChange={(e) => setFirst(e.target.value)} autoComplete="given-name" />
              </label>
              <label className="space-y-1 text-sm">
                <span>Last name</span>
                <input required className={inputCls} value={last} onChange={(e) => setLast(e.target.value)} autoComplete="family-name" />
              </label>
            </div>
          </>
        )}

        <label className="block space-y-1 text-sm">
          <span>Email address</span>
          <input required type="email" className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        </label>

        {mode === "signup" && type === "business" && (
          <>
            <label className="block space-y-1 text-sm">
              <span>Company name</span>
              <input required className={inputCls} value={company} onChange={(e) => setCompany(e.target.value)} autoComplete="organization" />
            </label>

            <Card>
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-sm font-medium">Team members (optional)</h2>
                <Button variant="secondary" onClick={() => setTeam((t) => [...t, { first_name: "", last_name: "", email: "" }])}>
                  Add a person
                </Button>
              </div>
              {team.length === 0 && <p className="text-sm text-ink-muted">You can also add colleagues later.</p>}
              <div className="space-y-3">
                {team.map((m, i) => (
                  <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_1.5fr_auto]">
                    <input required placeholder="First name" className={inputCls} value={m.first_name} onChange={(e) => updateMember(i, "first_name", e.target.value)} />
                    <input required placeholder="Last name" className={inputCls} value={m.last_name} onChange={(e) => updateMember(i, "last_name", e.target.value)} />
                    <input required type="email" placeholder="Email" className={inputCls} value={m.email} onChange={(e) => updateMember(i, "email", e.target.value)} />
                    <button type="button" className="text-sm text-bad hover:underline" onClick={() => setTeam((t) => t.filter((_, j) => j !== i))}>
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            </Card>
          </>
        )}

        <ErrorBox message={error} />

        <div className="flex flex-wrap items-center gap-4">
          <Button type="submit" disabled={busy}>
            {busy ? "One moment…" : mode === "signup" ? "Create account" : "Sign in"}
          </Button>
          <button
            type="button"
            className="text-sm text-brand hover:underline"
            onClick={() => {
              setMode(mode === "signup" ? "signin" : "signup");
              setError(null);
            }}
          >
            {mode === "signup" ? "Already have an account? Sign in" : "New here? Create an account"}
          </button>
        </div>
      </form>
    </>
  );
}

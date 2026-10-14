// Sign-up / sign-in form, by email address.
//
// Used in two places:
//   * the wizard's last step (create an account to download), and
//   * the /signin page (reached from the home page), which then opens the
//     signed-in workspace at /app.
//
// Signup asks: individual -> first name, last name, email, and (optional)
// location; business -> plus business name and optional team members.
// Sign-in asks only for the email. Guests never see these fields.
//
// FOOLPROOF INPUT (rules in lib/validation.ts, enforced again on the server):
//   * names: letters (accents fine), spaces, - ' ’ . ; the first letter is
//     capitalized for you as you type
//   * business name: letters, numbers and common company punctuation
//   * email: a real-looking address; each address only once in the form
//     (and the server refuses an address that already has an account)
//   * location: picked from the city list, never free text
// Messages appear under each field once you leave it (or press Create account).
//
// ⚠️ Email-only sign-in is a placeholder — see api/routers/accounts.py.

"use client";

import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { signin, signup } from "@/lib/api";
import type { Session } from "@/lib/session";
import type { AccountType, Industry, Location, TeamMemberInput } from "@/lib/types";
import { capitalizeFirst, companyNameError, emailError, personNameError, repeatedEmails } from "@/lib/validation";
import { Button, Card, ChoiceCard, ErrorBox, StepHeader } from "@/components/wizard/ui";
import LocationPicker from "./LocationPicker";

const inputCls = (bad: boolean) =>
  `w-full rounded-xl border bg-surface px-3 py-2 text-sm outline-none focus:ring-2 ${
    bad ? "border-bad focus:border-bad focus:ring-bad/20" : "border-line focus:border-brand focus:ring-brand/20"
  }`;

/** A labeled field that shows its error once the person has left it. */
function Field(props: { label: string; error: string | null; show: boolean; children: ReactNode; hint?: string }) {
  return (
    <label className="block space-y-1 text-sm">
      <span>{props.label}</span>
      {props.children}
      {props.show && props.error ? (
        <span role="alert" className="block text-xs text-bad">
          {props.error}
        </span>
      ) : (
        props.hint && <span className="block text-xs text-ink-muted">{props.hint}</span>
      )}
    </label>
  );
}

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
  const [location, setLocation] = useState<Location | null>(null);
  const [team, setTeam] = useState<TeamMemberInput[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which fields have been left (so errors don't shout while someone is still typing).
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitted, setSubmitted] = useState(false);
  const show = (key: string) => submitted || !!touched[key];
  const leave = (key: string) => () => setTouched((t) => ({ ...t, [key]: true }));

  const updateMember = (i: number, field: keyof TeamMemberInput, value: string) =>
    setTeam((t) => t.map((m, j) => (j === i ? { ...m, [field]: field === "email" ? value : capitalizeFirst(value) } : m)));

  // Every problem in the form, by field key. Empty = ready to send.
  const errors = useMemo(() => {
    const e: Record<string, string | null> = { email: emailError(email) };
    if (mode === "signup") {
      e.first = personNameError(first, "First name");
      e.last = personNameError(last, "Last name");
      if (type === "business") {
        e.company = companyNameError(company);
        const filled = team.filter((m) => m.first_name || m.last_name || m.email);
        filled.forEach((m, i) => {
          e[`m${i}first`] = personNameError(m.first_name, "First name");
          e[`m${i}last`] = personNameError(m.last_name, "Last name");
          e[`m${i}email`] = emailError(m.email);
        });
        const repeats = repeatedEmails([email, ...filled.map((m) => m.email)]);
        if (repeats.length) e.repeats = `Each person needs their own email address — used more than once: ${repeats.join(", ")}`;
      }
    }
    return e;
  }, [mode, type, first, last, email, company, team]);
  const hasErrors = Object.values(errors).some(Boolean);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    setError(null);
    if (hasErrors) return; // messages are now shown under each field
    setBusy(true);
    try {
      const res =
        mode === "signin"
          ? await signin(email.trim())
          : await signup({
              account_type: type,
              first_name: first.trim(),
              last_name: last.trim(),
              email: email.trim(),
              ...(location ? { location_id: location.id } : {}),
              ...(serviceIds.length ? { service_ids: serviceIds } : {}),
              ...(type === "business"
                ? {
                    company_name: company.trim(),
                    team_members: team.filter((m) => m.first_name || m.last_name || m.email),
                    ...(industry ?? {}),
                  }
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
      {/* noValidate: our own messages (above) replace the browser's generic ones. */}
      <form onSubmit={submit} noValidate className="max-w-xl space-y-4">
        {mode === "signup" && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <ChoiceCard label="Individual" selected={type === "individual"} onClick={() => setType("individual")} />
              <ChoiceCard label="Business" selected={type === "business"} onClick={() => setType("business")} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="First name" error={errors.first} show={show("first")}>
                <input
                  className={inputCls(show("first") && !!errors.first)}
                  value={first}
                  onChange={(e) => setFirst(capitalizeFirst(e.target.value))}
                  onBlur={leave("first")}
                  autoComplete="given-name"
                />
              </Field>
              <Field label="Last name" error={errors.last} show={show("last")}>
                <input
                  className={inputCls(show("last") && !!errors.last)}
                  value={last}
                  onChange={(e) => setLast(capitalizeFirst(e.target.value))}
                  onBlur={leave("last")}
                  autoComplete="family-name"
                />
              </Field>
            </div>
          </>
        )}

        <Field label="Email address" error={errors.email} show={show("email")}>
          <input
            type="email"
            inputMode="email"
            className={inputCls(show("email") && !!errors.email)}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={leave("email")}
            autoComplete="email"
          />
        </Field>

        {mode === "signup" && (
          <div className="space-y-1 text-sm">
            <span>
              Location <span className="text-ink-muted">(optional)</span>
            </span>
            <LocationPicker value={location} onChange={setLocation} />
          </div>
        )}

        {mode === "signup" && type === "business" && (
          <>
            <Field label="Business name" error={errors.company} show={show("company")}>
              <input
                className={inputCls(show("company") && !!errors.company)}
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                onBlur={leave("company")}
                autoComplete="organization"
              />
            </Field>

            <Card>
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-sm font-medium">Team members (optional)</h2>
                <Button variant="secondary" onClick={() => setTeam((t) => [...t, { first_name: "", last_name: "", email: "" }])}>
                  Add a person
                </Button>
              </div>
              {team.length === 0 && <p className="text-sm text-ink-muted">You can also add colleagues later.</p>}
              <div className="space-y-3">
                {team.map((m, i) => {
                  const err = (k: string) => (show(`m${i}${k}`) ? errors[`m${i}${k}`] : null);
                  return (
                    <div key={i} className="space-y-1">
                      <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1.5fr_auto]">
                        <input aria-label={`Team member ${i + 1} first name`} placeholder="First name" className={inputCls(!!err("first"))} value={m.first_name} onChange={(e) => updateMember(i, "first_name", e.target.value)} onBlur={leave(`m${i}first`)} />
                        <input aria-label={`Team member ${i + 1} last name`} placeholder="Last name" className={inputCls(!!err("last"))} value={m.last_name} onChange={(e) => updateMember(i, "last_name", e.target.value)} onBlur={leave(`m${i}last`)} />
                        <input aria-label={`Team member ${i + 1} email`} type="email" placeholder="Email" className={inputCls(!!err("email"))} value={m.email} onChange={(e) => updateMember(i, "email", e.target.value)} onBlur={leave(`m${i}email`)} />
                        <button type="button" className="text-sm text-bad hover:underline" onClick={() => setTeam((t) => t.filter((_, j) => j !== i))}>
                          Remove
                        </button>
                      </div>
                      {[err("first"), err("last"), err("email")].filter(Boolean).map((msg) => (
                        <p key={msg} role="alert" className="text-xs text-bad">
                          {msg}
                        </p>
                      ))}
                    </div>
                  );
                })}
              </div>
            </Card>
          </>
        )}

        {(submitted || touched.email) && errors.repeats && (
          <p role="alert" className="text-sm text-bad">
            {errors.repeats}
          </p>
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
              setSubmitted(false);
              setTouched({});
            }}
          >
            {mode === "signup" ? "Already have an account? Sign in" : "New here? Create an account"}
          </button>
        </div>
      </form>
    </>
  );
}

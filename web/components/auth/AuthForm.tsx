// Sign-up / sign-in form: email address + password.
//
// Used in two places:
//   * the wizard's last step (create an account to download), and
//   * the /signin page (reached from the home page), which then opens the
//     signed-in workspace at /app.
//
// Signup asks: individual -> first name, last name, email, password (typed
// twice), and (optional) location; business -> plus business name and optional
// team members. Sign-in asks for the email and password. Guests never see these fields.
//
// PASSWORDS (api/services/auth.py): at least 8 characters. Supabase Auth checks
// them when it's configured. If Supabase's "Confirm email" setting is on, a new
// sign-up gets no session yet: the form says "check your email" and switches to
// sign-in. A colleague (or a client a consultant added) has no password yet —
// the password typed at their FIRST sign-in becomes theirs.
//
// Signup also offers "I'd like <consultant> to do this for me" (a "done for
// you" client, assigned to the default consultant). A plan picked on "Your
// needs" is paid for after signup, on the download step (Stripe Checkout).
// CONSULTANTS use the same form plus their access code ("I'm a consultant"
// link, or shown automatically when the server asks for it).
//
// FOOLPROOF INPUT (rules in lib/validation.ts, enforced again on the server):
//   * names: letters (accents fine), spaces, - ' ’ . ; the first letter is
//     capitalized for you as you type
//   * business name: letters, numbers and common company punctuation
//   * email: a real-looking address; each address only once in the form
//     (and the server refuses an address that already has an account)
//   * location: picked from the city list, never free text
// Messages appear under each field once you leave it (or press Create account).

"use client";

import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { signin, signup } from "@/lib/api";
import type { Session } from "@/lib/session";
import type { AccountType, Industry, Location, TeamMemberInput } from "@/lib/types";
import { capitalizeFirst, companyNameError, emailError, personNameError, repeatedEmails } from "@/lib/validation";
import { Button, Card, ChoiceCard, ErrorBox, StepHeader } from "@/components/wizard/ui";
import LocationPicker from "./LocationPicker";

const MIN_PASSWORD = 8; // same rule as api/services/auth.py

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
  wantsConsultant: initialWants = false,
  consultantName = null,
}: {
  defaultType?: AccountType;
  industry?: Industry; // answered on the wizard's first step; sent only for business signups
  serviceIds?: string[]; // services chosen in the wizard; put on the new account's plan
  onSignedIn: (s: Session) => void;
  initialMode?: "signup" | "signin";
  title: { signup: string; signin: string };
  subtitle?: string;
  wantsConsultant?: boolean; // ticked on "Your needs"
  consultantName?: string | null; // the default consultant, e.g. "Brandon"
}) {
  const [mode, setMode] = useState<"signup" | "signin">(initialMode);
  const [type, setType] = useState<AccountType>(defaultType);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState(""); // sign-up: the password typed again
  const [notice, setNotice] = useState<string | null>(null); // e.g. "check your email"
  const [company, setCompany] = useState("");
  const [location, setLocation] = useState<Location | null>(null);
  const [team, setTeam] = useState<TeamMemberInput[]>([]);
  const [wants, setWants] = useState(initialWants);
  // Consultant sign-in: the access code box (hidden for everyone else).
  const [showCode, setShowCode] = useState(false);
  const [code, setCode] = useState("");
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
    e.password = !password
      ? "Enter your password."
      : mode === "signup" && password.length < MIN_PASSWORD
        ? `Use at least ${MIN_PASSWORD} characters.`
        : null;
    if (mode === "signup") {
      e.confirm = confirm !== password ? "The two passwords don't match." : null;
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
  }, [mode, type, first, last, email, password, confirm, company, team]);
  const hasErrors = Object.values(errors).some(Boolean);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    setError(null);
    setNotice(null);
    if (hasErrors) return; // messages are now shown under each field
    setBusy(true);
    try {
      const res =
        mode === "signin"
          ? await signin(email.trim(), password, showCode ? code.trim() : undefined)
          : await signup({
              account_type: type,
              first_name: first.trim(),
              last_name: last.trim(),
              email: email.trim(),
              password,
              ...(location ? { location_id: location.id } : {}),
              ...(serviceIds.length ? { service_ids: serviceIds } : {}),
              ...(wants ? { wants_consultant: true } : {}),
              ...(showCode && code.trim() ? { access_code: code.trim() } : {}),
              ...(type === "business"
                ? {
                    company_name: company.trim(),
                    team_members: team.filter((m) => m.first_name || m.last_name || m.email),
                    ...(industry ?? {}),
                  }
                : {}),
            });
      if (!res.access_token || res.needs_confirmation) {
        // Supabase wants the email confirmed first: no session yet.
        setNotice(res.message ?? "Check your email for a confirmation link, then sign in.");
        setMode("signin");
        setPassword("");
        setConfirm("");
        setSubmitted(false);
        return;
      }
      onSignedIn({ token: res.access_token, account: res.account });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Something went wrong.";
      if (message.includes("consultant access code")) setShowCode(true); // a consultant's email: ask for the code
      setError(message);
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

        <div className={mode === "signup" ? "grid gap-3 sm:grid-cols-2" : ""}>
          <Field
            label="Password"
            error={errors.password}
            show={show("password")}
            hint={mode === "signup" ? `At least ${MIN_PASSWORD} characters.` : "First time signing in to an account someone made for you? Choose your password here."}
          >
            <input
              type="password"
              className={inputCls(show("password") && !!errors.password)}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onBlur={leave("password")}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
            />
          </Field>
          {mode === "signup" && (
            <Field label="Password again" error={errors.confirm} show={show("confirm")}>
              <input
                type="password"
                className={inputCls(show("confirm") && !!errors.confirm)}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                onBlur={leave("confirm")}
                autoComplete="new-password"
              />
            </Field>
          )}
        </div>

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

        {mode === "signup" && (
          // "Done for you": a consultant does the work in this account.
          <label className="flex items-start gap-3 rounded-2xl border border-consultant/40 bg-consultant-soft p-4 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 accent-[var(--color-consultant)]"
              checked={wants}
              onChange={(e) => setWants(e.target.checked)}
            />
            <span>
              <span className="font-semibold">I&apos;d like {consultantName ?? "a consultant"} to do this for me</span>
              <span className="block text-xs text-ink-muted">
                No new platform to learn: your consultant signs in and does the cleaning, dashboards and analysis for you,
                with every tool unlocked. You can still log in and look around anytime.
              </span>
            </span>
          </label>
        )}

        {showCode ? (
          <label className="block space-y-1 text-sm">
            <span>Consultant access code</span>
            <input
              type="password"
              autoComplete="current-password"
              className={inputCls(false)}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
        ) : (
          <button type="button" className="text-xs text-ink-muted hover:text-consultant" onClick={() => setShowCode(true)}>
            I&apos;m a consultant
          </button>
        )}

        {(submitted || touched.email) && errors.repeats && (
          <p role="alert" className="text-sm text-bad">
            {errors.repeats}
          </p>
        )}
        {notice && (
          <p role="status" className="rounded-2xl border border-good/40 bg-good/10 p-3 text-sm">
            {notice}
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

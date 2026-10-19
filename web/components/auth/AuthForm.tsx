// Sign-up / sign-in form: email address + password.
//
// Used in two places:
//   * the wizard's last step (create an account to download), and
//   * the /signin page (reached from the home page), which then opens the
//     signed-in workspace at /workspace.
//
// Signup asks: individual -> first name, last name, email, password (and
// "Confirm password"), and (optional) location; business -> plus business name
// and optional team members. Then the TERMS BOX: "I agree to the Terms of
// Service and Privacy Policy" must be ticked before "Create account" works
// (the server refuses a sign-up without it too). Sign-in asks for the email
// and password. Guests never see these fields.
//
// PASSWORDS (api/services/auth.py): at least 8 characters. Supabase Auth checks
// them when it's configured. If Supabase's "Confirm email" setting is on, a new
// sign-up gets no session yet: the form says "check your email" and switches to
// sign-in. A colleague (or a client a consultant added) has no password yet —
// the password typed at their FIRST sign-in becomes theirs.
//
// Signup also offers "I'd like a consultant to do this for me": pick one by
// name (fill bars show how full each is) and one-time or monthly help
// (components/consultants/ConsultantPicker.tsx). A plan picked on "Your needs"
// is paid for after signup, on "Next steps" (Stripe Checkout).
// CONSULTANTS use the same form with no access code:
//   * SIGN IN: a clean "Account | Consultant" switch at the top. "Consultant"
//     only works for an approved consultant (the server says why otherwise).
//   * SIGN UP: "Signing up as" at the bottom — Client · I'm an admin (only
//     for an ADMIN_EMAILS address). There is NO consultant choice here: an
//     invited consultant signs up as a client, then sends the screening
//     application from Settings -> Account (an admin approves or denies it).
//     Nobody can make themselves a consultant. Admins don't see the
//     "I'd like a consultant" box.
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
// WRONG PASSWORDS (api/services/login_guard.py): each one says how many tries
// are left; after 3 in a row sign-in is locked for 15 minutes. When one try is
// left, or once locked, a "Reset my password" button appears under the error:
// it emails the reset link to the address typed (a reset unlocks at once).
//
// TWO-STEP SIGN-IN (api/services/mfa.py): when the account has it on, the
// password step answers "mfa_required" with a 5-minute ticket, and the form
// asks for the 6-digit code from the authenticator app (or a backup code).

"use client";

import Link from "next/link";
import { cloneElement, isValidElement, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, forgotPassword, getConsultants, signin, signinMfa, signup } from "@/lib/api";
import type { Session } from "@/lib/session";
import type { AccountType, ConsultantLoad, ConsultantPlan, Industry, Location, SignupAs, TeamMemberInput } from "@/lib/types";
import ConsultantPicker, { PLAN_LABELS } from "@/components/consultants/ConsultantPicker";
import { capitalizeFirst, companyNameError, emailError, personNameError, repeatedEmails } from "@/lib/validation";
import { Button, Card, ChoiceCard, ErrorBox, StepHeader } from "@/components/wizard/ui";
import LocationPicker from "./LocationPicker";

const MIN_PASSWORD = 8; // same rule as api/services/auth.py

const inputCls = (bad: boolean) =>
  `w-full rounded-xl border bg-surface px-3 py-2 text-sm outline-none focus:ring-2 ${
    bad ? "border-bad focus:border-bad focus:ring-bad/20" : "border-line focus:border-brand focus:ring-brand/20"
  }`;

/** A clean pill switch: one choice out of a few (Account | Consultant, ...). */
function Segmented<T extends string>(props: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: { id: T; label: string }[];
  accent?: "brand" | "consultant";
}) {
  const on = props.accent === "consultant" ? "bg-consultant text-on-consultant" : "bg-brand text-on-brand";
  return (
    <div role="radiogroup" aria-label={props.label} className="inline-flex flex-wrap gap-1 rounded-full border border-line bg-surface-2 p-1">
      {props.options.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={props.value === o.id}
          onClick={() => props.onChange(o.id)}
          className={`rounded-full px-4 py-1.5 text-sm font-semibold transition-colors ${
            props.value === o.id ? on : "text-ink-muted hover:text-ink"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

const SIGNUP_AS: { id: SignupAs; label: string; note: string }[] = [
  { id: "client", label: "Client", note: "Clean, explore and store your own data — or have a consultant do it. Invited to work as a consultant? Sign up here, then apply from Settings → Account." },
  { id: "admin", label: "I'm an admin", note: "Only for the platform's admin email addresses." },
];

/** A labeled field that shows its error once the person has left it. */
function Field(props: { label: string; error: string | null; show: boolean; children: ReactNode; hint?: string }) {
  // Screen readers announce a field with a problem as "invalid" (the message itself
  // is inside the label, so it's read with the field).
  const invalid = props.show && !!props.error;
  const child = isValidElement<{ "aria-invalid"?: boolean }>(props.children)
    ? cloneElement(props.children, { "aria-invalid": invalid || undefined })
    : props.children;
  return (
    <label className="block space-y-1 text-sm">
      <span>{props.label}</span>
      {child}
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
  consultants: givenConsultants,
  consultantId: initialPick = null,
  consultantPlan: initialPlan = "one_time",
}: {
  defaultType?: AccountType;
  industry?: Industry; // answered on the wizard's first step; sent only for business signups
  serviceIds?: string[]; // services chosen in the wizard; put on the new account's plan
  onSignedIn: (s: Session) => void;
  initialMode?: "signup" | "signin";
  title: { signup: string; signin: string };
  subtitle?: string;
  wantsConsultant?: boolean; // "Help from a consultant" picked on "Your needs"
  consultants?: ConsultantLoad[]; // the picker's list (fetched here when not given)
  consultantId?: string | null; // picked on "Your needs" (null = whoever has room)
  consultantPlan?: ConsultantPlan;
}) {
  const [mode, setMode] = useState<"signup" | "signin">(initialMode);
  const [type, setType] = useState<AccountType>(defaultType);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState(""); // sign-up: the password typed again
  const [notice, setNotice] = useState<string | null>(null); // e.g. "check your email"
  // Offer "Reset my password" after a wrong password (last try left) or once sign-in is locked.
  const [offerReset, setOfferReset] = useState(false);
  // Two-step sign-in: the ticket from the password step, and the code typed.
  const [mfaTicket, setMfaTicket] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  // When the code step appears, move the keyboard focus to its box (and screen
  // readers announce it) — done here rather than with autoFocus, which can
  // surprise screen-reader users when it fires on a normal page load.
  const codeRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (mfaTicket) codeRef.current?.focus();
  }, [mfaTicket]);
  const [company, setCompany] = useState("");
  const [location, setLocation] = useState<Location | null>(null);
  const [team, setTeam] = useState<TeamMemberInput[]>([]);
  const [wants, setWants] = useState(initialWants);
  const [pick, setPick] = useState<string | null>(initialPick);
  const [plan, setPlan] = useState<ConsultantPlan>(initialPlan);
  const [consultants, setConsultants] = useState<ConsultantLoad[]>(givenConsultants ?? []);
  const [acceptTerms, setAcceptTerms] = useState(false);
  const [signupAs, setSignupAs] = useState<SignupAs>("client"); // the choice at the bottom of sign-up
  const [signinAs, setSigninAs] = useState<"account" | "consultant">("account"); // the switch at the top of sign-in
  const isClient = signupAs === "client";
  // The consultant list for the picker, when the page didn't pass one (/signin).
  useEffect(() => {
    if (givenConsultants || !wants) return;
    getConsultants()
      .then((r) => setConsultants(r.consultants))
      .catch(() => {});
  }, [givenConsultants, wants]);
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
      e.terms = acceptTerms ? null : "Please accept the Terms of Service and Privacy Policy to continue.";
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
  }, [mode, type, first, last, email, password, confirm, company, team, acceptTerms]);
  const hasErrors = Object.values(errors).some(Boolean);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    setError(null);
    setNotice(null);
    setOfferReset(false);
    if (hasErrors) return; // messages are now shown under each field
    setBusy(true);
    try {
      const res =
        mode === "signin"
          ? await signin(email.trim(), password, signinAs === "consultant")
          : await signup({
              account_type: type,
              first_name: first.trim(),
              last_name: last.trim(),
              email: email.trim(),
              password,
              ...(location ? { location_id: location.id } : {}),
              ...(serviceIds.length ? { service_ids: serviceIds } : {}),
              ...(wants && isClient ? { wants_consultant: true, consultant_id: pick, consultant_plan: plan } : {}),
              signup_as: signupAs,
              accept_terms: acceptTerms,
              ...(type === "business"
                ? {
                    company_name: company.trim(),
                    team_members: team.filter((m) => m.first_name || m.last_name || m.email),
                    ...(industry ?? {}),
                  }
                : {}),
            });
      if (res.mfa_required && res.mfa_ticket) {
        // The password was right; now the authenticator code.
        setMfaTicket(res.mfa_ticket);
        setNotice(res.message ?? "Enter the 6-digit code from your authenticator app.");
        return;
      }
      if (!res.access_token || !res.account || res.needs_confirmation) {
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
      setError(err instanceof Error ? err.message : "Something went wrong.");
      // 429 = locked; a 401 that mentions resetting = one try left.
      if (mode === "signin" && err instanceof ApiError)
        setOfferReset(err.status === 429 || (err.status === 401 && err.message.includes("Reset your password")));
    } finally {
      setBusy(false);
    }
  }

  /** Step two: send the authenticator (or backup) code with the ticket. */
  async function submitCode(e: FormEvent) {
    e.preventDefault();
    if (!mfaTicket) return;
    setBusy(true);
    setError(null);
    try {
      const res = await signinMfa(mfaTicket, mfaCode.trim());
      if (res.access_token && res.account) onSignedIn({ token: res.access_token, account: res.account });
    } catch (err) {
      setError(err instanceof Error ? err.message : "That code didn't work.");
      if (err instanceof ApiError && err.status === 401 && err.message.includes("sign in again")) setMfaTicket(null);
      if (err instanceof ApiError) setOfferReset(err.status === 429);
    } finally {
      setBusy(false);
    }
  }

  /** Email the reset link to the address typed (same reply whether or not it has an account). */
  async function sendReset() {
    setBusy(true);
    try {
      setNotice((await forgotPassword(email.trim())).message);
      setError(null);
      setOfferReset(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't send the reset link.");
    } finally {
      setBusy(false);
    }
  }

  if (mfaTicket)
    return (
      <>
        <StepHeader title="Two-step sign-in" subtitle="Open your authenticator app and type the 6-digit code for this site." />
        <form onSubmit={submitCode} className="max-w-sm space-y-4" noValidate>
          <input
            className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-center font-mono text-lg tracking-widest outline-none focus:border-brand"
            value={mfaCode}
            onChange={(e) => setMfaCode(e.target.value)}
            inputMode="numeric"
            autoComplete="one-time-code"
            aria-label="6-digit code"
            placeholder="123456"
            ref={codeRef}
          />
          <p className="text-xs text-ink-muted">Lost your phone? Type one of your backup codes instead (like 7f3k-9q2m).</p>
          <ErrorBox message={error} />
          {offerReset && (
            <Button variant="secondary" onClick={sendReset} disabled={busy}>
              Reset my password
            </Button>
          )}
          <div className="flex flex-wrap items-center gap-4">
            <Button type="submit" disabled={busy || mfaCode.trim().length < 6}>
              {busy ? "Checking…" : "Verify"}
            </Button>
            <button type="button" className="text-sm text-brand-ink hover:underline"
              onClick={() => { setMfaTicket(null); setMfaCode(""); setNotice(null); setError(null); }}>
              Back to sign-in
            </button>
          </div>
        </form>
      </>
    );

  return (
    <>
      <StepHeader title={mode === "signup" ? title.signup : title.signin} subtitle={subtitle} />
      {/* noValidate: our own messages (above) replace the browser's generic ones. */}
      <form onSubmit={submit} noValidate className="max-w-xl space-y-4">
        {mode === "signin" && (
          <Segmented
            label="Sign in as"
            value={signinAs}
            onChange={setSigninAs}
            accent={signinAs === "consultant" ? "consultant" : "brand"}
            options={[
              { id: "account", label: "Account" },
              { id: "consultant", label: "Consultant" },
            ]}
          />
        )}
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
          {mode === "signin" && (
            // "Forgot password?" sits on the right of the Password label, like most apps.
            <div className="-mb-1 flex justify-end">
              <Link href="/forgot-password" className="text-xs text-brand-ink hover:underline">
                Forgot password?
              </Link>
            </div>
          )}
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
            <Field label="Confirm password" error={errors.confirm} show={show("confirm")}>
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

        {mode === "signup" && isClient && (
          // "Done for you": a consultant does the work in this account.
          <div className="space-y-3 rounded-2xl border border-consultant/40 bg-consultant-soft p-4 text-sm">
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-0.5 accent-[var(--color-consultant)]"
                checked={wants}
                onChange={(e) => setWants(e.target.checked)}
              />
              <span>
                <span className="font-semibold">I&apos;d like a consultant to do this for me</span>
                <span className="block text-xs text-ink-muted">
                  No new platform to learn: your consultant signs in and does the cleaning, dashboards and analysis for
                  you, with every tool unlocked. You can still log in and look around anytime.
                </span>
              </span>
            </label>
            {wants &&
              (initialWants && givenConsultants ? (
                // Already chosen on "Your needs": just say so.
                <p className="text-xs">
                  <strong>{PLAN_LABELS[plan].title}</strong> with{" "}
                  {consultants.find((c) => c.id === pick)?.name ?? "whoever has room"} (chosen on Your needs).
                </p>
              ) : (
                // The consultant panel slides in as soon as the box is ticked.
                <ConsultantPicker consultants={consultants} value={pick} onChange={setPick} plan={plan} onPlan={setPlan} autoOpen />
              ))}
          </div>
        )}

        {mode === "signup" && (
          // Who is signing up: a clean choice instead of a checkbox.
          <div className="space-y-2">
            <p className="text-sm font-medium">Signing up as</p>
            <Segmented
              label="Signing up as"
              value={signupAs}
              onChange={setSignupAs}
              accent={isClient ? "brand" : "consultant"}
              options={SIGNUP_AS.map((o) => ({ id: o.id, label: o.label }))}
            />
            <p className="text-xs text-ink-muted">{SIGNUP_AS.find((o) => o.id === signupAs)?.note}</p>
          </div>
        )}

        {mode === "signup" && (
          // The terms box: required before the account can be created.
          <div>
            <label className="flex items-start gap-3 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 accent-[var(--color-brand)]"
                checked={acceptTerms}
                onChange={(e) => setAcceptTerms(e.target.checked)}
                aria-invalid={show("terms") && !!errors.terms}
              />
              <span>
                I agree to the{" "}
                <Link href="/terms" target="_blank" className="text-brand-ink underline">
                  Terms of Service
                </Link>{" "}
                and{" "}
                <Link href="/privacy" target="_blank" className="text-brand-ink underline">
                  Privacy Policy
                </Link>
                .
              </span>
            </label>
            {show("terms") && errors.terms && (
              <p role="alert" className="mt-1 text-xs text-bad">
                {errors.terms}
              </p>
            )}
          </div>
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
        {offerReset && (
          <Button variant="secondary" onClick={sendReset} disabled={busy}>
            Reset my password
          </Button>
        )}

        <div className="flex flex-wrap items-center gap-4">
          <Button type="submit" disabled={busy || (mode === "signup" && !acceptTerms)}>
            {busy ? "One moment…" : mode === "signup" ? "Create account" : "Sign in"}
          </Button>
          <button
            type="button"
            className="text-sm text-brand-ink hover:underline"
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

// Step 5 — create an account (first time an account is required), then download.
//
// Signup fields:
//   individual: first name, last name, email
//   business:   + company name, + optional team members (name + email each)
//
// Once signed in, each dataset group gets CSV and Excel download buttons.
// Clicking one re-sends the files kept in memory; the server cleans them again
// and streams the result back (nothing is stored on the server).

"use client";

import { useState, type FormEvent } from "react";
import { downloadCleaned, signin, signup } from "@/lib/api";
import { fmtInt, fmtUsd } from "@/lib/format";
import type { Session } from "@/lib/session";
import type { AccountType, AnalyzeResponse, MissingStrategy, TeamMemberInput } from "@/lib/types";
import { Button, Card, ChoiceCard, ErrorBox, NavRow, StepHeader } from "./ui";

const inputCls =
  "w-full rounded-md border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20";

export default function StepAccount(props: {
  defaultType: AccountType;
  session: Session | null;
  onSignedIn: (s: Session) => void;
  onSignOut: () => void;
  files: File[];
  strategy: MissingStrategy;
  analysis: AnalyzeResponse;
  onBack: () => void;
}) {
  return (
    <section>
      {props.session ? (
        <DownloadPanel {...props} session={props.session} />
      ) : (
        <AuthPanel defaultType={props.defaultType} onSignedIn={props.onSignedIn} />
      )}
      <NavRow onBack={props.onBack} />
    </section>
  );
}

// ── Sign up / sign in ────────────────────────────────────────────────────────

function AuthPanel({ defaultType, onSignedIn }: { defaultType: AccountType; onSignedIn: (s: Session) => void }) {
  const [mode, setMode] = useState<"signup" | "signin">("signup");
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
              ...(type === "business"
                ? { company_name: company, team_members: team.filter((m) => m.email.trim()) }
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
      <StepHeader
        title={mode === "signup" ? "Create a free account to download" : "Welcome back"}
        subtitle="Your cleaned data is ready. We just need to know who to send it to."
      />
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

// ── Download (signed in) ─────────────────────────────────────────────────────

function DownloadPanel(props: {
  session: Session;
  onSignOut: () => void;
  files: File[];
  strategy: MissingStrategy;
  analysis: AnalyzeResponse;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [charged, setCharged] = useState<string | null>(null);
  const { account } = props.session;

  async function download(groupId: number, format: "csv" | "xlsx") {
    setBusy(`${groupId}-${format}`);
    setError(null);
    try {
      const { amountUsd } = await downloadCleaned({
        files: props.files,
        token: props.session.token,
        groupId,
        strategy: props.strategy,
        format,
      });
      if (amountUsd) setCharged(amountUsd);
    } catch (e) {
      // An expired token means the person must sign in again.
      setError(e instanceof Error ? e.message : "Download failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <StepHeader
        title={`You're all set, ${account.first_name}.`}
        subtitle="Download your cleaned data below. Processing is billed by the minute."
      />
      <div className="space-y-3">
        {props.analysis.groups.map((g) => (
          <Card key={g.group_id} className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="font-medium">{g.label}</div>
              <div className="text-sm text-ink-muted">
                {fmtInt(g.report.rows_after)} rows · {fmtInt(g.report.columns)} columns
              </div>
            </div>
            <div className="flex gap-2">
              <Button onClick={() => download(g.group_id, "csv")} disabled={busy !== null}>
                {busy === `${g.group_id}-csv` ? "Preparing…" : "Download CSV"}
              </Button>
              <Button variant="secondary" onClick={() => download(g.group_id, "xlsx")} disabled={busy !== null}>
                {busy === `${g.group_id}-xlsx` ? "Preparing…" : "Excel"}
              </Button>
            </div>
          </Card>
        ))}
      </div>

      <p className="mt-4 text-sm text-ink-muted">
        Estimated processing charge: {fmtUsd(props.analysis.runtime.amount_usd)} (
        {props.analysis.runtime.billable_minutes} billable minute
        {props.analysis.runtime.billable_minutes === 1 ? "" : "s"}).
        {charged && ` Last download recorded: $${charged}.`}
      </p>

      <ErrorBox message={error} />

      <p className="mt-6 text-sm text-ink-muted">
        Signed in as {account.email}.{" "}
        <button type="button" className="text-brand hover:underline" onClick={props.onSignOut}>
          Sign out
        </button>
      </p>
    </>
  );
}

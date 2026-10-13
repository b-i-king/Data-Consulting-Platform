// Step 5 — create an account (first time an account is required), then download.
//
// Signup fields (form in components/auth/AuthForm.tsx):
//   individual: first name, last name, email
//   business:   + company name, + optional team members (name + email each)
// The services chosen on "Your needs" are saved on the new account's plan,
// so the signed-in workspace (/app) opens them.
//
// Once signed in, each dataset group gets CSV and Excel download buttons.
// Clicking one re-sends the files kept in memory, plus the missing-value
// choices from step 3; the server cleans them again and streams the result
// back (nothing is stored on the server).

"use client";

import { useState } from "react";
import { downloadCleaned } from "@/lib/api";
import AuthForm from "@/components/auth/AuthForm";
import { fmtInt, fmtUsd } from "@/lib/format";
import type { Session } from "@/lib/session";
import type { AccountType, AnalyzeResponse, CleaningInput, Industry } from "@/lib/types";
import { Button, Card, ErrorBox, NavRow, StepHeader } from "./ui";


export default function StepAccount(props: {
  defaultType: AccountType;
  industry: Industry;
  serviceIds: string[]; // chosen on "Your needs"; saved on the new account's plan
  session: Session | null;
  onSignedIn: (s: Session) => void;
  onSignOut: () => void;
  input: CleaningInput;
  analysis: AnalyzeResponse;
  onBack: () => void;
}) {
  return (
    <section>
      {props.session ? (
        <DownloadPanel {...props} session={props.session} />
      ) : (
        <AuthForm
          defaultType={props.defaultType}
          industry={props.industry}
          serviceIds={props.serviceIds}
          onSignedIn={props.onSignedIn}
          title={{ signup: "Create a free account to download", signin: "Welcome back" }}
          subtitle="Your cleaned data is ready. We just need to know who to send it to."
        />
      )}
      <NavRow onBack={props.onBack} />
    </section>
  );
}

// ── Download (signed in) ─────────────────────────────────────────────────────

function DownloadPanel(props: {
  session: Session;
  onSignOut: () => void;
  input: CleaningInput;
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
        input: props.input,
        token: props.session.token,
        groupId,
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

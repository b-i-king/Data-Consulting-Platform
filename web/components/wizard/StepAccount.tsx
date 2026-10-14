// Step 5 — create an account (first time an account is required), then download.
//
// Signup fields (form in components/auth/AuthForm.tsx):
//   individual: first name, last name, email
//   business:   + company name, + optional team members (name + email each)
// The services chosen on "Your needs" are saved on the new account's plan,
// so the signed-in workspace (/app) opens them.
//
// If a plan was picked on "Your needs", a card offers to start it here
// (Stripe Checkout; in test mode it switches on at once). Download first:
// paying at Stripe leaves this page, and files live only in this tab.
//
// Once signed in, each dataset group gets CSV and Excel download buttons,
// with the estimated wait and charge. Clicking one first asks for
// confirmation (DownloadConfirm.tsx: rows × fields, time, total to pay);
// confirming re-sends the files kept in memory, plus the missing-value
// choices from step 3; the server cleans them again and streams the result
// back (nothing is stored on the server).

"use client";

import { useEffect, useState } from "react";
import { downloadCleaned, getMyServices, startCheckout } from "@/lib/api";
import AuthForm from "@/components/auth/AuthForm";
import { fmtInt, fmtUsd } from "@/lib/format";
import type { Session } from "@/lib/session";
import type { AccountType, AnalyzeResponse, CleaningInput, Industry, Subscription, SubscriptionChoice } from "@/lib/types";
import DownloadConfirm, { estimateLine, type DownloadFormat } from "./DownloadConfirm";
import { Button, Card, ErrorBox, NavRow, StepHeader } from "./ui";


export default function StepAccount(props: {
  defaultType: AccountType;
  industry: Industry;
  serviceIds: string[]; // chosen on "Your needs"; saved on the new account's plan
  subscription: SubscriptionChoice | null; // plan picked on "Your needs" (started on this step)
  plans: Subscription[];
  wantsConsultant: boolean; // "do it for me" picked on "Your needs"
  consultantName: string | null;
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
        <DownloadPanel {...props} session={props.session} subscription={props.subscription} plans={props.plans} />
      ) : (
        <AuthForm
          defaultType={props.defaultType}
          industry={props.industry}
          serviceIds={props.serviceIds}
          wantsConsultant={props.wantsConsultant}
          consultantName={props.consultantName}
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
  subscription: SubscriptionChoice | null;
  plans: Subscription[];
}) {
  // Downloads are free when the account's plan includes Data Cleaning.
  const [included, setIncluded] = useState(false);
  const [planNote, setPlanNote] = useState<string | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [planStarted, setPlanStarted] = useState(false);
  const chosen = props.subscription && props.plans.find((p) => p.id === props.subscription!.plan);
  useEffect(() => {
    getMyServices(props.session.token)
      .then((m) => {
        setIncluded(m.downloads_included);
        setPlanStarted(m.subscriptions.some((s) => s.plan_id === props.subscription?.plan));
      })
      .catch(() => {});
  }, [props.session.token, props.subscription?.plan]);

  async function startPlan() {
    if (!props.subscription) return;
    setPlanBusy(true);
    setPlanNote(null);
    try {
      const res = await startCheckout(props.session.token, props.subscription.plan, props.subscription.interval);
      if (res.url) {
        window.location.assign(res.url); // Stripe's secure page; it returns to the workspace
        return;
      }
      const m = await getMyServices(props.session.token);
      setIncluded(m.downloads_included);
      setPlanStarted(true);
      setPlanNote("Your plan is on (test mode — nothing was charged).");
    } catch (e) {
      setPlanNote(e instanceof Error ? e.message : "We couldn't start the plan.");
    } finally {
      setPlanBusy(false);
    }
  }
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [charged, setCharged] = useState<string | null>(null);
  // The download waiting for "Confirm" (null = no confirmation box open).
  const [asking, setAsking] = useState<{ groupId: number; format: DownloadFormat } | null>(null);
  const { account } = props.session;
  const askingGroup = asking && props.analysis.groups.find((g) => g.group_id === asking.groupId);

  async function download(groupId: number, format: DownloadFormat) {
    const group = props.analysis.groups.find((g) => g.group_id === groupId);
    setAsking(null);
    setBusy(`${groupId}-${format}`);
    setError(null);
    try {
      const { amountUsd } = await downloadCleaned({
        input: props.input,
        token: props.session.token,
        groupId,
        format,
        confirmedAmountUsd: group?.download_estimate.amount_usd,
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
      {chosen && props.subscription && !planStarted && (
        <Card className="mb-4 border-brand/40">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm">
              <strong>
                Start your {chosen.name} plan ({props.subscription.interval === "month" ? "monthly" : "yearly"}):{" "}
                {props.subscription.interval === "month" ? `${fmtUsd(chosen.monthly_usd)}/month` : `${fmtUsd(chosen.annual_usd)}/year`}
              </strong>
              <span className="block text-xs text-ink-muted">
                Cancel anytime. Download your files first — paying opens Stripe&apos;s secure page, and your files stay
                only in this tab.
              </span>
            </div>
            <Button onClick={startPlan} disabled={planBusy}>
              {planBusy ? "One moment…" : "Start my plan"}
            </Button>
          </div>
        </Card>
      )}
      {planNote && <p className="mb-4 text-sm text-good" role="status">{planNote}</p>}
      <div className="space-y-3">
        {props.analysis.groups.map((g) => (
          <Card key={g.group_id} className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="font-medium">{g.label}</div>
              <div className="text-sm text-ink-muted">
                {fmtInt(g.report.rows_after)} rows · {fmtInt(g.report.columns)} fields
              </div>
              <div className="text-xs text-ink-muted">CSV {estimateLine(g.download_estimate, "csv")} · Excel {estimateLine(g.download_estimate, "xlsx")}</div>
            </div>
            <div className="flex gap-2">
              <Button onClick={() => setAsking({ groupId: g.group_id, format: "csv" })} disabled={busy !== null}>
                {busy === `${g.group_id}-csv` ? "Preparing…" : "Download CSV"}
              </Button>
              <Button variant="secondary" onClick={() => setAsking({ groupId: g.group_id, format: "xlsx" })} disabled={busy !== null}>
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
        {charged && ` Last download charged: $${charged}.`}
      </p>

      <ErrorBox message={error} />

      {asking && askingGroup && (
        <DownloadConfirm
          tableLabel={askingGroup.label}
          format={asking.format}
          estimate={askingGroup.download_estimate}
          included={included}
          onConfirm={() => download(asking.groupId, asking.format)}
          onCancel={() => setAsking(null)}
        />
      )}

      <p className="mt-6 text-sm text-ink-muted">
        Signed in as {account.email}.{" "}
        <button type="button" className="text-brand hover:underline" onClick={props.onSignOut}>
          Sign out
        </button>
      </p>
    </>
  );
}

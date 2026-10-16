// Step 5 — "Next steps": create an account (the first time one is required),
// then do what was chosen on "Your needs".
//
// Signup fields (form in components/auth/AuthForm.tsx):
//   individual: first name, last name, email, password + confirm, terms box
//   business:   + company name, + optional team members (name + email each)
// The projects picked on "Your needs" are saved as interests; the consultant
// picked there (and one-time / monthly) is sent with the sign-up.
//
// Once signed in, the page leads with the path chosen on "Your needs":
//   * JUST DOWNLOAD — CSV and Excel buttons for each table, with the estimated
//     wait and charge. Clicking one first asks for confirmation
//     (DownloadConfirm.tsx); confirming re-sends the files kept in memory and
//     the choices from step 3; the server cleans them again and streams the
//     result back (nothing is stored).
//   * DO IT MYSELF — "Start my package" (Stripe Checkout; in test mode it
//     switches on at once). Download first if you want the file: paying at
//     Stripe leaves this page, and files live only in this tab.
//   * HELP FROM A CONSULTANT — who your consultant is, and "Share your data
//     with <consultant>": the Data Engineering load (components/explore/
//     WarehouseLoad.tsx) — a preview of how the data will be stored, the
//     storage consent, then "Accept and load" into fact.account_data. That is
//     what lets the consultant open the same data from their workspace.
//     WITHOUT this step nothing is stored (files are in this tab only), so the
//     consultant would have to ask for the files again.
//
// THE STORE POP-UP: as soon as the account's plan includes Data Engineering
// (a package with it, once started — "after they paid"), a pop-up opens on its
// own: "You're storing your data — here's a preview of everything that will be
// stored", with the load preview for each table (and the option to stack or
// join tables first). Accept = stored; close = nothing stored (a "Review and
// store" button stays on the page). Where the data came from decides how hard
// we push:
//   * files and business apps (QuickBooks, ...): the pop-up opens on its own —
//     storing is what Data Engineering is for;
//   * cloud storage (Snowflake, BigQuery, ...): the data is already structured
//     and lives somewhere safe, so we RECOMMEND analyzing it in place; storing
//     is offered as an option (no automatic pop-up).

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { chooseConsultant, downloadCleaned, getMyServices, startCheckout } from "@/lib/api";
import AuthForm from "@/components/auth/AuthForm";
import WarehouseLoad from "@/components/explore/WarehouseLoad";
import { PLAN_LABELS } from "@/components/consultants/ConsultantPicker";
import { fmtInt, fmtUsd } from "@/lib/format";
import type { Session } from "@/lib/session";
import type { AccountType, AnalyzeResponse, CleaningInput, ConsultantLoad, ConsultantPlan, Industry, MyServices, Subscription, SubscriptionChoice } from "@/lib/types";
import DownloadConfirm, { estimateLine, type DownloadFormat } from "./DownloadConfirm";
import { packageTotal } from "@/components/billing/PackageBuilder";
import type { NextPath } from "./StepServices";
import { Button, Card, ErrorBox, NavRow, StepHeader } from "./ui";

const ENGINEERING = "data_engineering";

export default function StepAccount(props: {
  /** Where the data lives (step 2): "flat_files", "cloud_storage" or "business_apps". */
  location: string | null;
  defaultType: AccountType;
  industry: Industry;
  serviceIds: string[]; // projects chosen on "Your needs"; saved as interests
  path: NextPath;
  subscription: SubscriptionChoice | null; // package picked on "Your needs" (started on this step)
  plans: Subscription[];
  wantsConsultant: boolean; // "Help from a consultant" picked on "Your needs"
  consultants: ConsultantLoad[];
  consultantId: string | null;
  consultantPlan: ConsultantPlan;
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
        <NextSteps {...props} session={props.session} />
      ) : (
        <AuthForm
          defaultType={props.defaultType}
          industry={props.industry}
          serviceIds={props.serviceIds}
          wantsConsultant={props.wantsConsultant}
          consultants={props.consultants}
          consultantId={props.consultantId}
          consultantPlan={props.consultantPlan}
          onSignedIn={props.onSignedIn}
          title={{ signup: "Create a free account for your next steps", signin: "Welcome back" }}
          subtitle="Your cleaned data is ready. We just need to know who it belongs to."
        />
      )}
      <NavRow onBack={props.onBack} />
    </section>
  );
}

// ── Next steps (signed in) ──────────────────────────────────────────────────

function NextSteps(props: {
  location: string | null;
  session: Session;
  onSignOut: () => void;
  input: CleaningInput;
  analysis: AnalyzeResponse;
  path: NextPath;
  subscription: SubscriptionChoice | null;
  plans: Subscription[];
  wantsConsultant: boolean;
  consultants: ConsultantLoad[];
  consultantId: string | null;
  consultantPlan: ConsultantPlan;
}) {
  const { token } = props.session;
  const [plan, setPlan] = useState<MyServices | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const chosen = props.subscription && props.subscription.plans.length ? props.subscription : null;
  const chosenNames = chosen ? chosen.plans.map((id) => props.plans.find((p) => p.id === id)?.name).join(" + ") : "";

  const refresh = useCallback(async () => {
    const m = await getMyServices(token);
    setPlan(m);
    return m;
  }, [token]);

  // Signed in BEFORE choosing a consultant on "Your needs" (an existing
  // account): ask for one now, so they can share the data straight away.
  useEffect(() => {
    getMyServices(token)
      .then(async (m) => {
        const needsOne = props.wantsConsultant && !m.account.consultant_name && m.can_add && m.account.role === "client";
        setPlan(needsOne ? await chooseConsultant(token, props.consultantId, props.consultantPlan) : m);
      })
      .catch((e) => setNote(e instanceof Error ? e.message : "We couldn't load your plan."));
  }, [token, props.wantsConsultant, props.consultantId, props.consultantPlan]);

  const planStarted = (plan?.subscriptions.length ?? 0) > 0;
  const canStore = !!plan?.active.includes(ENGINEERING);
  const consultantName = plan?.account.consultant_name ?? null;
  const name = plan ? (plan.account.company_name ?? `${plan.account.first_name} ${plan.account.last_name}`) : "";

  async function startPlan() {
    if (!chosen) return;
    setPlanBusy(true);
    setNote(null);
    try {
      const res = await startCheckout(token, chosen.plans, chosen.interval);
      if (res.url) {
        window.location.assign(res.url); // Stripe's secure page; it returns to the workspace
        return;
      }
      await refresh();
      setNote("Your package is on (test mode — nothing was charged).");
    } catch (e) {
      setNote(e instanceof Error ? e.message : "We couldn't start the package.");
    } finally {
      setPlanBusy(false);
    }
  }

  // The store pop-up: opens on its own once Data Engineering is on (unless the
  // data is in cloud storage), until it's closed or the data is stored.
  const cloud = props.location === "cloud_storage";
  const [dismissed, setDismissed] = useState(false);
  const [openedByHand, setOpenedByHand] = useState(false);
  const [storedOk, setStoredOk] = useState(false);
  const showStore = canStore && !storedOk && (openedByHand || (!cloud && !dismissed));
  const forConsultant = props.path === "consultant" ? consultantName : null;

  const download = <DownloadBox {...props} included={!!plan?.downloads_included} />;
  const store = canStore && (
    <Card className="mb-4 border-brand/40">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-sm">
          {storedOk ? (
            <strong>Your data is stored. ✓ {forConsultant ? `${forConsultant} can open it now.` : "It opens in your workspace."}</strong>
          ) : cloud ? (
            <>
              <strong>Your data is in cloud storage — we recommend analyzing it where it is.</strong>
              <span className="block text-xs text-ink-muted">
                It&apos;s already structured, so every service (analysis, dashboards, ML) can work on it directly. You can
                still store a copy here if you&apos;d like.
              </span>
            </>
          ) : (
            <>
              <strong>Store your cleaned data{forConsultant ? ` so ${forConsultant} can open it` : ""}.</strong>
              <span className="block text-xs text-ink-muted">Check the preview first — nothing is stored until you accept.</span>
            </>
          )}
        </div>
        {!storedOk && (
          <Button variant={cloud ? "secondary" : "primary"} onClick={() => setOpenedByHand(true)}>
            {cloud ? "Store a copy anyway" : "Review and store"}
          </Button>
        )}
      </div>
    </Card>
  );

  return (
    <>
      <StepHeader
        title={`Next steps, ${props.session.account.first_name}`}
        subtitle={
          props.path === "consultant"
            ? "Share your cleaned data with your consultant, so they can start."
            : props.path === "package"
              ? "Start your package, and download your cleaned data if you'd like a copy."
              : "Download your cleaned data below. Processing is billed by the minute."
        }
      />

      {props.path === "consultant" && (
        <Card className="mb-4 border-consultant/40 bg-consultant-soft text-sm">
          {consultantName ? (
            <p>
              <strong>Your consultant: {consultantName}</strong> ·{" "}
              {PLAN_LABELS[plan?.account.consultant_plan ?? props.consultantPlan].title}. They&apos;ll reach out for your free
              30-minute consultation.{" "}
              <a href={plan?.consulting.booking_url} target="_blank" rel="noreferrer" className="text-brand underline">
                Or book it now
              </a>
              .
            </p>
          ) : plan?.account.role === "consultant" ? (
            <p>You&apos;re a consultant — consultants don&apos;t hire one for themselves.</p>
          ) : (
            <p>
              <strong>You&apos;re first in line.</strong> Every consultant is fully booked right now; you&apos;ll be assigned as
              soon as one has room.
            </p>
          )}
        </Card>
      )}

      {props.path === "package" && chosen && !planStarted && (
        <Card className="mb-4 border-brand/40">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm">
              <strong>
                Start your package — {chosenNames} ({chosen.interval === "month" ? "monthly" : "yearly"}):{" "}
                {fmtUsd(packageTotal(props.plans, chosen.plans, chosen.interval))}/{chosen.interval === "month" ? "month" : "year"}
              </strong>
              <span className="block text-xs text-ink-muted">
                Cancel anytime. Download your files first if you want them — paying opens Stripe&apos;s secure page, and
                your files stay only in this tab.
              </span>
            </div>
            <Button onClick={startPlan} disabled={planBusy}>
              {planBusy ? "One moment…" : "Start my package"}
            </Button>
          </div>
        </Card>
      )}
      {note && <p className="mb-4 text-sm" role="status">{note}</p>}

      {/* Storing comes first (when the plan includes it), then the download. */}
      {store ||
        (props.path === "consultant" && (
          <Card className="mb-4 text-sm text-ink-muted">
            Sharing your data with your consultant needs Data Engineering in your package (the tools are paid here; your
            consultant&apos;s time on Cal.com). Meanwhile you can download it below.
          </Card>
        ))}
      <h2 className="mb-2 mt-6 font-semibold">{store ? "Want a copy too?" : "Download"}</h2>
      {download}

      {showStore && (
        <StoreDialog
          onClose={() => {
            setDismissed(true);
            setOpenedByHand(false);
          }}
        >
          <StoreBox token={token} analysis={props.analysis} input={props.input} name={name} canDelete={!!plan?.can_add}
            consultantName={forConsultant} onLoaded={() => setStoredOk(true)} />
        </StoreDialog>
      )}

      <p className="mt-6 text-sm text-ink-muted">
        Signed in as {props.session.account.email}.{" "}
        <Link href="/app" className="text-brand hover:underline">
          Open your workspace
        </Link>{" "}
        ·{" "}
        <button type="button" className="text-brand hover:underline" onClick={props.onSignOut}>
          Sign out
        </button>
      </p>
    </>
  );
}

/** The store pop-up: a large, scrollable dialog over the page. */
function StoreDialog({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Store your data">
      <div className="mx-auto max-w-4xl rounded-2xl border border-line bg-canvas p-5 shadow-[var(--shadow-card)]">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold">You&apos;re storing your data — here&apos;s a preview</h2>
            <p className="text-sm text-ink-muted">
              Everything below is exactly what will be stored. Accept to store it; close to store nothing.
            </p>
          </div>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** "Share your data": the Data Engineering load for each table (preview, consent, accept). */
function StoreBox(props: {
  token: string;
  analysis: AnalyzeResponse;
  input: CleaningInput;
  name: string;
  canDelete: boolean;
  consultantName: string | null;
  onLoaded?: () => void;
}) {
  const [active, setActive] = useState(0);
  const group = props.analysis.groups[active];
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-muted">
        {props.consultantName
          ? `Store your cleaned data in your account so ${props.consultantName} can open it from their workspace — `
          : "Store your cleaned data in your account so you can open it on every visit — "}
        nothing is stored until you check the preview and accept. You can delete it anytime.
      </p>
      {props.analysis.groups.length > 1 && (
        <div role="tablist" className="flex flex-wrap gap-2">
          {props.analysis.groups.map((g, i) => (
            <button
              key={g.group_id}
              role="tab"
              aria-selected={i === active}
              onClick={() => setActive(i)}
              className={`rounded-full border px-3 py-1.5 text-sm ${
                i === active ? "border-brand bg-brand-soft font-medium" : "border-line bg-surface hover:border-brand"
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>
      )}
      <WarehouseLoad
        key={`load-${group.group_id}`}
        token={props.token}
        input={props.input}
        groupId={group.group_id}
        clientName={props.name}
        canDelete={props.canDelete}
        onStoredChanged={() => {}}
        tables={props.analysis.groups.map((g) => ({ group_id: g.group_id, label: g.label, columns: g.preview.columns }))}
        relationships={props.analysis.categorization.relationships}
        onLoaded={props.onLoaded}
        compact
      />
    </div>
  );
}

/** CSV / Excel download for each table, each confirmed first (DownloadConfirm.tsx). */
function DownloadBox(props: { session: Session; input: CleaningInput; analysis: AnalyzeResponse; included: boolean }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [charged, setCharged] = useState<string | null>(null);
  // The download waiting for "Confirm" (null = no confirmation box open).
  const [asking, setAsking] = useState<{ groupId: number; format: DownloadFormat } | null>(null);
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
        Estimated processing charge: {fmtUsd(props.analysis.runtime.amount_usd)} ({props.analysis.runtime.billable_minutes}{" "}
        billable minute{props.analysis.runtime.billable_minutes === 1 ? "" : "s"}).
        {charged && ` Last download charged: $${charged}.`}
      </p>

      <ErrorBox message={error} />

      {asking && askingGroup && (
        <DownloadConfirm
          tableLabel={askingGroup.label}
          format={asking.format}
          estimate={askingGroup.download_estimate}
          included={props.included}
          onConfirm={() => download(asking.groupId, asking.format)}
          onCancel={() => setAsking(null)}
        />
      )}
    </>
  );
}

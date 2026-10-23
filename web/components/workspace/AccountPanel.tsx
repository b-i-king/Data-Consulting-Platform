// Settings -> Account: everything about YOU and the business you're working in.
//
//   Your profile        name, email, business, role (and your consultant)
//   Your team           business accounts: people, roles, 5 at most (TeamPanel.tsx)
//   Two-step sign-in, Your devices, Activity log (healthcare)  — SecurityPanel.tsx
//   Health information (HIPAA)  business owners: ask an admin to confirm (HipaaCard.tsx)
//   Password            "Reset password": we email you a link (Supabase Auth)
//   About your business what you do, your goals and the questions you ask
//                       most — dashboards are built around it (for a
//                       consultant working on a client: the CLIENT's business)
//   Work as a consultant  clients: ask to become one (an admin decides)
//   Update your most recent upload  replace the latest stored file
//   Sign out
//   Delete my account   (your own account only; never shown while working on a client)

"use client";

import { useEffect, useState } from "react";
import { getDashboards, saveBusinessContext, sendMyResetLink } from "@/lib/api";
import type { Account, BusinessContext, WarehouseCounts } from "@/lib/types";
import { PLAN_LABELS } from "@/components/consultants/ConsultantPicker";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";
import { AskToConsult, DeleteAccount, UpdateLatestUpload } from "./SettingsPanel";
import EnterpriseCard from "./EnterpriseCard";
import PartnerCard from "./PartnerCard";
import HipaaCard from "./HipaaCard";
import { ActivityLogCard, DevicesCard, TwoStepCard } from "./SecurityPanel";
import TeamPanel from "./TeamPanel";

// How a team role reads next to the business name.
const TEAM_ROLE: Record<string, string> = { owner: "owner", developer: "developer", member: "member, view only" };

const field = "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand";

export default function AccountPanel(props: {
  token: string;
  account: Account; // me
  onAccount: (a: Account) => void;
  acting: boolean; // a consultant/admin working on a client
  workspaceName: string; // whose workspace is open
  stored: WarehouseCounts | null;
  canStore: boolean;
  canChange: boolean; // plan holder (or their consultant)
  bookingUrl: string; // "Talk to us about Enterprise" when the team is full
  onToken: (token: string) => void; // a fresh token after turning on two-step sign-in
  onRefreshAccount: () => void; // reload the account (two-step sign-in changed)
  onStoredChanged: (counts: WarehouseCounts) => void;
  onSignOut: () => void;
  onDeleted: () => void;
}) {
  const a = props.account;
  return (
    <div className="max-w-3xl space-y-5">
      <Card>
        <h2 className="font-semibold">Your profile</h2>
        <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="text-ink-muted">Name</dt>
          <dd>{a.first_name} {a.last_name}</dd>
          <dt className="text-ink-muted">Email</dt>
          <dd>{a.email}</dd>
          {a.company_name && (
            <>
              <dt className="text-ink-muted">Business</dt>
              <dd>{a.company_name}{a.team_role ? ` (${TEAM_ROLE[a.team_role] ?? a.team_role})` : ""}</dd>
            </>
          )}
          <dt className="text-ink-muted">Role</dt>
          <dd>{a.is_admin ? "Admin and consultant" : a.role === "consultant" ? "Consultant" : "Client"}</dd>
          {a.consultant_name && (
            <>
              <dt className="text-ink-muted">Your consultant</dt>
              <dd>
                {a.consultant_name}
                {a.consultant_plan ? ` · ${PLAN_LABELS[a.consultant_plan].title}` : ""}
              </dd>
            </>
          )}
        </dl>
        <div className="mt-4 flex flex-wrap gap-3">
          <ResetPassword token={props.token} />
          <Button variant="secondary" onClick={props.onSignOut}>
            Sign out
          </Button>
        </div>
      </Card>

      {!props.acting && (
        <>
          <TwoStepCard token={props.token} account={a} onToken={props.onToken} onChanged={props.onRefreshAccount} />
          <DevicesCard token={props.token} />
        </>
      )}
      {/* Shown only for enterprise clients; the owner accepts the agreement here. */}
      <EnterpriseCard token={props.token} isOwner={!props.acting && (!a.team_id || a.team_role === "owner")} />
      {!props.acting && a.account_type === "business" && props.canChange && <PartnerCard token={props.token} />}
      {!props.acting && a.account_type === "business" && props.canChange && (
        <HipaaCard token={props.token} onChanged={props.onRefreshAccount} />
      )}
      {!props.acting && a.is_healthcare && props.canChange && <ActivityLogCard token={props.token} />}

      {!props.acting && a.team_id && <TeamPanel token={props.token} bookingUrl={props.bookingUrl} />}

      {(props.canChange || props.acting) && <BusinessContextCard token={props.token} name={props.workspaceName} />}

      {!props.acting && a.role !== "consultant" && <AskToConsult token={props.token} account={a} onAccount={props.onAccount} />}

      {props.stored?.latest_upload && props.canStore && props.canChange && (
        <UpdateLatestUpload token={props.token} latest={props.stored.latest_upload} name={props.workspaceName}
          onStoredChanged={props.onStoredChanged} />
      )}

      {!props.acting && <DeleteAccount token={props.token} account={a} onDeleted={props.onDeleted} />}
    </div>
  );
}

/** "Reset password": email myself a link (the same flow as "Forgot password?"). */
function ResetPassword({ token }: { token: string }) {
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      setNote((await sendMyResetLink(token)).message);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't send the link.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <Button onClick={send} disabled={busy || !!note}>
        {busy ? "Sending…" : "Reset password"}
      </Button>
      {note && <p className="text-xs text-good" role="status">{note}</p>}
      <ErrorBox message={error} />
    </div>
  );
}

/** "About your business": what the dashboards are built around. */
function BusinessContextCard({ token, name }: { token: string; name: string }) {
  const [ctx, setCtx] = useState<BusinessContext | null>(null);
  const [industry, setIndustry] = useState<string | null>(null);
  const [questions, setQuestions] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDashboards(token)
      .then((d) => {
        setCtx(d.context);
        setIndustry(d.industry);
        setQuestions(d.context.key_questions.join("\n"));
      })
      .catch(() => setCtx({ about: "", goals: "", key_questions: [] }));
  }, [token]);

  async function save() {
    if (!ctx) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await saveBusinessContext(token, { ...ctx, key_questions: questions.split("\n") });
      setCtx(r.context);
      setQuestions(r.context.key_questions.join("\n"));
      setNote("Saved. Your dashboard suggestions now follow it.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save.");
    } finally {
      setBusy(false);
    }
  }

  if (!ctx) return null;
  return (
    <Card>
      <h2 className="font-semibold">About {name}&apos;s business</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Dashboards are built around your business. Tell us what you do and what you want to know
        {industry ? ` (industry: ${industry})` : ""} — your dashboard then suggests the right questions and tabs.
      </p>
      <div className="mt-4 space-y-3">
        <label className="block space-y-1 text-sm">
          <span>What does the business do, and for whom?</span>
          <textarea rows={2} className={field} value={ctx.about} placeholder="Two neighborhood bakeries in Atlanta, mostly walk-in customers and catering."
            onChange={(e) => setCtx({ ...ctx, about: e.target.value })} />
        </label>
        <label className="block space-y-1 text-sm">
          <span>What are you trying to improve?</span>
          <textarea rows={2} className={field} value={ctx.goals} placeholder="Grow catering, cut waste on slow days."
            onChange={(e) => setCtx({ ...ctx, goals: e.target.value })} />
        </label>
        <label className="block space-y-1 text-sm">
          <span>The questions you ask most (one per line)</span>
          <textarea rows={3} className={`${field} font-mono text-xs`} value={questions}
            placeholder={"top 5 product by amount\ntotal amount by store"}
            onChange={(e) => setQuestions(e.target.value)} />
          <span className="block text-xs text-ink-muted">Plain English, like “total amount by store” or “top 5 product by amount in 2025”.</span>
        </label>
        <Button onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </Button>
        {note && <p className="text-sm text-good" role="status">{note}</p>}
        <ErrorBox message={error} />
      </div>
    </Card>
  );
}

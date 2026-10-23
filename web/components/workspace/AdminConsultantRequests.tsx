// Admin panel -> consultant SCREENING: the invite list and the applications.
//
//   * CONSULTANT INVITES: while applications are invite-only
//     (CONSULTANT_APPLICATIONS=invite on the server, the default), only these
//     emails can apply. Add an email, then send the person the sign-up link
//     yourself ("Copy the sign-up link"); they sign up with THAT email and
//     apply from Settings -> Account. Removing an invite stops new
//     applications only.
//   * CONSULTANT APPLICATIONS: waiting ones first, each with the screening
//     answers (services, experience range, tools, LinkedIn, portfolio, a
//     project) and how many times they've applied. The REVIEW CHECKLIST says
//     what to check before deciding. Approve = consultant mode at once (room
//     for 3 clients), and their onboarding starts. Deny = a reason from the
//     fixed list, PERSONAL FEEDBACK (required) and a waiting period (3, 6 or
//     12 months). They see the reason and the feedback, under a note that a
//     real person decided and that it's a judgment call.
//
// Server side: api/routers/admin.py, rules in api/services/consultants.py.

"use client";

import { useEffect, useMemo, useState } from "react";
import { addConsultantInvite, decideConsultantRequest, getIntakeOptions, removeConsultantInvite } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { SIGN_UP, workspacePath } from "@/lib/routes";
import type { AdminPanelData, ConsultantRequest, DenialReason } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const STATUS: Record<ConsultantRequest["status"], { label: string; cls: string }> = {
  pending: { label: "Waiting", cls: "bg-warn/15 text-warn" },
  approved: { label: "Approved", cls: "bg-good/15 text-good" },
  denied: { label: "Denied", cls: "bg-bad/15 text-bad" },
};

// What to check before deciding (the longer version: "Consultant screening" in docs/ARCHITECTURE.md).
const CHECKLIST = [
  "Identity: the LinkedIn profile is a real person with the same name, and their history looks genuine.",
  "Experience: enough hands-on work for client projects — usually 1–3 years or more, or strong work samples.",
  "Skills: the services they picked match their tools, experience and the project they described.",
  "Work sample: it exists, it's theirs, and it shows clean, careful work.",
  "Screening call: a short video call (15–30 minutes) — walk through a project and how they'd protect client data.",
  "Conduct: they agreed to the code of conduct; nothing in the call raised a confidentiality concern.",
];

/** The deny form for one application: reason, waiting period, note. */
type DenyDraft = { reason: DenialReason | ""; months: number; note: string };

export default function AdminConsultantRequests(props: {
  token: string;
  data: AdminPanelData;
  onData: (d: AdminPanelData) => void;
  onChanged?: () => void;
}) {
  const { token, data } = props;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);
  const [denying, setDenying] = useState<Record<string, DenyDraft>>({});
  const [serviceNames, setServiceNames] = useState<Record<string, string>>({});

  useEffect(() => {
    getIntakeOptions()
      .then((o) => setServiceNames(Object.fromEntries(o.services.map((s) => [s.id, s.name]))))
      .catch(() => {});
  }, []);

  // Run one admin action; the server sends back the refreshed panel.
  async function run(key: string, action: () => Promise<AdminPanelData>, after?: () => void) {
    setBusy(key);
    setError(null);
    try {
      props.onData(await action());
      after?.();
      props.onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(null);
    }
  }

  // The link an invited person signs up with (then lands on Settings -> Account to apply).
  const signupLink = useMemo(
    () => (typeof window === "undefined" ? "" : `${window.location.origin}${SIGN_UP}?next=${workspacePath({ settings: "account" })}`),
    [],
  );
  async function copyLink() {
    try {
      await navigator.clipboard.writeText(signupLink);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError(`Copy this link: ${signupLink}`);
    }
  }

  const minMonths = data.reapply_choices[0] ?? 3;
  const startDeny = (r: ConsultantRequest) =>
    setDenying((d) => ({ ...d, [r.account_id]: { reason: "", months: minMonths, note: "" } }));
  const updateDeny = (id: string, change: Partial<DenyDraft>) =>
    setDenying((d) => ({ ...d, [id]: { ...d[id], ...change } }));
  const stopDeny = (id: string) =>
    setDenying((d) => {
      const next = { ...d };
      delete next[id];
      return next;
    });

  const waiting = data.requests.filter((r) => r.status === "pending");
  const decided = data.requests.filter((r) => r.status !== "pending");
  const input = "rounded-xl border border-line bg-surface px-3 py-1.5 text-sm";

  return (
    <>
      <ErrorBox message={error} />

      <Card>
        <h2 className="font-semibold">Consultant invites</h2>
        <p className="mt-1 text-sm text-ink-muted">
          {data.applications_mode === "invite"
            ? "Applications are by invitation: only these emails can apply. "
            : "Applications are open to anyone with an account (CONSULTANT_APPLICATIONS=open); invites are optional. "}
          Add the person&apos;s email, then send them the sign-up link. They sign up with that email and apply from
          Settings → Account.
        </p>
        <form
          className="mt-3 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run("invite", () => addConsultantInvite(token, email.trim(), note.trim()), () => {
              setEmail("");
              setNote("");
            });
          }}
        >
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com"
            aria-label="Email to invite" className={`${input} min-w-0 flex-1`} />
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} placeholder="Note for admins (optional)"
            aria-label="Note" className={`${input} min-w-0 flex-1`} />
          <Button type="submit" className="px-4 py-1" disabled={busy !== null || !email.trim()}>
            {busy === "invite" ? "Adding…" : "Invite"}
          </Button>
        </form>
        <button type="button" onClick={copyLink} className="mt-2 text-sm text-brand-ink hover:underline">
          {copied ? "Copied ✓" : "Copy the sign-up link"}
        </button>
        {data.invites.length === 0 && <p className="mt-3 text-sm">Nobody is invited yet.</p>}
        <ul className="mt-2 divide-y divide-line">
          {data.invites.map((i) => (
            <li key={i.email} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              <span className="min-w-0">
                <span className="font-medium">{i.email}</span>
                <span className="block text-xs text-ink-muted">
                  {[i.created_at && `invited ${fmtDate(i.created_at)}`, i.has_account ? "has an account" : "no account yet", i.note]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </span>
              <Button variant="secondary" className="px-3 py-1" disabled={busy !== null}
                onClick={() => run(`rm:${i.email}`, () => removeConsultantInvite(token, i.email))}>
                {busy === `rm:${i.email}` ? "Removing…" : "Remove"}
              </Button>
            </li>
          ))}
        </ul>
      </Card>

      <Card>
        <h2 className="font-semibold">Consultant applications{waiting.length ? ` (${waiting.length} waiting)` : ""}</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Approve to give them consultant mode at once; they start with room for 3 clients. Denying needs a reason
          (they see it) and starts a waiting period before they can apply again.
        </p>
        <details className="mt-3 rounded-xl border border-line p-3 text-sm">
          <summary className="cursor-pointer font-medium">Review checklist</summary>
          <ol className="mt-2 list-inside list-decimal space-y-1 text-ink-muted">
            {CHECKLIST.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ol>
        </details>
        {waiting.length === 0 && <p className="mt-4 text-sm">Nobody is waiting right now.</p>}
        <ul className="mt-2 divide-y divide-line">
          {[...waiting, ...decided].map((r) => {
            const app = r.application;
            const deny = denying[r.account_id];
            return (
              <li key={r.account_id} className="space-y-2 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{r.name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS[r.status].cls}`}>
                    {STATUS[r.status].label}
                  </span>
                  {app && app.times_applied > 1 && (
                    <span className="text-xs text-ink-muted">applied {app.times_applied} times</span>
                  )}
                </div>
                <div className="text-xs text-ink-muted">
                  {[r.email, r.company_name, r.location].filter(Boolean).join(" · ")}
                  {r.requested_at && ` · applied ${fmtDate(r.requested_at)}`}
                  {r.decided_at && r.status !== "pending" && ` · decided ${fmtDate(r.decided_at)}`}
                </div>
                {app ? (
                  <dl className="grid gap-x-3 gap-y-1 text-xs sm:grid-cols-[9rem_minmax(0,1fr)]">
                    <dt className="text-ink-muted">Services</dt>
                    <dd>{app.services.map((s) => serviceNames[s] ?? s).join(" · ")}</dd>
                    <dt className="text-ink-muted">Experience</dt>
                    <dd>{app.experience_label} · {app.tools}</dd>
                    <dt className="text-ink-muted">LinkedIn</dt>
                    <dd className="truncate">
                      <a href={app.linkedin_url} target="_blank" rel="noreferrer" className="text-brand-ink hover:underline">
                        {app.linkedin_url}
                      </a>
                    </dd>
                    <dt className="text-ink-muted">Portfolio</dt>
                    <dd className="truncate">
                      {app.work_sample_url ? (
                        <a href={app.work_sample_url} target="_blank" rel="noreferrer" className="text-brand-ink hover:underline">
                          {app.work_sample_url}
                        </a>
                      ) : (
                        "none given"
                      )}
                    </dd>
                    <dt className="text-ink-muted">A project</dt>
                    <dd className="whitespace-pre-line">{app.project_summary}</dd>
                    {r.status === "denied" && app.denial_reason && (
                      <>
                        <dt className="text-ink-muted">Denied because</dt>
                        <dd>
                          {data.denial_reasons.find((d) => d.id === app.denial_reason)?.text}
                          {app.decision_note && ` Feedback: ${app.decision_note}`}
                          {app.reapply_after && ` · may apply again ${fmtDate(app.reapply_after)}`}
                        </dd>
                      </>
                    )}
                  </dl>
                ) : (
                  <p className="text-xs text-ink-muted">Asked before screening existed — no application answers.</p>
                )}

                {r.status === "pending" && !deny && (
                  <div className="flex gap-2">
                    <Button className="px-4 py-1" disabled={busy !== null}
                      onClick={() => run(r.account_id, () => decideConsultantRequest(token, r.account_id, "approve"))}>
                      {busy === r.account_id ? "One moment…" : "Approve"}
                    </Button>
                    <Button variant="secondary" className="px-4 py-1" disabled={busy !== null} onClick={() => startDeny(r)}>
                      Deny…
                    </Button>
                  </div>
                )}

                {r.status === "pending" && deny && (
                  <div className="space-y-2 rounded-xl border border-line p-3">
                    <label className="block">
                      <span className="text-xs font-medium">Reason (they&apos;ll see this)</span>
                      <select value={deny.reason} onChange={(e) => updateDeny(r.account_id, { reason: e.target.value as DenialReason })}
                        className={`${input} mt-1 w-full`}>
                        <option value="">Choose a reason…</option>
                        {data.denial_reasons.map((d) => (
                          <option key={d.id} value={d.id}>{d.text}</option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium">They can apply again after</span>
                      <select value={deny.months} onChange={(e) => updateDeny(r.account_id, { months: Number(e.target.value) })}
                        className={`${input} mt-1`}>
                        {data.reapply_choices.map((m) => (
                          <option key={m} value={m}>{m} months{m === minMonths ? " (minimum)" : ""}</option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium">Personal feedback (required — they&apos;ll read this)</span>
                      <textarea value={deny.note} maxLength={1500} rows={4}
                        onChange={(e) => updateDeny(r.account_id, { note: e.target.value })}
                        placeholder="In your own words: what you saw, and what would make a stronger application next time."
                        className={`${input} mt-1 w-full`} />
                      <span className="block text-xs text-ink-muted">
                        <span className="font-mono">{deny.note.trim().length}</span> / {data.min_feedback_chars}+ characters
                      </span>
                    </label>
                    <p className="rounded-lg bg-surface-2 p-2 text-xs text-ink-muted">
                      <span className="font-medium">They&apos;ll also see:</span> {data.human_review_note}
                    </p>
                    <div className="flex gap-2">
                      <Button className="px-4 py-1"
                        disabled={busy !== null || !deny.reason || deny.note.trim().length < data.min_feedback_chars}
                        onClick={() => run(r.account_id, () => decideConsultantRequest(token, r.account_id, "deny", {
                          denial_reason: deny.reason as DenialReason, wait_months: deny.months, note: deny.note.trim(),
                        }), () => stopDeny(r.account_id))}>
                        {busy === r.account_id ? "One moment…" : "Deny application"}
                      </Button>
                      <Button variant="secondary" className="px-4 py-1" disabled={busy !== null} onClick={() => stopDeny(r.account_id)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
    </>
  );
}

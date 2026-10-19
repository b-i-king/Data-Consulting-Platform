// Admin panel (🛡️ in the workspace's left pane, admins only — ADMIN_EMAILS).
//
//   * CONSULTANT INVITES + APPLICATIONS (AdminConsultantRequests.tsx): who may
//     apply, and the screening applications with a review checklist. Approving
//     makes them a consultant at once (their dim.consultants row is created;
//     their workspace switches to consultant mode on its next load). Denying
//     needs a reason they see and starts a waiting period (3+ months).
//   * CONSULTANTS: everyone, with a fill bar (clients ÷ capacity) and a
//     Deactivate / Reactivate switch, plus their ONBOARDING: when they agreed
//     to the independent-contractor terms, and which required steps are left. Deactivating moves their clients to
//     someone with room; they keep their own account (as a client).
//   * HIPAA REQUESTS (AdminHipaa.tsx): confirm a healthcare business before it stores health data.
//   * PRICES (AdminPrices.tsx): change what each tool costs (and its free trial), in one place.
//   * CONSULTANT HELP (AdminConsultingPrices.tsx): setup fee, ongoing-help rate and hours rule, platform share.
//   * SEASONAL SALES (AdminSales.tsx): yearly discount windows, switched on once.
//   * TESTERS (AdminTesters.tsx): people who try the paid tools for free.
//   * ACCOUNT DELETIONS: how many people deleted their account, and why —
//     anonymous counts only (dim.account_deletions), never who.
//
// Server side: api/routers/admin.py (checks admin status on every call).

"use client";

import { useEffect, useState } from "react";
import { getAdminPanel, setConsultantActive } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { AdminPanelData, TeamConsultant } from "@/lib/types";
import { LoadBar } from "@/components/consultants/ConsultantPicker";
import { Button, Card, ConfirmDialog, ErrorBox } from "@/components/wizard/ui";
import AdminConsultantRequests from "./AdminConsultantRequests";
import AdminHipaa from "./AdminHipaa";
import AdminConsultingPrices from "./AdminConsultingPrices";
import AdminPrices from "./AdminPrices";
import AdminSales from "./AdminSales";
import AdminTesters from "./AdminTesters";

// Stripe's cancellation reasons (its customer portal), in plain words.
const CANCEL_REASONS: Record<string, string> = {
  too_expensive: "Too expensive",
  missing_features: "Missing features",
  switched_service: "Switched to another service",
  unused: "Not using it enough",
  customer_service: "Customer service",
  too_complex: "Too complicated",
  low_quality: "Quality",
  other: "Other",
  none: "No reason given",
};

// The delete dialog's "Why are you leaving?" choices, as the deletions card shows them.
const REASONS: Record<string, string> = {
  too_expensive: "Too expensive",
  missing_features: "Missing something they needed",
  not_needed: "Don't need it anymore",
  switching_tools: "Switching to another tool",
  privacy: "Privacy",
  other: "Other",
  none: "No reason given",
};

export default function AdminPanel({ token, onChanged }: { token: string; onChanged?: () => void }) {
  const [data, setData] = useState<AdminPanelData | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Deactivating asks first (with an optional note); reactivating doesn't.
  const [confirming, setConfirming] = useState<TeamConsultant | null>(null);
  const [reason, setReason] = useState("");

  useEffect(() => {
    getAdminPanel(token)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load the admin panel."));
  }, [token]);

  async function switchConsultant(c: TeamConsultant, active: boolean) {
    setBusy(c.id);
    setError(null);
    try {
      setData(await setConsultantActive(token, c.id, active, reason));
      setConfirming(null);
      setReason("");
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5">
      <ErrorBox message={error} />
      {!data && !error && <p className="text-sm text-ink-muted">Loading…</p>}

      {data && <AdminConsultantRequests token={token} data={data} onData={setData} onChanged={onChanged} />}

      {data && (
        <Card>
          <h2 className="font-semibold">Consultants</h2>
          <p className="mt-1 text-sm text-ink-muted">
            Deactivating someone stops them being picked or opening clients; their clients move to a consultant with
            room. They keep their own account and can be reactivated any time.
          </p>
          {data.notice && (
            <p role="status" className="mt-3 rounded-xl border border-good/40 bg-good/10 p-2 text-sm">
              {data.notice}
            </p>
          )}
          <ul className="mt-2 divide-y divide-line">
            {data.team.map((c) => (
              <li key={c.id} className="flex flex-col gap-2 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <span className={c.active ? "font-medium" : "font-medium text-ink-muted"}>{c.name}</span>
                  {c.is_default && <span className="ml-2 text-xs text-ink-muted">(default)</span>}
                  {!c.active && (
                    <span className="ml-2 rounded-full bg-bad/15 px-2 py-0.5 text-[11px] font-semibold text-bad">
                      Deactivated
                    </span>
                  )}
                  <span className="block truncate text-xs text-ink-muted">
                    {c.email}
                    {c.created_at && ` · since ${fmtDate(c.created_at)}`}
                    {!c.active && c.deactivated_at && ` · off since ${fmtDate(c.deactivated_at)}`}
                    {!c.active && c.deactivation_reason && ` (${c.deactivation_reason})`}
                    {c.active && c.reactivated_at && ` · back since ${fmtDate(c.reactivated_at)}`}
                  </span>
                  {/* Onboarding: your confirmation they agreed to the contractor terms, and what's left. */}
                  <span className="block text-xs">
                    {c.contractor_agreed_at && c.contractor_terms_current ? (
                      <span className="text-good">✓ Contractor terms agreed {fmtDate(c.contractor_agreed_at)}</span>
                    ) : c.contractor_agreed_at ? (
                      <span className="text-warn">Agreed to older contractor terms ({fmtDate(c.contractor_agreed_at)}) — not the current ones yet</span>
                    ) : c.onboarding_left.some((l) => l.includes("contractor")) ? (
                      <span className="text-warn">Contractor terms not agreed yet</span>
                    ) : null}
                    {c.onboarding_done ? (
                      <span className="ml-2 text-good">· Onboarding complete</span>
                    ) : (
                      <span className="ml-2 text-ink-muted" title={c.onboarding_left.join(" · ")}>
                        · Onboarding: {c.onboarding_left.length} step{c.onboarding_left.length === 1 ? "" : "s"} left
                      </span>
                    )}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  {c.active && <LoadBar load={c} />}
                  {!c.is_default && (
                    <Button variant="secondary" className="px-4 py-1" disabled={busy !== null}
                      onClick={() => (c.active ? setConfirming(c) : switchConsultant(c, true))}>
                      {busy === c.id ? "One moment…" : c.active ? "Deactivate" : "Reactivate"}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {confirming && (
        <ConfirmDialog
          title={`Deactivate ${confirming.name}?`}
          confirmLabel={busy ? "Deactivating…" : "Deactivate"}
          onConfirm={() => !busy && switchConsultant(confirming, false)}
          onCancel={() => {
            setConfirming(null);
            setReason("");
          }}
        >
          <p>
            {confirming.clients
              ? `Their ${confirming.clients} client${confirming.clients === 1 ? "" : "s"} will move to a consultant with room (or wait for one if everyone is full).`
              : "They have no clients right now."}{" "}
            You can reactivate them later.
          </p>
          <input
            className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm"
            placeholder="Note for your records (optional), e.g. On leave until March"
            maxLength={300}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            aria-label="Reason (optional)"
          />
        </ConfirmDialog>
      )}

      <AdminHipaa token={token} />
      <AdminPrices token={token} />
      <AdminConsultingPrices token={token} />
      <AdminSales token={token} />
      <AdminTesters token={token} />

      {data?.cancellations && (
        <Card>
          <h2 className="font-semibold">Why clients cancel</h2>
          <p className="mt-1 text-sm text-ink-muted">
            From Stripe&apos;s cancel page (last 12 months). Turn on &quot;Collect cancellation reason&quot; in Stripe →
            Settings → Billing → Customer portal.
          </p>
          <div className="mt-3 font-mono text-2xl">{data.cancellations.total.toLocaleString("en-US")}</div>
          {Object.keys(data.cancellations.by_reason).length > 0 && (
            <ul className="mt-2 space-y-1 text-sm">
              {Object.entries(data.cancellations.by_reason)
                .sort((a, b) => b[1] - a[1])
                .map(([r, n]) => (
                  <li key={r} className="flex justify-between gap-3">
                    <span>{CANCEL_REASONS[r] ?? r}</span>
                    <span className="font-mono">{n.toLocaleString("en-US")}</span>
                  </li>
                ))}
            </ul>
          )}
          {data.cancellations.comments.length > 0 && (
            <ul className="mt-3 space-y-2 text-sm">
              {data.cancellations.comments.map((c, i) => (
                <li key={i} className="rounded-xl bg-surface-2 p-2">
                  &ldquo;{c.comment}&rdquo;
                  <span className="block text-xs text-ink-muted">
                    {CANCEL_REASONS[c.reason ?? "none"] ?? c.reason}{c.at ? ` · ${fmtDate(c.at)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {data && (
        <Card>
          <h2 className="font-semibold">Account deletions</h2>
          <p className="mt-1 text-sm text-ink-muted">
            Counts only: when someone deletes their account, everything about them is removed. We keep just that it
            happened, and the reason they picked (if any).
          </p>
          <div className="mt-3 flex flex-wrap gap-6 text-sm">
            <div>
              <div className="font-mono text-2xl">{data.deletions.recent.toLocaleString("en-US")}</div>
              <div className="text-xs text-ink-muted">last {data.deletions.recent_days} days</div>
            </div>
            <div>
              <div className="font-mono text-2xl">{data.deletions.total.toLocaleString("en-US")}</div>
              <div className="text-xs text-ink-muted">all time</div>
            </div>
          </div>
          {Object.keys(data.deletions.by_reason).length > 0 && (
            <ul className="mt-3 space-y-1 text-sm">
              {Object.entries(data.deletions.by_reason)
                .sort((a, b) => b[1] - a[1])
                .map(([r, n]) => (
                  <li key={r} className="flex justify-between gap-3">
                    <span>{REASONS[r] ?? r}</span>
                    <span className="font-mono">{n.toLocaleString("en-US")}</span>
                  </li>
                ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

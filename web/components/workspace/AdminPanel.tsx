// Admin panel (🛡️ in the workspace's left pane, admins only — ADMIN_EMAILS).
//
//   * CONSULTANT REQUESTS: everyone who ticked "I'd like to work as a
//     consultant". Waiting ones first, each with Approve / Deny. Approving
//     makes them a consultant at once (their dim.consultants row is created;
//     their workspace switches to consultant mode on its next load). Denied
//     people stay clients and may ask again.
//   * CONSULTANTS: every consultant's fill bar (clients ÷ capacity).
//
// Server side: api/routers/admin.py (checks admin status on every call).

"use client";

import { useEffect, useState } from "react";
import { decideConsultantRequest, getAdminPanel } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { AdminPanelData, ConsultantRequest } from "@/lib/types";
import { LoadBar } from "@/components/consultants/ConsultantPicker";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const STATUS: Record<ConsultantRequest["status"], { label: string; cls: string }> = {
  pending: { label: "Waiting", cls: "bg-warn/15 text-warn" },
  approved: { label: "Approved", cls: "bg-good/15 text-good" },
  denied: { label: "Denied", cls: "bg-bad/15 text-bad" },
};

export default function AdminPanel({ token, onChanged }: { token: string; onChanged?: () => void }) {
  const [data, setData] = useState<AdminPanelData | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAdminPanel(token)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load the admin panel."));
  }, [token]);

  async function decide(r: ConsultantRequest, decision: "approve" | "deny") {
    setBusy(r.account_id);
    setError(null);
    try {
      setData(await decideConsultantRequest(token, r.account_id, decision));
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(null);
    }
  }

  const waiting = data?.requests.filter((r) => r.status === "pending") ?? [];
  const decided = data?.requests.filter((r) => r.status !== "pending") ?? [];

  return (
    <div className="space-y-5">
      <ErrorBox message={error} />
      {!data && !error && <p className="text-sm text-ink-muted">Loading…</p>}

      {data && (
        <Card>
          <h2 className="font-semibold">Consultant requests</h2>
          <p className="mt-1 text-sm text-ink-muted">
            People who asked to work as a consultant. Approve to give them consultant mode at once; they start with room
            for 3 clients and can change it in their Settings.
          </p>
          {waiting.length === 0 && <p className="mt-4 text-sm">Nobody is waiting right now.</p>}
          <ul className="mt-4 divide-y divide-line">
            {[...waiting, ...decided].map((r) => (
              <li key={r.account_id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="text-sm">
                  <span className="font-medium">{r.name}</span>{" "}
                  <span className={`ml-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS[r.status].cls}`}>
                    {STATUS[r.status].label}
                  </span>
                  <span className="block text-xs text-ink-muted">
                    {[r.email, r.company_name, r.location].filter(Boolean).join(" · ")}
                    {r.requested_at && ` · asked ${fmtDate(r.requested_at)}`}
                  </span>
                </div>
                {r.status === "pending" && (
                  <div className="flex gap-2">
                    <Button onClick={() => decide(r, "approve")} disabled={busy !== null}>
                      {busy === r.account_id ? "One moment…" : "Approve"}
                    </Button>
                    <Button variant="secondary" onClick={() => decide(r, "deny")} disabled={busy !== null}>
                      Deny
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {data && (
        <Card>
          <h2 className="font-semibold">Consultants</h2>
          <ul className="mt-3 space-y-2">
            {data.consultants.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 text-sm">
                <span>
                  {c.name}
                  {c.is_default && <span className="ml-2 text-xs text-ink-muted">(default)</span>}
                </span>
                <LoadBar load={c} />
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

// Admin panel -> "HIPAA requests": confirm a healthcare business before it stores health information.
//
// Each request shows who asked, the practice's legal name, its NPI with a
// link to the public NPPES registry (check that the NPI exists, belongs to an
// organization with that name, and is active), and what they'd store.
// Approve: storing works again and every access goes to their activity log —
// sign a Business Associate Agreement (BAA) with them before patient data is
// uploaded. Deny (with a reason they'll see): they can store ordinary business
// data only. Server side: api/services/hipaa.py.

"use client";

import { useEffect, useState } from "react";
import { decideHipaa, getHipaaRequests } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { HipaaRequest } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const TONE: Record<string, string> = {
  pending: "bg-warn/15 text-warn", approved: "bg-good/15 text-good", denied: "bg-bad/15 text-bad",
};

export default function AdminHipaa({ token }: { token: string }) {
  const [rows, setRows] = useState<HipaaRequest[] | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHipaaRequests(token)
      .then((r) => setRows(r.requests))
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load the requests."));
  }, [token]);

  async function decide(r: HipaaRequest, decision: "approve" | "deny") {
    setBusy(r.account_id);
    setError(null);
    try {
      setRows((await decideHipaa(token, r.account_id, decision, notes[r.account_id])).requests);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(null);
    }
  }

  const waiting = rows?.filter((r) => r.status === "pending").length ?? 0;

  return (
    <Card>
      <h2 className="font-semibold">HIPAA requests{waiting ? ` (${waiting} waiting)` : ""}</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Businesses that want to store health information. Check the NPI in the registry (it should exist, be active and
        match the name). Their storing is paused until you decide. After approving, sign a Business Associate Agreement
        with them before they upload patient data.
      </p>
      <ErrorBox message={error} />
      {rows?.length === 0 && <p className="mt-3 text-sm">No requests yet.</p>}
      <ul className="mt-2 divide-y divide-line">
        {rows?.map((r) => (
          <li key={r.account_id} className="space-y-2 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{r.organization ?? r.company_name ?? r.name}</span>
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${TONE[r.status ?? "pending"]}`}>
                {r.label}
              </span>
              {r.naics_sector === "62" && <span className="text-xs text-ink-muted">(Health Care industry)</span>}
            </div>
            <div className="text-xs text-ink-muted">
              {r.name} · {r.email}
              {r.requested_at && ` · asked ${fmtDate(r.requested_at)}`}
              {r.decided_at && ` · decided ${fmtDate(r.decided_at)}`}
            </div>
            <div className="text-xs">
              NPI:{" "}
              {r.npi ? (
                <a href={r.lookup_url ?? "#"} target="_blank" rel="noreferrer" className="font-mono text-brand hover:underline">
                  {r.npi} (look it up)
                </a>
              ) : (
                <span className="text-warn">not given yet</span>
              )}
              {r.note && <span className="block text-ink-muted">Would store: {r.note}</span>}
              {r.decision_note && <span className="block text-ink-muted">Your note: {r.decision_note}</span>}
            </div>
            {r.status === "pending" && (
              <div className="flex flex-wrap items-center gap-2">
                <input className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-3 py-1.5 text-sm"
                  placeholder="Note to them (optional), e.g. NPI checked" value={notes[r.account_id] ?? ""}
                  onChange={(e) => setNotes((n) => ({ ...n, [r.account_id]: e.target.value }))} aria-label="Note" />
                <Button className="px-4 py-1" disabled={busy !== null} onClick={() => decide(r, "approve")}>
                  {busy === r.account_id ? "One moment…" : "Approve"}
                </Button>
                <Button variant="secondary" className="px-4 py-1" disabled={busy !== null} onClick={() => decide(r, "deny")}>
                  Deny
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

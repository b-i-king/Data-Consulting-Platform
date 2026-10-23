"use client";

// ── Admin: move a client to another consultant ───────────────────────────────
// Find a client (name, email or company), pick an active consultant, move them
// (api/services/consultants.py reassign_client). The client's plan, stored
// data and connections belong to the client, so the new consultant can open
// them at once and the old one can't. Ongoing help paid to the old consultant
// ends now — the result lists what to refund in the Stripe dashboard.

import { useState } from "react";
import { reassignClient, searchAdminAccounts } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import type { AdminPerson, TeamConsultant } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const field = "rounded-xl border border-line bg-surface px-3 py-2 text-sm";

export default function AdminReassign({ token, team, onChanged }: {
  token: string;
  team: TeamConsultant[];
  onChanged?: () => void;
}) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<AdminPerson[] | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});   // client id -> consultant id
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const active = team.filter((c) => c.active);
  const nameOf = (id: string | null) => team.find((c) => c.id === id)?.name ?? "No consultant";

  async function search() {
    setError(null);
    try {
      setFound((await searchAdminAccounts(token, q)).accounts.filter((a) => a.role === "client"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search didn't work.");
    }
  }

  async function move(p: AdminPerson) {
    setError(null);
    setDone(null);
    try {
      const r = await reassignClient(token, p.id, picked[p.id]);
      const refunds = r.help_ended.map((e) => `${fmtUsd(e.amount_usd)}/${e.billing_interval ?? "period"}`).join(", ");
      setDone(`${r.client} moved from ${r.from ?? "no consultant"} to ${r.to}.` +
        (refunds ? ` Their ongoing help with ${r.from} ended — refund the unused time in Stripe (${refunds}).` : "") +
        (r.over_capacity ? ` Note: ${r.to} is now over their client limit.` : ""));
      setFound((list) => list?.map((a) => (a.id === p.id ? { ...a, consultant_id: picked[p.id] } : a)) ?? null);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    }
  }

  return (
    <Card>
      <h3 className="font-semibold">Move a client to another consultant</h3>
      <p className="mt-1 text-sm text-ink-muted">
        Their plan, stored data and connections go with them. Ongoing help paid to the old consultant ends now.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <input className={`${field} min-w-0 flex-1`} value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && search()} placeholder="Client name, email or company" aria-label="Find a client" />
        <Button variant="secondary" onClick={search}>Find</Button>
      </div>
      {found && found.length === 0 && <p className="mt-2 text-sm text-ink-muted">No clients found.</p>}
      <ul className="mt-3 space-y-2">
        {(found ?? []).map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-2 rounded-2xl border border-line p-3 text-sm">
            <div className="min-w-0 flex-1">
              <p className="font-semibold">{p.company_name || p.name}</p>
              <p className="truncate text-ink-muted">{p.email} · now: {nameOf(p.consultant_id)}</p>
            </div>
            <select className={field} value={picked[p.id] ?? ""} aria-label={`New consultant for ${p.name}`}
              onChange={(e) => setPicked((m) => ({ ...m, [p.id]: e.target.value }))}>
              <option value="">Choose a consultant…</option>
              {active.filter((c) => c.id !== p.consultant_id).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.clients}/{c.capacity})
                </option>
              ))}
            </select>
            <Button disabled={!picked[p.id]} onClick={() => move(p)}>Move</Button>
          </li>
        ))}
      </ul>
      {done && <p role="status" className="mt-3 text-sm text-good">{done}</p>}
      <ErrorBox message={error} />
    </Card>
  );
}

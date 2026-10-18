// Admin panel -> "Testers": let chosen people try the paid tools for free.
//
// A TESTER (dim.accounts.is_tester) goes through the normal "pick a package"
// flow, but it switches on at once with no charge — even when Stripe is live —
// and their downloads are free. Real clients still pay. The switch is on the
// plan holder (an individual, or a business's owner; colleagues follow the
// owner). Server side: api/routers/admin.py and billing.set_tester (which
// refuses while someone pays through Stripe, and ends a tester's free package
// when the switch is turned off).
//
// Each row also has the security switches: HEALTHCARE (their data access goes
// to the activity log — set automatically for NAICS 62), SIGN OUT EVERYWHERE
// (a lost laptop), and RESET TWO-STEP (lost phone and backup codes).

"use client";

import { useEffect, useState } from "react";
import { adminResetMfa, adminSignOut, searchAdminAccounts, setHealthcare, setTester } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { AdminPerson } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

export default function AdminTesters({ token }: { token: string }) {
  const [q, setQ] = useState("");
  const [people, setPeople] = useState<AdminPerson[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Search as the admin types (a short pause first, so we don't call on every key).
  useEffect(() => {
    const timer = setTimeout(() => {
      searchAdminAccounts(token, q)
        .then((r) => setPeople(r.accounts))
        .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load the accounts."));
    }, 250);
    return () => clearTimeout(timer);
  }, [token, q]);

  const [notice, setNotice] = useState<string | null>(null);

  /** Run one admin change on a person; a returned account replaces their row. */
  async function act(p: AdminPerson, work: () => Promise<{ account?: AdminPerson; signed_out?: number }>) {
    setBusy(p.id);
    setError(null);
    setNotice(null);
    try {
      const res = await work();
      if (res.account) {
        const account = res.account;
        setPeople((list) => list?.map((x) => (x.id === account.id ? account : x)) ?? null);
      }
      if (res.signed_out !== undefined) setNotice(`${p.name}: signed out on ${res.signed_out} device(s).`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(null);
    }
  }
  const toggle = (p: AdminPerson) => act(p, () => setTester(token, p.id, !p.is_tester));

  return (
    <Card>
      <h2 className="font-semibold">Testers</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Testers try any package for free, even once payments are live: it switches on at once and downloads are free.
        Turning a tester off ends their free package.
      </p>
      <input
        className="mt-3 w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand"
        placeholder="Search by name, email or company"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label="Search accounts"
      />
      <ErrorBox message={error} />
      {notice && <p role="status" className="mt-2 text-sm text-good">{notice}</p>}
      {people === null && !error && <p className="mt-3 text-sm text-ink-muted">Loading…</p>}
      {people?.length === 0 && <p className="mt-3 text-sm">No accounts match.</p>}
      <ul className="mt-2 divide-y divide-line">
        {people?.map((p) => (
          <li key={p.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 text-sm">
              <span className="font-medium">{p.company_name ?? p.name}</span>
              {p.is_tester && (
                <span className="ml-2 rounded-full bg-brand/15 px-2 py-0.5 text-[11px] font-semibold text-brand">Tester</span>
              )}
              {p.is_healthcare && (
                <span className="ml-2 rounded-full bg-warn/15 px-2 py-0.5 text-[11px] font-semibold text-warn">Healthcare</span>
              )}
              {p.role !== "client" && <span className="ml-2 text-xs text-ink-muted">({p.role})</span>}
              <span className="block truncate text-xs text-ink-muted">
                {p.company_name ? `${p.name} · ` : ""}
                {p.email}
                {p.created_at && ` · joined ${fmtDate(p.created_at)}`}
                {p.updated_at && ` · changed ${fmtDate(p.updated_at)}`}
                {p.has_plan && " · has a package"}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {p.role !== "admin" && (
                <Button variant={p.is_tester ? "secondary" : "primary"} className="px-4 py-1" disabled={busy !== null}
                  onClick={() => toggle(p)}>
                  {busy === p.id ? "One moment…" : p.is_tester ? "Stop testing" : "Make tester"}
                </Button>
              )}
              <Button variant="secondary" className="px-3 py-1" disabled={busy !== null}
                onClick={() => act(p, () => setHealthcare(token, p.id, !p.is_healthcare))}>
                {p.is_healthcare ? "Not healthcare" : "Healthcare"}
              </Button>
              <Button variant="secondary" className="px-3 py-1" disabled={busy !== null}
                onClick={() => act(p, () => adminSignOut(token, p.id))}>
                Sign out everywhere
              </Button>
              {p.mfa_enabled && (
                <Button variant="secondary" className="px-3 py-1" disabled={busy !== null}
                  onClick={() => act(p, () => adminResetMfa(token, p.id))}>
                  Reset two-step
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

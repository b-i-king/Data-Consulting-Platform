"use client";

// ── Admin: enterprise clients (Settings -> Administration) ────────────────────
// The list of enterprise clients (contracts ending soonest first), and a form to
// set or change one client's contract: the tag, price and billing, setup fee,
// contract dates, included services, raised limits and private notes (e.g. the
// Wave invoice numbers). Everything is saved on the client's account
// (api/services/enterprise.py) and takes effect at once — no Stripe, no redeploy.

import { useCallback, useEffect, useState } from "react";
import { getEnterpriseClients, getEnterpriseContract, searchAdminAccounts, setEnterpriseContract } from "@/lib/api";
import { fmtDate, fmtInt, fmtUsd } from "@/lib/format";
import type { AdminPerson, EnterpriseSummary } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const field = "rounded-xl border border-line bg-surface px-3 py-2 text-sm";
const SERVICES: [string, string][] = [
  ["data_cleaning", "Data Cleaning"], ["data_visualization", "Dashboard"], ["data_engineering", "Data Engineering"],
  ["data_exploration_analysis", "Exploration & Analysis"], ["data_science_ml", "Data Science & ML"], ["ai", "AI"],
];
const LIMITS: [string, string][] = [["max_upload_mb", "Upload size (MB)"], ["team_seats", "Team seats"],
  ["table_rows", "Rows per database table"]];

type Form = Record<string, string | boolean | string[]>;

function toForm(s: EnterpriseSummary): Form {
  return {
    is_enterprise: s.is_enterprise, enterprise_price_usd: s.price_usd?.toString() ?? "", enterprise_billing: s.billing ?? "",
    enterprise_setup_fee_usd: s.setup_fee_usd?.toString() ?? "", enterprise_contract_start: s.contract_start ?? "",
    enterprise_contract_end: s.contract_end ?? "", enterprise_services: s.services, enterprise_notes: s.notes ?? "",
    ...Object.fromEntries(LIMITS.map(([k]) => [`limit_${k}`, s.raw_limits?.[k]?.toString() ?? ""])),
  };
}

export default function AdminEnterprise({ token }: { token: string }) {
  const [clients, setClients] = useState<(EnterpriseSummary & { account_id: string; name: string; email: string })[]>([]);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<AdminPerson[] | null>(null);
  const [open, setOpen] = useState<{ id: string; name: string; summary: EnterpriseSummary } | null>(null);
  const [form, setForm] = useState<Form>({});
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getEnterpriseClients(token).then((r) => setClients(r.clients)).catch(() => setClients([]));
  }, [token]);
  useEffect(load, [load]);

  async function pick(id: string, name: string) {
    setError(null);
    setDone(null);
    try {
      const summary = await getEnterpriseContract(token, id);
      setOpen({ id, name, summary });
      setForm(toForm(summary));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't open that account.");
    }
  }

  async function save() {
    if (!open) return;
    setError(null);
    try {
      const limits = Object.fromEntries(LIMITS.map(([k]) => [k, form[`limit_${k}`] || null]));
      const body = { ...Object.fromEntries(Object.entries(form).filter(([k]) => !k.startsWith("limit_"))), enterprise_limits: limits };
      const summary = await setEnterpriseContract(token, open.id, body);
      setOpen({ ...open, summary });
      setForm(toForm(summary));
      setDone(`Saved for ${open.name}.`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save.");
    }
  }

  const set = (k: string) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const services = (form.enterprise_services as string[]) ?? [];

  return (
    <Card>
      <h3 className="font-semibold">Enterprise clients</h3>
      <p className="mt-1 text-sm text-ink-muted">
        Custom contracts: they skip checkout; you invoice them yourself (e.g. from Wave). The tag unlocks the services
        and raises the limits below while the contract dates are current.
      </p>

      {clients.length > 0 && (
        <ul className="mt-3 divide-y divide-line rounded-xl border border-line text-sm">
          {clients.map((c) => (
            <li key={c.account_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span>
                <strong>{c.name}</strong> <span className="text-ink-muted">· {c.email}</span>
                <span className="block text-xs text-ink-muted">
                  {c.active ? "Active" : "Not active"}
                  {c.price_usd !== null && ` · ${fmtUsd(c.price_usd)} ${c.billing ?? ""}`}
                  {c.contract_end && ` · ends ${fmtDate(c.contract_end)}`}
                  {c.agreement.needs_acceptance ? " · agreement not accepted yet" : " · agreement accepted"}
                </span>
              </span>
              <Button variant="secondary" onClick={() => pick(c.account_id, c.name)}>Edit</Button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <input className={`${field} min-w-0 flex-1`} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find a client"
          onKeyDown={(e) => e.key === "Enter" && searchAdminAccounts(token, q).then((r) => setFound(r.accounts.filter((a) => a.role === "client")))}
          placeholder="Find a client to make enterprise (name, email or company)" />
        <Button variant="secondary" onClick={() => searchAdminAccounts(token, q).then((r) => setFound(r.accounts.filter((a) => a.role === "client")))}>
          Find
        </Button>
      </div>
      {found && (
        <ul className="mt-2 space-y-1 text-sm">
          {found.length === 0 && <li className="text-ink-muted">No clients found.</li>}
          {found.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-2">
              <span>{p.company_name || p.name} <span className="text-ink-muted">· {p.email}</span></span>
              <Button variant="secondary" onClick={() => pick(p.id, p.company_name || p.name)}>Open</Button>
            </li>
          ))}
        </ul>
      )}

      {open && (
        <div className="mt-4 grid gap-3 rounded-2xl border border-line p-4 text-sm">
          <p className="font-semibold">{open.name}</p>
          <label className="flex items-center gap-2">
            <input type="checkbox" className="accent-[var(--color-brand)]" checked={!!form.is_enterprise}
              onChange={(e) => setForm((f) => ({ ...f, is_enterprise: e.target.checked }))} />
            Enterprise client
          </label>
          <div className="grid gap-2 sm:grid-cols-3">
            <label className="grid gap-1">Price (USD)
              <input className={field} inputMode="decimal" value={form.enterprise_price_usd as string} onChange={set("enterprise_price_usd")} />
            </label>
            <label className="grid gap-1">Billing
              <select className={field} value={form.enterprise_billing as string} onChange={set("enterprise_billing")}>
                <option value="">—</option><option value="monthly">Monthly</option><option value="yearly">Yearly</option>
                <option value="one_time">One time</option>
              </select>
            </label>
            <label className="grid gap-1">Setup fee (USD, one time)
              <input className={field} inputMode="decimal" value={form.enterprise_setup_fee_usd as string} onChange={set("enterprise_setup_fee_usd")} />
            </label>
            <label className="grid gap-1">Contract start
              <input type="date" className={field} value={form.enterprise_contract_start as string} onChange={set("enterprise_contract_start")} />
            </label>
            <label className="grid gap-1">Contract end (empty = open-ended)
              <input type="date" className={field} value={form.enterprise_contract_end as string} onChange={set("enterprise_contract_end")} />
            </label>
          </div>
          <fieldset className="grid gap-1">
            <legend className="mb-1">Included services</legend>
            <div className="flex flex-wrap gap-3">
              {SERVICES.map(([id, label]) => (
                <label key={id} className="flex items-center gap-1.5">
                  <input type="checkbox" className="accent-[var(--color-brand)]" checked={services.includes(id)}
                    onChange={(e) => setForm((f) => ({ ...f, enterprise_services: e.target.checked ? [...services, id] : services.filter((s) => s !== id) }))} />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-2 sm:grid-cols-3">
            {LIMITS.map(([k, label]) => (
              <label key={k} className="grid gap-1">{label}
                <input className={field} inputMode="numeric" value={form[`limit_${k}`] as string} onChange={set(`limit_${k}`)}
                  title={`Up to ${fmtInt(open.summary.ceilings?.[k] ?? 0)}`}
                  placeholder={`default ${fmtInt(open.summary.defaults[k as keyof typeof open.summary.defaults])}`} />
              </label>
            ))}
          </div>
          <label className="grid gap-1">Private notes (invoice numbers, contacts)
            <textarea className={field} rows={2} value={form.enterprise_notes as string} onChange={set("enterprise_notes")} />
          </label>
          <p className="text-xs text-ink-muted">
            Agreement: {open.summary.agreement.accepted_at
              ? `accepted by ${open.summary.agreement.accepted_by} on ${fmtDate(open.summary.agreement.accepted_at)} (version ${open.summary.agreement.accepted_version})`
              : "not accepted yet — the client's owner accepts it in Settings → Account."}
            {" "}Raising uploads past about 100 MB also needs a bigger Render plan (more memory).
          </p>
          <Button onClick={save}>Save contract</Button>
        </div>
      )}
      {done && <p role="status" className="mt-3 text-sm text-good">{done}</p>}
      <ErrorBox message={error} />
    </Card>
  );
}

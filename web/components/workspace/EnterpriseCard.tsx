"use client";

// ── Enterprise plan (Settings -> Account, enterprise clients) ─────────────────
// Shown only when the client has an enterprise contract (api/services/enterprise.py):
// the agreed price, contract dates, included services and raised limits — and the
// Enterprise Agreement (/enterprise-agreement), which the account's OWNER accepts
// here by typing their name and ticking "I agree" (version, time and name recorded).

import Link from "next/link";
import { useEffect, useState } from "react";
import { acceptEnterpriseAgreement, getMyEnterprise } from "@/lib/api";
import { fmtDate, fmtInt, fmtUsd } from "@/lib/format";
import type { EnterpriseSummary } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const BILLING: Record<string, string> = { monthly: "per month", yearly: "per year", one_time: "one time" };
const SERVICE_NAMES: Record<string, string> = {
  data_cleaning: "Data Cleaning", data_visualization: "Dashboard", data_engineering: "Data Engineering",
  data_exploration_analysis: "Exploration & Analysis", data_science_ml: "Data Science & ML", ai: "AI",
  data_strategy_discovery: "Strategy & Discovery",
};

export default function EnterpriseCard({ token, isOwner }: { token: string; isOwner: boolean }) {
  const [info, setInfo] = useState<EnterpriseSummary | null>(null);
  const [name, setName] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMyEnterprise(token).then(setInfo).catch(() => setInfo(null));
  }, [token]);

  if (!info?.is_enterprise) return null;

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      setInfo(await acceptEnterpriseAgreement(token, name, agreed));
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    } finally {
      setBusy(false);
    }
  }

  const a = info.agreement;
  return (
    <Card>
      <h3 className="font-semibold">Enterprise plan</h3>
      <p className="mt-1 text-sm text-ink-muted">
        {info.active ? "Active" : "Not active right now (outside the contract dates)"}
        {info.contract_start && ` · from ${fmtDate(info.contract_start)}`}
        {info.contract_end && ` to ${fmtDate(info.contract_end)}`}
      </p>
      <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        {info.price_usd !== null && (
          <><dt className="text-ink-muted">Price</dt><dd>{fmtUsd(info.price_usd)} {BILLING[info.billing ?? ""] ?? ""}</dd></>
        )}
        {info.setup_fee_usd !== null && (
          <><dt className="text-ink-muted">Setup fee</dt><dd>{fmtUsd(info.setup_fee_usd)} (one time)</dd></>
        )}
        <dt className="text-ink-muted">Services</dt>
        <dd>{info.services.length ? info.services.map((s) => SERVICE_NAMES[s] ?? s).join(" · ") : "—"}</dd>
        <dt className="text-ink-muted">Limits</dt>
        <dd>
          Uploads up to {fmtInt(info.limits.max_upload_mb)} MB · {fmtInt(info.limits.team_seats)} team seats ·{" "}
          {fmtInt(info.limits.table_rows)} rows per database table
        </dd>
      </dl>

      <div className="mt-4 border-t border-line pt-3 text-sm">
        <p className="font-semibold">Enterprise Agreement</p>
        {!a.needs_acceptance && a.accepted_at ? (
          <p className="mt-1 text-ink-muted">
            Accepted by {a.accepted_by} on {fmtDate(a.accepted_at)} (version {a.accepted_version}).{" "}
            <Link href="/enterprise-agreement" target="_blank" className="underline">Read it</Link>
          </p>
        ) : isOwner ? (
          <div className="mt-2 grid gap-2">
            <p>
              Please read the <Link href="/enterprise-agreement" target="_blank" className="underline">Enterprise Agreement</Link>{" "}
              (version {a.version}) and accept it for your organization.
            </p>
            <input className="rounded-xl border border-line bg-surface px-3 py-2" placeholder="Your full name"
              aria-label="Your full name" value={name} onChange={(e) => setName(e.target.value)} />
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-1 accent-[var(--color-brand)]" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
              <span>I agree to the Enterprise Agreement and am authorized to accept it for my organization.</span>
            </label>
            <Button disabled={busy || !agreed || name.trim().length < 3} onClick={accept}>
              {busy ? "Saving…" : "Accept the agreement"}
            </Button>
          </div>
        ) : (
          <p className="mt-1 text-ink-muted">Waiting for your account&apos;s owner to accept the agreement.</p>
        )}
      </div>
      <ErrorBox message={error} />
    </Card>
  );
}

// Admin panel -> "Consultant help" prices: what clients pay their consultant
// through the platform (dim.consulting_prices, ONE row):
//   setup fee · hourly rate for ongoing help · hours a week for Data Cleaning /
//   Data Engineering (counted once) · hours per other service · weekly maximum ·
//   the platform's share (Stripe sends the rest to the consultant).
// New purchases use the new numbers at once (each checkout sends Stripe its
// own price, so there's nothing to sync); running ongoing help keeps its price.
// Server side: api/services/price_store.py (set_consulting_prices).

"use client";

import { useEffect, useState } from "react";
import { getConsultingPrices, setConsultingPrices } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import type { ConsultingPrices, ConsultingPriceValues } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const FIELDS: { key: keyof ConsultingPriceValues; label: string; unit: string; step: number }[] = [
  { key: "setup_usd", label: "Setup fee (once)", unit: "$", step: 1 },
  { key: "help_rate_usd", label: "Ongoing help, per hour", unit: "$", step: 1 },
  { key: "core_hours", label: "Hours a week: Data Cleaning / Data Engineering", unit: "hrs", step: 0.5 },
  { key: "service_hours", label: "Hours a week: each other service", unit: "hrs", step: 0.5 },
  { key: "max_hours", label: "Most hours a week", unit: "hrs", step: 0.5 },
  { key: "platform_fee_percent", label: "Platform's share of each payment", unit: "%", step: 1 },
];

export default function AdminConsultingPrices({ token }: { token: string }) {
  const [data, setData] = useState<ConsultingPrices | null>(null);
  const [draft, setDraft] = useState<ConsultingPriceValues | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    getConsultingPrices(token)
      .then((d) => {
        setData(d);
        setDraft(d.values);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load the consultant prices."));
  }, [token]);

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const d = await setConsultingPrices(token, draft);
      setData(d);
      setDraft(d.values);
      setNotice("Saved. New purchases use these at once; running ongoing help keeps its price.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That wasn't saved.");
    } finally {
      setBusy(false);
    }
  }

  const changed = !!data && !!draft && FIELDS.some((f) => draft[f.key] !== data.values[f.key]);

  return (
    <Card>
      <h2 className="font-semibold">Consultant help</h2>
      <p className="mt-1 text-sm text-ink-muted">
        What clients pay their consultant through the platform. Ongoing help is priced by the services they pick: the
        Data Cleaning / Engineering hours (once), plus the hours for each other service, up to the weekly maximum.
        A year costs 10 months. Stripe sends the consultant everything but the platform&apos;s share (none when the
        consultant is an admin).
      </p>
      <ErrorBox message={error} />
      {notice && <p role="status" className="mt-3 rounded-xl border border-good/40 bg-good/10 p-2 text-sm">{notice}</p>}
      {data && draft && (
        <div className="mt-3 space-y-3 text-sm">
          <div className="grid gap-3 sm:grid-cols-2">
            {FIELDS.map((f) => (
              <label key={f.key} className="block">
                <span className="text-xs font-medium">{f.label}</span>
                <span className="mt-1 flex items-center gap-1">
                  {f.unit === "$" && <span className="text-ink-muted">$</span>}
                  <input type="number" min={0} step={f.step} value={draft[f.key]}
                    onChange={(e) => setDraft({ ...draft, [f.key]: Number(e.target.value) })}
                    className="w-28 rounded-xl border border-line bg-surface px-3 py-1.5 font-mono" />
                  {f.unit !== "$" && <span className="text-ink-muted">{f.unit}</span>}
                </span>
              </label>
            ))}
          </div>
          <p className="text-xs text-ink-muted">
            Example at the saved numbers: Data Cleaning + Data Engineering + one more service ={" "}
            <span className="font-mono">{data.example.hours}</span> hours a week,{" "}
            <span className="font-mono">{fmtUsd(data.example.monthly_usd)}</span>/month.
          </p>
          <Button onClick={save} disabled={!changed || busy}>{busy ? "Saving…" : "Save consultant prices"}</Button>
        </div>
      )}
    </Card>
  );
}

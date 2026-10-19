// Plan & billing -> "Your consultant": pay your consultant for their help.
//
//   SETUP          a one-time fee: they connect, clean and load your data and
//                  build your first dashboard
//   ONGOING HELP   priced by WHAT you need help with (not a level you pick):
//                  Data Cleaning / Data Engineering = the core block of hours,
//                  each other service adds a few hours, AI adds none — up to a
//                  weekly maximum. Monthly, or a year for 10 months.
//
// Before paying, the billing terms must be ticked (cancel any time; it stays
// on and is paid to the end of the period). One ongoing help at a time: to
// change it, end it and choose again when it ends. If the consultant leaves or
// is deactivated, ongoing help ends at once and this card says why.
//
// The hours and price shown here follow the SAME rule as the server
// (pricing.help_hours / retainer_price_usd); the server works them out again
// at checkout, and that's what Stripe charges. Stripe sends the consultant
// their share automatically (Stripe Connect). Hourly sessions booked on their
// calendar are separate. Server side: api/services/consulting_billing.py.

"use client";

import { useEffect, useState } from "react";
import { cancelConsultingHelp, getConsulting, startConsultingCheckout } from "@/lib/api";
import { categoryEmoji } from "@/lib/categories";
import { fmtDate, fmtUsd } from "@/lib/format";
import type { ConsultingCatalog, ConsultingOverview } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

/** Hours a week for the services picked (same rule as pricing.help_hours). */
function helpHours(c: ConsultingCatalog, picked: string[]): number {
  const core = picked.some((s) => c.core_services.includes(s)) ? c.core_hours : 0;
  const light = picked.filter((s) => c.light_services.includes(s)).length * c.service_hours;
  return Math.min(core + light, c.max_hours);
}

/** The price for a month, or a year (= annual_months months) — same rule as pricing.retainer_price_usd. */
function helpPrice(c: ConsultingCatalog, hours: number, interval: "month" | "year"): number {
  const monthly = Math.round(hours * (52 / 12) * c.hourly_usd * 100) / 100;
  return interval === "year" ? Math.round(monthly * c.annual_months * 100) / 100 : monthly;
}

export default function ConsultantHelp({ token, acting }: { token: string; acting?: boolean }) {
  const [data, setData] = useState<ConsultingOverview | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [interval, setBilling] = useState<"month" | "year">("month");
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = () =>
    getConsulting(token)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load your consultant's prices."));
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function buy(kind: "setup" | "retainer") {
    setBusy(kind);
    setError(null);
    setNote(null);
    try {
      const res = await startConsultingCheckout(
        token,
        kind === "setup" ? { kind, accept_terms: agreed } : { kind, services: picked, interval, accept_terms: agreed },
      );
      if (res.url) {
        window.location.assign(res.url); // Stripe's secure page; it comes back to Plan & billing
        return;
      }
      setNote("Done — test mode, so nothing was charged.");
      setPicked([]);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(null);
    }
  }

  async function end() {
    setBusy("cancel");
    setError(null);
    try {
      await cancelConsultingHelp(token);
      setNote("Ongoing help will end when the period you've paid for ends.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(null);
    }
  }

  if (!data) return error ? <ErrorBox message={error} /> : null;
  const { catalog, retainer, ended } = data;
  const hours = helpHours(catalog, picked);
  const price = helpPrice(catalog, hours, interval);
  const lastSetup = data.engagements.find((e) => e.kind === "setup" && e.status === "paid");
  const canBuy = data.can_buy && !acting;
  const names = Object.fromEntries(data.services.map((s) => [s.id, s.name]));
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  return (
    <Card className="space-y-4">
      <div>
        <h2 className="font-semibold">Your consultant{data.consultant ? `: ${data.consultant.name}` : ""}</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Pay for your consultant&apos;s help here: a one-time setup, ongoing weekly help, or both. Your consultant is
          paid automatically. Hourly sessions booked on their calendar ({fmtUsd(catalog.session_rate_usd)}/hour) are
          separate.
        </p>
      </div>
      {ended && (
        <p role="status" className="rounded-xl border border-warn/40 bg-warn/10 p-3 text-sm">
          Your ongoing help{ended.consultant_name ? ` with ${ended.consultant_name}` : ""} ended early because{" "}
          {ended.consultant_name ?? "your consultant"} is no longer available. Start ongoing help with your new
          consultant below; we&apos;ll refund the time you&apos;d paid for but didn&apos;t get.
        </p>
      )}
      {data.reason && <p className="rounded-xl border border-warn/40 bg-warn/10 p-3 text-sm">{data.reason}</p>}
      {acting && <p className="text-sm text-ink-muted">Your client chooses and pays for your help from their own account.</p>}

      <div className="rounded-xl border border-line p-3 text-sm">
        <p className="font-medium">
          Setup — <span className="font-mono">{fmtUsd(catalog.setup_usd)}</span> once
        </p>
        <p className="text-ink-muted">Connecting your data, cleaning and loading it, and building your first dashboard.</p>
        {lastSetup && <p className="mt-1 text-good">✓ Paid {lastSetup.created_at ? fmtDate(lastSetup.created_at) : ""}</p>}
        <Button className="mt-2" onClick={() => buy("setup")} disabled={!canBuy || !agreed || busy !== null}>
          {busy === "setup" ? "One moment…" : lastSetup ? "Pay for another setup" : "Pay for setup"}
        </Button>
      </div>

      <div className="rounded-xl border border-line p-3 text-sm">
        <p className="font-medium">Ongoing help</p>
        {retainer ? (
          <div className="mt-1 space-y-2">
            <p>
              {retainer.services.map((s) => `${categoryEmoji(s)} ${names[s] ?? s}`).join(" · ")}
            </p>
            <p>
              About <span className="font-mono">{retainer.hours_per_week}</span> hours a week ·{" "}
              <span className="font-mono">{fmtUsd(retainer.amount_usd)}</span>/{retainer.billing_interval === "year" ? "year" : "month"}
              {retainer.current_period_end && ` · paid through ${fmtDate(retainer.current_period_end)}`}
            </p>
            {retainer.cancel_at_period_end ? (
              <p className="text-ink-muted">
                Ends {retainer.current_period_end ? `on ${fmtDate(retainer.current_period_end)}` : "when the paid period ends"}.
                You can choose new ongoing help then.
              </p>
            ) : (
              <>
                <p className="text-xs text-ink-muted">
                  To change the services, end it — it stays on until the period you&apos;ve paid for ends — and choose
                  again then.
                </p>
                <Button variant="secondary" onClick={end} disabled={acting || busy !== null}>
                  {busy === "cancel" ? "One moment…" : "End ongoing help"}
                </Button>
              </>
            )}
          </div>
        ) : (
          <div className="mt-2 space-y-3">
            <p className="text-ink-muted">
              Pick what you need help with. Data Cleaning or Data Engineering is about {catalog.core_hours} hours a week
              (files and QuickBooks / Xero take the most work); each other service adds about {catalog.service_hours}.
              AI adds nothing — you ask it yourself. At most {catalog.max_hours} hours a week, at{" "}
              {fmtUsd(catalog.hourly_usd)}/hour.
            </p>
            <fieldset className="grid gap-1 sm:grid-cols-2">
              <legend className="sr-only">What do you need help with?</legend>
              {data.services.map((s) => (
                <label key={s.id} className="flex items-center gap-2">
                  <input type="checkbox" checked={picked.includes(s.id)} onChange={() => toggle(s.id)} />
                  <span>{categoryEmoji(s.id)} {s.name}</span>
                </label>
              ))}
            </fieldset>
            <p className="text-xs text-ink-muted">
              Only tools in your package are listed — your consultant works with your tools. Your consultant may help
              beyond these hours at their discretion.
            </p>
            <fieldset className="flex gap-4">
              <legend className="sr-only">Billing</legend>
              {(["month", "year"] as const).map((i) => (
                <label key={i} className="flex items-center gap-2">
                  <input type="radio" name="help-interval" checked={interval === i} onChange={() => setBilling(i)} />
                  {i === "month" ? "Monthly" : `Yearly (pay ${catalog.annual_months} months)`}
                </label>
              ))}
            </fieldset>
            <p>
              About <span className="font-mono">{hours}</span> hours a week ·{" "}
              <span className="font-mono">{fmtUsd(price)}</span>/{interval}
            </p>
            <Button onClick={() => buy("retainer")} disabled={!canBuy || !agreed || !hours || busy !== null}>
              {busy === "retainer" ? "One moment…" : `Start ongoing help — ${fmtUsd(price)}/${interval}`}
            </Button>
          </div>
        )}
      </div>

      {/* Required before any purchase (the server checks it too). */}
      {canBuy && (
        <label className="flex items-start gap-2 rounded-xl border border-line p-3 text-sm">
          <input type="checkbox" className="mt-1" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
          <span>
            <span className="font-medium">Billing terms: </span>
            {data.cancel_terms} I agree.
          </span>
        </label>
      )}
      {data.test_mode && <p className="text-xs text-ink-muted">Test mode: purchases switch on at once and nothing is charged.</p>}
      {note && <p role="status" className="text-sm text-good">{note}</p>}
      <ErrorBox message={error} />
    </Card>
  );
}

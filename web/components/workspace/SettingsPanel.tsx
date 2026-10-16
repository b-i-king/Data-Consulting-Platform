// Settings (⚙️ in the workspace's left pane).
//
//   * CONSULTANTS (the "finish setting up" steps for a new consultant):
//       1. Your booking links — their own FREE 30-minute and PAID session
//          Cal.com links (paid through their own Cal.com + Stripe, so they're
//          paid directly). Their clients see these instead of the defaults.
//       2. Your Cal.com webhook — the URL (/api/consultations/calcom-webhook/
//          <their id>) and a secret the app makes, with the Cal.com steps
//          (Settings -> Developer -> Webhooks). Bookings then arrive signed and
//          are recorded as theirs.
//       3. Capacity — how many clients at once (1 to 10, default 3), with the
//          fill bar. When full, nobody new can pick them (lowering drops nobody).
//   * CLIENTS: "Work as a consultant" — send a request; an admin approves or
//     denies (status shown here). Approval switches this workspace to
//     consultant mode on its next load.
//
// Server side: api/routers/consultants.py (settings), api/routers/accounts.py
// (consultant-request).

"use client";

import { useEffect, useState } from "react";
import { API_URL, getConsultantSettings, newWebhookSecret, requestConsultantRole, saveConsultantSettings } from "@/lib/api";
import type { Account, ConsultantSettings } from "@/lib/types";
import { LoadBar } from "@/components/consultants/ConsultantPicker";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

export default function SettingsPanel(props: {
  token: string;
  account: Account;
  onAccount: (a: Account) => void;
  /** Consultants: told when "finish setting up" is done (or needed again). */
  onSetupNeeded?: (needed: boolean) => void;
}) {
  const isConsultant = props.account.role === "consultant";
  return isConsultant ? <ConsultantSetup token={props.token} onSetupNeeded={props.onSetupNeeded} /> : <AskToConsult {...props} />;
}

const field = "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-consultant";

function ConsultantSetup({ token, onSetupNeeded }: { token: string; onSetupNeeded?: (needed: boolean) => void }) {
  const [data, setStoredData] = useState<ConsultantSettings | null>(null);
  const setData = (d: ConsultantSettings) => {
    setStoredData(d);
    onSetupNeeded?.(d.setup_needed);
  };
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The first load only fills the page (the workspace already knows if setup is needed).
    getConsultantSettings(token)
      .then(setStoredData)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load your settings."));
  }, [token]);

  if (!data) return error ? <ErrorBox message={error} /> : <p className="text-sm text-ink-muted">Loading…</p>;
  return (
    <div className="max-w-2xl space-y-5">
      {data.setup_needed && (
        <p role="status" className="rounded-2xl border border-consultant/40 bg-consultant-soft px-4 py-3 text-sm">
          <strong>Welcome! Finish setting up:</strong> add your Cal.com booking links (1), then connect your Cal.com
          webhook (2). Until then your clients see the platform&apos;s default links.
        </p>
      )}
      <BookingLinks token={token} data={data} onSaved={setData} />
      <Webhook token={token} data={data} onSaved={setData} />
      <Capacity token={token} data={data} onSaved={setData} />
    </div>
  );
}

type SectionProps = { token: string; data: ConsultantSettings; onSaved: (d: ConsultantSettings) => void };

/** 1. The consultant's own free + paid Cal.com links (their clients see these). */
function BookingLinks({ token, data, onSaved }: SectionProps) {
  const [free, setFree] = useState(data.free_booking_url ?? "");
  const [paid, setPaid] = useState(data.paid_booking_url ?? "");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const changed = free !== (data.free_booking_url ?? "") || paid !== (data.paid_booking_url ?? "");

  async function save() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      onSaved(await saveConsultantSettings(token, { free_booking_url: free.trim(), paid_booking_url: paid.trim() }));
      setNote("Saved. Your clients now see these links.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">1. Your booking links</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Your clients book you through these. In Cal.com, make two event types: a <strong>free 30-minute</strong>{" "}
        consultation, and a <strong>paid session</strong> (turn on Cal.com&apos;s Stripe app and set the price, so clients
        pay you directly when they book). Paste each event&apos;s link here.
      </p>
      <div className="mt-4 space-y-3">
        <label className="block space-y-1 text-sm">
          <span>Free consultation link</span>
          <input className={field} placeholder="https://cal.com/you/30min" value={free} onChange={(e) => setFree(e.target.value)} />
        </label>
        <label className="block space-y-1 text-sm">
          <span>Paid session link</span>
          <input className={field} placeholder="https://cal.com/you/session" value={paid} onChange={(e) => setPaid(e.target.value)} />
          <span className="block text-xs text-ink-muted">Shown once a client&apos;s free call is confirmed. Empty = the free link is used.</span>
        </label>
        <Button onClick={save} disabled={busy || !changed}>
          {busy ? "Saving…" : "Save links"}
        </Button>
        {note && <p className="text-sm text-good" role="status">{note}</p>}
        <ErrorBox message={error} />
      </div>
    </Card>
  );
}

/** 2. The consultant's own Cal.com webhook: URL + a secret the app makes. */
function Webhook({ token, data, onSaved }: SectionProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const url = `${API_URL}${data.webhook.path}`;

  async function makeSecret() {
    setBusy(true);
    setError(null);
    try {
      onSaved(await newWebhookSecret(token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't make a secret.");
    } finally {
      setBusy(false);
    }
  }
  function copy(label: string, text: string) {
    navigator.clipboard?.writeText(text).then(
      () => setCopied(label),
      () => setCopied(null),
    );
  }

  return (
    <Card>
      <h2 className="font-semibold">2. Your Cal.com webhook</h2>
      <p className="mt-1 text-sm text-ink-muted">
        So the app knows when your clients book, reschedule, cancel or meet you — and which free call is used up. Every
        booking that comes in this way is recorded as yours.
      </p>
      <ol className="mt-4 list-inside list-decimal space-y-2 text-sm">
        <li>
          In Cal.com, open{" "}
          <a href="https://app.cal.com/settings/developer/webhooks" target="_blank" rel="noreferrer" className="text-brand underline">
            Settings → Developer → Webhooks
          </a>{" "}
          and press <strong>New</strong>.
        </li>
        <li>
          <strong>Subscriber URL:</strong>{" "}
          <code className="break-all rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">{url}</code>{" "}
          <button type="button" className="text-xs text-brand hover:underline" onClick={() => copy("url", url)}>
            {copied === "url" ? "Copied" : "Copy"}
          </button>
        </li>
        <li>
          <strong>Event triggers:</strong> Booking created, rescheduled, cancelled, rejected, requested, paid, Meeting
          ended, and No-show updated.
        </li>
        <li>
          <strong>Secret:</strong>{" "}
          {data.webhook.secret ? (
            <>
              <code className="break-all rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">{data.webhook.secret}</code>{" "}
              <button type="button" className="text-xs text-brand hover:underline" onClick={() => copy("secret", data.webhook.secret!)}>
                {copied === "secret" ? "Copied" : "Copy"}
              </button>
            </>
          ) : (
            <span className="text-ink-muted">make one below, then paste it.</span>
          )}
        </li>
        <li>Leave the payload template as it is, keep the webhook on, and save. Cal.com&apos;s <strong>Ping test</strong> should answer OK.</li>
      </ol>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button variant={data.webhook.secret ? "secondary" : "primary"} onClick={makeSecret} disabled={busy}>
          {busy ? "One moment…" : data.webhook.secret ? "Replace my secret" : "Make my webhook secret"}
        </Button>
        {data.webhook.secret && (
          <span className="text-xs text-ink-muted">Replacing it stops the old one at once — paste the new one into Cal.com.</span>
        )}
      </div>
      <ErrorBox message={error} />
    </Card>
  );
}

/** 3. How many clients at once (1 to max), with the fill bar. */
function Capacity({ token, data, onSaved }: SectionProps) {
  const [draft, setDraft] = useState(data.load.capacity);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const max = data.max_capacity;

  async function save() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      onSaved(await saveConsultantSettings(token, { capacity: draft }));
      setNote("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">3. Capacity</h2>
      <p className="mt-1 text-sm text-ink-muted">
        How many clients you take at once (1 to {max}). Clients see how full you are when they pick a consultant; when
        you&apos;re full, nobody new can pick you. Lowering it never drops a client.
      </p>
      <div className="mt-4 space-y-4">
        <LoadBar load={data.load} />
        <label className="flex items-center gap-3 text-sm">
          <span>Clients at once</span>
          <input
            type="number"
            min={1}
            max={max}
            value={draft}
            onChange={(e) => setDraft(Math.max(1, Math.min(max, Number(e.target.value) || 1)))}
            className="w-20 rounded-xl border border-line bg-surface px-3 py-1.5 font-mono text-sm"
          />
          <Button onClick={save} disabled={busy || draft === data.load.capacity}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </label>
        {note && <p className="text-sm text-good" role="status">{note}</p>}
      </div>
      <ErrorBox message={error} />
    </Card>
  );
}

function AskToConsult(props: { token: string; account: Account; onAccount: (a: Account) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = props.account.consultant_request;

  async function ask() {
    setBusy(true);
    setError(null);
    try {
      props.onAccount(await requestConsultantRole(props.token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't send your request.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="max-w-xl">
      <h2 className="font-semibold">Work as a consultant</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Consultants sign in to their clients&apos; accounts and do the work for them. Every consultant is approved by us
        first.
      </p>
      {status === "pending" ? (
        <p className="mt-4 text-sm" role="status">
          <strong>Your request is waiting for approval.</strong> We&apos;ll switch your workspace to consultant mode once
          it&apos;s approved.
        </p>
      ) : (
        <>
          {status === "denied" && (
            <p className="mt-4 text-sm">Your last request wasn&apos;t approved. You&apos;re welcome to ask again.</p>
          )}
          <Button className="mt-4" onClick={ask} disabled={busy}>
            {busy ? "Sending…" : "Request to be a consultant"}
          </Button>
        </>
      )}
      <ErrorBox message={error} />
    </Card>
  );
}

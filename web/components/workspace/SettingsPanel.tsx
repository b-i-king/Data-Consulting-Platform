// Settings pieces, used by the workspace's Account area (AccountPanel.tsx and
// the Consultant section):
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
//   * EVERYONE with stored data (the account holder, their consultant working
//     on them, or an admin): "Update your most recent upload" — pick the
//     corrected file; it REPLACES the latest stored file (same name), after the
//     usual preview. Data from connected apps (QuickBooks, ...) is edited in
//     that app, not here.
//   * YOUR OWN ACCOUNT (never shown while working on a client): "Delete my
//     account" — type your email; the account and EVERYTHING in it is deleted
//     (a business owner's whole team too). Cannot be undone.
//
// Server side: api/routers/consultants.py (settings), api/routers/accounts.py
// (consultant-request, DELETE /me), api/routers/warehouse.py (replace_source).

"use client";

import { useMemo, useEffect, useState } from "react";
import {
  API_URL,
  analyzeFiles,
  deleteMyAccount,
  getConsultantSettings,
  newWebhookSecret,
  requestConsultantRole,
  saveConsultantSettings,
} from "@/lib/api";
import type {
  Account,
  AnalyzeResponse,
  CleaningInput,
  ConsultantSettings,
  DeletionReason,
  WarehouseCounts,
} from "@/lib/types";
import { LoadBar } from "@/components/consultants/ConsultantPicker";
import WarehouseLoad from "@/components/explore/WarehouseLoad";
import { Button, Card, ConfirmDialog, ErrorBox } from "@/components/wizard/ui";

/** "Update your most recent upload": pick the corrected file; it replaces the stored one after a preview. */
export function UpdateLatestUpload(props: {
  token: string;
  latest: string;
  name: string;
  onStoredChanged: (counts: WarehouseCounts) => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const input: CleaningInput = useMemo(
    () => ({ files, strategy: "keep", columnActions: {}, columnNames: {}, transforms: {} }),
    [files],
  );

  async function pick(chosen: File[]) {
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      setFiles(chosen);
      setAnalysis(await analyzeFiles({ files: chosen, strategy: "keep", columnActions: {}, columnNames: {}, transforms: {} }));
    } catch (e) {
      setAnalysis(null);
      setError(e instanceof Error ? e.message : "We couldn't read that file.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="max-w-4xl">
      <h2 className="font-semibold">Update your most recent upload</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Fixed something in <strong>{props.latest}</strong>? Choose the corrected file: after the usual preview it
        replaces the stored one (same name), and rows no longer in the file are removed. Data from connected apps
        (QuickBooks, ...) is edited in that app, then refreshed — not here.
      </p>
      <label className="mt-4 inline-flex cursor-pointer items-center gap-2 rounded-full bg-brand px-5 py-2 text-sm font-semibold text-on-brand">
        {busy ? "Cleaning your file…" : "Choose the corrected file"}
        <input type="file" className="sr-only" disabled={busy} onChange={(e) => e.target.files?.length && pick(Array.from(e.target.files))} />
      </label>
      <ErrorBox message={error} />
      {done && <p className="mt-3 text-sm text-good" role="status">Updated. ✓</p>}
      {analysis && analysis.groups[0] && !done && (
        <div className="mt-5">
          <WarehouseLoad
            key={`replace-${files.map((f) => f.name).join()}`}
            token={props.token}
            input={input}
            groupId={analysis.groups[0].group_id}
            clientName={props.name}
            canDelete={false}
            replaceSource={props.latest}
            onStoredChanged={props.onStoredChanged}
            onLoaded={() => setDone(true)}
            compact
          />
        </div>
      )}
    </Card>
  );
}

/** "Delete my account": type your email to confirm; everything goes. */
export function DeleteAccount(props: { token: string; account: Account; onDeleted: () => void }) {
  const [asking, setAsking] = useState(false);
  const [typed, setTyped] = useState("");
  // Optional "Why are you leaving?" — kept only as an anonymous count, never with who you are.
  const [reason, setReason] = useState<DeletionReason | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const owner = props.account.team_role === "owner";

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteMyAccount(props.token, typed, reason || null);
      props.onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't delete the account.");
      setBusy(false);
    }
  }

  return (
    <Card className="max-w-xl border-bad/40">
      <h2 className="font-semibold text-bad">Delete my account</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Deletes your account and everything in it: stored data, your package (billing stops), downloads history,
        consultations and connectors.
        {owner && " As your business's owner, your colleagues' accounts are deleted too."} This can&apos;t be undone.
      </p>
      <Button variant="secondary" className="mt-4" onClick={() => setAsking(true)}>
        Delete my account…
      </Button>
      {asking && (
        <ConfirmDialog
          title="Delete your account for good?"
          confirmLabel={busy ? "Deleting…" : "Delete everything"}
          onConfirm={() => {
            if (busy) return;
            if (typed.trim().toLowerCase() !== props.account.email) setError("Type your email address exactly to confirm.");
            else remove();
          }}
          onCancel={() => {
            setAsking(false);
            setTyped("");
          }}
        >
          <p>
            Type <strong>{props.account.email}</strong> to confirm. Everything is deleted at once and can&apos;t be brought
            back.
          </p>
          <input
            className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            aria-label="Your email address"
            autoComplete="off"
          />
          <label className="block">
            <span className="text-xs text-ink-muted">
              Why are you leaving? (optional — we keep only a count, never who you are)
            </span>
            <select
              className="mt-1 w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm"
              value={reason}
              onChange={(e) => setReason(e.target.value as DeletionReason | "")}
            >
              <option value="">Rather not say</option>
              <option value="too_expensive">It costs too much</option>
              <option value="missing_features">It&apos;s missing something I need</option>
              <option value="not_needed">I don&apos;t need it anymore</option>
              <option value="switching_tools">I&apos;m switching to another tool</option>
              <option value="privacy">Privacy concerns</option>
              <option value="other">Something else</option>
            </select>
          </label>
          <ErrorBox message={error} />
        </ConfirmDialog>
      )}
    </Card>
  );
}

const field = "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-consultant";

export function ConsultantSetup({ token, onSetupNeeded }: { token: string; onSetupNeeded?: (needed: boolean) => void }) {
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
          <a href="https://app.cal.com/settings/developer/webhooks" target="_blank" rel="noreferrer" className="text-brand-ink underline">
            Settings → Developer → Webhooks
          </a>{" "}
          and press <strong>New</strong>.
        </li>
        <li>
          <strong>Subscriber URL:</strong>{" "}
          <code className="break-all rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">{url}</code>{" "}
          <button type="button" className="text-xs text-brand-ink hover:underline" onClick={() => copy("url", url)}>
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
              <button type="button" className="text-xs text-brand-ink hover:underline" onClick={() => copy("secret", data.webhook.secret!)}>
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

export function AskToConsult(props: { token: string; account: Account; onAccount: (a: Account) => void }) {
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

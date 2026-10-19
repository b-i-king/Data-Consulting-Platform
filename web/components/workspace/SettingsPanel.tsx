// Settings pieces, used by the workspace's Account area (AccountPanel.tsx and
// the Consultant section):
//
//   * CONSULTANTS — ONBOARDING after approval: a checklist at the top (two-step
//     sign-in, the independent-contractor agreement, both booking links, the
//     webhook; capacity is optional), the agreement itself (they get paid
//     directly and pay their own taxes — "I agree" is saved with its version),
//     then the setup cards:
//       1. Your booking links — their own FREE 30-minute and PAID session
//          Cal.com links (paid through their own Cal.com + Stripe, so they're
//          paid directly). Their clients see these instead of the defaults.
//       2. Your Cal.com webhook — the URL (/api/consultations/calcom-webhook/
//          <their id>) and a secret the app makes, with the Cal.com steps
//          (Settings -> Developer -> Webhooks). Bookings then arrive signed and
//          are recorded as theirs.
//       3. Capacity — how many clients at once (1 to 10, default 3), with the
//          fill bar. When full, nobody new can pick them (lowering drops nobody).
//   * CLIENTS: "Work as a consultant" — the screening application (services of
//     the seven, an experience range, tools, their LinkedIn profile (required),
//     an optional portfolio / work sample, a project, the code of conduct).
//     A denial shows that a real person decided, the reason and their personal
//     feedback. Only for invited emails while applications are invite-only,
//     never while one is waiting, and not before the waiting period after a
//     denial ends (the reason and the date are shown). An admin approves or
//     denies; approval switches this workspace to consultant mode on its next load.
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
  agreeToContractorTerms,
  openPayoutsDashboard,
  startPayouts,
  analyzeFiles,
  deleteMyAccount,
  getConsultantApplication,
  getConsultantSettings,
  getIntakeOptions,
  newWebhookSecret,
  requestConsultantRole,
  saveConsultantSettings,
} from "@/lib/api";
import type {
  Account,
  AnalyzeResponse,
  CleaningInput,
  ConsultantApplicationInput,
  ConsultantApplicationStatus,
  ConsultantOnboarding,
  ConsultantSettings,
  DeletionReason,
  Service,
  WarehouseCounts,
} from "@/lib/types";
import { fmtDate } from "@/lib/format";
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

/** Onboarding: what's done and what's left (the required steps hide the nudge once done). */
function OnboardingChecklist({ onboarding }: { onboarding: ConsultantOnboarding }) {
  const left = onboarding.steps.filter((st) => st.required && !st.done).length;
  return (
    <Card className={onboarding.done ? "" : "border-consultant/40 bg-consultant-soft"}>
      <h2 className="font-semibold">{onboarding.done ? "Onboarding complete ✓" : "Welcome! Your onboarding"}</h2>
      <p className="mt-1 text-sm text-ink-muted">
        {onboarding.done
          ? "You're all set. You can change any of these below at any time."
          : `${left} step${left === 1 ? "" : "s"} left. Until they're done, your clients see the platform's default booking links.`}
      </p>
      <ul className="mt-3 space-y-1.5 text-sm">
        {onboarding.steps.map((st) => (
          <li key={st.id} className="flex items-start gap-2">
            <span aria-hidden className={st.done ? "text-good" : "text-ink-muted"}>{st.done ? "✓" : "○"}</span>
            <span className={st.done ? "text-ink-muted" : ""}>
              {st.label}
              <span className="sr-only">{st.done ? " (done)" : " (to do)"}</span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/**
 * Onboarding: connect a Stripe payout account. Clients pay the platform for setup
 * and ongoing help; Stripe sends this consultant their share (consulting_billing).
 * Stripe collects identity, bank and tax details on its own page — never this app.
 */
function Payouts(props: { token: string; data: ConsultantSettings; onSaved: (d: ConsultantSettings) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { payouts } = props.data;

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      const res = await startPayouts(props.token);
      if (res.url) {
        window.location.assign(res.url); // Stripe's page; it comes back to Settings -> Consultant
        return;
      }
      props.onSaved(await getConsultantSettings(props.token)); // test mode: connected at once
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  }
  async function dashboard() {
    setError(null);
    try {
      window.open((await openPayoutsDashboard(props.token)).url, "_blank", "noopener");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">Payouts</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Clients pay for your setup and ongoing help through the platform, and Stripe sends your share to your own Stripe
        account automatically. Stripe asks for your identity, bank and tax details on its own secure page.
      </p>
      {payouts.ready ? (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
          <span className="text-good">✓ Payouts are set up.</span>
          {!payouts.test_mode && (
            <Button variant="secondary" onClick={dashboard}>Open my Stripe payouts</Button>
          )}
        </div>
      ) : (
        <Button className="mt-3" onClick={connect} disabled={busy}>
          {busy ? "One moment…" : payouts.connected ? "Finish setting up payouts" : "Set up payouts with Stripe"}
        </Button>
      )}
      <ErrorBox message={error} />
    </Card>
  );
}

/** Onboarding: the independent-contractor terms — they're paid directly and pay their own taxes. */
function ContractorAgreement(props: { token: string; onboarding: ConsultantOnboarding; onSaved: (d: ConsultantSettings) => void }) {
  const [ticked, setTicked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const agreedAt = props.onboarding.contractor_agreed_at;
  const current = props.onboarding.steps.find((st) => st.id === "contractor")?.done;

  async function agree() {
    setBusy(true);
    setError(null);
    try {
      props.onSaved(await agreeToContractorTerms(props.token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">Independent-contractor terms</h2>
      <p className="mt-1 text-sm text-ink-muted">How you work with us, how you get paid, and your taxes.</p>
      <ul className="mt-3 list-inside list-disc space-y-1 text-sm">
        {props.onboarding.contractor_terms.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
      {current && agreedAt ? (
        <p className="mt-3 text-sm text-good" role="status">You agreed on {fmtDate(agreedAt)}.</p>
      ) : (
        <div className="mt-3 space-y-2">
          {agreedAt && <p className="text-sm text-warn">These terms changed since you agreed — please read and agree again.</p>}
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={ticked} onChange={(e) => setTicked(e.target.checked)} />
            I&apos;ve read these terms and I agree to work as an independent contractor.
          </label>
          <Button onClick={agree} disabled={!ticked || busy}>{busy ? "Saving…" : "I agree"}</Button>
        </div>
      )}
      <ErrorBox message={error} />
    </Card>
  );
}

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
      <OnboardingChecklist onboarding={data.onboarding} />
      {data.onboarding.steps.some((st) => st.id === "contractor" && st.required) && (
        <ContractorAgreement token={token} onboarding={data.onboarding} onSaved={setData} />
      )}
      {data.payouts.needed && <Payouts token={token} data={data} onSaved={setData} />}
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
        consultation, and a <strong>paid session</strong>. For the paid one, install a payment app in Cal.com
        (Stripe or PayPal — your choice, your own account) and set your price, so clients pay you directly when they
        book. Paste each event&apos;s link here.
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

/** The screening application's empty form. */
const EMPTY_APPLICATION: ConsultantApplicationInput = {
  services: [],
  experience: "",
  tools: "",
  linkedin_url: "",
  work_sample_url: "",
  project_summary: "",
  agree_conduct: false,
};
const MIN_SUMMARY = 100; // characters (ConsultantApplicationIn.project_summary on the server)

/**
 * "Work as a consultant" (Settings -> Account): the screening application.
 * Shows the form only when the person may apply (invited, nothing waiting, past
 * any waiting period); otherwise why not, and the last decision with its reason.
 */
export function AskToConsult(props: { token: string; account: Account; onAccount: (a: Account) => void }) {
  const [check, setCheck] = useState<ConsultantApplicationStatus | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [form, setForm] = useState<ConsultantApplicationInput>(EMPTY_APPLICATION);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getConsultantApplication(props.token)
      .then(setCheck)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't check your application."));
    getIntakeOptions()
      .then((o) => setServices(o.services))
      .catch(() => {});
    // Re-check whenever the account's request status changes (e.g. just sent).
  }, [props.token, props.account.consultant_request]);

  const set = <K extends keyof ConsultantApplicationInput>(key: K, value: ConsultantApplicationInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const toggleService = (id: string) =>
    set("services", form.services.includes(id) ? form.services.filter((s) => s !== id) : [...form.services, id]);
  const https = (v: string) => /^https:\/\/\S{4,}$/i.test(v.trim());
  // Same rule as the server (LINKEDIN_PROFILE): a personal profile on linkedin.com ("https://" optional here).
  const linkedin = (v: string) => /^(https?:\/\/)?([a-z]{2,3}\.)?linkedin\.com\/(in|pub)\/[^\s/?#]{2,}\S*$/i.test(v.trim());
  // What's still missing, in plain words (the server checks the same rules).
  const missing = [
    !form.services.length && "pick at least one service",
    !form.experience && "choose your experience",
    form.tools.trim().length < 2 && "list your tools",
    !linkedin(form.linkedin_url) && "add your LinkedIn profile link",
    form.work_sample_url.trim() !== "" && !https(form.work_sample_url) && "fix the portfolio link (https://)",
    form.project_summary.trim().length < MIN_SUMMARY && `describe a project (${MIN_SUMMARY}+ characters)`,
    !form.agree_conduct && "agree to the code of conduct",
  ].filter(Boolean) as string[];

  async function send() {
    setBusy(true);
    setError(null);
    try {
      props.onAccount(await requestConsultantRole(props.token, form));
      setOpen(false);
      setForm(EMPTY_APPLICATION);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't send your application.");
    } finally {
      setBusy(false);
    }
  }

  const latest = check?.latest;
  const input = "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm";

  return (
    <Card className="max-w-xl">
      <h2 className="font-semibold">Work as a consultant</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Consultants sign in to their clients&apos; accounts and do the work for them. Every consultant is screened and
        approved by us first{check?.mode === "invite" ? ", and applications are by invitation for now" : ""}.
      </p>

      {/* The last decision, with the reason when it wasn't approved. */}
      {latest?.status === "denied" && (
        <div className="mt-4 rounded-xl border border-line bg-surface-2 p-3 text-sm">
          <p>
            <strong>Your application from {fmtDate(latest.submitted_at)} wasn&apos;t approved.</strong>
          </p>
          {latest.human_review_note && <p className="mt-1 text-ink-muted">{latest.human_review_note}</p>}
          <p className="mt-2"><span className="font-medium">The main reason:</span> {latest.denial_reason_text}</p>
          {latest.decision_note && (
            <p className="mt-1 whitespace-pre-line"><span className="font-medium">Personal feedback:</span> {latest.decision_note}</p>
          )}
        </div>
      )}

      {check && !check.can_apply && (
        <p className="mt-4 text-sm" role="status">
          <strong>{check.message}</strong>
          {check.blocked === "pending" && " We'll switch your workspace to consultant mode once it's approved."}
        </p>
      )}

      {check?.can_apply && !open && (
        <Button className="mt-4" onClick={() => setOpen(true)}>
          {latest?.status === "denied" ? "Apply again" : "Apply to be a consultant"}
        </Button>
      )}

      {check?.can_apply && open && (
        <div className="mt-4 space-y-4 text-sm">
          <fieldset>
            <legend className="font-medium">Services you can deliver</legend>
            <div className="mt-2 grid gap-1 sm:grid-cols-2">
              {services.map((s) => (
                <label key={s.id} className="flex items-center gap-2">
                  <input type="checkbox" checked={form.services.includes(s.id)} onChange={() => toggleService(s.id)} />
                  {s.name}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="block">
            <span className="font-medium">Hands-on data experience</span>
            <select value={form.experience} onChange={(e) => set("experience", e.target.value as ConsultantApplicationInput["experience"])}
              className={`${input} mt-1 sm:w-56`}>
              <option value="">Choose…</option>
              {check.experience_ranges.map((r) => (
                <option key={r.id} value={r.id}>{r.label}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="font-medium">Tools you use well</span>
            <input value={form.tools} maxLength={300} placeholder="e.g. SQL, pandas, Power BI, Tableau"
              onChange={(e) => set("tools", e.target.value)} className={`${input} mt-1`} />
          </label>
          <label className="block">
            <span className="font-medium">Your LinkedIn profile (required)</span>
            <input type="url" value={form.linkedin_url} maxLength={500} placeholder="https://www.linkedin.com/in/your-name"
              onChange={(e) => set("linkedin_url", e.target.value)} className={`${input} mt-1`} />
            <span className="mt-1 block text-xs text-ink-muted">
              We use it to confirm you&apos;re a real, verifiable person and to see your experience.
            </span>
          </label>
          <label className="block">
            <span className="font-medium">A portfolio or work sample (optional)</span>
            <input type="url" value={form.work_sample_url} maxLength={500}
              placeholder="https://github.com/you/project — a report, notebook or dashboard"
              onChange={(e) => set("work_sample_url", e.target.value)} className={`${input} mt-1`} />
          </label>
          <label className="block">
            <span className="font-medium">A data project you delivered</span>
            <textarea value={form.project_summary} maxLength={2000} rows={5}
              placeholder="The problem, what you did (data, tools, steps), and the result for the business."
              onChange={(e) => set("project_summary", e.target.value)} className={`${input} mt-1`} />
            <span className="mt-1 block text-xs text-ink-muted">
              <span className="font-mono">{form.project_summary.trim().length}</span> / {MIN_SUMMARY}+ characters
            </span>
          </label>
          <fieldset className="rounded-xl border border-line p-3">
            <legend className="px-1 font-medium">Consultant code of conduct</legend>
            <ul className="list-inside list-disc space-y-1 text-ink-muted">
              {check.code_of_conduct.map((rule) => (
                <li key={rule}>{rule}</li>
              ))}
            </ul>
            <label className="mt-3 flex items-start gap-2">
              <input type="checkbox" className="mt-1" checked={form.agree_conduct}
                onChange={(e) => set("agree_conduct", e.target.checked)} />
              I agree to follow the code of conduct.
            </label>
          </fieldset>
          {missing.length > 0 && <p className="text-xs text-ink-muted">To send it: {missing.join(", ")}.</p>}
          <div className="flex gap-2">
            <Button onClick={send} disabled={busy || missing.length > 0}>
              {busy ? "Sending…" : "Send my application"}
            </Button>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      <ErrorBox message={error} />
    </Card>
  );
}

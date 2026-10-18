// Settings -> Account -> security cards (your OWN account; never shown while working on a client).
//
//   Two-step sign-in   set up an authenticator app (Google Authenticator,
//                      Microsoft Authenticator, 1Password...): type the key
//                      into the app, then its 6-digit code; save the 10 backup
//                      codes (shown ONCE). Required for admins and consultants.
//   Your devices       every device signed in now (3 at most), with "Sign out"
//                      per device and "Sign out everywhere else".
//   Activity log       HEALTHCARE clients only (the owner): who touched the
//                      data and when — sign-ins, loads, exports, downloads —
//                      never the data itself. Download as CSV.
// Server side: api/services/mfa.py, sessions.py, audit.py.

"use client";

import { useEffect, useState } from "react";
import {
  disableMfa,
  downloadAuditLog,
  enableMfa,
  endOtherSessions,
  endSession,
  getAuditLog,
  getSessions,
  startMfaSetup,
} from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type { Account, AuditEntry, DeviceSessions, MfaSetup } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const field = "rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand";

/** Two-step sign-in: set up, show backup codes once, or turn off (not for staff). */
export function TwoStepCard(props: { token: string; account: Account; onToken: (token: string) => void;
  onChanged: () => void }) {
  const a = props.account;
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className={a.mfa_required && !a.mfa_enabled ? "border-warn/50" : ""}>
      <h2 className="font-semibold">Two-step sign-in</h2>
      <p className="mt-1 text-sm text-ink-muted">
        After your password, a 6-digit code from an authenticator app on your phone — so a stolen password alone can&apos;t
        open your account.
        {a.mfa_required && " Admins and consultants need it before the Admin panel or any client opens."}
      </p>
      <ErrorBox message={error} />

      {codes ? (
        <div className="mt-3 space-y-2 text-sm">
          <p>
            <strong>It&apos;s on.</strong> Save these backup codes somewhere safe (a password manager). Each works once if
            you lose your phone. They won&apos;t be shown again.
          </p>
          <ul className="grid grid-cols-2 gap-1 rounded-xl bg-surface-2 p-3 font-mono sm:grid-cols-5">
            {codes.map((c) => <li key={c}>{c}</li>)}
          </ul>
          <Button variant="secondary" onClick={() => { setCodes(null); props.onChanged(); }}>I saved them</Button>
        </div>
      ) : a.mfa_enabled ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <span className="rounded-full bg-good/15 px-2 py-0.5 text-[11px] font-semibold text-good">On</span>
          {!a.mfa_required && (
            <>
              <input className={`${field} w-32 font-mono`} placeholder="Code to turn off" value={code}
                onChange={(e) => setCode(e.target.value)} inputMode="numeric" aria-label="Current code" />
              <Button variant="secondary" disabled={busy || code.trim().length < 6}
                onClick={() => run(async () => { await disableMfa(props.token, code.trim()); setCode(""); props.onChanged(); })}>
                Turn off
              </Button>
            </>
          )}
        </div>
      ) : !setup ? (
        <Button className="mt-3" disabled={busy} onClick={() => run(async () => setSetup(await startMfaSetup(props.token)))}>
          Set up two-step sign-in
        </Button>
      ) : (
        <div className="mt-3 space-y-3 text-sm">
          <ol className="list-decimal space-y-2 pl-5">
            <li>
              Open an authenticator app and add an account. Type this key (or, on your phone,{" "}
              <a href={setup.otpauth_url} className="text-brand hover:underline">open this link</a>):
              <span className="mt-1 block break-all rounded-lg bg-surface-2 px-2 py-1 font-mono">{setup.secret}</span>
            </li>
            <li>Type the 6-digit code the app shows:</li>
          </ol>
          <div className="flex flex-wrap gap-2">
            <input className={`${field} w-32 text-center font-mono tracking-widest`} value={code} placeholder="123456"
              onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" aria-label="6-digit code" />
            <Button disabled={busy || code.trim().length < 6}
              onClick={() => run(async () => {
                const res = await enableMfa(props.token, code.trim());
                props.onToken(res.access_token); // this device carries on; others sign in with the code
                setCodes(res.recovery_codes);
                setSetup(null);
                setCode("");
              })}>
              {busy ? "Checking…" : "Turn on"}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

/** Your devices: each one signed in now, sign out one or all others. */
export function DevicesCard({ token }: { token: string }) {
  const [data, setData] = useState<DeviceSessions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getSessions(token).then(setData).catch((e) => setError(e instanceof Error ? e.message : "We couldn't load your devices."));
  }, [token]);

  async function run(work: () => Promise<DeviceSessions>) {
    setBusy(true);
    setError(null);
    try {
      setData(await work());
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <h2 className="font-semibold">Your devices</h2>
      <p className="mt-1 text-sm text-ink-muted">
        You can be signed in on {data?.max_devices ?? 3} devices at once; signing in on another signs out the one used
        longest ago. A device is signed out after {data?.idle_hours ?? 8} hours without use.
      </p>
      <ErrorBox message={error} />
      <ul className="mt-2 divide-y divide-line">
        {data?.sessions.map((s) => (
          <li key={s.id} className="flex items-center justify-between gap-3 py-2 text-sm">
            <span className="min-w-0">
              <span className="font-medium">{s.device}</span>
              {s.this_device && <span className="ml-2 text-xs text-good">this device</span>}
              <span className="block text-xs text-ink-muted">Last used {fmtDateTime(s.last_used_at)}</span>
            </span>
            {!s.this_device && (
              <Button variant="secondary" className="px-4 py-1" disabled={busy}
                onClick={() => run(() => endSession(token, s.id))}>
                Sign out
              </Button>
            )}
          </li>
        ))}
      </ul>
      {(data?.sessions.length ?? 0) > 1 && (
        <Button variant="secondary" className="mt-2" disabled={busy} onClick={() => run(() => endOtherSessions(token))}>
          Sign out everywhere else
        </Button>
      )}
    </Card>
  );
}

// The actions in plain words.
const ACTION: Record<string, string> = {
  sign_in: "Signed in", view_stored: "Opened stored data", export: "Downloaded stored data", load: "Stored data",
  delete_stored: "Deleted stored data", download: "Downloaded a cleaned file", connect: "Connected a tool",
  sync: "Synced a tool", disconnect: "Disconnected a tool", view_live: "Opened live tool data",
  ai_question: "Asked Beacon",
};

/** Healthcare clients: who touched the data, when (never the data itself). */
export function ActivityLogCard({ token }: { token: string }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAuditLog(token)
      .then((r) => setEntries(r.entries))
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load the activity log."));
  }, [token]);

  async function download() {
    try {
      const blob = await downloadAuditLog(token);
      const url = URL.createObjectURL(blob);
      const link = Object.assign(document.createElement("a"), { href: url, download: "activity_log.csv" });
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The download didn't work.");
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">Activity log</h2>
        <Button variant="secondary" className="px-4 py-1" onClick={download}>Download CSV</Button>
      </div>
      <p className="mt-1 text-sm text-ink-muted">
        For healthcare businesses: every time someone on your team, your consultant or an admin signs in or opens,
        stores, downloads or deletes your data. Only names and counts are kept here — never the data itself.
      </p>
      <ErrorBox message={error} />
      {entries?.length === 0 && <p className="mt-3 text-sm">Nothing yet.</p>}
      <ul className="mt-2 max-h-80 divide-y divide-line overflow-y-auto text-sm">
        {entries?.map((e, i) => (
          <li key={`${e.at}-${i}`} className="py-2">
            <span className="font-medium">{ACTION[e.action] ?? e.action}</span>
            <span className="text-ink-muted"> · {e.actor_name ?? "someone"} ({e.actor_role})</span>
            <span className="block text-xs text-ink-muted">
              {fmtDateTime(e.at)}
              {Object.entries(e.detail).map(([k, v]) => ` · ${k}: ${v}`).join("")}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

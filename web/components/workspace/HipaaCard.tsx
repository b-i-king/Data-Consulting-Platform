// Settings -> Account -> "Health information (HIPAA)" (a business's owner; not while working on a client).
//
// Most businesses never need this: names, amounts, visits counts and revenue
// by service aren't health records. A business that WILL store protected health
// information (patient records, diagnoses, measurements over time) asks here —
// automatically when it picked "Health Care" at sign-up — with its legal name,
// its NPI (National Provider Identifier) and what it would store. An admin
// confirms it's a real healthcare business, then approves (every access is
// written to the activity log) or doesn't. While it's waiting, storing data is
// paused; cleaning and analyzing files (kept in memory only) still work.
// Server side: api/services/hipaa.py.

"use client";

import { useEffect, useState } from "react";
import { getHipaa, requestHipaa, withdrawHipaa } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { HipaaStatus } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const field = "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand";
const TONE: Record<string, string> = {
  pending: "bg-warn/15 text-warn", approved: "bg-good/15 text-good", denied: "bg-bad/15 text-bad",
};

export default function HipaaCard({ token, onChanged }: { token: string; onChanged?: () => void }) {
  const [data, setData] = useState<HipaaStatus | null>(null);
  const [editing, setEditing] = useState(false);
  const [org, setOrg] = useState("");
  const [npi, setNpi] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHipaa(token)
      .then((d) => {
        setData(d);
        setOrg(d.organization ?? "");
        setNpi(d.npi ?? "");
        setNote(d.note ?? "");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load this."));
  }, [token]);

  async function run(work: () => Promise<HipaaStatus>) {
    setBusy(true);
    setError(null);
    try {
      setData(await work());
      setEditing(false);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  }

  if (!data) return error ? <ErrorBox message={error} /> : null;
  const canAsk = data.status !== "approved";

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">Health information (HIPAA)</h2>
        {data.status && (
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${TONE[data.status]}`}>{data.label}</span>
        )}
      </div>
      <p className="mt-1 text-sm text-ink-muted">
        Only needed if you&apos;ll store patients&apos; health records (diagnoses, measurements, visit notes). Business
        numbers like revenue by service or visit counts don&apos;t need it. We confirm your practice first; until then,
        don&apos;t upload patient health information.
      </p>
      <ErrorBox message={error} />

      {data.status === "pending" && (
        <p className="mt-3 rounded-xl border border-warn/40 bg-warn/10 p-2 text-sm">
          We&apos;re confirming your healthcare business (asked {data.requested_at ? fmtDate(data.requested_at) : ""}).{" "}
          <strong>Storing data is paused</strong> until then — you can still clean and analyze files.
          {!data.npi && " Adding your NPI speeds this up."}
        </p>
      )}
      {data.status === "approved" && (
        <p className="mt-3 text-sm">
          Approved{data.decided_at ? ` on ${fmtDate(data.decided_at)}` : ""}. Every access to your data is recorded in
          your activity log.
        </p>
      )}
      {data.status === "denied" && (
        <p className="mt-3 text-sm">
          Not approved{data.decision_note ? `: ${data.decision_note}` : "."} You can store ordinary business data, but not
          patient health information. You can ask again with more details.
        </p>
      )}

      {canAsk && (editing || data.status === "pending" ? (
        <div className="mt-3 space-y-2">
          <input className={field} placeholder="Legal name of your practice or organization" value={org}
            onChange={(e) => setOrg(e.target.value)} aria-label="Organization's legal name" />
          <input className={field} placeholder="NPI (10 digits, from the NPPES registry)" value={npi} inputMode="numeric"
            onChange={(e) => setNpi(e.target.value)} aria-label="NPI" />
          <textarea className={field} rows={2} placeholder="What health information would you store?" value={note}
            onChange={(e) => setNote(e.target.value)} aria-label="What you'd store" maxLength={1000} />
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy || org.trim().length < 2}
              onClick={() => run(() => requestHipaa(token, { organization: org.trim(), npi: npi.trim() || undefined,
                note: note.trim() || undefined }))}>
              {data.status === "pending" ? "Update my request" : "Ask for HIPAA"}
            </Button>
            {data.status === "pending" ? (
              <Button variant="secondary" disabled={busy} onClick={() => run(() => withdrawHipaa(token))}>
                Withdraw (I won&apos;t store health data)
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
            )}
          </div>
        </div>
      ) : (
        <Button variant="secondary" className="mt-3" onClick={() => setEditing(true)}>
          {data.status === "denied" ? "Ask again" : "I'll store health information"}
        </Button>
      ))}
    </Card>
  );
}

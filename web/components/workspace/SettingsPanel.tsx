// Settings (⚙️ in the workspace's left pane).
//
//   * CONSULTANTS: "Capacity" — how many clients I take at once (1 to 10,
//     default 3), with my fill bar. Clients see the bar when they pick a
//     consultant; when I'm full, nobody new can pick me (lowering it drops nobody).
//   * CLIENTS: "Work as a consultant" — send a request; an admin approves or
//     denies (status shown here). Approval switches this workspace to
//     consultant mode on its next load.
//
// Server side: api/routers/consultants.py (settings), api/routers/accounts.py
// (consultant-request).

"use client";

import { useEffect, useState } from "react";
import { getConsultantSettings, requestConsultantRole, setConsultantCapacity } from "@/lib/api";
import type { Account, ConsultantLoad } from "@/lib/types";
import { LoadBar } from "@/components/consultants/ConsultantPicker";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

export default function SettingsPanel(props: { token: string; account: Account; onAccount: (a: Account) => void }) {
  const isConsultant = props.account.role === "consultant";
  return isConsultant ? <Capacity token={props.token} /> : <AskToConsult {...props} />;
}

function Capacity({ token }: { token: string }) {
  const [load, setLoad] = useState<ConsultantLoad | null>(null);
  const [max, setMax] = useState(10);
  const [draft, setDraft] = useState(3);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getConsultantSettings(token)
      .then((r) => {
        setLoad(r.load);
        setMax(r.max_capacity);
        setDraft(r.load.capacity);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load your settings."));
  }, [token]);

  async function save() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await setConsultantCapacity(token, draft);
      setLoad(r.load);
      setNote("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="max-w-xl">
      <h2 className="font-semibold">Capacity</h2>
      <p className="mt-1 text-sm text-ink-muted">
        How many clients you take at once (1 to {max}). Clients see how full you are when they pick a consultant; when
        you&apos;re full, nobody new can pick you. Lowering it never drops a client.
      </p>
      {load && (
        <div className="mt-4 space-y-4">
          <LoadBar load={load} />
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
            <Button onClick={save} disabled={busy || draft === load.capacity}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </label>
          {note && <p className="text-sm text-good" role="status">{note}</p>}
        </div>
      )}
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

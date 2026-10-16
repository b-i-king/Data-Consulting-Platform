// Settings -> Integrations: the data sources an account has connected, in two
// sections (api/services/connections.py CONNECTORS):
//
//   CLOUD STORAGE — read where it lives, never copied (the client already
//     manages it). First: "Supabase database" — the client's own Supabase or
//     any PostgreSQL. Paste a connection string (ideally a READ-ONLY user);
//     the server checks it, lists the tables, and saves it ENCRYPTED. Each
//     table then has "Open in workspace": it opens for analysis, dashboards,
//     ML and AI. Data Cleaning only reports on it; storing is switched off.
//   BUSINESS TOOLS — copied into managed storage on a schedule (needs Data
//     Engineering). First: QuickBooks (coming soon), with its refresh schedule.
//
// Server side: api/routers/connections.py, api/services/cloud_sources.py.

"use client";

import { useEffect, useState, type FormEvent } from "react";
import { connectSupabase, disconnectIntegration, getCloudTables, getConnections } from "@/lib/api";
import { fmtInt } from "@/lib/format";
import type { CloudTable, ConnectionsInfo, DataConnection } from "@/lib/types";
import DataRefresh from "@/components/billing/DataRefresh";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const STATUS: Record<string, { label: string; cls: string }> = {
  connected: { label: "Connected", cls: "bg-good/15 text-good" },
  pending: { label: "Schedule chosen", cls: "bg-warn/15 text-warn" },
  not_set_up: { label: "Not connected", cls: "bg-surface-2 text-ink-muted" },
  error: { label: "Problem", cls: "bg-bad/15 text-bad" },
};

export default function IntegrationsPanel(props: {
  token: string;
  /** The plan holder (or their consultant) may connect; team members may not. */
  canChange: boolean;
  /** Open one of the client's cloud tables in the workspace. */
  onOpenTable: (t: CloudTable) => void;
}) {
  const [info, setInfo] = useState<ConnectionsInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getConnections(props.token)
      .then(setInfo)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load your integrations."));
  }, [props.token]);

  if (!info) return error ? <ErrorBox message={error} /> : <p className="text-sm text-ink-muted">Loading…</p>;
  const sections: DataConnection["kind"][] = ["cloud_storage", "business_tool"];

  return (
    <div className="max-w-3xl space-y-8">
      {sections.map((kind) => (
        <section key={kind} className="space-y-3">
          <h2 className="text-lg font-semibold">{info.kinds[kind] ?? kind}</h2>
          <p className="text-sm text-ink-muted">
            {kind === "cloud_storage"
              ? "Your data already lives (and is managed) here. We read it where it is — nothing is copied — and you analyze it, build dashboards, and run ML and AI on it."
              : "Your business apps. Their data is copied into your managed storage on the schedule you choose, so every tool can use it."}
          </p>
          {info.connectors
            .filter((c) => c.kind === kind)
            .map((c) =>
              c.connector === "supabase" ? (
                <SupabaseCard key={c.connector} token={props.token} connector={c} canChange={props.canChange}
                  onChanged={(connectors) => setInfo({ ...info, connectors })} onOpenTable={props.onOpenTable} />
              ) : (
                <Card key={c.connector} className="space-y-3">
                  <Header c={c} />
                  <p className="text-sm text-ink-muted">{c.about}</p>
                  {!c.available && (
                    <p className="text-xs text-ink-muted">
                      Coming soon: connecting it opens {c.name}&apos;s own sign-in. You can choose the refresh schedule now.
                    </p>
                  )}
                  {c.stores_data && <DataRefresh token={props.token} canChange={props.canChange} connector={c.connector} />}
                </Card>
              ),
            )}
        </section>
      ))}
    </div>
  );
}

function Header({ c }: { c: DataConnection }) {
  const st = STATUS[c.status] ?? STATUS.not_set_up;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold">
        {c.name}
        {!c.available && <span className="ml-2 text-xs font-normal text-ink-muted">coming soon</span>}
      </h3>
      <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${st.cls}`}>{st.label}</span>
    </div>
  );
}

/** The client's own Supabase / PostgreSQL database: connect, see its tables, open one, disconnect. */
function SupabaseCard(props: {
  token: string;
  connector: DataConnection;
  canChange: boolean;
  onChanged: (connectors: DataConnection[]) => void;
  onOpenTable: (t: CloudTable) => void;
}) {
  const c = props.connector;
  const connected = c.status === "connected";
  const [dsn, setDsn] = useState("");
  const [tables, setTables] = useState<CloudTable[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!connected) return;
    getCloudTables(props.token)
      .then((r) => setTables(r.tables))
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't read your database."));
  }, [connected, props.token]);

  async function connect(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await connectSupabase(props.token, dsn);
      setDsn("");
      setTables(r.tables);
      props.onChanged(r.connectors);
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't connect.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      props.onChanged((await disconnectIntegration(props.token, c.connector)).connectors);
      setTables(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't disconnect.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="space-y-3">
      <Header c={c} />
      <p className="text-sm text-ink-muted">{c.about}</p>
      {connected ? (
        <>
          <p className="text-sm">
            Connected to <strong>{c.display_name}</strong>. Read-only — we never change or copy your data.
          </p>
          {tables === null && !error && <p className="text-sm text-ink-muted">Reading your tables…</p>}
          {tables && tables.length === 0 && <p className="text-sm text-ink-muted">No tables found (check the user&apos;s permissions).</p>}
          {tables && tables.length > 0 && (
            <ul className="max-h-80 divide-y divide-line overflow-y-auto rounded-xl border border-line">
              {tables.map((t) => (
                <li key={`${t.schema}.${t.name}`} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                  <span>
                    <span className="font-mono">{t.schema}.{t.name}</span>
                    <span className="ml-2 text-xs text-ink-muted">
                      {t.kind}
                      {t.rows_estimate > 0 && ` · ~${fmtInt(t.rows_estimate)} rows`}
                    </span>
                  </span>
                  <Button variant="secondary" onClick={() => props.onOpenTable(t)}>
                    Open in workspace
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {props.canChange && (
            <button type="button" className="text-sm text-bad hover:underline" disabled={busy} onClick={disconnect}>
              Disconnect (deletes the saved login)
            </button>
          )}
        </>
      ) : props.canChange ? (
        <form onSubmit={connect} className="space-y-2">
          <label className="block space-y-1 text-sm">
            <span>Connection string</span>
            <input
              type="password"
              autoComplete="off"
              placeholder="postgresql://readonly_user:password@db.xxxx.supabase.co:5432/postgres"
              value={dsn}
              onChange={(e) => setDsn(e.target.value)}
              className="w-full rounded-xl border border-line bg-surface px-3 py-2 font-mono text-xs"
            />
            <span className="block text-xs text-ink-muted">
              In Supabase: Project → Connect → Connection string. Best practice: create a read-only database user for
              us. The login is checked, then saved encrypted; we only ever read.
            </span>
          </label>
          <Button type="submit" disabled={busy || dsn.trim().length < 12}>
            {busy ? "Checking…" : "Connect"}
          </Button>
        </form>
      ) : (
        <p className="text-sm text-ink-muted">Ask your team&apos;s owner to connect it.</p>
      )}
      <ErrorBox message={error} />
    </Card>
  );
}

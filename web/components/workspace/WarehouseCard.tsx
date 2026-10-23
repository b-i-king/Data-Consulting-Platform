"use client";

// ── Snowflake, BigQuery, Salesforce: connect, list tables, summarize or open ──
// One card for the three, in Settings -> Integrations -> Cloud storage
// (api/services/warehouses.py):
//   * Snowflake — account, read-only user, its PRIVATE key (key-pair sign-in;
//     no password), warehouse and database.
//   * BigQuery  — the service account's key file (JSON), with only BigQuery
//     Data Viewer + Job User.
//   * Salesforce — the "Connect Salesforce" sign-in.
// Then each table (object) can be SUMMARIZED in the source (SummarizePanel —
// only the answer comes back) or OPENED (capped: 20,000 rows; Salesforce 2,000).
// Nothing is stored.

import { useEffect, useState, type FormEvent } from "react";
import {
  connectBigQuery, connectSnowflake, disconnectIntegration, getWarehouseTableFile, getWarehouseTables, startConnect,
} from "@/lib/api";
import { fmtInt } from "@/lib/format";
import type { DataConnection } from "@/lib/types";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";
import SummarizePanel from "./SummarizePanel";

const field = "rounded-xl border border-line bg-surface px-3 py-2 text-sm";
const SHORT: Record<string, string> = { snowflake: "Snowflake", bigquery: "BigQuery", salesforce: "Salesforce" };

export default function WarehouseCard(props: {
  token: string;
  connector: DataConnection;
  canChange: boolean;
  onChanged: (connectors: DataConnection[]) => void;
  onOpenFile?: (file: File, from: string, note: string | null) => void;
}) {
  const c = props.connector;
  const name = SHORT[c.connector] ?? c.name;
  const connected = c.status === "connected";
  const [form, setForm] = useState<Record<string, string>>({});
  const [tables, setTables] = useState<{ name: string; label?: string; rows: number | null }[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);      // the table whose Summarize form is showing
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: string) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  useEffect(() => {
    if (!connected) return;
    getWarehouseTables(props.token, c.connector).then((r) => setTables(r.tables))
      .catch((e) => setError(e instanceof Error ? e.message : `We couldn't read ${name}.`));
  }, [connected, c.connector, props.token, name]);

  async function act(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    } finally {
      setBusy(false);
    }
  }

  const connect = (e: FormEvent) => {
    e.preventDefault();
    void act(async () => {
      const r = c.connector === "snowflake"
        ? await connectSnowflake(props.token, form)
        : await connectBigQuery(props.token, form.service_account_json ?? "", form.location ?? "");
      setForm({});
      setTables(r.tables);
      props.onChanged(r.connectors);
    });
  };

  const openTable = (t: string) => act(async () => {
    const got = await getWarehouseTableFile(props.token, c.connector, t);
    props.onOpenFile?.(got.file, `Live from ${name}`,
      got.truncated ? `the first ${fmtInt(got.rows)} rows (the table has more — summarize to use all of it)` : null);
  });

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">{c.name}</h3>
        <span className="text-xs text-ink-muted">{connected ? `Connected: ${c.display_name}` : "Not connected"}</span>
      </div>
      <p className="text-sm text-ink-muted">{c.about}</p>

      {!connected && c.connector === "salesforce" && (
        c.sign_in ? (
          <Button disabled={!props.canChange || busy}
            onClick={() => act(async () => window.location.assign(await startConnect(props.token, "salesforce")))}>
            Connect Salesforce
          </Button>
        ) : <p className="text-sm text-ink-muted">Coming soon.</p>
      )}
      {!connected && c.connector === "snowflake" && (
        <form onSubmit={connect} className="grid gap-2 sm:grid-cols-2">
          <input className={field} placeholder="Account (myorg-myaccount)" aria-label="Account" value={form.account ?? ""} onChange={set("account")} />
          <input className={field} placeholder="Read-only user" aria-label="User" value={form.user ?? ""} onChange={set("user")} />
          <input className={field} placeholder="Warehouse (e.g. SMALL_WH)" aria-label="Warehouse" value={form.warehouse ?? ""} onChange={set("warehouse")} />
          <input className={field} placeholder="Database" aria-label="Database" value={form.database ?? ""} onChange={set("database")} />
          <input className={field} placeholder="Role (optional)" aria-label="Role" value={form.role ?? ""} onChange={set("role")} />
          <input className={field} placeholder="Schema (optional)" aria-label="Schema" value={form.schema ?? ""} onChange={set("schema")} />
          <textarea className={`${field} sm:col-span-2`} rows={4} placeholder="-----BEGIN PRIVATE KEY----- … (the user's key pair; saved encrypted)"
            aria-label="Private key" value={form.private_key ?? ""} onChange={set("private_key")} />
          <Button type="submit" disabled={!props.canChange || busy}>{busy ? "Checking…" : "Connect Snowflake"}</Button>
        </form>
      )}
      {!connected && c.connector === "bigquery" && (
        <form onSubmit={connect} className="grid gap-2">
          <textarea className={field} rows={5} placeholder='The service account key file (JSON): {"type": "service_account", …}'
            aria-label="Service account key" value={form.service_account_json ?? ""} onChange={set("service_account_json")} />
          <input className={field} placeholder="Location (optional, e.g. US or EU)" aria-label="Location" value={form.location ?? ""} onChange={set("location")} />
          <Button type="submit" disabled={!props.canChange || busy}>{busy ? "Checking…" : "Connect BigQuery"}</Button>
        </form>
      )}

      {connected && tables && (
        <ul className="max-h-96 divide-y divide-line overflow-y-auto rounded-xl border border-line">
          {tables.length === 0 && <li className="px-3 py-2 text-sm text-ink-muted">No tables found.</li>}
          {tables.map((t) => (
            <li key={t.name} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
              <span>
                <span className="font-mono">{t.name}</span>
                {t.rows !== null && <span className="ml-2 text-xs text-ink-muted">~{fmtInt(t.rows)} rows</span>}
              </span>
              <span className="flex gap-2">
                <Button variant="secondary" onClick={() => setOpen(open === t.name ? null : t.name)}>Summarize</Button>
                <Button variant="secondary" disabled={busy || !props.onOpenFile} onClick={() => openTable(t.name)}>Open</Button>
              </span>
              {open === t.name && props.onOpenFile && (
                <SummarizePanel token={props.token} connector={c.connector} table={t.name} sourceName={name}
                  onOpenFile={props.onOpenFile} />
              )}
            </li>
          ))}
        </ul>
      )}
      {connected && props.canChange && (
        <Button variant="secondary" disabled={busy}
          onClick={() => act(async () => { props.onChanged((await disconnectIntegration(props.token, c.connector)).connectors); setTables(null); })}>
          Disconnect
        </Button>
      )}
      <ErrorBox message={error} />
    </Card>
  );
}

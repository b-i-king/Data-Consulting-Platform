// Settings -> Integrations: the data sources an account has connected, in two
// sections (api/services/connections.py CONNECTORS):
//
//   CLOUD STORAGE — read where it lives, never copied (the client already
//     manages it). "Supabase / PostgreSQL database", connected either way:
//       * "Sign in with Supabase" (when the platform's Supabase OAuth keys are
//         set): no password at all; they pick a project, and we read through
//         Supabase's read-only API;
//       * or paste a connection string (ideally a READ-ONLY user); checked,
//         then saved ENCRYPTED.
//     Tables are shown schema by schema; EMPTY tables (no rows) are hidden —
//     there's nothing to analyze, so nothing is spent on them. Each table with
//     data has "Open in workspace". Data Cleaning only reports on it; storing is off.
//   BUSINESS TOOLS — QuickBooks and Xero, connected with a "Connect" sign-in
//     on their own site (read-only). Connected, they give a free LIVE quick
//     look (QuickLook.tsx) and their records can be opened in the workspace —
//     read when asked, never stored. With Data Engineering, "Store your
//     records" turns on the STORED SYNC (api/services/sync.py): the full
//     history, then only what changed, on the refresh schedule.
// At the top: "Is this the same customer?" (EntityReview.tsx) when names from
// two sources look alike.
//     FREE-PLAN LIMITS: Xero allows 5 connected organizations, QuickBooks we
//     cap at 15 — when all are taken, "Connect" is replaced by a note — and
//     each company has a daily call allowance, shown on the card ("calls today").
//
// Under each section, the tools on the roadmap ("Coming soon" / "Later",
// api/services/connections.py COMING_SOON) so clients know theirs is planned.
//
// Server side: api/routers/connections.py, api/services/oauth.py,
// api/services/business_tools.py, api/services/cloud_sources.py.

"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  chooseSupabaseProject,
  connectSupabase,
  disconnectIntegration,
  getCloudTables,
  getConnections,
  getLiveDatasets,
  getSupabaseProjects,
  startConnect,
  startSync,
} from "@/lib/api";
import { fmtDateTime, fmtInt } from "@/lib/format";
import EntityReview from "@/components/workspace/EntityReview";
import type {
  CloudSchema,
  CloudTable,
  ConnectionsInfo,
  DataConnection,
  LiveDataset,
  PlannedIntegration,
  SupabaseProject,
} from "@/lib/types";
import DataRefresh from "@/components/billing/DataRefresh";
import QuickLook from "@/components/workspace/QuickLook";
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
  /** Open a QuickBooks / Xero record type in the workspace (live, never stored). */
  onOpenLive: (connector: string, dataset: LiveDataset) => void;
  /** After coming back from a provider's sign-in: what happened. */
  notice?: { ok: boolean; text: string } | null;
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
  const changed = (connectors: DataConnection[]) => setInfo({ ...info, connectors });

  return (
    <div className="max-w-3xl space-y-8">
      <EntityReview token={props.token} canChange={props.canChange} />
      {props.notice && (
        <p role="status"
          className={`rounded-2xl border p-3 text-sm ${props.notice.ok ? "border-good/40 bg-good/10" : "border-bad/40 bg-bad/10"}`}>
          {props.notice.text}
        </p>
      )}
      {sections.map((kind) => (
        <section key={kind} className="space-y-3">
          <h2 className="text-lg font-semibold">{info.kinds[kind] ?? kind}</h2>
          <p className="text-sm text-ink-muted">
            {kind === "cloud_storage"
              ? "Your data already lives (and is managed) here. We read it where it is — nothing is copied — and you analyze it, build dashboards, and run ML and AI on it."
              : "Your business apps. Connect one for a free live quick look and to open its records in the workspace — read when you ask, nothing stored. Storing it on a schedule (for history and trends) comes with Data Engineering."}
          </p>
          {info.connectors
            .filter((c) => c.kind === kind)
            .map((c) =>
              c.connector === "supabase" ? (
                <SupabaseCard key={c.connector} token={props.token} connector={c} canChange={props.canChange}
                  onChanged={changed} onOpenTable={props.onOpenTable} />
              ) : (
                <BusinessToolCard key={c.connector} token={props.token} connector={c} canChange={props.canChange}
                  onChanged={changed} onOpenLive={props.onOpenLive} canStore={info.can_connect}
                  full={!!info.slots?.[c.connector] && info.slots[c.connector].used >= info.slots[c.connector].max} />
              ),
            )}
          <Planned items={(info.coming_soon ?? []).filter((p) => p.kind === kind)} />
        </section>
      ))}
    </div>
  );
}

/** The tools on the roadmap for one section, as name chips: "Coming soon" first, then "Later". */
function Planned({ items }: { items: PlannedIntegration[] }) {
  if (items.length === 0) return null;
  const groups = [
    { when: "soon", title: "Coming soon" },
    { when: "later", title: "Later" },
  ] as const;
  return (
    <div className="space-y-2 rounded-xl border border-dashed border-line px-4 py-3">
      {groups.map((g) => {
        const names = items.filter((p) => p.when === g.when);
        if (names.length === 0) return null;
        return (
          <div key={g.when} className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="mr-1 font-semibold text-ink-muted">{g.title}:</span>
            {names.map((p) => (
              <span key={p.name} className="rounded-full bg-surface-2 px-2 py-0.5 text-ink-muted">
                {p.name}
              </span>
            ))}
          </div>
        );
      })}
      <p className="text-xs text-ink-muted">
        Need one of these now? Upload an export as a file in the meantime — or book a consultation and we&apos;ll help.
      </p>
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

/**
 * "Store your records" (Data Engineering): the stored sync for a connected QuickBooks / Xero —
 * the full history once, then only what changed, on the refresh schedule below it.
 */
function StoredSync(props: {
  token: string;
  connector: DataConnection;
  canStore: boolean;
  canChange: boolean;
  onChanged: (connectors: DataConnection[]) => void;
}) {
  const c = props.connector;
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = !!c.last_synced_at;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await startSync(props.token, c.connector, consent);
      props.onChanged(r.connectors);
      const s = r.sync;
      setNote(
        `${fmtInt(s.records)} record(s) read: ${fmtInt(s.rows_loaded)} new row(s), ${fmtInt(s.rows_updated)} updated.` +
          (s.complete ? "" : " Stopped at today's allowance — it carries on tomorrow.") +
          (s.duplicates_elsewhere ? ` ${fmtInt(s.duplicates_elsewhere)} were also in your uploaded files.` : "") +
          (s.matches_to_review ? ` ${fmtInt(s.matches_to_review)} name(s) to confirm above.` : ""),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "The sync didn't run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-xl border border-line p-3 text-sm">
      <div className="font-semibold">Store your records (Data Engineering)</div>
      {!props.canStore ? (
        <p className="text-ink-muted">
          Add Data Engineering to your package to keep your full history — trends over the years, and {c.name} combined
          with your other data.
        </p>
      ) : (
        <>
          <p className="text-ink-muted">
            {started
              ? `Stored and kept up to date. Last sync: ${fmtDateTime(c.last_synced_at!)}${c.next_sync_at ? ` · next: ${fmtDateTime(c.next_sync_at)}` : ""}.`
              : "The full history is stored once, then only what changed. Customers are matched with your other sources."}
          </p>
          {!started && props.canChange && (
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-1" />
              <span>Yes, store my {c.name} data in my account. I can delete it anytime.</span>
            </label>
          )}
          {props.canChange && (
            <Button variant={started ? "secondary" : "primary"} disabled={busy} onClick={run}>
              {busy ? "Storing…" : started ? "Sync now" : "Turn on the stored sync"}
            </Button>
          )}
          {note && <p role="status" className="text-good">{note}</p>}
        </>
      )}
      <ErrorBox message={error} />
    </div>
  );
}

/** Send the browser to the provider's sign-in page ("Connect QuickBooks"); it comes back to returnTo. */
function useConnect(token: string, connector: string, returnTo: "app" | "start" = "app") {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      window.location.assign(await startConnect(token, connector, returnTo));
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't start the sign-in.");
      setBusy(false);
    }
  }
  return { go, busy, error };
}

/** QuickBooks / Xero: connect, quick look, open records, disconnect. Also used by Get Started (connectOnly). */
export function BusinessToolCard(props: {
  token: string;
  connector: DataConnection;
  canChange: boolean;
  onChanged: (connectors: DataConnection[]) => void;
  onOpenLive: (connector: string, dataset: LiveDataset) => void;
  /** Every connection spot is taken (the tool's free-plan limit): connecting isn't offered. */
  full?: boolean;
  /** Get Started: only connect (quick look and records come on the next step); no schedule, no disconnect. */
  connectOnly?: boolean;
  /** Where the provider sends the person back after sign-in. */
  returnTo?: "app" | "start";
  /** The plan includes Data Engineering: the stored sync can be turned on. */
  canStore?: boolean;
}) {
  const c = props.connector;
  const connected = c.status === "connected";
  const connect = useConnect(props.token, c.connector, props.returnTo);
  const [showLook, setShowLook] = useState(false);
  const [datasets, setDatasets] = useState<LiveDataset[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Records are listed only when asked: each check is a call to QuickBooks / Xero.
  async function listRecords() {
    setBusy(true);
    setError(null);
    try {
      setDatasets((await getLiveDatasets(props.token, c.connector)).datasets);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't read the records.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      props.onChanged((await disconnectIntegration(props.token, c.connector)).connectors);
      setDatasets(null);
      setShowLook(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't disconnect.");
    } finally {
      setBusy(false);
    }
  }

  const withRecords = (datasets ?? []).filter((d) => d.has_rows);
  const empty = (datasets ?? []).length - withRecords.length;

  return (
    <Card className="space-y-3">
      <Header c={c} />
      <p className="text-sm text-ink-muted">{c.about}</p>
      {connected ? (
        <>
          <p className="text-sm">
            Connected to <strong>{c.display_name}</strong>. Read-only — we never change your books.
          </p>
          {c.calls_budget !== null && (
            <p className="text-xs text-ink-muted">
              {c.name} calls today: {fmtInt(c.calls_today)} of {fmtInt(c.calls_budget)} (starts over at midnight UTC).
            </p>
          )}
          {!props.connectOnly && (
          <>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setShowLook(!showLook)}>
              {showLook ? "Hide quick look" : "Quick look"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={listRecords}>
              {datasets ? "Check records again" : "Open records in the workspace"}
            </Button>
          </div>
          {showLook && <QuickLook token={props.token} connector={c.connector} />}
          {datasets && (
            <div className="rounded-xl border border-line">
              <div className="border-b border-line bg-surface-2 px-3 py-1.5 text-xs text-ink-muted">
                {fmtInt(withRecords.length)} with records{empty > 0 && ` · ${fmtInt(empty)} empty, hidden`} · read live, nothing stored
              </div>
              {withRecords.length === 0 ? (
                <p className="px-3 py-2 text-xs text-ink-muted">No records yet, so there&apos;s nothing to analyze.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {withRecords.map((d) => (
                    <li key={d.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                      <span>{d.name}</span>
                      <Button variant="secondary" onClick={() => props.onOpenLive(c.connector, d)}>
                        Open in workspace
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <StoredSync token={props.token} connector={c} canStore={!!props.canStore} canChange={props.canChange}
            onChanged={props.onChanged} />
          {props.canChange && (
            <button type="button" className="text-sm text-bad hover:underline" disabled={busy} onClick={disconnect}>
              Disconnect (cancels our access)
            </button>
          )}
          </>
          )}
        </>
      ) : !c.available ? (
        <p className="text-xs text-ink-muted">
          Coming soon: connecting it opens {c.name}&apos;s own sign-in. You can choose the refresh schedule now.
        </p>
      ) : props.full ? (
        <p className="text-sm text-ink-muted">
          {c.name} connections are full right now. Upload an export from {c.name} as a file meanwhile — or book a
          consultation and we&apos;ll open a spot.
        </p>
      ) : props.canChange ? (
        <div className="space-y-1">
          <Button disabled={connect.busy} onClick={connect.go}>
            {connect.busy ? "Opening…" : `Connect ${c.name}`}
          </Button>
          <p className="text-xs text-ink-muted">
            You sign in on {c.name}&apos;s own page and allow read-only access. Free: the quick look stores nothing.
          </p>
        </div>
      ) : (
        <p className="text-sm text-ink-muted">Ask your team&apos;s owner to connect it.</p>
      )}
      {c.stores_data && !props.connectOnly && <DataRefresh token={props.token} canChange={props.canChange} connector={c.connector} />}
      <ErrorBox message={error ?? connect.error} />
    </Card>
  );
}

/**
 * The client's own Supabase / PostgreSQL database: connect, pick a project, see
 * its tables, open one, disconnect. Also used by Get Started ("Preview this table").
 */
export function SupabaseCard(props: {
  token: string;
  connector: DataConnection;
  canChange: boolean;
  onChanged: (connectors: DataConnection[]) => void;
  onOpenTable: (t: CloudTable) => void;
  /** The button on each table (default "Open in workspace"). */
  openLabel?: string;
  /** Where "Sign in with Supabase" sends the person back. */
  returnTo?: "app" | "start";
}) {
  const c = props.connector;
  const connected = c.status === "connected";
  const connect = useConnect(props.token, "supabase", props.returnTo);
  const [dsn, setDsn] = useState("");
  const [tables, setTables] = useState<CloudTable[] | null>(null);
  const [schemas, setSchemas] = useState<CloudSchema[]>([]);
  const [projects, setProjects] = useState<SupabaseProject[] | null>(null); // set = "pick a project"
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Read the tables when connected — and again after a project is picked
  // (display_name changes to "Supabase: <project>").
  useEffect(() => {
    if (!connected) return;
    getCloudTables(props.token)
      .then(async (r) => {
        if (r.needs_project) {
          setProjects((await getSupabaseProjects(props.token)).projects);
          return;
        }
        setProjects(null);
        setTables(r.tables);
        setSchemas(r.schemas);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't read your database."));
  }, [connected, c.display_name, props.token]);

  async function connectWithString(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await connectSupabase(props.token, dsn);
      setDsn("");
      setTables(r.tables);
      setSchemas(r.schemas);
      props.onChanged(r.connectors);
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't connect.");
    } finally {
      setBusy(false);
    }
  }

  async function pick(ref: string) {
    setBusy(true);
    setError(null);
    try {
      props.onChanged((await chooseSupabaseProject(props.token, ref)).connectors);
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't pick that project.");
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
      setProjects(null);
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
          {projects && (
            <div className="space-y-2">
              <p className="text-sm font-semibold">Which project should we read?</p>
              {projects.length === 0 && <p className="text-sm text-ink-muted">No projects found in that Supabase account.</p>}
              <div className="flex flex-wrap gap-2">
                {projects.map((p) => (
                  <Button key={p.ref} variant="secondary" disabled={busy || !props.canChange} onClick={() => pick(p.ref)}>
                    {p.name}
                    {p.region && <span className="ml-1 text-xs text-ink-muted">({p.region})</span>}
                  </Button>
                ))}
              </div>
            </div>
          )}
          {!projects && tables === null && !error && <p className="text-sm text-ink-muted">Reading your tables…</p>}
          {tables && tables.length === 0 && <p className="text-sm text-ink-muted">No tables found (check the user&apos;s permissions).</p>}
          {!projects && tables && tables.length > 0 && (
            <div className="max-h-96 space-y-3 overflow-y-auto">
              {schemas.map((s) => {
                // Only tables with data are listed; empty ones are counted, not shown.
                const withData = tables.filter((t) => t.schema === s.schema && t.has_rows !== false);
                const empty = s.tables - withData.length;
                return (
                  <div key={s.schema} className="rounded-xl border border-line">
                    <div className="flex items-center justify-between gap-2 border-b border-line bg-surface-2 px-3 py-1.5 text-xs">
                      <span className="font-mono font-semibold">{s.schema}</span>
                      <span className="text-ink-muted">
                        {fmtInt(withData.length)} with data
                        {empty > 0 && ` · ${fmtInt(empty)} empty, hidden`}
                      </span>
                    </div>
                    {withData.length === 0 ? (
                      <p className="px-3 py-2 text-xs text-ink-muted">Every table here is empty, so there&apos;s nothing to analyze yet.</p>
                    ) : (
                      <ul className="divide-y divide-line">
                        {withData.map((t) => (
                          <li key={t.name} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                            <span>
                              <span className="font-mono">{t.name}</span>
                              <span className="ml-2 text-xs text-ink-muted">
                                {t.kind}
                                {t.rows_estimate > 0 && ` · ~${fmtInt(t.rows_estimate)} rows`}
                              </span>
                            </span>
                            <Button variant="secondary" onClick={() => props.onOpenTable(t)}>
                              {props.openLabel ?? "Open in workspace"}
                            </Button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {props.canChange && (
            <button type="button" className="text-sm text-bad hover:underline" disabled={busy} onClick={disconnect}>
              Disconnect (deletes the saved login)
            </button>
          )}
        </>
      ) : props.canChange ? (
        <div className="space-y-3">
          {c.sign_in && (
            <div className="space-y-1">
              <Button disabled={connect.busy} onClick={connect.go}>
                {connect.busy ? "Opening…" : "Sign in with Supabase"}
              </Button>
              <p className="text-xs text-ink-muted">No password needed: you allow read-only access and pick a project.</p>
            </div>
          )}
          <form onSubmit={connectWithString} className="space-y-2">
            <label className="block space-y-1 text-sm">
              <span>{c.sign_in ? "Or paste a connection string (any PostgreSQL)" : "Connection string"}</span>
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
            <Button type="submit" variant={c.sign_in ? "secondary" : undefined} disabled={busy || dsn.trim().length < 12}>
              {busy ? "Checking…" : "Connect"}
            </Button>
          </form>
        </div>
      ) : (
        <p className="text-sm text-ink-muted">Ask your team&apos;s owner to connect it.</p>
      )}
      <ErrorBox message={error ?? connect.error} />
    </Card>
  );
}

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
// LESS ON ONE SCREEN: each section (Cloud storage, Business tools) folds away
// behind an arrow, with a one-line summary ("1 connected · 2 ready · 9 coming
// soon"); each tool's card folds too. A section or card starts OPEN only when
// something in it is connected (or the person just came back from its
// sign-in). A folded card doesn't call its tool at all (no table listing).
// Each tool has a plain lettered badge ("QB") — not the company's logo, which
// is a trademark used only under that company's brand rules.
//
// Server side: api/routers/connections.py, api/services/oauth.py,
// api/services/business_tools.py, api/services/cloud_sources.py.

"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  chooseSupabaseProject,
  connectSupabase,
  connectWaveToken,
  disconnectIntegration,
  getCloudTables,
  getConnections,
  getLiveDatasets,
  getSheetTabs,
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
import SummarizePanel from "./SummarizePanel";
import WarehouseCard from "./WarehouseCard";

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
  /** Open one tab of a Google Sheet in the workspace (live, never stored). */
  onOpenSheet?: (link: string, tab: string) => void;
  /** Open a file read live from a source (a warehouse table, or a summary that ran in the source). */
  onOpenFile?: (file: File, from: string, note: string | null) => void;
  /** After coming back from a provider's sign-in: what happened. */
  notice?: { ok: boolean; text: string } | null;
  /** Admins can connect their own Wave with a personal token (Wave's free plan). */
  isAdmin?: boolean;
}) {
  const [info, setInfo] = useState<ConnectionsInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Sections the person opened or closed (others follow startsOpen below).
  const [opened, setOpened] = useState<Partial<Record<DataConnection["kind"], boolean>>>({});

  useEffect(() => {
    getConnections(props.token)
      .then(setInfo)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load your integrations."));
  }, [props.token]);

  if (!info) return error ? <ErrorBox message={error} /> : <p className="text-sm text-ink-muted">Loading…</p>;
  const sections: DataConnection["kind"][] = ["cloud_storage", "business_tool"];
  const changed = (connectors: DataConnection[]) => setInfo({ ...info, connectors });
  // Open by default: a section with something connected (the person's own tools first).
  const startsOpen = (kind: DataConnection["kind"]) => info.connectors.some((c) => c.kind === kind && c.status === "connected");
  const isOpen = (kind: DataConnection["kind"]) => opened[kind] ?? startsOpen(kind);

  return (
    <div className="max-w-3xl space-y-8">
      <EntityReview token={props.token} canChange={props.canChange} />
      {props.notice && (
        <p role="status"
          className={`rounded-2xl border p-3 text-sm ${props.notice.ok ? "border-good/40 bg-good/10" : "border-bad/40 bg-bad/10"}`}>
          {props.notice.text}
        </p>
      )}
      {sections.map((kind) => {
        const tools = info.connectors.filter((c) => c.kind === kind);
        const connected = tools.filter((c) => c.status === "connected").length;
        const ready = tools.filter((c) => c.available && c.status !== "connected").length;
        const planned = (info.coming_soon ?? []).filter((p) => p.kind === kind).length;
        const open = isOpen(kind);
        return (
        <section key={kind} className="space-y-3">
          <button type="button" aria-expanded={open} onClick={() => setOpened((o) => ({ ...o, [kind]: !open }))}
            className="flex w-full items-center gap-3 rounded-2xl border border-line bg-surface px-4 py-3 text-left shadow-[var(--shadow-card)] hover:border-brand">
            <Chevron open={open} />
            <span className="min-w-0 flex-1">
              <span className="block text-lg font-semibold">{info.kinds[kind] ?? kind}</span>
              <span className="block text-xs text-ink-muted">
                {[connected && `${connected} connected`, ready && `${ready} ready to connect`, planned && `${planned} coming soon`]
                  .filter(Boolean).join(" · ") || "Nothing here yet"}
                {" · "}{tools.map((t) => t.name).join(", ")}
              </span>
            </span>
          </button>
          {open && (
          <div className="space-y-3 pl-1">
          <p className="text-sm text-ink-muted">
            {kind === "cloud_storage"
              ? "Your data already lives (and is managed) here. We read it where it is — nothing is copied — and you analyze it, build dashboards, and run ML and AI on it."
              : "Your business apps. Connect one for a free live quick look and to open its records in the workspace — read when you ask, nothing stored. Storing it on a schedule (for history and trends) comes with Data Engineering."}
          </p>
          {info.connectors
            .filter((c) => c.kind === kind)
            .map((c) =>
              ["snowflake", "bigquery", "salesforce"].includes(c.connector) ? (
                <WarehouseCard key={c.connector} token={props.token} connector={c} canChange={props.canChange}
                  onChanged={changed} onOpenFile={props.onOpenFile} />
              ) : c.connector === "google_sheets" ? (
                <GoogleSheetsCard key={c.connector} token={props.token} connector={c} canChange={props.canChange}
                  onChanged={changed} onOpenSheet={props.onOpenSheet} />
              ) : c.connector === "supabase" ? (
                <SupabaseCard key={c.connector} token={props.token} connector={c} canChange={props.canChange}
                  onChanged={changed} onOpenTable={props.onOpenTable} onOpenFile={props.onOpenFile} collapsible
                  defaultOpen={c.status === "connected"} />
              ) : (
                <BusinessToolCard key={c.connector} token={props.token} connector={c} canChange={props.canChange}
                  onChanged={changed} onOpenLive={props.onOpenLive} canStore={info.can_connect} isAdmin={props.isAdmin}
                  full={!!info.slots?.[c.connector] && info.slots[c.connector].used >= info.slots[c.connector].max}
                  collapsible defaultOpen={c.status === "connected"} />
              ),
            )}
          <Planned items={(info.coming_soon ?? []).filter((p) => p.kind === kind)} />
          </div>
          )}
        </section>
        );
      })}
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

/** The arrow beside a heading: points right when folded, down when open. */
function Chevron({ open }: { open: boolean }) {
  return (
    <span aria-hidden className={`inline-block text-ink-muted transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
  );
}

// A plain lettered badge per tool (not the company's logo: logos are trademarks,
// used only under each company's brand guidelines), in the tool's own primary
// color (--color-tool-* in app/globals.css) — the name too.
const BADGES: Record<string, string> = { supabase: "DB", quickbooks: "QB", xero: "XO", wave: "WV" };
const toolColor = (connector: string) => (connector in BADGES ? `var(--color-tool-${connector})` : undefined);

function Header({ c, collapsible, open, onToggle }: {
  c: DataConnection;
  collapsible?: boolean;
  open?: boolean;
  onToggle?: () => void;
}) {
  const st = STATUS[c.status] ?? STATUS.not_set_up;
  const inner = (
    <>
      <span className="flex min-w-0 items-center gap-2">
        {collapsible && <Chevron open={!!open} />}
        <span aria-hidden className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-surface-2 font-mono text-xs font-semibold text-ink-muted"
          style={toolColor(c.connector) ? {
            color: toolColor(c.connector),
            // A soft tint of the tool's color behind its letters (the theme's surface shows through).
            backgroundColor: `color-mix(in srgb, ${toolColor(c.connector)} 14%, transparent)`,
          } : undefined}>
          {BADGES[c.connector] ?? c.name.slice(0, 2).toUpperCase()}
        </span>
        <h3 className="font-semibold" style={toolColor(c.connector) ? { color: toolColor(c.connector) } : undefined}>
          {c.name}
          {!c.available && c.status !== "connected" && <span className="ml-2 text-xs font-normal text-ink-muted">coming soon</span>}
        </h3>
      </span>
      <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${st.cls}`}>{st.label}</span>
    </>
  );
  return collapsible ? (
    <button type="button" aria-expanded={!!open} onClick={onToggle}
      className="flex w-full flex-wrap items-center justify-between gap-2 text-left">
      {inner}
    </button>
  ) : (
    <div className="flex flex-wrap items-center justify-between gap-2">{inner}</div>
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

/**
 * Admins only: connect your OWN Wave business with a personal "full access
 * token". Wave's normal sign-in needs Wave Pro; personal tokens work on the
 * free plan but Wave allows them only for personal use and testing — so
 * clients never see this. POST /api/connections/wave/token.
 */
function WaveTokenForm(props: { token: string; onChanged: (connectors: DataConnection[]) => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      props.onChanged((await connectWaveToken(props.token, value.trim())).connectors);
      setValue("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Wave didn't accept that token.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-2 rounded-xl border border-dashed border-line p-3 text-sm">
      <p className="font-semibold">Admins: use your own Wave with a personal token</p>
      <p className="text-xs text-ink-muted">
        For testing with your own business on Wave&apos;s free plan (the Connect button needs Wave Pro). In
        developer.waveapps.com → your application → create a full access token, and paste it here. It&apos;s saved
        encrypted; delete it in Wave to stop it.
      </p>
      <input type="password" autoComplete="off" aria-label="Wave full access token" value={value}
        onChange={(e) => setValue(e.target.value)} placeholder="Wave full access token"
        className="w-full rounded-xl border border-line bg-surface px-3 py-2 font-mono text-xs" />
      <Button type="submit" variant="secondary" disabled={busy || value.trim().length < 10}>
        {busy ? "Checking…" : "Connect with this token"}
      </Button>
      <ErrorBox message={error} />
    </form>
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

/**
 * Google Sheets: connect a Google account (read-only), paste a sheet's link,
 * pick a tab and open it in the workspace — always the latest rows, nothing stored.
 */
function GoogleSheetsCard(props: {
  token: string;
  connector: DataConnection;
  canChange: boolean;
  onChanged: (connectors: DataConnection[]) => void;
  onOpenSheet?: (link: string, tab: string) => void;
}) {
  const c = props.connector;
  const connected = c.status === "connected";
  const connect = useConnect(props.token, c.connector);
  const [link, setLink] = useState("");
  const [sheet, setSheet] = useState<{ title: string; tabs: { title: string; rows: number }[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function showTabs(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSheet(await getSheetTabs(props.token, link));
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't open that sheet.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      props.onChanged((await disconnectIntegration(props.token, c.connector)).connectors);
      setSheet(null);
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
      {!connected ? (
        c.sign_in ? (
          <Button disabled={!props.canChange || connect.busy} onClick={connect.go}>
            {connect.busy ? "Opening Google…" : "Connect Google Sheets"}
          </Button>
        ) : (
          <p className="text-sm text-ink-muted">Coming soon.</p>
        )
      ) : (
        <>
          <p className="text-sm">
            Connected: <strong>{c.display_name}</strong>. Read-only — we never change your sheets.
          </p>
          <form onSubmit={showTabs} className="flex flex-wrap gap-2">
            <input className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-3 py-2 text-sm" value={link}
              onChange={(e) => setLink(e.target.value)} placeholder="Paste your sheet's link (docs.google.com/spreadsheets/d/…)"
              aria-label="Google Sheet link" />
            <Button type="submit" variant="secondary" disabled={busy || !link.trim()}>Show tabs</Button>
          </form>
          {sheet && (
            <ul className="space-y-2">
              {sheet.tabs.map((t) => (
                <li key={t.title} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line px-3 py-2 text-sm">
                  <span>
                    <strong>{sheet.title}</strong> › {t.title}
                  </span>
                  <Button variant="secondary" disabled={!props.onOpenSheet} onClick={() => props.onOpenSheet?.(link, t.title)}>
                    Open in workspace
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {props.canChange && (
            <Button variant="secondary" disabled={busy} onClick={disconnect}>Disconnect</Button>
          )}
        </>
      )}
      <ErrorBox message={error ?? connect.error} />
    </Card>
  );
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
  /** An admin: Wave offers "use a personal token" (for your own business, Wave's free plan). */
  isAdmin?: boolean;
  /** Settings -> Integrations: the card folds behind its header (Get Started shows it open). */
  collapsible?: boolean;
  defaultOpen?: boolean;
}) {
  const c = props.connector;
  const connected = c.status === "connected";
  const [open, setOpen] = useState(!props.collapsible || !!props.defaultOpen);
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
      <Header c={c} collapsible={props.collapsible} open={open} onToggle={() => setOpen(!open)} />
      {open && (
      <>
      <p className="text-sm text-ink-muted">{c.about}</p>
      {c.connector === "xero" && (
        // Said up front, before connecting (also in Get Started): Xero's developer terms rule out AI and
        // machine learning on data from its API, so those tools are off for it (api/services/xero_guard.py).
        <p role="note" className="rounded-xl border border-brand/40 bg-brand-soft px-3 py-2 text-sm">
          <strong>Good to know:</strong> Xero&apos;s terms don&apos;t allow data from Xero to be used with AI or
          machine learning, so Beacon (AI questions) and the Data Science &amp; Machine Learning tools are off for
          your Xero data. Cleaning, exploring and dashboards work as usual.
        </p>
      )}
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
      {c.connector === "wave" && !connected && props.isAdmin && !props.connectOnly && (
        <WaveTokenForm token={props.token} onChanged={props.onChanged} />
      )}
      {c.stores_data && !props.connectOnly && <DataRefresh token={props.token} canChange={props.canChange} connector={c.connector} />}
      <ErrorBox message={error ?? connect.error} />
      </>
      )}
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
  onOpenFile?: (file: File, from: string, note: string | null) => void;
  /** The button on each table (default "Open in workspace"). */
  openLabel?: string;
  /** Where "Sign in with Supabase" sends the person back. */
  returnTo?: "app" | "start";
  /** Settings -> Integrations: the card folds behind its header (Get Started shows it open). */
  collapsible?: boolean;
  defaultOpen?: boolean;
}) {
  const [summarizing, setSummarizing] = useState<string | null>(null);   // the table whose Summarize form shows
  const c = props.connector;
  const connected = c.status === "connected";
  const [open, setOpen] = useState(!props.collapsible || !!props.defaultOpen);
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
    if (!connected || !open) return;   // folded: no call to the database
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
  }, [connected, open, c.display_name, props.token]);

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
      <Header c={c} collapsible={props.collapsible} open={open} onToggle={() => setOpen(!open)} />
      {open && (
      <>
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
                          <li key={t.name} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2 text-sm">
                            <span>
                              <span className="font-mono">{t.name}</span>
                              <span className="ml-2 text-xs text-ink-muted">
                                {t.kind}
                                {t.rows_estimate > 0 && ` · ~${fmtInt(t.rows_estimate)} rows`}
                              </span>
                            </span>
                            <span className="flex gap-2">
                              {props.onOpenFile && (
                                // Do the math in their database; only the answer comes back.
                                <Button variant="secondary" onClick={() => setSummarizing(summarizing === `${t.schema}.${t.name}` ? null : `${t.schema}.${t.name}`)}>
                                  Summarize
                                </Button>
                              )}
                              <Button variant="secondary" onClick={() => props.onOpenTable(t)}>
                                {props.openLabel ?? "Open in workspace"}
                              </Button>
                            </span>
                            {props.onOpenFile && summarizing === `${t.schema}.${t.name}` && (
                              <SummarizePanel token={props.token} connector="supabase" table={`${t.schema}.${t.name}`}
                                sourceName="your database" onOpenFile={props.onOpenFile} />
                            )}
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
      </>
      )}
    </Card>
  );
}

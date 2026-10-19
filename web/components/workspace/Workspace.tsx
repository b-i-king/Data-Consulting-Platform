// The signed-in workspace ("/workspace"), laid out like Copilot Money on the web:
// a left pane of high-level options, and the chosen one on the right.
//
// ADDRESSES: each page has its own — /workspace/dashboard,
// /workspace/data-cleaning, /workspace/settings/billing ... (the map is
// lib/routes.ts). Changing pages changes the address WITHOUT leaving this
// page (open data stays in memory), so Back / Forward, bookmarks and refresh
// all work. Plain /workspace opens the first page in the left pane.
//
// LEFT PANE — the services in a fixed ORDER (a hierarchy, top to bottom):
//     📊 Dashboard                 first when there is data already
//     🧹 Data Cleaning             choose / add data here (first when there's none yet)
//     Data Engineering             tabs: ER diagram · Data dictionary · Load into database
//     Data Strategy & Discovery
//     Data Exploration & Analysis
//     Data Science & ML
//     AI                           at the bottom
//   Services not on the plan stay in their place, GRAYED OUT, with an ⓘ that
//   explains what they add and how to get them. On phones the pane becomes a
//   row you can swipe sideways.
//   At the BOTTOM LEFT: ⚙️ Settings (with your name). It opens the ACCOUNT AREA,
//   whose own left pane has: Account · Plan & billing · Integrations ·
//   Consultant (consultants only) · Administration (admins only) — and
//   "← Back to workspace". Sign out is on the Account page.
//
// DATA — chosen only on the DATA CLEANING page:
//   * Choose files (CSV or Excel best) — kept in this browser tab's memory;
//   * the data stored for this account (managed storage) — opened as the
//     original table; the most recent upload opens BY ITSELF on each visit;
//   * a table from the client's own CLOUD database (Settings -> Integrations
//     -> Supabase database) — read where it lives, never stored. For cloud
//     data the cleaning page is a REPORT ONLY (what's missing, what looks off
//     — fix it at the source) and "Load into database" is switched off.
//   Every other page without data open sends you to Data Cleaning.
//   "ADD ANOTHER SOURCE" (AddSourceDialog.tsx, next to "Change data") opens a
//   second source NEXT TO the open data — a file, stored data, a cloud table
//   or QuickBooks / Xero records — combined in this tab, never stored. The
//   stricter rule wins: any cloud or live source makes it all report-only,
//   with storing off. Same-column tables are stacked; the others become tabs,
//   with how they relate (shared IDs, or names matched across sources).
//
// Each service page has its skills as TABS at the top (like Streamlit) and
// opens on its primary view: Data Cleaning = the cleaning report (+ download);
// Dashboard = DashboardTabs (Overview, the business context, the client's own
// tabs); Data Engineering = ER diagram first, "Load into database" last;
// everything else = its primary skill, run automatically when it needs no options.
//
// CONSULTANT MODE (lavender): a "Working on" picker at the top of the left pane
// (ClientPicker.tsx). Picking a client shows that client's plan and tools;
// every signed-in request then carries X-Acting-As, and the server checks the
// client is theirs (admins may open any client). Working on a client also
// shows the Consultations card (ConsultationPanel.tsx).
//
// Plan comes from GET /api/accounts/services (api/services/entitlements.py).
// Tools unlock only through a package: "Add to my package" opens Settings ->
// Plan & billing with that tool ticked. Downloads ask first, with the
// estimated wait and charge (DownloadConfirm.tsx). Coming back from Stripe
// (?checkout=success) opens Plan & billing with a thank-you note.

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import AddSourceDialog, { type AddedSource, type SourceKind } from "./AddSourceDialog";
import {
  analyzeFiles,
  downloadCleaned,
  getCloudTableFile,
  getCloudTables,
  getLiveDatasetFile,
  getConsultantSettings,
  getIntakeOptions,
  getMe,
  getMyClients,
  getMyServices,
  getSkills,
  getStoredDataFile,
  getWarehouseSummary,
  logQuestion,
  setActingAs,
  signOutServer,
} from "@/lib/api";
import { categoryColor, categoryEmoji } from "@/lib/categories";
import { FALLBACK_BOOKING_URL, fmtInt, fmtUsd, SITE_NAME, toolName } from "@/lib/format";
import { HOME, setAddress, SIGN_IN, SIGN_UP, workspacePath, workspaceViewFromPath, type SettingsSection } from "@/lib/routes";
import { clearSession, loadSession, saveSession, type Session } from "@/lib/session";
import type {
  AnalyzeResponse,
  CleaningInput,
  CloudTable,
  LiveDataset,
  ColumnAction,
  ColumnActions,
  ColumnNames,
  ConsultantClient,
  ConsultantLoad,
  IntakeOptions,
  MyServices,
  Service,
  SkillInfo,
  SkillResult,
  TransformStep,
  Transforms,
  WarehouseCounts,
} from "@/lib/types";
import ClientPicker, { AddClientForm } from "./ClientPicker";
import ConsultationPanel from "./ConsultationPanel";
import AdminPanel from "./AdminPanel";
import AccountPanel from "./AccountPanel";
import IntegrationsPanel from "./IntegrationsPanel";
import { ConsultantSetup } from "./SettingsPanel";
import Tour, { TourButton, type TourStep } from "@/components/ui/Tour";
import WarehouseLoad from "@/components/explore/WarehouseLoad";
import BillingPanel from "@/components/billing/BillingPanel";
import ConsultantHelp from "@/components/billing/ConsultantHelp";
import DashboardTabs from "@/components/explore/DashboardTabs";
import AssistantPanel from "@/components/explore/AssistantPanel";
import CompareOverTime from "@/components/explore/CompareOverTime";
import { priceLabel } from "@/components/explore/LockedService";
import SkillsPanel from "@/components/explore/SkillsPanel";
import DownloadConfirm, { estimateLine, type DownloadFormat } from "@/components/wizard/DownloadConfirm";
import { GroupDetails } from "@/components/wizard/StepResults";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

// The first-visit guide for the workspace (components/ui/Tour.tsx).
const WORKSPACE_TOUR: TourStep[] = [
  { target: "ws-nav", title: "Your tools, in order",
    body: "Dashboard first, then Data Cleaning, Data Engineering, analysis, ML and AI. Grayed-out ones show what they add and how to get them." },
  { target: "ws-nav", title: "Your data lives in Data Cleaning",
    body: "Choose files, open the data stored for you, or a table from your own database. Every other page uses what you open there." },
  { target: "ws-nav", title: "Build your dashboard",
    body: "Dashboard opens on an Overview built from your data. Add your own tabs with the questions your business asks — we suggest them for your industry." },
  { target: "ws-settings", title: "Settings, bottom left",
    body: "Your account and password, plan & billing, integrations (QuickBooks, your Supabase database), and — for consultants — your Cal.com links and capacity." },
];

const DASHBOARD = "data_visualization";
const CLEANING = "data_cleaning";
const ENGINEERING = "data_engineering";
const AI = "ai";
// The hierarchy, top to bottom (Dashboard and Data Cleaning swap when there's no data yet).
const ORDER = [DASHBOARD, CLEANING, ENGINEERING, "data_strategy_discovery", "data_exploration_analysis", "data_science_ml", AI];
// Data Engineering's tabs: diagram and dictionary first, the load last.
const ENGINEERING_TABS = ["er-diagram", "data-dictionary-creator"];
const VIEW = "__view__"; // tab value for the built-in view (cleaning report / dashboard / database load)
// The skill each built-in view already shows, so it isn't listed twice in the tabs
// (Data Engineering's load view isn't a skill, so nothing is hidden for it).
const VIEW_SKILL: Record<string, { skill: string; label: string }> = {
  [CLEANING]: { skill: "data-cleaning", label: "Cleaning report" },
  [DASHBOARD]: { skill: "build-dashboard", label: "Dashboard" },
  [ENGINEERING]: { skill: "", label: "Load into database" },
};

/** A service as the plan sends it: the catalog entry plus whether it's on the plan. */
type PlanService = MyServices["services"][number];
/** The account area's sections (Settings, bottom left). */
type AccountTab = SettingsSection; // "account" | "billing" | "integrations" | "consultant" | "admin"
/** The browser tab's title for each Settings section. */
const SETTINGS_TITLES: Record<AccountTab, string> = {
  account: "Account", billing: "Plan & billing", integrations: "Integrations", consultant: "Consultant", admin: "Administration",
};
/** Where the open data came from: picked files, managed storage, or the client's own cloud database. */
type Origin = "file" | "stored" | "cloud";

/** The name shown in the left pane: the visualization service IS the dashboard. */
const navName = (s: Service) => (s.id === DASHBOARD ? "Dashboard" : s.name);

/** Plain-English "how do I get this?" for a locked service. */
function howToGet(s: Service): string {
  if (s.id === CLEANING) return "Included with every account.";
  if (s.tool_monthly_usd != null) {
    const needs = s.tool_requires.length ? " Needs Data Engineering in your package." : "";
    const soon = s.available ? "" : " Free until it launches.";
    return `Add it to your package: ${fmtUsd(s.tool_monthly_usd)}/month or ${fmtUsd(s.tool_annual_usd ?? 0)}/year, cancel anytime.${needs}${soon} Rather have it done for you? ${s.consultation.text}.`;
  }
  if (s.pricing_model === "hourly") return `${s.consultation.text} — book and pay for a session on the booking page.`;
  return `${s.consultation.text}.`;
}

function ServiceInfo({ service, small = false }: { service: Service; small?: boolean }) {
  return (
    <div className={`space-y-2 ${small ? "text-xs" : "text-sm"}`}>
      <p className="text-ink-muted">{service.description}</p>
      {service.includes.length > 0 && (
        <ul className="list-inside list-disc space-y-0.5">
          {service.includes.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      )}
      <p>
        <span className="font-semibold">{priceLabel(service)}</span> · {howToGet(service)}
      </p>
    </div>
  );
}

export default function Workspace() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null | undefined>(undefined); // undefined = still checking
  const [plan, setPlan] = useState<MyServices | null>(null);
  const [bookingUrl, setBookingUrl] = useState(FALLBACK_BOOKING_URL);
  const [section, setSection] = useState<string | null>(null);
  const [accountTab, setAccountTab] = useState<AccountTab | null>(null); // null = the workspace itself
  const [infoOpen, setInfoOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The address bar: read once on arrival (urlReady), then kept in step with the
  // page on screen. urlKey is the page the address last showed (see the effect below).
  const [urlReady, setUrlReady] = useState(false);
  const urlKey = useRef<string | null>(null);

  // Data for this visit (never stored on the server unless the client stores it).
  const [files, setFiles] = useState<File[]>([]);
  const [origin, setOrigin] = useState<Origin>("file");
  // What each open file is (by file name): its kind and a label for the header.
  const [sourceInfo, setSourceInfo] = useState<Record<string, { kind: SourceKind; label: string }>>({});
  const [adding, setAdding] = useState(false); // the "Add another source" dialog
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [columnActions, setColumnActions] = useState<ColumnActions>({});
  const [columnNames, setColumnNames] = useState<ColumnNames>({});
  const [transforms, setTransforms] = useState<Transforms>({});
  const [groupIndex, setGroupIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  // What this client has in managed storage, and their cloud tables (if connected).
  const [stored, setStored] = useState<WarehouseCounts | null>(null);
  const [cloudTables, setCloudTables] = useState<CloudTable[] | null>(null); // only tables with rows
  const [cloudNote, setCloudNote] = useState<string | null>(null); // "the first 100,000 rows" when cut short
  // Get Started's cloud path ends with "Open your workspace" on the table it previewed (?open_table=).
  const pendingTable = useRef<CloudTable | null>(null);
  const [cloudFrom, setCloudFrom] = useState("From your database"); // or "Live from QuickBooks"
  // Back from a provider's sign-in (QuickBooks, Xero, Supabase): what happened.
  const [integrationNotice, setIntegrationNotice] = useState<{ ok: boolean; text: string } | null>(null);

  // Consultant mode: my clients, the one I'm working on (null = my own workspace).
  const [options, setOptions] = useState<IntakeOptions | null>(null);
  const [clients, setClients] = useState<ConsultantClient[]>([]);
  const [myLoad, setMyLoad] = useState<ConsultantLoad | null>(null); // how full I am (consultants)
  const [setupNeeded, setSetupNeeded] = useState(false); // a new consultant without booking links yet
  const [actingId, setActingId] = useState<string | null>(null);
  const [addingClient, setAddingClient] = useState(false);
  const [returnNote, setReturnNote] = useState<string | null>(null); // after paying at Stripe

  // Skill tabs: every skill, the tab chosen per service, and results kept for this visit.
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, SkillResult>>({});

  useEffect(() => {
    // The page in the address (/workspace/data-engineering, /workspace/settings/billing).
    const fromUrl = workspaceViewFromPath(window.location.pathname);
    const s = loadSession();
    // Consultant / Administration only for consultants / admins; others get Account.
    const allowed =
      (fromUrl.settings === "consultant" && s?.account.role !== "consultant") || (fromUrl.settings === "admin" && !s?.account.is_admin)
        ? "account"
        : fromUrl.settings;
    setSection(fromUrl.service);
    setAccountTab(allowed);
    setUrlReady(true);
    setSession(s);
    setActingAs(null); // a fresh visit starts in your own workspace
    if (!s) return;
    // Consultants: my clients and how full I am.
    const loadClients = (token: string) => {
      getMyClients(token)
        .then((r) => {
          setClients(r.clients);
          setMyLoad(r.load);
        })
        .catch(() => {});
      // New consultant? Nudge them to add their Cal.com links and webhook.
      getConsultantSettings(token)
        .then((c) => setSetupNeeded(c.setup_needed))
        .catch(() => {});
    };
    // Re-read the account: an approval (or a new consultant) shows up at once.
    getMe(s.token)
      .then((account) => {
        const fresh = { ...s, account };
        saveSession(fresh);
        setSession(fresh);
        if (account.role === "consultant") loadClients(s.token);
      })
      .catch(() => {
        if (s.account.role === "consultant") loadClients(s.token);
      });
    getMyServices(s.token)
      .then(setPlan)
      .catch((e) => {
        if (e.status === 401) {
          // Expired sign-in, or an account the (database-less) test server forgot:
          // sign out so the header and this page offer sign-in / create account again.
          clearSession();
          setSession(null);
        }
        setError(e.message);
      });
    getSkills()
      .then(setSkills)
      .catch(() => {});
    // Back from Stripe: say what happened (the webhook may take a few seconds).
    const q = new URLSearchParams(window.location.search);
    if (q.get("checkout") === "success" || q.get("consulting") === "success") {
      setReturnNote("Thank you! Your payment went through — it can take a few seconds to show here.");
      setAccountTab("billing");
    } else if (q.get("checkout") === "canceled" || q.get("consulting") === "canceled") {
      setReturnNote("Payment canceled — nothing was charged.");
      setAccountTab("billing");
    }
    if (q.get("checkout") || q.get("consulting")) window.history.replaceState(null, "", window.location.pathname); // a refresh won't show it again
    // From Get Started's last step: open Integrations (business apps), or the
    // cloud table that was previewed (opened once the session is ready, below).
    if (q.get("show") === "integrations") setAccountTab("integrations");
    const table = q.get("open_table");
    if (table && table.includes(".")) {
      const [schema, ...rest] = table.split(".");
      pendingTable.current = { schema, name: rest.join("."), kind: "table", rows_estimate: 0, has_rows: true };
    }
    if (q.get("show") || table) window.history.replaceState(null, "", window.location.pathname);
    // Back from "Connect QuickBooks / Xero / Supabase" (api/routers/connections.py oauth_callback).
    const integration = q.get("integration");
    if (integration) {
      const name = toolName(integration);
      setIntegrationNotice(
        q.get("connected") === "1"
          ? { ok: true, text: `${name} is connected. Nothing is stored — it's read live when you ask.` }
          : { ok: false, text: q.get("integration_error") ?? `${name} wasn't connected.` },
      );
      setAccountTab("integrations");
      window.history.replaceState(null, "", window.location.pathname); // a refresh won't show it again
    }
    getIntakeOptions()
      .then((o) => {
        setBookingUrl(o.booking_url);
        setOptions(o);
      })
      .catch(() => {});
  }, []);

  function clearData() {
    setFiles([]);
    setSourceInfo({});
    setAnalysis(null);
    setColumnActions({});
    setColumnNames({});
    setTransforms({});
    setResults({});
  }

  // Consultant: switch to another client (or back to my own workspace). Their
  // data is separate, so the files and results from the last client are cleared.
  async function chooseClient(id: string | null) {
    if (!session) return;
    setActingAs(id);
    setActingId(id);
    setAddingClient(false);
    clearData();
    setSection(null);
    setAccountTab(null);
    setError(null);
    try {
      setPlan(await getMyServices(session.token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't open that client.");
    }
  }

  async function reloadPlan() {
    if (session) setPlan(await getMyServices(session.token));
  }

  // Whenever the workspace's account changes (sign-in, or a consultant switching
  // client): what's in managed storage, and the cloud tables if a database is connected.
  const planAccountId = plan?.account.id;
  useEffect(() => {
    if (!session || !planAccountId) return;
    getWarehouseSummary(session.token)
      .then((s) => setStored(s.warehouse))
      .catch(() => setStored(null));
    getCloudTables(session.token)
      // Empty tables are left out: there's nothing in them to clean or analyze.
      .then((r) => setCloudTables(r.tables.filter((t) => t.has_rows !== false)))
      .catch(() => setCloudTables(null)); // 404 = no database connected
  }, [session, planAccountId]);

  async function clean(chosen: File[], from: Origin, info?: Record<string, { kind: SourceKind; label: string }>) {
    setResults({}); // new data: earlier skill results no longer apply
    setBusy(true);
    setError(null);
    try {
      setFiles(chosen);
      setOrigin(from);
      setSourceInfo(info ?? Object.fromEntries(chosen.map((f) => [f.name, { kind: from, label: f.name }])));
      setColumnActions({});
      setColumnNames({});
      setTransforms({});
      setGroupIndex(0);
      setAnalysis(await analyzeFiles({ files: chosen, strategy: "keep", columnActions: {}, columnNames: {}, transforms: {} }));
    } catch (e) {
      setAnalysis(null);
      setError(e instanceof Error ? e.message : "We couldn't read that data.");
    } finally {
      setBusy(false);
    }
  }

  // "Add another source": the new source joins the open files; the stricter rule wins.
  function addSources(added: AddedSource[]) {
    const names = new Set(files.map((f) => f.name));
    const renamed = added.map((a) => {
      // Two sources with the same file name would collide: "sales.csv" -> "sales (2).csv".
      let name = a.file.name;
      for (let i = 2; names.has(name); i++) name = a.file.name.replace(/(\.[^.]*)?$/, (ext) => ` (${i})${ext}`);
      names.add(name);
      return { ...a, file: name === a.file.name ? a.file : new File([a.file], name, { type: a.file.type }) };
    });
    const info = { ...sourceInfo, ...Object.fromEntries(renamed.map((a) => [a.file.name, { kind: a.kind, label: a.label }])) };
    const kinds = Object.values(info).map((i) => i.kind);
    const merged: Origin = kinds.includes("cloud") ? "cloud" : kinds.includes("stored") ? "stored" : "file";
    if (merged === "cloud") setCloudFrom("Combined sources (read live, not stored)");
    setCloudNote(null);
    setAdding(false);
    void clean([...files, ...renamed.map((a) => a.file)], merged, info);
  }

  // "Use my stored data": the stored table comes back as a CSV file and is
  // opened exactly like a picked file.
  async function openStored(source: string) {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      await clean([await getStoredDataFile(session.token, source)], "stored");
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't open the stored data.");
      setBusy(false);
    }
  }

  // A table from the client's own database: read-only, never stored.
  async function openCloud(t: CloudTable) {
    if (!session) return;
    setAccountTab(null);
    setSection(CLEANING);
    setBusy(true);
    setError(null);
    try {
      const got = await getCloudTableFile(session.token, t.schema, t.name);
      setCloudNote(got.truncated ? `the first ${fmtInt(got.rows)} rows (the table has more)` : null);
      setCloudFrom("From your database");
      await clean([got.file], "cloud");
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't read that table.");
      setBusy(false);
    }
  }

  // QuickBooks / Xero records, read live (never stored) — opened like cloud data:
  // analysis, dashboards, ML and AI; storing is off for it.
  async function openLive(connector: string, d: LiveDataset) {
    if (!session) return;
    setAccountTab(null);
    setSection(CLEANING);
    setBusy(true);
    setError(null);
    try {
      const got = await getLiveDatasetFile(session.token, connector, d.id);
      setCloudNote(got.truncated ? `the first ${fmtInt(got.rows)} records (there are more)` : null);
      setCloudFrom(`Live from ${toolName(connector)}`);
      await clean([got.file], "cloud");
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't read those records.");
      setBusy(false);
    }
  }

  // Open the most recent stored upload automatically, once per account per visit.
  const autoOpened = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!stored || !planAccountId || analysis || files.length) return;
    const source = stored.latest_upload ?? stored.sources[0]?.source_name;
    if (!source || autoOpened.current.has(planAccountId)) return;
    autoOpened.current.add(planAccountId);
    void Promise.resolve().then(() => openStored(source));
    // Runs when the stored-data summary arrives; openStored itself is recreated every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stored, planAccountId]);

  // Open the table Get Started previewed (instead of the latest stored upload).
  useEffect(() => {
    if (!session || !planAccountId || !pendingTable.current) return;
    const t = pendingTable.current;
    pendingTable.current = null;
    autoOpened.current.add(planAccountId);
    void Promise.resolve().then(() => openCloud(t));
    // openCloud is recreated every render; this runs once the account is known.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, planAccountId]);

  // The left pane, in the hierarchy's order. With data (open, stored or in the
  // cloud) the Dashboard leads; without any, Data Cleaning leads.
  const hasData = !!analysis || (stored?.rows ?? 0) > 0 || (cloudTables?.length ?? 0) > 0;
  const ordered = useMemo(() => {
    if (!plan) return [] as PlanService[];
    const order = hasData ? ORDER : [CLEANING, DASHBOARD, ...ORDER.slice(2)];
    const byId = Object.fromEntries(plan.services.map((s) => [s.id, s]));
    const known = order.map((id) => byId[id]).filter(Boolean);
    return [...known, ...plan.services.filter((s) => !order.includes(s.id))];
  }, [plan, hasData]);
  // A service the plan doesn't list (e.g. a mistyped /workspace/... address)
  // opens the first page instead; the address is corrected below.
  const sectionKnown = !plan || !section || plan.services.some((s) => s.id === section);
  const current = (sectionKnown ? section : null) ?? ordered.find((s) => s.active)?.id ?? null;
  const currentService = accountTab ? undefined : plan?.services.find((s) => s.id === current);

  // ── The address bar follows the page on screen ──
  // A page someone CHOSE (section / accountTab changed) adds a history entry,
  // so Back returns to the last page. A page picked automatically (the first
  // in the left pane, which moves when data arrives) only replaces the address.
  useEffect(() => {
    if (!urlReady) return;
    const key = `${section}|${accountTab}`;
    const chosen = urlKey.current !== null && key !== urlKey.current;
    urlKey.current = key;
    setAddress(workspacePath({ service: current, settings: accountTab }), !chosen);
  }, [urlReady, section, accountTab, current]);

  // The browser's Back / Forward: open the page in the address.
  useEffect(() => {
    const onPop = () => {
      const view = workspaceViewFromPath(window.location.pathname);
      urlKey.current = `${view.service}|${view.settings}`; // already in the address: nothing to add
      setSection(view.service);
      setAccountTab(view.settings);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // The browser tab's title names the page ("Data Cleaning · Workspace · ...").
  useEffect(() => {
    const page = accountTab ? SETTINGS_TITLES[accountTab] : currentService ? navName(currentService) : null;
    document.title = `${page ? `${page} · ` : ""}Workspace · ${SITE_NAME}`;
  }, [accountTab, currentService]);

  // Files from the computer live only in this page's memory: ask before a
  // refresh or a closed tab throws them away. (Stored and cloud data reopen.)
  useEffect(() => {
    if (origin !== "file" || !files.length) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [origin, files.length]);

  const input: CleaningInput = useMemo(
    () => ({ files, strategy: "keep", columnActions, columnNames, transforms }),
    [files, columnActions, columnNames, transforms],
  );
  const group = analysis?.groups[groupIndex];

  // The current service's skill tabs: its built-in view and its skills.
  const menu = useMemo(() => {
    if (!current) return [] as { value: string; label: string }[];
    const view = VIEW_SKILL[current];
    const list = skills.filter((k) => k.category === current && k.id !== view?.skill);
    // Primary skill first, then catalog order; Data Engineering: diagram, dictionary, the rest.
    list.sort((a, b) =>
      current === ENGINEERING
        ? (ENGINEERING_TABS.indexOf(a.id) + 1 || 99) - (ENGINEERING_TABS.indexOf(b.id) + 1 || 99)
        : Number(b.primary) - Number(a.primary),
    );
    const tabs = list.map((k) => ({ value: k.id, label: k.label }));
    if (!view) return tabs;
    return current === ENGINEERING ? [...tabs, { value: VIEW, label: view.label }] : [{ value: VIEW, label: view.label }, ...tabs];
  }, [skills, current]);
  const choice = (current && picked[current]) || menu[0]?.value || VIEW;

  async function applyActions(groupId: number, actions: Record<string, ColumnAction>) {
    const next = { ...columnActions, [String(groupId)]: actions };
    setAnalysis(await analyzeFiles({ files, strategy: "keep", columnActions: next, columnNames, transforms }));
    setColumnActions(next);
    setResults({});
  }

  async function applyNames(groupId: number, names: Record<string, string>, actions?: Record<string, ColumnAction>) {
    const nextNames = { ...columnNames, [String(groupId)]: names };
    const nextActions = actions ? { ...columnActions, [String(groupId)]: actions } : columnActions;
    setAnalysis(await analyzeFiles({ files, strategy: "keep", columnActions: nextActions, columnNames: nextNames, transforms }));
    setColumnNames(nextNames);
    setColumnActions(nextActions);
    setResults({}); // skill results used the old names
  }
  // The table's transform steps (Power Query-style "Applied steps"), in order.
  async function applyTransforms(groupId: number, steps: TransformStep[]) {
    const next = { ...transforms, [String(groupId)]: steps };
    setAnalysis(await analyzeFiles({ files, strategy: "keep", columnActions, columnNames, transforms: next }));
    setTransforms(next);
    setResults({}); // skill results used the old data
  }

  // Every "Add" button opens Plan & billing with that tool already ticked in the
  // package selector: tools unlock only through a package (entitlements.py).
  const [suggestTool, setSuggestTool] = useState<string | null>(null);
  const [askDownload, setAskDownload] = useState<DownloadFormat | null>(null);

  function add(id: string) {
    setSuggestTool(id);
    setInfoOpen(null);
    setAccountTab("billing");
  }

  function goTo(id: string, tab?: string) {
    setAccountTab(null);
    setSection(id);
    if (tab) setPicked((p) => ({ ...p, [id]: tab }));
  }

  async function download(format: DownloadFormat) {
    setAskDownload(null);
    if (!session || !group) return;
    setError(null);
    try {
      await downloadCleaned({
        input,
        token: session.token,
        groupId: group.group_id,
        format,
        confirmedAmountUsd: group.download_estimate.amount_usd, // never charge more than confirmed
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed.");
    }
  }

  function signOut() {
    void signOutServer(session?.token); // the token stops working on the server at once
    clearSession();
    router.push(HOME);
  }

  if (session === undefined) return <p className="text-sm text-ink-muted">Loading…</p>;
  if (session === null) {
    // After signing in, come back to this same page (e.g. /workspace/settings/billing).
    const next = `?next=${encodeURIComponent(window.location.pathname)}`;
    return (
      <Card className="mx-auto max-w-md text-center">
        <h1 className="text-xl font-semibold">Sign in to open your workspace</h1>
        <p className="mt-1 text-sm text-ink-muted">{error ?? "Use the email address you signed up with."}</p>
        <div className="mt-4 flex justify-center gap-3">
          <Link href={`${SIGN_IN}${next}`} className="rounded-full bg-brand px-5 py-2 text-sm font-semibold text-on-brand">
            Sign in
          </Link>
          <Link href={`${SIGN_UP}${next}`} className="rounded-full border border-line px-5 py-2 text-sm font-semibold">
            Create an account
          </Link>
        </div>
      </Card>
    );
  }

  const isConsultant = session.account.role === "consultant";
  // Whose workspace is on screen: the client a consultant is working on, or me.
  const who = plan?.account ?? session.account;
  const whoName = who.company_name ?? `${who.first_name} ${who.last_name}`;
  const cloud = origin === "cloud";
  // Free consultation until a consultant confirms it happened; then the paid link.
  // Services paid from the first hour (Data Strategy & Discovery) always use the paid link.
  const bookFor = (s?: Service) =>
    plan ? (s && s.consultation.free_minutes === 0 ? plan.consulting.paid_booking_url : plan.consulting.booking_url) : bookingUrl;
  const freeLeft = !!plan && !plan.consulting.free_used;
  const previewChip = (
    <span className="rounded-full border border-consultant/50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-consultant">
      Preview
    </span>
  );
  const ACCOUNT_TABS: { id: AccountTab; emoji: string; label: string }[] = [
    { id: "account", emoji: "👤", label: "Account" },
    { id: "billing", emoji: "💳", label: "Plan & billing" },
    { id: "integrations", emoji: "🔌", label: "Integrations" },
    ...(isConsultant ? [{ id: "consultant" as const, emoji: "🤝", label: "Consultant" }] : []),
    ...(session.account.is_admin ? [{ id: "admin" as const, emoji: "🛡️", label: "Administration" }] : []),
  ];
  const accountTitle = ACCOUNT_TABS.find((t) => t.id === accountTab);

  const item = (s: PlanService) => {
    const isLocked = !s.active;
    return (
      <li key={s.id} className="shrink-0 lg:shrink">
        <div className={`flex items-center gap-1 rounded-xl ${current === s.id ? "bg-brand-soft" : "hover:bg-surface-2"} ${isLocked ? "opacity-80" : ""}`}>
          <button
            type="button"
            onClick={() => goTo(s.id)}
            aria-current={current === s.id ? "page" : undefined}
            className="flex min-w-0 flex-1 items-center gap-2 whitespace-nowrap px-3 py-2 text-left text-sm lg:whitespace-normal"
          >
            <span aria-hidden className="text-base">
              {categoryEmoji(s.id)}
            </span>
            <span className={current === s.id ? "font-semibold" : ""}>{navName(s)}</span>
            {!s.available && !isLocked && previewChip}
            {isLocked && <span className="sr-only">(not on your plan)</span>}
          </button>
          {isLocked && (
            <button
              type="button"
              aria-label={`About ${s.name}: what it adds and how to get it`}
              aria-expanded={infoOpen === s.id}
              onClick={() => {
                setInfoOpen(infoOpen === s.id ? null : s.id);
                goTo(s.id);
              }}
              className="mr-1 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-line text-xs font-semibold text-ink-muted hover:border-brand hover:text-brand-ink"
            >
              i
            </button>
          )}
        </div>
        {/* Desktop: the details open right under the item, inside the pane. */}
        {isLocked && infoOpen === s.id && (
          <div className="mx-1 mb-2 mt-1 hidden rounded-xl border border-line bg-surface p-3 lg:block">
            <p className="mb-1 text-xs font-semibold" style={{ color: categoryColor(s.id) }}>
              {s.name}
            </p>
            <ServiceInfo service={s} small />
          </div>
        )}
      </li>
    );
  };

  // "Choose your data" — only on the Data Cleaning page.
  const dataSources = (
    <div data-tour="ws-choose">
      <Card>
        <h2 className="font-semibold">Choose your data</h2>
        <p className="mt-1 text-sm text-ink-muted">CSV or Excel files work best. Picked files are cleaned in this browser tab.</p>
        <label className="mt-4 inline-flex cursor-pointer items-center gap-2 rounded-full bg-brand px-5 py-2 text-sm font-semibold text-on-brand">
          {busy ? "Opening your data…" : "Choose files"}
          <input type="file" multiple className="sr-only" disabled={busy}
            onChange={(e) => e.target.files?.length && clean(Array.from(e.target.files), "file")} />
        </label>
        {stored && stored.rows > 0 && (
          <div className="mt-5 border-t border-line pt-4">
            <h3 className="text-sm font-semibold">Stored for {whoName}</h3>
            <p className="mt-1 text-xs text-ink-muted">Kept in managed storage (Data Engineering). Your consultant sees the same data.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {/* One button per stored file: it opens as the original table again. */}
              {stored.sources.map((s) => (
                <Button key={`${s.source_system}/${s.source_name}`} variant="secondary" disabled={busy} onClick={() => openStored(s.source_name)}>
                  {s.source_name} · {fmtInt(s.records)} rows
                </Button>
              ))}
            </div>
          </div>
        )}
        {cloudTables && cloudTables.length > 0 && (
          <div className="mt-5 border-t border-line pt-4">
            <h3 className="text-sm font-semibold">From your database</h3>
            <p className="mt-1 text-xs text-ink-muted">Read where it lives — never copied or changed. Managed in Settings → Integrations.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {cloudTables.slice(0, 24).map((t) => (
                <Button key={`${t.schema}.${t.name}`} variant="secondary" disabled={busy} onClick={() => openCloud(t)}>
                  {t.schema}.{t.name}
                  {t.rows_estimate > 0 && ` · ~${fmtInt(t.rows_estimate)}`}
                </Button>
              ))}
            </div>
          </div>
        )}
      </Card>
    </div>
  );

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
      {/* ── Left pane: the services, or the account area's sections. Settings sits at the bottom. ── */}
      <aside className="min-w-0 lg:sticky lg:top-20 lg:flex lg:h-[calc(100vh-7rem)] lg:flex-col lg:self-start">
        <Tour id="workspace" steps={WORKSPACE_TOUR} />
        {accountTab ? (
          <nav aria-label="Settings" className="lg:flex-1">
            <button type="button" onClick={() => setAccountTab(null)} className="mb-3 px-3 text-sm text-brand-ink hover:underline">
              ← Back to workspace
            </button>
            <ul className="flex gap-1 overflow-x-auto pb-1 lg:flex-col">
              {ACCOUNT_TABS.map((t) => (
                <li key={t.id} className="shrink-0">
                  <button
                    type="button"
                    onClick={() => setAccountTab(t.id)}
                    aria-current={accountTab === t.id ? "page" : undefined}
                    className={`flex w-full items-center gap-2 whitespace-nowrap rounded-xl px-3 py-2 text-left text-sm ${
                      accountTab === t.id ? "bg-brand-soft font-semibold" : "hover:bg-surface-2"
                    }`}
                  >
                    <span aria-hidden>{t.emoji}</span> {t.label}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        ) : (
          <div className="min-h-0 lg:flex-1 lg:overflow-y-auto">
            {isConsultant && (
              <ClientPicker clients={clients} actingId={actingId} onChoose={chooseClient} onAddClient={() => setAddingClient(true)} load={myLoad}
                services={options?.services} />
            )}
            <p className="mb-2 hidden px-3 text-xs text-ink-muted lg:block">{whoName}</p>
            <nav aria-label="Workspace" data-tour="ws-nav">
              {/* relative: keeps the screen-reader-only text inside this scrolling row on phones. */}
              <ul className="relative flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">{ordered.map(item)}</ul>
            </nav>
            <TourButton id="workspace" className="mt-3 hidden px-3 lg:block" />
          </div>
        )}
        {/* Settings: bottom left. */}
        <button
          type="button"
          data-tour="ws-settings"
          onClick={() => setAccountTab(accountTab ? null : "account")}
          aria-pressed={!!accountTab}
          className={`mt-3 flex w-full items-center gap-2 rounded-xl border px-3 py-2 text-left text-sm ${
            accountTab ? "border-brand bg-brand-soft font-semibold" : "border-line bg-surface hover:border-brand"
          }`}
        >
          <span aria-hidden>⚙️</span>
          <span className="min-w-0 flex-1">
            Settings
            <span className="block truncate text-xs font-normal text-ink-muted">
              {session.account.first_name} {session.account.last_name}
            </span>
          </span>
          {isConsultant && setupNeeded && <span className="h-2 w-2 rounded-full bg-warn" aria-label="Setup needed" />}
        </button>
      </aside>

      {/* ── Right side ── */}
      <section className="min-w-0 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold">
            {accountTitle && (
              <>
                <span aria-hidden>{accountTitle.emoji}</span> {accountTitle.label}
              </>
            )}
            {currentService && (
              <>
                <span aria-hidden>{categoryEmoji(currentService.id)}</span> {navName(currentService)}{" "}
                {!currentService.available && currentService.active && previewChip}
              </>
            )}
          </h1>
          {analysis && !accountTab && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-xs text-ink-muted">
                {Object.keys(sourceInfo).length > 1 ? "Sources" : cloud ? cloudFrom : origin === "stored" ? "Stored data" : "Your files"}:{" "}
                {files.map((f) => sourceInfo[f.name]?.label ?? f.name).join(", ")}
                {cloud && cloudNote && ` — ${cloudNote}`}
              </span>
              <Button variant="secondary" disabled={busy} onClick={() => setAdding(true)}>
                Add another source
              </Button>
              <Button variant="secondary" onClick={() => { clearData(); goTo(CLEANING); }}>
                Change data
              </Button>
            </div>
          )}
        </div>

        {isConsultant && setupNeeded && accountTab !== "consultant" && (
          <div role="status" className="flex flex-col gap-2 rounded-2xl border border-consultant/40 bg-consultant-soft px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
            <span>
              <strong>Welcome aboard! Finish your onboarding:</strong> two-step sign-in, the independent-contractor terms,
              and your Cal.com booking links and webhook, so clients can book you (and pay you) directly.
            </span>
            <Button variant="secondary" onClick={() => setAccountTab("consultant")}>
              Continue onboarding
            </Button>
          </div>
        )}
        {isConsultant && plan?.acting && (
          <p role="status" className="rounded-2xl border border-consultant/40 bg-consultant-soft px-4 py-3 text-sm">
            <span className="mr-2 rounded-full bg-consultant px-2 py-0.5 text-xs font-semibold text-on-consultant">Consultant</span>
            You&apos;re working on <strong>{whoName}</strong>
            {who.company_name && ` (${who.first_name} ${who.last_name})`}. Downloads are billed to them.
            {plan.interests.length > 0 && (
              <span className="mt-1 block text-xs text-ink-muted">
                Asked for help with: {plan.interests.map((id) => plan.services.find((s) => s.id === id)?.name ?? id).join(", ")}
              </span>
            )}
          </p>
        )}
        {isConsultant && plan?.acting && !addingClient && !accountTab && (
          <ConsultationPanel token={session.token} summary={plan.consulting} freeMinutes={plan.consulting.free_minutes} onChanged={reloadPlan} />
        )}
        {isConsultant && current === AI && currentService && !currentService.available && (
          <p className="rounded-2xl border border-consultant/40 bg-consultant-soft px-4 py-3 text-sm">
            <strong>Beacon uses {plan?.account.company_name ?? "this client"}&apos;s allowance.</strong> Every client has
            a free monthly allowance (premium with the AI package); your questions count toward theirs. Answers come
            from the built-in parser, or Google Gemini when the platform has a Gemini key.
          </p>
        )}
        {isConsultant && addingClient && (
          <AddClientForm
            token={session.token}
            options={options}
            onCancel={() => setAddingClient(false)}
            onDone={(c) => {
              setClients((all) => [...all, c].sort((a, b) => a.label.localeCompare(b.label)));
              chooseClient(c.id);
            }}
          />
        )}
        <ErrorBox message={error} />
        {!plan && !error && <p className="text-sm text-ink-muted">Loading your plan…</p>}
        {session.account.mfa_required && !session.account.mfa_enabled && (
          // Admins and consultants: staff pages stay locked until two-step sign-in is on (api/services/mfa.py).
          <div role="status" className="flex flex-col gap-2 rounded-2xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
            <span>
              <strong>Set up two-step sign-in:</strong> admins and consultants need it before the Admin panel or any
              client&apos;s workspace opens.
            </span>
            <Button variant="secondary" onClick={() => setAccountTab("account")}>Set it up</Button>
          </div>
        )}
        {plan?.hipaa?.storing_paused && (
          // A HIPAA request is waiting for an admin (api/services/hipaa.py): nothing is stored until then.
          <div role="status" className="flex flex-col gap-2 rounded-2xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
            <span>
              <strong>Storing data is paused</strong> while we confirm your healthcare business (HIPAA). Cleaning and
              analyzing files still work.
            </span>
            <Button variant="secondary" onClick={() => setAccountTab("account")}>See the request</Button>
          </div>
        )}
        {plan && !plan.can_edit && (
          <p role="status" className="rounded-2xl border border-line bg-surface p-3 text-sm">
            <strong>View only:</strong> you can look at and download everything your team has, but not change what&apos;s
            stored or connected. Your team&apos;s owner can give you Developer access.
          </p>
        )}

        {/* ── The account area (Settings, bottom left) ── */}
        {accountTab === "account" && (
          <AccountPanel
            key={`account-${planAccountId ?? "me"}`}
            token={session.token}
            account={session.account}
            onAccount={(account) => {
              const fresh = { ...session, account };
              saveSession(fresh);
              setSession(fresh);
            }}
            acting={!!plan?.acting}
            workspaceName={whoName}
            stored={stored}
            canStore={!!plan?.active.includes(ENGINEERING)}
            canChange={!!plan?.can_add}
            bookingUrl={plan?.consulting.paid_booking_url ?? bookingUrl}
            // Both read the LATEST session (setSession(prev => ...)), so a refresh never puts back an old token.
            onToken={(token) =>
              setSession((prev) => {
                if (!prev) return prev;
                const fresh = { ...prev, token };
                saveSession(fresh);
                return fresh;
              })
            }
            onRefreshAccount={() => {
              const latest = loadSession();
              if (!latest) return;
              getMe(latest.token)
                .then((account) =>
                  setSession((prev) => {
                    if (!prev) return prev;
                    const fresh = { ...prev, account };
                    saveSession(fresh);
                    return fresh;
                  }),
                )
                .catch(() => {});
            }}
            onStoredChanged={setStored}
            onSignOut={signOut}
            onDeleted={() => {
              clearSession();
              router.push(HOME);
            }}
          />
        )}
        {accountTab === "billing" && plan && (
          <>
            {returnNote && (
              <p role="status" className="rounded-2xl border border-good/40 bg-good/10 p-3 text-sm">
                {returnNote}
              </p>
            )}
            <BillingPanel key={suggestTool ?? "billing"} token={session.token} plan={plan} bookingUrl={bookFor()}
              onChanged={reloadPlan} suggestTool={suggestTool} />
            {/* Paying a consultant for setup / ongoing help (not for admins' own accounts: nothing to buy). */}
            {(plan.acting || !session.account.is_admin) && (
              <ConsultantHelp key={`consult-${planAccountId}`} token={session.token} acting={plan.acting} />
            )}
          </>
        )}
        {adding && session && (
          <AddSourceDialog token={session.token} stored={stored} cloudTables={cloudTables}
            openNames={files.map((f) => f.name)} onAdd={addSources} onClose={() => setAdding(false)} />
        )}
        {accountTab === "integrations" && (
          // Owners and developers connect data; a view-only member just looks (teams.py).
          <IntegrationsPanel key={`integrations-${planAccountId ?? "me"}`} token={session.token} canChange={!!plan?.can_edit}
            isAdmin={!!session.account.is_admin}
            onOpenTable={openCloud} onOpenLive={openLive} notice={integrationNotice} />
        )}
        {accountTab === "consultant" && isConsultant && <ConsultantSetup token={session.token} onSetupNeeded={setSetupNeeded} />}
        {accountTab === "admin" && session.account.is_admin && <AdminPanel token={session.token} />}

        {/* ── The workspace ── */}
        {!accountTab && plan && plan.active.includes(ENGINEERING) && !cloud && stored?.set_up && stored.rows === 0 &&
          !(current === ENGINEERING && choice === VIEW) && (
          <div role="status" className="flex flex-col gap-2 rounded-2xl border border-brand/40 bg-brand-soft px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
            <span>
              <strong>Store your data:</strong> your plan includes Data Engineering, but nothing is stored yet.
              {analysis ? " Check the preview and accept." : " Choose your file in Data Cleaning first."}
            </span>
            <Button variant="secondary" onClick={() => (analysis ? goTo(ENGINEERING, VIEW) : goTo(CLEANING))}>
              {analysis ? "Load into database" : "Open Data Cleaning"}
            </Button>
          </div>
        )}

        {currentService && !currentService.active && current !== AI ? (
          // A grayed-out service: what it adds, and how to get it. (AI is never
          // grayed out: everyone gets the free allowance — AssistantPanel.)
          <Card>
            <ServiceInfo service={currentService} />
            <div className="mt-4 flex flex-wrap gap-3">
              {!plan?.can_add ? (
                <p className="text-sm text-ink-muted">Ask your team&apos;s owner to add this tool.</p>
              ) : currentService.tool_monthly_usd != null ? (
                <Button onClick={() => add(currentService.id)}>Add {navName(currentService)} to my package</Button>
              ) : null}
              {!isConsultant && (
                <a href={bookFor(currentService)} target="_blank" rel="noreferrer"
                  className="rounded-full border border-line px-5 py-2 text-sm font-semibold hover:border-brand">
                  {currentService.consultation.free_minutes > 0 && freeLeft
                    ? `Book a free ${currentService.consultation.free_minutes}-minute call`
                    : `Book a consulting session (${fmtUsd(plan?.consulting.rate_per_hour_usd ?? 0)}/hour)`}
                </a>
              )}
            </div>
          </Card>
        ) : currentService && !analysis ? (
          current === CLEANING ? (
            dataSources
          ) : (
            // Every other page uses the data opened in Data Cleaning.
            <Card>
              <p className="text-sm">
                {busy ? "Opening your data…" : <><strong>No data open yet.</strong> Choose it in Data Cleaning — upload files, open your stored data, or a table from your database.</>}
              </p>
              {!busy && (
                <Button className="mt-3" onClick={() => goTo(CLEANING)}>
                  Go to Data Cleaning
                </Button>
              )}
            </Card>
          )
        ) : current === AI && analysis && group && plan ? (
          // The AI page: Beacon, with this month's allowance (free / premium / unlimited).
          <AssistantPanel
            key={`ai-${group.group_id}-${planAccountId}`}
            token={session.token}
            input={input}
            groupId={group.group_id}
            tableLabel={group.label}
            rows={group.report.rows_after}
            columns={group.report.column_reports
              .filter((c) => c.action !== "dropped")
              .map((c) => ({ name: c.column, type: c.inferred_type === "number" ? "numeric" : c.inferred_type }))}
            isAdmin={!!session.account.is_admin}
            canUpgrade={!!plan.can_add}
            onUpgrade={() => add(AI)}
          />
        ) : currentService && analysis && group ? (
          <div className="space-y-6">
            {analysis.groups.length > 1 && (
              <div role="tablist" aria-label="Tables" className="flex flex-wrap gap-2">
                {analysis.groups.map((g, i) => (
                  <button key={g.group_id} role="tab" aria-selected={i === groupIndex} onClick={() => setGroupIndex(i)}
                    className={`rounded-full border px-3 py-1.5 text-sm ${
                      i === groupIndex ? "border-brand bg-brand-soft font-medium" : "border-line bg-surface hover:border-brand"
                    }`}>
                    {g.label}
                  </button>
                ))}
              </div>
            )}
            {/* How the open tables relate: 🔗 a shared ID, ≈ customers matched by name / email / phone. */}
            {analysis.groups.length > 1 && analysis.categorization.relationships.length > 0 && (
              <div className="space-y-1 text-sm">
                {analysis.categorization.relationships.map((r) => (
                  <p key={`${r.left_group}-${r.right_group}`}>
                    <span aria-hidden>{r.method === "fuzzy" ? "≈" : "🔗"}</span> {r.description}
                  </p>
                ))}
              </div>
            )}

            {/* Under 30 rows: analysis pages explain why instead of running anything. */}
            {current !== CLEANING && !group.report.analysis_ready && (
              <Card>
                <p className="text-sm">
                  <strong>Not enough rows to analyze.</strong> {group.report.analysis_note}
                </p>
                <Button className="mt-3" variant="secondary" onClick={() => goTo(CLEANING)}>
                  Go to Data Cleaning
                </Button>
              </Card>
            )}

            {/* The service's skills, as tabs. */}
            {(current === CLEANING || group.report.analysis_ready) && menu.length > 1 && (
              <div role="tablist" aria-label={`${navName(currentService)} tabs`} className="flex flex-wrap gap-1 border-b border-line">
                {menu.map((m) => {
                  const off = cloud && current === ENGINEERING && m.value === VIEW;
                  return (
                    <button key={m.value} role="tab" aria-selected={choice === m.value}
                      onClick={() => current && setPicked((p) => ({ ...p, [current]: m.value }))}
                      className={`-mb-px rounded-t-xl border-b-2 px-4 py-2 text-sm ${
                        choice === m.value ? "border-brand font-semibold text-ink" : "border-transparent text-ink-muted hover:text-ink"
                      } ${off ? "opacity-50" : ""}`}>
                      {m.label}
                    </button>
                  );
                })}
              </div>
            )}

            {current === CLEANING && choice === VIEW && (
              <>
                {!cloud && (
                  <div className="flex flex-wrap items-center gap-2">
                    <Button onClick={() => setAskDownload("csv")}>Download CSV</Button>
                    <Button variant="secondary" onClick={() => setAskDownload("xlsx")}>
                      Download Excel
                    </Button>
                    <span className="text-xs text-ink-muted">
                      CSV {estimateLine(group.download_estimate, "csv")} · Excel {estimateLine(group.download_estimate, "xlsx")}
                    </span>
                  </div>
                )}
                {askDownload && (
                  <DownloadConfirm tableLabel={group.label} format={askDownload} estimate={group.download_estimate}
                    included={plan?.downloads_included} onConfirm={() => download(askDownload)} onCancel={() => setAskDownload(null)} />
                )}
                <GroupDetails
                  key={group.group_id}
                  readOnly={cloud}
                  group={group}
                  bookingUrl={bookFor()}
                  applied={columnActions[String(group.group_id)] ?? {}}
                  onApply={(actions) => applyActions(group.group_id, actions)}
                  appliedNames={columnNames[String(group.group_id)] ?? {}}
                  onApplyNames={(names, actions) => applyNames(group.group_id, names, actions)}
                  appliedSteps={transforms[String(group.group_id)] ?? []}
                  onApplySteps={(steps) => applyTransforms(group.group_id, steps)}
                />
              </>
            )}

            {/* Beta: two open tables, compared by period (CompareOverTime.tsx — easy to remove). */}
            {current === DASHBOARD && choice === VIEW && analysis.groups.length >= 2 && (
              <CompareOverTime key={`compare-${files.map((f) => f.name).join("|")}`} input={input} analysis={analysis} />
            )}
            {current === DASHBOARD && choice === VIEW && group.report.analysis_ready && (
              <DashboardTabs
                key={`dash-${group.group_id}-${planAccountId}`}
                token={session.token}
                input={input}
                groupId={group.group_id}
                columns={group.report.column_reports
                  .filter((c) => c.action !== "dropped")
                  .map((c) => ({ name: c.column, type: c.inferred_type === "number" ? "numeric" : c.inferred_type }))}
                hasDates={group.report.column_reports.some((c) => c.inferred_type.startsWith("date") && c.action !== "dropped")}
                canBuild={!!plan?.active.includes(DASHBOARD)}
                onEditContext={() => setAccountTab("account")}
                viewerName={session.account.first_name}
                // One file: its name ("sales.csv" -> "Sales"); several files or a database: the table's label.
                dataName={files.length === 1 ? files[0].name : group.label}
              />
            )}

            {current === ENGINEERING && choice === VIEW &&
              (cloud ? (
                <Card>
                  <h2 className="font-semibold">Storing is off for cloud data</h2>
                  <p className="mt-1 text-sm text-ink-muted">
                    This table lives in your own database, which already manages it — we analyze it where it is and don&apos;t
                    keep a copy. The ER diagram and data dictionary tabs work on it as usual. (Storing is for files and
                    business tools like QuickBooks.)
                  </p>
                </Card>
              ) : !plan?.can_edit ? (
                <Card>
                  <h2 className="font-semibold">Storing data needs Developer access</h2>
                  <p className="mt-1 text-sm text-ink-muted">
                    You&apos;re a view-only member of this team. Ask your team&apos;s owner to make you a Developer.
                  </p>
                </Card>
              ) : (
                <WarehouseLoad
                  key={`load-${group.group_id}`}
                  token={session.token}
                  input={input}
                  groupId={group.group_id}
                  clientName={whoName}
                  canDelete={!!plan?.can_add}
                  onStoredChanged={setStored}
                  tables={analysis.groups.map((g) => ({ group_id: g.group_id, label: g.label, columns: g.preview.columns }))}
                  relationships={analysis.categorization.relationships}
                />
              ))}

            {choice !== VIEW && plan && (current === CLEANING || group.report.analysis_ready) && (
              <SkillsPanel
                key={`${choice}-${group.group_id}`}
                input={input}
                groupId={group.group_id}
                columns={group.preview.columns}
                services={plan.services}
                selectedServices={plan.active}
                onAddService={add}
                fixedSkill={choice}
                autoRun
                initialResult={results[`${group.group_id}:${choice}`] ?? null}
                onResult={(r) => setResults((old) => ({ ...old, [`${group.group_id}:${choice}`]: r }))}
                onAsked={(q, answered) => logQuestion(session.token, q, answered)}
              />
            )}
          </div>
        ) : null}
      </section>
    </div>
  );
}

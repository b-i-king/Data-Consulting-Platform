// The signed-in workspace ("/app"), laid out like Copilot Money on the web:
// a left pane of high-level options, and the chosen one on the right.
//
// LEFT PANE (one row per service, each with its own emoji)
//   * Services on the account's plan come first and open normally:
//       - Dashboard (📊, the Data Visualization service) at the top if the
//         plan has it,
//       - otherwise Data Cleaning (🧹, always included) at the top,
//       - then the other services on the plan, in catalog order.
//   * Every other service is listed below, GRAYED OUT, with an ⓘ button
//     explaining what it adds and how to get it (price, how payment works).
//   * On phones the pane becomes a row you can swipe sideways.
//
// RIGHT SIDE
//   Each visit starts by picking files; they stay in this browser tab's memory
//   and are re-sent when needed. Clients with MANAGED STORAGE (data loaded
//   through Data Engineering) can instead press "Use my stored data": the
//   stored table comes back as a CSV file (GET /api/warehouse/export) and goes
//   through exactly the same flow — so the client, signed in with their own
//   account, sees the same dashboards and analysis as their consultant. Each service's page has a SKILL MENU at the top and opens on
//   its primary view:
//       Data Cleaning       the cleaning report + download
//       Data Visualization  the dashboard
//       everything else     the service's primary skill (data_skills/catalog.json
//                           "primary"), run automatically when it needs no options
//   Picking another skill from the menu shows that skill instead. Results are
//   kept while the files stay the same, so switching back is instant.
//
// CONSULTANT MODE (lavender): a consultant sees a "Working on" picker at the
// top of the left pane (ClientPicker.tsx). Picking a client shows that
// client's plan and data tools; every signed-in request then carries
// X-Acting-As, and the server checks the client is assigned to this consultant.
// "Book a call" prompts are hidden for consultants. Consultants see ALL SEVEN
// services, including AI (not launched yet) marked "Preview". Working on a
// client also shows the Consultations card (ConsultationPanel.tsx): confirm
// the free call happened, which switches the client to the paid booking link.
//
// DATA ENGINEERING opens on "Load into database": a preview of how the data
// will be transformed for the warehouse, and why, to accept or not
// (components/explore/WarehouseLoad.tsx).
//
// BOOKING LINKS come from the plan (api/services/consultations.py): the free
// consultation link until a consultant confirms it happened, then the paid one.
//
// PLAN & BILLING (💳, bottom of the left pane): plans (monthly / yearly via
// Stripe Checkout), "Manage billing · cancel anytime", the free 30-minute
// consultation and prepaid consulting hours — components/billing/BillingPanel.tsx.
// Coming back from Stripe (?checkout=success) shows a thank-you note.
//
// SETTINGS (⚙️): consultants set their capacity (how many clients at once);
// clients can ask to work as a consultant — components/workspace/SettingsPanel.tsx.
// ADMIN (🛡️, admins only): approve or deny consultant requests, and every
// consultant's fill bar — components/workspace/AdminPanel.tsx.
// Each visit re-reads the account (GET /api/accounts/me), so someone approved
// as a consultant gets consultant mode without signing in again.
//
// The plan comes from GET /api/accounts/services (api/services/entitlements.py).
// Tools unlock only through a package: "Add to my package" opens Plan &
// billing with that tool ticked (the package selector shows the new total). Downloads also
// ask first, with the estimated wait and charge (DownloadConfirm.tsx).

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  analyzeFiles,
  downloadCleaned,
  getIntakeOptions,
  getMe,
  getMyClients,
  getMyServices,
  getSkills,
  getStoredDataFile,
  getWarehouseSummary,
  setActingAs,
} from "@/lib/api";
import { categoryColor, categoryEmoji } from "@/lib/categories";
import { FALLBACK_BOOKING_URL, fmtUsd } from "@/lib/format";
import { clearSession, loadSession, saveSession, type Session } from "@/lib/session";
import type { ConsultantClient, ConsultantLoad, IntakeOptions } from "@/lib/types";
import ClientPicker, { AddClientForm } from "./ClientPicker";
import ConsultationPanel from "./ConsultationPanel";
import AdminPanel from "./AdminPanel";
import SettingsPanel from "./SettingsPanel";
import WarehouseLoad from "@/components/explore/WarehouseLoad";
import BillingPanel from "@/components/billing/BillingPanel";
import type { AnalyzeResponse, CleaningInput, ColumnAction, ColumnActions, ColumnNames, TransformStep, Transforms, MyServices, Service, SkillInfo, SkillResult, WarehouseCounts } from "@/lib/types";
import Dashboard from "@/components/explore/Dashboard";
import { priceLabel } from "@/components/explore/LockedService";
import SkillsPanel from "@/components/explore/SkillsPanel";
import DownloadConfirm, { estimateLine, type DownloadFormat } from "@/components/wizard/DownloadConfirm";
import { GroupDetails } from "@/components/wizard/StepResults";
import { Button, Card, ConfirmDialog, ErrorBox } from "@/components/wizard/ui";

const DASHBOARD = "data_visualization";
const CLEANING = "data_cleaning";
const ENGINEERING = "data_engineering";
const AI = "ai";
const BILLING = "__billing"; // the "Plan & billing" page (not a service)
const SETTINGS = "__settings"; // capacity (consultants) / "work as a consultant" (clients)
const ADMIN = "__admin"; // consultant requests (admins only)
const VIEW = "__view__";   // menu value for the built-in view (cleaning report / dashboard / database load)
// The skill each built-in view already shows, so it isn't listed twice in the menu
// (Data Engineering's load view isn't a skill, so nothing is hidden for it).
const VIEW_SKILL: Record<string, { skill: string; label: string }> = {
  [CLEANING]: { skill: "data-cleaning", label: "Cleaning report" },
  [DASHBOARD]: { skill: "build-dashboard", label: "Dashboard" },
  [ENGINEERING]: { skill: "", label: "Load into database (preview first)" },
};

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
  const [infoOpen, setInfoOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Data for this visit (never stored on the server).
  const [files, setFiles] = useState<File[]>([]);
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [columnActions, setColumnActions] = useState<ColumnActions>({});
  const [columnNames, setColumnNames] = useState<ColumnNames>({});
  const [transforms, setTransforms] = useState<Transforms>({});
  const [groupIndex, setGroupIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  // What this client has in managed storage (offered as "Use my stored data").
  const [stored, setStored] = useState<WarehouseCounts | null>(null);

  // Consultant mode: my clients, the one I'm working on (null = my own workspace).
  const [options, setOptions] = useState<IntakeOptions | null>(null);
  const [clients, setClients] = useState<ConsultantClient[]>([]);
  const [myLoad, setMyLoad] = useState<ConsultantLoad | null>(null); // how full I am (consultants)
  const [actingId, setActingId] = useState<string | null>(null);
  const [addingClient, setAddingClient] = useState(false);
  const [returnNote, setReturnNote] = useState<string | null>(null); // after paying at Stripe

  // Skill menu: every skill, the choice per service, and results kept for this visit.
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, SkillResult>>({});

  useEffect(() => {
    const s = loadSession();
    setSession(s);
    setActingAs(null); // a fresh visit starts in your own workspace
    if (!s) return;
    // Consultants: my clients and how full I am.
    const loadClients = (token: string) =>
      getMyClients(token)
        .then((r) => {
          setClients(r.clients);
          setMyLoad(r.load);
        })
        .catch(() => {});
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
      setSection(BILLING);
    } else if (q.get("checkout") === "canceled" || q.get("consulting") === "canceled") {
      setReturnNote("Payment canceled — nothing was charged.");
      setSection(BILLING);
    }
    getIntakeOptions()
      .then((o) => {
        setBookingUrl(o.booking_url);
        setOptions(o);
      })
      .catch(() => {});
  }, []);

  // Consultant: switch to another client (or back to my own workspace). Their
  // data is separate, so the files and results from the last client are cleared.
  async function chooseClient(id: string | null) {
    if (!session) return;
    setActingAs(id);
    setActingId(id);
    setAddingClient(false);
    setFiles([]);
    setAnalysis(null);
    setColumnActions({});
    setColumnNames({});
    setTransforms({});
    setResults({});
    setSection(null);
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
  // client), check what's in managed storage for it.
  const planAccountId = plan?.account.id;
  useEffect(() => {
    if (!session || !planAccountId) return;
    getWarehouseSummary(session.token)
      .then((s) => setStored(s.warehouse))
      .catch(() => setStored(null));
  }, [session, planAccountId]);

  // "Use my stored data": the stored table comes back as a CSV file and is
  // opened exactly like a picked file.
  async function openStored(source: string) {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const file = await getStoredDataFile(session.token, source);
      await clean([file]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't open the stored data.");
      setBusy(false);
    }
  }


  // Left-pane order: Dashboard (if on the plan) or Data Cleaning first, then the
  // rest of the plan, then everything not on the plan (grayed out).
  const { open, locked } = useMemo(() => {
    if (!plan) return { open: [] as Service[], locked: [] as Service[] };
    const byId = Object.fromEntries(plan.services.map((s) => [s.id, s]));
    const first = plan.active.includes(DASHBOARD) ? [DASHBOARD, CLEANING] : [CLEANING];
    const ids = [...first, ...plan.active.filter((id) => !first.includes(id))];
    return { open: ids.map((id) => byId[id]).filter(Boolean), locked: plan.services.filter((s) => !s.active) };
  }, [plan]);
  const current = section ?? open[0]?.id ?? null;
  const currentService = plan?.services.find((s) => s.id === current);

  const input: CleaningInput = useMemo(
    () => ({ files, strategy: "keep", columnActions, columnNames, transforms }),
    [files, columnActions, columnNames, transforms],
  );
  const group = analysis?.groups[groupIndex];

  // The current service's skill menu: built-in view first (if any), then its skills.
  const menu = useMemo(() => {
    if (!current) return [] as { value: string; label: string }[];
    const view = VIEW_SKILL[current];
    const list = skills.filter((k) => k.category === current && k.id !== view?.skill);
    // Primary skill first, then catalog order.
    list.sort((a, b) => Number(b.primary) - Number(a.primary));
    return [...(view ? [{ value: VIEW, label: view.label }] : []), ...list.map((k) => ({ value: k.id, label: k.label }))];
  }, [skills, current]);
  const choice = (current && picked[current]) || menu[0]?.value || VIEW;

  async function clean(chosen: File[]) {
    setResults({});   // new data: earlier skill results no longer apply
    setBusy(true);
    setError(null);
    try {
      setFiles(chosen);
      setColumnActions({});
      setColumnNames({});
      setTransforms({});
      setGroupIndex(0);
      setAnalysis(await analyzeFiles({ files: chosen, strategy: "keep", columnActions: {}, columnNames: {}, transforms: {} }));
    } catch (e) {
      setAnalysis(null);
      setError(e instanceof Error ? e.message : "We couldn't read those files.");
    } finally {
      setBusy(false);
    }
  }

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
    setSection(BILLING);
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
    clearSession();
    router.push("/");
  }

  if (session === undefined) return <p className="text-sm text-ink-muted">Loading…</p>;
  if (session === null) {
    return (
      <Card className="mx-auto max-w-md text-center">
        <h1 className="text-xl font-semibold">Sign in to open your workspace</h1>
        <p className="mt-1 text-sm text-ink-muted">{error ?? "Use the email address you signed up with."}</p>
        <div className="mt-4 flex justify-center gap-3">
          <Link href="/signin" className="rounded-full bg-brand px-5 py-2 text-sm font-semibold text-on-brand">
            Sign in
          </Link>
          <Link href="/signin?mode=signup" className="rounded-full border border-line px-5 py-2 text-sm font-semibold">
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

  const item = (s: Service, isLocked: boolean) => (
    <li key={s.id} className="shrink-0 lg:shrink">
      <div
        className={`flex items-center gap-1 rounded-xl ${
          current === s.id ? "bg-brand-soft" : "hover:bg-surface-2"
        } ${isLocked ? "opacity-55" : ""}`}
      >
        <button
          type="button"
          onClick={() => setSection(s.id)}
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
              setSection(s.id);
            }}
            className="mr-1 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-line text-xs font-semibold text-ink-muted hover:border-brand hover:text-brand"
          >
            i
          </button>
        )}
      </div>
      {/* Desktop: the details open right under the item, inside the pane, so they
          never cover the page. (Phones see the same details on the right side.) */}
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

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
      {/* ── Left pane ── */}
      <aside className="min-w-0 lg:sticky lg:top-20 lg:self-start">
        {isConsultant && (
          <ClientPicker clients={clients} actingId={actingId} onChoose={chooseClient} onAddClient={() => setAddingClient(true)} load={myLoad} />
        )}
        <p className="mb-2 hidden px-3 text-xs text-ink-muted lg:block">
          {who.company_name ?? `${who.first_name} ${who.last_name}`}
        </p>
        <nav aria-label="Workspace">
          {/* relative: keeps the screen-reader-only text inside this scrolling row on phones. */}
          <ul className="relative flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
            {open.map((s) => item(s, false))}
            {locked.length > 0 && (
              <li className="hidden px-3 pb-1 pt-4 text-xs font-semibold uppercase tracking-wide text-ink-muted lg:block">
                Not on your plan
              </li>
            )}
            {locked.map((s) => item(s, true))}
          </ul>
        </nav>
        <button
          type="button"
          onClick={() => setSection(BILLING)}
          aria-current={current === BILLING ? "page" : undefined}
          className={`mt-3 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm ${
            current === BILLING ? "bg-brand-soft font-semibold" : "hover:bg-surface-2"
          }`}
        >
          <span aria-hidden>💳</span> Plan &amp; billing
        </button>
        <button
          type="button"
          onClick={() => setSection(SETTINGS)}
          aria-current={current === SETTINGS ? "page" : undefined}
          className={`mt-1 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm ${
            current === SETTINGS ? "bg-brand-soft font-semibold" : "hover:bg-surface-2"
          }`}
        >
          <span aria-hidden>⚙️</span> Settings
        </button>
        {session.account.is_admin && (
          <button
            type="button"
            onClick={() => setSection(ADMIN)}
            aria-current={current === ADMIN ? "page" : undefined}
            className={`mt-1 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm ${
              current === ADMIN ? "bg-brand-soft font-semibold" : "hover:bg-surface-2"
            }`}
          >
            <span aria-hidden>🛡️</span> Admin
          </button>
        )}
        <button type="button" onClick={signOut} className="mt-4 hidden px-3 text-sm text-ink-muted hover:text-ink lg:block">
          Sign out
        </button>
      </aside>

      {/* ── Right side ── */}
      <section className="min-w-0 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold">
            {current === BILLING && (
              <>
                <span aria-hidden>💳</span> Plan &amp; billing
              </>
            )}
            {current === SETTINGS && (
              <>
                <span aria-hidden>⚙️</span> Settings
              </>
            )}
            {current === ADMIN && (
              <>
                <span aria-hidden>🛡️</span> Admin
              </>
            )}
            {currentService && (
              <>
                <span aria-hidden>{categoryEmoji(currentService.id)}</span> {navName(currentService)}{" "}
                {!currentService.available && currentService.active && previewChip}
              </>
            )}
          </h1>
          <div className="flex items-center gap-3 text-sm">
            {analysis && (
              <Button variant="secondary" onClick={() => setAnalysis(null)}>
                Change files
              </Button>
            )}
            <button type="button" onClick={signOut} className="text-ink-muted hover:text-ink lg:hidden">
              Sign out
            </button>
          </div>
        </div>
        {isConsultant && plan?.acting && (
          <p role="status" className="rounded-2xl border border-consultant/40 bg-consultant-soft px-4 py-3 text-sm">
            <span className="mr-2 rounded-full bg-consultant px-2 py-0.5 text-xs font-semibold text-on-consultant">Consultant</span>
            You&apos;re working on <strong>{whoName}</strong>
            {who.company_name && ` (${who.first_name} ${who.last_name})`}. Downloads are billed to them.
            {plan.interests.length > 0 && (
              <span className="mt-1 block text-xs text-ink-muted">
                Asked for help with:{" "}
                {plan.interests.map((id) => plan.services.find((s) => s.id === id)?.name ?? id).join(", ")}
              </span>
            )}
          </p>
        )}
        {isConsultant && plan?.acting && !addingClient && current !== BILLING && (
          <ConsultationPanel
            token={session.token}
            summary={plan.consulting}
            freeMinutes={plan.consulting.free_minutes}
            onChanged={reloadPlan}
          />
        )}
        {isConsultant && current === AI && currentService && !currentService.available && (
          <p className="rounded-2xl border border-consultant/40 bg-consultant-soft px-4 py-3 text-sm">
            <strong>AI is in preview.</strong> Only consultants see it here; clients see it as “Early access”. Questions
            are answered by the built-in plain-English parser (totals, averages, top N, by category, by year) with the
            calculation shown. A language model comes later (docs/ROADMAP.md).
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

        {current === SETTINGS && (
          <SettingsPanel
            token={session.token}
            account={session.account}
            onAccount={(account) => {
              const fresh = { ...session, account };
              saveSession(fresh);
              setSession(fresh);
            }}
          />
        )}
        {current === ADMIN && session.account.is_admin && (
          <AdminPanel token={session.token} />
        )}

        {current === BILLING && plan && (
          <>
            {returnNote && (
              <p role="status" className="rounded-2xl border border-good/40 bg-good/10 p-3 text-sm">
                {returnNote}
              </p>
            )}
            <BillingPanel
              key={suggestTool ?? "billing"}
              token={session.token}
              plan={plan}
              bookingUrl={bookFor()}
              onChanged={reloadPlan}
              suggestTool={suggestTool}
            />
          </>
        )}

        {currentService && !currentService.active ? (
          // A grayed-out service: what it adds, and how to get it.
          <Card>
            <ServiceInfo service={currentService} />
            <div className="mt-4 flex flex-wrap gap-3">
              {!plan?.can_add ? (
                <p className="text-sm text-ink-muted">Ask your team&apos;s owner to add this tool.</p>
              ) : currentService.tool_monthly_usd != null ? (
                <Button onClick={() => add(currentService.id)}>Add {navName(currentService)} to my package</Button>
              ) : null}
              {!isConsultant && (
                <a
                  href={bookFor(currentService)}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-full border border-line px-5 py-2 text-sm font-semibold hover:border-brand"
                >
                  {currentService.consultation.free_minutes > 0 && freeLeft
                    ? `Book a free ${currentService.consultation.free_minutes}-minute call`
                    : `Book a consulting session (${fmtUsd(plan?.consulting.rate_per_hour_usd ?? 0)}/hour)`}
                </a>
              )}

            </div>
          </Card>
        ) : currentService && !analysis ? (
          // Each visit starts by picking files — they stay in this browser tab.
          <Card>
            <h2 className="font-semibold">Choose your data</h2>
            <p className="mt-1 text-sm text-ink-muted">
              CSV or Excel recommended. Your files are processed in memory and never stored, so pick them again on each visit.
            </p>
            <label className="mt-4 inline-flex cursor-pointer items-center gap-2 rounded-full bg-brand px-5 py-2 text-sm font-semibold text-on-brand">
              {busy ? "Cleaning your data…" : "Choose files"}
              <input
                type="file"
                multiple
                className="sr-only"
                disabled={busy}
                onChange={(e) => e.target.files?.length && clean(Array.from(e.target.files))}
              />
            </label>
            {stored && stored.rows > 0 && (
              <div className="mt-5 border-t border-line pt-4">
                <h3 className="text-sm font-semibold">Or use the data stored for {whoName}</h3>
                <p className="mt-1 text-xs text-ink-muted">
                  Kept in managed storage (Data Engineering). Your consultant sees the same data.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {/* One button per stored file: it opens as the original table again. */}
                  {stored.sources.map((s) => (
                    <Button key={`${s.source_system}/${s.source_name}`} variant="secondary" disabled={busy}
                      onClick={() => openStored(s.source_name)}>
                      {s.source_name} · {s.records.toLocaleString("en-US")} rows
                    </Button>
                  ))}
                </div>
              </div>
            )}
          </Card>
        ) : currentService && analysis && group ? (
          <div className="space-y-6">
            {analysis.groups.length > 1 && (
              <div role="tablist" className="flex flex-wrap gap-2">
                {analysis.groups.map((g, i) => (
                  <button
                    key={g.group_id}
                    role="tab"
                    aria-selected={i === groupIndex}
                    onClick={() => setGroupIndex(i)}
                    className={`rounded-full border px-3 py-1.5 text-sm ${
                      i === groupIndex ? "border-brand bg-brand-soft font-medium" : "border-line bg-surface hover:border-brand"
                    }`}
                  >
                    {g.label}
                  </button>
                ))}
              </div>
            )}

            {/* Under 30 rows: analysis pages explain why instead of running anything. */}
            {current !== CLEANING && !group.report.analysis_ready && (
              <Card>
                <p className="text-sm">
                  <strong>Not enough rows to analyze.</strong> {group.report.analysis_note}
                </p>
                <Button className="mt-3" variant="secondary" onClick={() => setSection(CLEANING)}>
                  Go to Data Cleaning
                </Button>
              </Card>
            )}

            {/* Skill menu: opens on the service's primary view or skill. */}
            {(current === CLEANING || group.report.analysis_ready) && menu.length > 1 && (
              <label className="block max-w-md space-y-1 text-sm">
                <span className="font-medium">Skill</span>
                <select
                  className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm"
                  value={choice}
                  onChange={(e) => current && setPicked((p) => ({ ...p, [current]: e.target.value }))}
                >
                  {menu.map((m, i) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                      {i === 0 ? " (default)" : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}

            {current === CLEANING && choice === VIEW && (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Button onClick={() => setAskDownload("csv")}>Download CSV</Button>
                  <Button variant="secondary" onClick={() => setAskDownload("xlsx")}>
                    Download Excel
                  </Button>
                  <span className="text-xs text-ink-muted">
                    CSV {estimateLine(group.download_estimate, "csv")} · Excel {estimateLine(group.download_estimate, "xlsx")}
                  </span>
                </div>
                {askDownload && (
                  <DownloadConfirm
                    tableLabel={group.label}
                    format={askDownload}
                    estimate={group.download_estimate}
                    included={plan?.downloads_included}
                    onConfirm={() => download(askDownload)}
                    onCancel={() => setAskDownload(null)}
                  />
                )}
                <GroupDetails
                  key={group.group_id}
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

            {current === DASHBOARD && choice === VIEW && group.report.analysis_ready &&
              (group.report.column_reports.some((c) => c.inferred_type.startsWith("date") && c.action !== "dropped") ? (
                <Dashboard key={group.group_id} input={input} groupId={group.group_id} />
              ) : (
                <Card>
                  <p className="text-sm">
                    This table has no date column, so there&apos;s no time dashboard. Pick another visualization skill from the menu above.
                  </p>
                </Card>
              ))}

            {current === ENGINEERING && choice === VIEW && (
              <WarehouseLoad
                key={`load-${group.group_id}`}
                token={session.token}
                input={input}
                groupId={group.group_id}
                clientName={whoName}
                canDelete={!!plan?.can_add}
                onStoredChanged={setStored}
              />
            )}

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
              />
            )}
          </div>
        ) : null}
      </section>
    </div>
  );
}

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
//   Client data is never stored (docs/ARCHITECTURE.md), so each visit starts
//   by picking files; they stay in this browser tab's memory and are re-sent
//   when needed. Each service's page has a SKILL MENU at the top and opens on
//   its primary view:
//       Data Cleaning       the cleaning report + download
//       Data Visualization  the dashboard
//       everything else     the service's primary skill (data_skills/catalog.json
//                           "primary"), run automatically when it needs no options
//   Picking another skill from the menu shows that skill instead. Results are
//   kept while the files stay the same, so switching back is instant.
//
// The plan comes from GET /api/accounts/services (api/services/entitlements.py).
// Until payments exist, "Add to my plan" unlocks a service right away.

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { addMyService, analyzeFiles, downloadCleaned, getIntakeOptions, getMyServices, getSkills } from "@/lib/api";
import { categoryColor, categoryEmoji } from "@/lib/categories";
import { FALLBACK_BOOKING_URL, fmtUsd } from "@/lib/format";
import { clearSession, loadSession, type Session } from "@/lib/session";
import type { AnalyzeResponse, CleaningInput, ColumnAction, ColumnActions, MyServices, Service, SkillInfo, SkillResult } from "@/lib/types";
import Dashboard from "@/components/explore/Dashboard";
import { priceLabel } from "@/components/explore/LockedService";
import SkillsPanel from "@/components/explore/SkillsPanel";
import { GroupDetails } from "@/components/wizard/StepResults";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const DASHBOARD = "data_visualization";
const CLEANING = "data_cleaning";
const VIEW = "__view__";   // menu value for the built-in view (cleaning report / dashboard)
// The skill each built-in view already shows, so it isn't listed twice in the menu.
const VIEW_SKILL: Record<string, { skill: string; label: string }> = {
  [CLEANING]: { skill: "data-cleaning", label: "Cleaning report" },
  [DASHBOARD]: { skill: "build-dashboard", label: "Dashboard" },
};

/** The name shown in the left pane: the visualization service IS the dashboard. */
const navName = (s: Service) => (s.id === DASHBOARD ? "Dashboard" : s.name);

/** Plain-English "how do I get this?" for a locked service. */
function howToGet(s: Service): string {
  if (s.pricing_model === "consultation") return "Starts with a free call with a consultant, then a quote for the work.";
  if (s.pricing_model === "early_access") return "Free while in early access — add it to try it.";
  if (s.pricing_model === "package")
    return `A one-time package from ${fmtUsd(s.starting_price_usd)}. Add it to your plan; online checkout is coming soon, so for now we'll confirm the price with you before any charge.`;
  return "Included with every account.";
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
  const [groupIndex, setGroupIndex] = useState(0);
  const [busy, setBusy] = useState(false);

  // Skill menu: every skill, the choice per service, and results kept for this visit.
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, SkillResult>>({});

  useEffect(() => {
    const s = loadSession();
    setSession(s);
    if (!s) return;
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
    getIntakeOptions()
      .then((o) => setBookingUrl(o.booking_url))
      .catch(() => {});
  }, []);

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

  const input: CleaningInput = useMemo(() => ({ files, strategy: "keep", columnActions }), [files, columnActions]);
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
      setGroupIndex(0);
      setAnalysis(await analyzeFiles({ files: chosen, strategy: "keep", columnActions: {} }));
    } catch (e) {
      setAnalysis(null);
      setError(e instanceof Error ? e.message : "We couldn't read those files.");
    } finally {
      setBusy(false);
    }
  }

  async function applyActions(groupId: number, actions: Record<string, ColumnAction>) {
    const next = { ...columnActions, [String(groupId)]: actions };
    setAnalysis(await analyzeFiles({ files, strategy: "keep", columnActions: next }));
    setColumnActions(next);
    setResults({});
  }

  async function add(id: string) {
    if (!session) return;
    setError(null);
    try {
      setPlan(await addMyService(session.token, id));
      setSection(id);
      setInfoOpen(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't add that service.");
    }
  }

  async function download(format: "csv" | "xlsx") {
    if (!session || !group) return;
    setError(null);
    try {
      await downloadCleaned({ input, token: session.token, groupId: group.group_id, format });
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
        <p className="mb-2 hidden px-3 text-xs text-ink-muted lg:block">
          {session.account.company_name ?? `${session.account.first_name} ${session.account.last_name}`}
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
        <button type="button" onClick={signOut} className="mt-4 hidden px-3 text-sm text-ink-muted hover:text-ink lg:block">
          Sign out
        </button>
      </aside>

      {/* ── Right side ── */}
      <section className="min-w-0 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold">
            {currentService && (
              <>
                <span aria-hidden>{categoryEmoji(currentService.id)}</span> {navName(currentService)}
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
        <ErrorBox message={error} />
        {!plan && !error && <p className="text-sm text-ink-muted">Loading your plan…</p>}

        {currentService && !currentService.active ? (
          // A grayed-out service: what it adds, and how to get it.
          <Card>
            <ServiceInfo service={currentService} />
            <div className="mt-4 flex flex-wrap gap-3">
              {plan?.can_add ? (
                <Button onClick={() => add(currentService.id)}>Add {currentService.name} to my plan</Button>
              ) : (
                <p className="text-sm text-ink-muted">Ask your team&apos;s owner to add this service.</p>
              )}
              {currentService.pricing_model === "consultation" && (
                <a href={bookingUrl} target="_blank" rel="noreferrer" className="rounded-full border border-line px-5 py-2 text-sm font-semibold hover:border-brand">
                  Book a free call
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
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => download("csv")}>Download CSV</Button>
                  <Button variant="secondary" onClick={() => download("xlsx")}>
                    Download Excel
                  </Button>
                </div>
                <GroupDetails
                  key={group.group_id}
                  group={group}
                  bookingUrl={bookingUrl}
                  applied={columnActions[String(group.group_id)] ?? {}}
                  onApply={(actions) => applyActions(group.group_id, actions)}
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

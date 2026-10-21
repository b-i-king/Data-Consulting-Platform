// ── The guest questionnaire ("wizard", Get Started) ──────────────────────────
// This component owns ALL the state for the intake flow and decides which
// step to show. Each step is its own component that receives just the values
// and callbacks it needs. After "Where is your data?" the flow takes one of
// THREE PATHS (StepDataLocation.tsx decides which), each with the same rules
// as the workspace:
//
//   FILES ON MY COMPUTER
//     About you -> Your data -> Upload -> Clean & preview (full cleaning:
//     fix, fill, rename, transform; dashboard and tools) -> Your needs ->
//     Next steps (sign up only now; download, package, or share with a consultant)
//   CLOUD STORAGE (Supabase / PostgreSQL)
//     About you -> Your data -> Account -> Connect & choose a table (StepConnect)
//     -> Preview (READ-ONLY report, nothing fixed) -> Your needs (no "just
//     download") -> Next steps (the workspace opens on that table)
//   BUSINESS APPS (QuickBooks, Xero)
//     About you -> Your data -> Account -> Connect (the tool's own sign-in) ->
//     Quick look (free, StepQuickLook; optional read-only preview of a record
//     type) -> Your needs -> Next steps ("Turn on the stored sync" with Data
//     Engineering)
//
// The account comes BEFORE "Connect" on the last two paths: a connection is
// saved, encrypted, on an account. Connecting QuickBooks / Xero (or "Sign in
// with Supabase") LEAVES this page for the provider's sign-in; the answers so
// far are saved in sessionStorage first (this tab only), and the provider
// sends the person back to /get-started/connect?integration=... — the wizard
// picks up at "Connect" again.
//
// ADDRESSES: every step has its own (/get-started/about-you, /upload, /clean,
// /your-needs, ... — the map is lib/routes.ts). Moving between steps changes
// the address WITHOUT leaving the page, so the files stay in memory and the
// browser's Back / Forward buttons move between steps. A refresh (or a link)
// can only open a step that needs nothing earlier — About you, Your data or
// Upload; any other step falls back to the nearest one that can be shown.
// While files are loaded, leaving or refreshing the page asks first.
//
// NAVIGATION on every page (components/wizard/ui.tsx NavRow): "← Previous"
// on every page but the first; "« First step" from the third page on (your
// answers are kept); "Skip to download" on every page but the first and the
// last — for someone who changes their mind and just wants the cleaned file.
// Downloading needs a FILE (cloud and live data are never offered as a
// download), so without cleaned file data it goes to Upload, and straight to
// the download once the file is read.
//
// IMPORTANT: the uploaded File objects stay in this component's memory. They
// are re-sent (with the missing-value choices) whenever the server needs the
// data again — fills, dashboard, skills, download — so it never stores client data.
// Cloud tables and QuickBooks / Xero records come in as the same kind of File.

"use client";

import { useEffect, useMemo, useState } from "react";
import { analyzeFiles, getCloudTableFile, getIntakeOptions, getLiveDatasetFile, signOutServer } from "@/lib/api";
import { FALLBACK_BOOKING_URL, SITE_NAME, TOOL_NAMES } from "@/lib/format";
import { setAddress, wizardPath, wizardStepFromPath, WORKSPACE, workspacePath } from "@/lib/routes";
import { clearSession, loadSession, saveSession, type Session } from "@/lib/session";
import type { AccountType, AnalyzeResponse, CleaningInput, CloudTable, ColumnAction, ColumnActions, ColumnNames, ConsultantPlan, LiveDataset, SubscriptionChoice, TransformStep, Transforms, Industry, IntakeOptions, MissingStrategy } from "@/lib/types";
import AuthForm from "@/components/auth/AuthForm";
import { Button, Card, ErrorBox, NavRow, Progress, WizardNavContext } from "./ui";
import StepAccountType from "./StepAccountType";
import StepDataLocation, { type DataSource } from "./StepDataLocation";
import StepConnect from "./StepConnect";
import StepQuickLook from "./StepQuickLook";
import StepUpload from "./StepUpload";
import StepResults from "./StepResults";
import { AddServiceConfirm } from "./ServiceConfirm";
import StepServices, { type NextPath } from "./StepServices";
import StepAccount from "./StepAccount";

type StepId = "about" | "data" | "upload" | "clean" | "account" | "connect" | "preview" | "quicklook" | "needs" | "next";

/** The steps of each path, in order, with the progress bar's labels. */
const FLOWS: Record<DataSource, { id: StepId; label: string }[]> = {
  files: [
    { id: "about", label: "About you" }, { id: "data", label: "Your data" }, { id: "upload", label: "Upload" },
    { id: "clean", label: "Clean & preview" }, { id: "needs", label: "Your needs" }, { id: "next", label: "Next steps" },
  ],
  cloud: [
    { id: "about", label: "About you" }, { id: "data", label: "Your data" }, { id: "account", label: "Account" },
    { id: "connect", label: "Connect" }, { id: "preview", label: "Preview" }, { id: "needs", label: "Your needs" },
    { id: "next", label: "Next steps" },
  ],
  business: [
    { id: "about", label: "About you" }, { id: "data", label: "Your data" }, { id: "account", label: "Account" },
    { id: "connect", label: "Connect" }, { id: "quicklook", label: "Quick look" }, { id: "needs", label: "Your needs" },
    { id: "next", label: "Next steps" },
  ],
};
// Steps a fresh page load (refresh, bookmark, typed address) can open: they
// need no answers from earlier steps. Any other step starts at "About you".
const ENTRY_STEPS: StepId[] = ["about", "data", "upload"];
const PROVIDER_NAMES = TOOL_NAMES;
// The answers kept while the browser is away at a provider's sign-in (this tab only).
const SAVED_KEY = "dcp-get-started";

export default function Wizard() {
  // Options (questions + answer choices) come from the backend.
  const [options, setOptions] = useState<IntakeOptions | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);   // still loading after 4 s: the server is probably waking up

  // Answers collected along the way.
  const [step, setStep] = useState<StepId>("about");
  const [source, setSource] = useState<DataSource>("files");
  // Cloud / business app paths: what was previewed, and messages from the connect step.
  const [previewLabel, setPreviewLabel] = useState<string | null>(null);
  const [pickedTable, setPickedTable] = useState<CloudTable | null>(null);
  const [liveBusy, setLiveBusy] = useState(false);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [connectNotice, setConnectNotice] = useState<{ ok: boolean; text: string } | null>(null);
  // "Skip to download" was pressed before there was cleaned file data: after Upload, go straight to it.
  const [downloadOnly, setDownloadOnly] = useState(false);
  const [accountType, setAccountType] = useState<AccountType | null>(null);
  const [industry, setIndustry] = useState<Industry>({
    industry_supersector: null,
    naics_sector: null,
    naics_subsector: null,
    company_size: null,
    industry_other: null,
  });
  // "Your needs": the path (download / package / consultant), the package
  // (monthly / yearly), and the consultant picked by name + one-time or monthly.
  const [path, setPath] = useState<NextPath | null>(null);
  const [subscription, setSubscription] = useState<SubscriptionChoice | null>(null);
  const [consultantId, setConsultantId] = useState<string | null>(null);
  const [consultantPlan, setConsultantPlan] = useState<ConsultantPlan>("one_time");
  const wantsConsultant = path === "consultant";
  const [location, setLocation] = useState<string | null>(null);
  const [locationOption, setLocationOption] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [strategy, setStrategy] = useState<MissingStrategy>("keep");
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  // Per-column missing-value choices, by table: {"0": {"amount": "median"}}.
  const [columnActions, setColumnActions] = useState<ColumnActions>({});
  const [columnNames, setColumnNames] = useState<ColumnNames>({});
  const [transforms, setTransforms] = useState<Transforms>({});
  const [serviceIds, setServiceIds] = useState<string[]>(["data_cleaning"]);
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    // Restore a sign-in from earlier in this browser tab.
    setSession(loadSession());
    // Back from a provider's sign-in (api/routers/connections.py oauth_callback, return_to=start):
    // restore the answers saved before leaving, and pick up at "Connect".
    const q = new URLSearchParams(window.location.search);
    const integration = q.get("integration");
    if (integration) {
      try {
        const saved = JSON.parse(sessionStorage.getItem(SAVED_KEY) ?? "null");
        if (saved) {
          setAccountType(saved.accountType ?? null);
          if (saved.industry) setIndustry(saved.industry);
          setLocation(saved.location ?? null);
          setLocationOption(saved.locationOption ?? null);
          setSource(saved.source ?? "files");
        }
      } catch {
        // storage blocked: "Connect" offers to choose the data again (already connected)
      }
      setStep("connect");
      setAddress(wizardPath("connect"), true);
      const name = PROVIDER_NAMES[integration] ?? integration;
      setConnectNotice(
        q.get("connected") === "1"
          ? { ok: true, text: `${name} is connected. Read-only — nothing is copied.` }
          : { ok: false, text: q.get("integration_error") ?? `${name} wasn't connected.` },
      );
      window.history.replaceState(null, "", window.location.pathname);
    } else {
      // Open the step in the address if it can be shown on a fresh load;
      // otherwise (and for plain /get-started) start at "About you".
      const asked = wizardStepFromPath(window.location.pathname);
      const first = asked && ENTRY_STEPS.includes(asked) ? asked : "about";
      setStep(first);
      setAddress(wizardPath(first), true);
    }
    const timer = setTimeout(() => setSlow(true), 4000);
    getIntakeOptions()
      .then(setOptions)
      .catch((e) =>
        setLoadError(
          e instanceof Error && e.message
            ? e.message
            : "We couldn't reach the server. If you're running locally, start the API with `uvicorn api.main:app --reload`.",
        ),
      )
      .finally(() => clearTimeout(timer));
  }, []);

  const bookingUrl = options?.booking_url ?? FALLBACK_BOOKING_URL;
  // Memoized so the dashboard and skills only re-run when the data really changes.
  const input: CleaningInput = useMemo(
    () => ({ files, strategy, columnActions, columnNames, transforms }),
    [files, strategy, columnActions, columnNames, transforms],
  );

  // Re-run the cleaning with new fill/remove choices for one table. The new
  // report replaces the old one only if the server accepts the choices.
  async function applyActions(groupId: number, actions: Record<string, ColumnAction>) {
    const next = { ...columnActions, [String(groupId)]: actions };
    setAnalysis(await analyzeFiles({ files, strategy, columnActions: next, columnNames, transforms }));
    setColumnActions(next);
  }
  // Same for the client's own column names (and, optionally, column choices
  // made at the same time — e.g. "remove all unnamed columns").
  async function applyNames(groupId: number, names: Record<string, string>, actions?: Record<string, ColumnAction>) {
    const nextNames = { ...columnNames, [String(groupId)]: names };
    const nextActions = actions ? { ...columnActions, [String(groupId)]: actions } : columnActions;
    setAnalysis(await analyzeFiles({ files, strategy, columnActions: nextActions, columnNames: nextNames, transforms }));
    setColumnNames(nextNames);
    setColumnActions(nextActions);
  }
  // The table's transform steps (Power Query-style "Applied steps"), in order.
  async function applyTransforms(groupId: number, steps: TransformStep[]) {
    const next = { ...transforms, [String(groupId)]: steps };
    setAnalysis(await analyzeFiles({ files, strategy, columnActions, columnNames, transforms: next }));
    setTransforms(next);
  }

  // A tool picked from a locked badge in Explore joins the PACKAGE being built
  // (after a confirmation with the new total) and unlocks it in this preview.
  // A non-tool (Data Strategy & Discovery) is recorded as project interest.
  const [pendingAdd, setPendingAdd] = useState<string | null>(null);
  const addService = (id: string) => setPendingAdd(id);
  const isTool = (id: string) => (options?.subscriptions ?? []).some((p) => p.id === id);
  const confirmAdd = (id: string, nextPackage: string[]) => {
    if (isTool(id)) setSubscription({ plans: nextPackage, interval: subscription?.interval ?? "year" });
    else setServiceIds((ids) => (ids.includes(id) ? ids : [...ids, id]));
    setPendingAdd(null);
  };
  // What the preview unlocks: the services of the package being built (a bundle's
  // or each tool's), plus project interests.
  const previewServices = useMemo(
    () => Array.from(new Set([...serviceIds, ...(subscription?.plans ?? []).flatMap(
      (id) => (options?.subscriptions ?? []).find((p) => p.id === id)?.service_ids ?? [id])])),
    [serviceIds, subscription, options],
  );
  const go = (n: StepId) => {
    setStep(n);
    setAddress(wizardPath(n)); // a new history entry: the browser's Back returns here
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  // Keep the answers while on "Connect": the provider's sign-in leaves this page.
  useEffect(() => {
    if (step !== "connect") return;
    try {
      sessionStorage.setItem(SAVED_KEY, JSON.stringify({ accountType, industry, location, locationOption, source }));
    } catch {
      // storage blocked: coming back starts over (the connection itself is kept)
    }
  }, [step, accountType, industry, location, locationOption, source]);

  // The connected integration on the cloud / business app paths.
  const connector =
    options?.data_locations.find((l) => l.id === location)?.options.find((o) => o.id === locationOption)?.connector ?? null;

  // Whether a step has what it needs to be shown (the browser's Back / Forward
  // can ask for any step, e.g. "Clean & preview" after the files were changed).
  const canShow = (s: StepId): boolean => {
    switch (s) {
      case "about":
      case "data":
      case "upload":
        return true;
      case "account":
        return source !== "files";
      case "connect":
      case "quicklook":
        return !!session && !!connector;
      case "clean":
        return source === "files" && !!analysis;
      case "preview":
        return !!analysis;
      case "needs":
      case "next":
        return !!analysis || source !== "files";
    }
  };

  // The browser's Back / Forward: show the step in the address — or, if it
  // can't be shown now, the nearest earlier step on this path that can.
  useEffect(() => {
    const onPop = () => {
      const asked = wizardStepFromPath(window.location.pathname) ?? "about";
      if (canShow(asked)) {
        setStep(asked);
        return;
      }
      const ids = FLOWS[source].map((f) => f.id);
      const upTo = ids.indexOf(asked) >= 0 ? ids.indexOf(asked) : ids.indexOf("data");
      const fallback = ids.slice(0, upTo + 1).reverse().find(canShow) ?? "about";
      setStep(fallback);
      setAddress(wizardPath(fallback), true);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  });

  // The browser tab's title names the step ("Upload · Get started · ...").
  useEffect(() => {
    const label = FLOWS[source].find((f) => f.id === step)?.label ?? (step === "preview" ? "Preview" : null);
    document.title = `${label ? `${label} · ` : ""}Get started · ${SITE_NAME}`;
  }, [step, source]);

  // Files live only in this page's memory: ask before a refresh or a closed
  // tab throws them away (the browser shows its own "Leave site?" message).
  useEffect(() => {
    if (!files.length) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [files.length]);

  // A cloud table or a QuickBooks / Xero record type, read live and opened like a
  // file — but only as a READ-ONLY preview (the same rule as the workspace).
  async function previewLive(read: () => Promise<{ file: File }>, label: string) {
    setLiveBusy(true);
    setLiveError(null);
    try {
      const { file } = await read();
      const empty = { columnActions: {}, columnNames: {}, transforms: {} };
      const result = await analyzeFiles({ files: [file], strategy: "keep", ...empty });
      setFiles([file]);
      setStrategy("keep");
      setColumnActions({});
      setColumnNames({});
      setTransforms({});
      setAnalysis(result);
      setPreviewLabel(label);
      go("preview");
    } catch (e) {
      setLiveError(e instanceof Error ? e.message : "We couldn't read that.");
    } finally {
      setLiveBusy(false);
    }
  }

  const onSignedIn = (s: Session) => {
    saveSession(s);
    setSession(s);
  };
  const onSignOut = () => {
    void signOutServer(loadSession()?.token); // the token stops working on the server at once
    clearSession();
    setSession(null);
  };

  if (loadError) return <ErrorBox message={loadError} />;
  if (!options)
    return (
      <p className="text-ink-muted" aria-live="polite">
        {slow ? "Waking up the server — on free hosting this can take up to a minute the first time…" : "Loading…"}
      </p>
    );

  const flow = FLOWS[source];
  // A record type's preview belongs to the business path's "Quick look" stage.
  const stage = source === "business" && step === "preview" ? "quicklook" : step;
  const stepIndex = Math.max(0, flow.findIndex((f) => f.id === stage));
  const providerName = PROVIDER_NAMES[connector ?? ""] ?? "your app";
  const workspaceHref =
    source === "cloud" && pickedTable
      ? `${workspacePath({ service: "data_cleaning" })}?open_table=${encodeURIComponent(`${pickedTable.schema}.${pickedTable.name}`)}`
      : source === "business"
        ? workspacePath({ settings: "integrations" })
        : WORKSPACE;

  // "Skip to download": with cleaned file data, straight to the download (the
  // "Just download" path); otherwise via Upload (a cloud / app choice switches
  // to the files path: downloading needs a file exported from it).
  function skipToDownload() {
    setPath("download");
    if (source === "files" && analysis && files.length) {
      go("next");
      return;
    }
    setSource("files");
    setDownloadOnly(true);
    go("upload");
  }
  const lastStep = step === "next";
  const nav = {
    // From the third page on: back to the first page, answers kept.
    onFirst: stepIndex >= 2 ? () => go("about") : undefined,
    // Every page but the first and the last (and not again on Upload once chosen).
    onSkipToDownload: step !== "about" && !lastStep && !(step === "upload" && downloadOnly) ? skipToDownload : undefined,
  };

  return (
    <WizardNavContext.Provider value={nav}>
    <div>
      <Progress step={stepIndex} total={flow.length} labels={flow.map((f) => f.label)} />

      {step === "upload" && downloadOnly && (
        <p role="status" className="mb-4 rounded-2xl border border-brand/40 bg-brand-soft p-3 text-sm">
          <strong>Skipping to the download.</strong> Choose your file{connector ? ` (export it from ${providerName})` : ""}:
          once it&apos;s read and cleaned, you go straight to creating your account and downloading.
        </p>
      )}

      {pendingAdd && options.services.find((s) => s.id === pendingAdd) && (
        <AddServiceConfirm
          service={options.services.find((s) => s.id === pendingAdd)!}
          plans={options.subscriptions ?? []}
          currentPlans={subscription?.plans ?? []}
          interval={subscription?.interval ?? "year"}
          onConfirm={(next) => confirmAdd(pendingAdd, next)}
          onCancel={() => setPendingAdd(null)}
        />
      )}

      {step === "about" && (
        <StepAccountType
          choices={options.account_types}
          value={accountType}
          onChange={setAccountType}
          industries={options.industries}
          companySizes={options.company_sizes}
          industry={industry}
          onIndustryChange={setIndustry}
          onNext={() => go("data")}
        />
      )}

      {step === "data" && (
        <StepDataLocation
          locations={options.data_locations}
          location={location}
          option={locationOption}
          bookingUrl={bookingUrl}
          onChange={(loc, opt) => {
            setLocation(loc);
            setLocationOption(opt);
          }}
          onBack={() => go("about")}
          onNext={(next) => {
            setSource(next);
            setConnectNotice(null);
            setDownloadOnly(false);
            go(next === "files" ? "upload" : session ? "connect" : "account");
          }}
        />
      )}

      {/* Cloud / business apps: the account first (a connection is saved on it). */}
      {step === "account" && (
        <section>
          {session ? (
            <Card className="space-y-3">
              <p className="text-sm">
                Signed in as <strong>{session.account.email}</strong>. Your {providerName} connection will be saved on
                this account.
              </p>
              <NavRow onBack={() => go("data")}>
                <Button onClick={() => go("connect")}>Continue</Button>
              </NavRow>
            </Card>
          ) : (
            <>
              <AuthForm
                defaultType={accountType ?? "individual"}
                industry={industry}
                onSignedIn={(s) => {
                  onSignedIn(s);
                  go("connect");
                }}
                title={{ signup: `Create your free account to connect ${providerName}`, signin: "Welcome back" }}
                subtitle="Your connection is saved, encrypted, on your account. It's read-only: nothing is copied or changed."
              />
              <NavRow onBack={() => go("data")} />
            </>
          )}
        </section>
      )}

      {step === "connect" && session && connector && (
        <StepConnect
          token={session.token}
          connector={connector}
          source={source}
          notice={connectNotice}
          busy={liveBusy}
          error={liveError}
          onPickTable={(t) => {
            setPickedTable(t);
            void previewLive(() => getCloudTableFile(session.token, t.schema, t.name), `${t.schema}.${t.name}`);
          }}
          onFileExport={() => {
            setSource("files");
            go("upload");
          }}
          onBack={() => go(session ? "data" : "account")}
          onNext={() => go("quicklook")}
        />
      )}
      {step === "connect" && (!session || !connector) && (
        <Card className="space-y-3 text-sm">
          <p>Let&apos;s pick up where you left off: choose where your data is again.</p>
          <ErrorBox message={connectNotice && !connectNotice.ok ? connectNotice.text : null} />
          <Button onClick={() => go("data")}>Back to Your data</Button>
        </Card>
      )}

      {step === "quicklook" && session && connector && (
        <StepQuickLook
          token={session.token}
          connector={connector}
          name={providerName}
          busy={liveBusy}
          error={liveError}
          onPreview={(d: LiveDataset) =>
            void previewLive(() => getLiveDatasetFile(session.token, connector, d.id), `${providerName} ${d.name.toLowerCase()}`)}
          onBack={() => go("connect")}
          onNext={() => go("needs")}
        />
      )}

      {step === "preview" && analysis && (
        <StepResults
          readOnly
          sourceLabel={previewLabel ?? undefined}
          analysis={analysis}
          input={input}
          onApplyActions={applyActions}
          onApplyNames={applyNames}
          onApplyTransforms={applyTransforms}
          services={options.services}
          selectedServices={previewServices}
          onAddService={addService}
          bookingUrl={bookingUrl}
          onBack={() => go(source === "business" ? "quicklook" : "connect")}
          onNext={() => go("needs")}
        />
      )}

      {step === "upload" && (
        <StepUpload
          files={files}
          strategy={strategy}
          options={options}
          onFilesChange={(f) => {
            setFiles(f);
            setAnalysis(null); // new files invalidate the old results
            setColumnActions({});
            setColumnNames({});
            setTransforms({});
          }}
          onStrategyChange={(s) => {
            setStrategy(s);
            setAnalysis(null);
            setColumnActions({});
            setColumnNames({});
            setTransforms({});
          }}
          onAnalyzed={(result) => {
            setAnalysis(result);
            go(downloadOnly ? "next" : "clean");
          }}
          onBack={() => go("data")}
        />
      )}

      {step === "clean" && analysis && (
        <StepResults
          analysis={analysis}
          input={input}
          onApplyActions={applyActions}
          onApplyNames={applyNames}
          onApplyTransforms={applyTransforms}
          services={options.services}
          selectedServices={previewServices}
          onAddService={addService}
          bookingUrl={bookingUrl}
          onBack={() => go("upload")}
          onNext={() => go("needs")}
        />
      )}

      {step === "needs" && (analysis || source !== "files") && (
        <StepServices
          source={source}
          services={options.services}
          selected={serviceIds}
          onChange={setServiceIds}
          runtimeSeconds={source === "files" && analysis ? analysis.runtime.runtime_seconds : 0}
          bookingUrl={bookingUrl}
          subscriptions={options.subscriptions ?? []}
          subscription={subscription}
          onSubscription={setSubscription}
          consultingRate={options.consulting_rate_per_hour_usd ?? 80}
          consultingCatalog={options.consulting}
          path={path}
          onPath={setPath}
          consultants={options.consultants ?? []}
          consultantId={consultantId}
          onConsultantId={setConsultantId}
          consultantPlan={consultantPlan}
          onConsultantPlan={setConsultantPlan}
          onBack={() => go(source === "files" ? "clean" : source === "business" ? "quicklook" : analysis ? "preview" : "connect")}
          onNext={() => go("next")}
        />
      )}

      {step === "next" && (analysis || source !== "files") && (
        <StepAccount
          source={source}
          connector={connector}
          workspaceHref={workspaceHref}
          location={location}
          defaultType={accountType ?? "individual"}
          industry={industry}
          serviceIds={serviceIds}
          path={path ?? "download"}
          // A package counts only on the "Do it myself" path.
          subscription={path === "package" ? subscription : null}
          plans={options.subscriptions ?? []}
          wantsConsultant={wantsConsultant}
          consultants={options.consultants ?? []}
          consultantId={consultantId}
          consultantPlan={consultantPlan}
          session={session}
          onSignedIn={onSignedIn}
          onSignOut={onSignOut}
          input={input}
          analysis={source === "files" ? analysis : null}
          onBack={() => go("needs")}
        />
      )}
    </div>
    </WizardNavContext.Provider>
  );
}

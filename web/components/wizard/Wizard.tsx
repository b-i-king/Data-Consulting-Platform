// ── The guest questionnaire ("wizard") ────────────────────────────────────────
// This component owns ALL the state for the intake flow and decides which
// step to show. Each step is its own component that receives just the values
// and callbacks it needs. Flow:
//
//   0. Who are you?            individual / business (+ size and industry for businesses)
//   1. Where is your data?     flat files (live) · cloud · business apps (coming soon)
//   2. Upload files            one or many; choose how to treat missing values
//   3. Clean & preview         categorization, cleaning report, fill/remove gaps,
//                              first-rows preview, Explore (dashboard + skills)
//   4. What do you need?       pick services, see a price estimate
//   5. Account & download      sign up (only now!) and download the clean file
//
// IMPORTANT: the uploaded File objects stay in this component's memory. They
// are re-sent (with the missing-value choices) whenever the server needs the
// data again — fills, dashboard, skills, download — so it never stores client data.

"use client";

import { useEffect, useMemo, useState } from "react";
import { analyzeFiles, getIntakeOptions } from "@/lib/api";
import { FALLBACK_BOOKING_URL } from "@/lib/format";
import { clearSession, loadSession, saveSession, type Session } from "@/lib/session";
import type { AccountType, AnalyzeResponse, CleaningInput, ColumnAction, ColumnActions, Industry, IntakeOptions, MissingStrategy } from "@/lib/types";
import { ErrorBox, Progress } from "./ui";
import StepAccountType from "./StepAccountType";
import StepDataLocation from "./StepDataLocation";
import StepUpload from "./StepUpload";
import StepResults from "./StepResults";
import StepServices from "./StepServices";
import StepAccount from "./StepAccount";

const LABELS = ["About you", "Your data", "Upload", "Clean & preview", "Your needs", "Download"];

export default function Wizard() {
  // Options (questions + answer choices) come from the backend.
  const [options, setOptions] = useState<IntakeOptions | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);   // still loading after 4 s: the server is probably waking up

  // Answers collected along the way.
  const [step, setStep] = useState(0);
  const [accountType, setAccountType] = useState<AccountType | null>(null);
  const [industry, setIndustry] = useState<Industry>({
    industry_supersector: null,
    naics_sector: null,
    naics_subsector: null,
    company_size: null,
  });
  const [location, setLocation] = useState<string | null>(null);
  const [locationOption, setLocationOption] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [strategy, setStrategy] = useState<MissingStrategy>("keep");
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  // Per-column missing-value choices, by table: {"0": {"amount": "median"}}.
  const [columnActions, setColumnActions] = useState<ColumnActions>({});
  const [serviceIds, setServiceIds] = useState<string[]>(["data_cleaning"]);
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    // Restore a sign-in from earlier in this browser tab.
    setSession(loadSession());
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
  const input: CleaningInput = useMemo(() => ({ files, strategy, columnActions }), [files, strategy, columnActions]);

  // Re-run the cleaning with new fill/remove choices for one table. The new
  // report replaces the old one only if the server accepts the choices.
  async function applyActions(groupId: number, actions: Record<string, ColumnAction>) {
    const next = { ...columnActions, [String(groupId)]: actions };
    setAnalysis(await analyzeFiles({ files, strategy, columnActions: next }));
    setColumnActions(next);
  }
  // A service picked from a locked badge in Explore is added to the order.
  const addService = (id: string) => setServiceIds((ids) => (ids.includes(id) ? ids : [...ids, id]));
  const go = (n: number) => {
    setStep(n);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const onSignedIn = (s: Session) => {
    saveSession(s);
    setSession(s);
  };
  const onSignOut = () => {
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

  return (
    <div>
      <Progress step={step} total={LABELS.length} labels={LABELS} />

      {step === 0 && (
        <StepAccountType
          choices={options.account_types}
          value={accountType}
          onChange={setAccountType}
          industries={options.industries}
          companySizes={options.company_sizes}
          industry={industry}
          onIndustryChange={setIndustry}
          onNext={() => go(1)}
        />
      )}

      {step === 1 && (
        <StepDataLocation
          locations={options.data_locations}
          location={location}
          option={locationOption}
          bookingUrl={bookingUrl}
          onChange={(loc, opt) => {
            setLocation(loc);
            setLocationOption(opt);
          }}
          onBack={() => go(0)}
          onNext={() => go(2)}
        />
      )}

      {step === 2 && (
        <StepUpload
          files={files}
          strategy={strategy}
          options={options}
          onFilesChange={(f) => {
            setFiles(f);
            setAnalysis(null); // new files invalidate the old results
            setColumnActions({});
          }}
          onStrategyChange={(s) => {
            setStrategy(s);
            setAnalysis(null);
            setColumnActions({});
          }}
          onAnalyzed={(result) => {
            setAnalysis(result);
            go(3);
          }}
          onBack={() => go(1)}
        />
      )}

      {step === 3 && analysis && (
        <StepResults
          analysis={analysis}
          input={input}
          onApplyActions={applyActions}
          services={options.services}
          selectedServices={serviceIds}
          onAddService={addService}
          bookingUrl={bookingUrl}
          onBack={() => go(2)}
          onNext={() => go(4)}
        />
      )}

      {step === 4 && analysis && (
        <StepServices
          services={options.services}
          selected={serviceIds}
          onChange={setServiceIds}
          runtimeSeconds={analysis.runtime.runtime_seconds}
          bookingUrl={bookingUrl}
          onBack={() => go(3)}
          onNext={() => go(5)}
        />
      )}

      {step === 5 && analysis && (
        <StepAccount
          defaultType={accountType ?? "individual"}
          industry={industry}
          serviceIds={serviceIds}
          session={session}
          onSignedIn={onSignedIn}
          onSignOut={onSignOut}
          input={input}
          analysis={analysis}
          onBack={() => go(4)}
        />
      )}
    </div>
  );
}

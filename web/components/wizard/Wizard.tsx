// ── The guest questionnaire ("wizard") ────────────────────────────────────────
// This component owns ALL the state for the intake flow and decides which
// step to show. Each step is its own component that receives just the values
// and callbacks it needs. Flow:
//
//   0. Who are you?            individual / business
//   1. Where is your data?     flat files (live) · cloud · business apps (coming soon)
//   2. Upload files            one or many; choose how to treat missing values
//   3. Clean & preview         categorization, cleaning report, full preview
//   4. What do you need?       pick services, see a price estimate
//   5. Account & download      sign up (only now!) and download the clean file
//
// IMPORTANT: the uploaded File objects stay in this component's memory. They
// are re-sent at download time so the server never has to store client data.

"use client";

import { useEffect, useState } from "react";
import { getIntakeOptions } from "@/lib/api";
import { FALLBACK_BOOKING_URL } from "@/lib/format";
import { clearSession, loadSession, saveSession, type Session } from "@/lib/session";
import type { AccountType, AnalyzeResponse, IntakeOptions, MissingStrategy } from "@/lib/types";
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

  // Answers collected along the way.
  const [step, setStep] = useState(0);
  const [accountType, setAccountType] = useState<AccountType | null>(null);
  const [location, setLocation] = useState<string | null>(null);
  const [locationOption, setLocationOption] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [strategy, setStrategy] = useState<MissingStrategy>("keep");
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [serviceIds, setServiceIds] = useState<string[]>(["data_cleaning"]);
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    // Restore a sign-in from earlier in this browser tab.
    setSession(loadSession());
    getIntakeOptions()
      .then(setOptions)
      .catch(() =>
        setLoadError(
          "We couldn't reach the server. If you're running locally, start the API with `uvicorn api.main:app --reload`.",
        ),
      );
  }, []);

  const bookingUrl = options?.booking_url ?? FALLBACK_BOOKING_URL;
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
  if (!options) return <p className="text-ink-muted">Loading…</p>;

  return (
    <div>
      <Progress step={step} total={LABELS.length} labels={LABELS} />

      {step === 0 && (
        <StepAccountType
          choices={options.account_types}
          value={accountType}
          onChange={setAccountType}
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
          }}
          onStrategyChange={(s) => {
            setStrategy(s);
            setAnalysis(null);
          }}
          onAnalyzed={(result) => {
            setAnalysis(result);
            go(3);
          }}
          onBack={() => go(1)}
        />
      )}

      {step === 3 && analysis && (
        <StepResults analysis={analysis} bookingUrl={bookingUrl} onBack={() => go(2)} onNext={() => go(4)} />
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
          session={session}
          onSignedIn={onSignedIn}
          onSignOut={onSignOut}
          files={files}
          strategy={strategy}
          analysis={analysis}
          onBack={() => go(4)}
        />
      )}
    </div>
  );
}

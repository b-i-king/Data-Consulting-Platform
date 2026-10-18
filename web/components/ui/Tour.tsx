// First-time walkthrough ("tour"): a small card, top right, that walks a new
// visitor through a page one step at a time — Back / Next / Skip — and
// highlights the part of the page each step is about.
//
// HOW IT WORKS
//   * Each step names a target: an element marked data-tour="<target>" on the
//     page. The target is scrolled into view and gets a gold outline
//     (the .tour-target class in app/globals.css). A step whose target isn't
//     on screen (say, the dashboard before any data is open) still shows its
//     text, without a highlight.
//   * It opens by itself the FIRST time someone sees the page, then never
//     again in that browser (remembered in localStorage under "dcp.tour.<id>";
//     if the browser blocks storage, it simply shows each visit).
//   * <TourButton id="..." /> ("Take the tour") replays it anytime.
//
// Used on the home page (id "home"), the workspace ("workspace") and the
// dashboard ("dashboard").

"use client";

import { useCallback, useEffect, useState } from "react";

export interface TourStep {
  /** data-tour value of the element to highlight (optional). */
  target?: string;
  title: string;
  body: string;
}

const key = (id: string) => `dcp.tour.${id}`;
const REPLAY = "dcp-tour-replay"; // window event: "show tour <id> again"

function seen(id: string): boolean {
  try {
    return localStorage.getItem(key(id)) === "done";
  } catch {
    return false;
  }
}

function markSeen(id: string) {
  try {
    localStorage.setItem(key(id), "done");
  } catch {
    /* storage blocked: the tour just shows again next time */
  }
}

/** Highlight one step's target (and remove the last highlight). */
function highlight(target?: string) {
  document.querySelectorAll(".tour-target").forEach((el) => el.classList.remove("tour-target"));
  if (!target) return;
  const el = document.querySelector(`[data-tour="${target}"]`);
  if (el) {
    el.classList.add("tour-target");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

export default function Tour({ id, steps, autoStart = true }: { id: string; steps: TourStep[]; autoStart?: boolean }) {
  const [step, setStep] = useState<number | null>(null);

  // First visit: start after the page has drawn (so targets exist).
  useEffect(() => {
    if (!autoStart || seen(id)) return;
    const t = setTimeout(() => setStep(0), 600);
    return () => clearTimeout(t);
  }, [id, autoStart]);

  // "Take the tour" buttons ask for a replay through a window event.
  useEffect(() => {
    const replay = (e: Event) => (e as CustomEvent<string>).detail === id && setStep(0);
    window.addEventListener(REPLAY, replay);
    return () => window.removeEventListener(REPLAY, replay);
  }, [id]);

  useEffect(() => {
    if (step !== null) highlight(steps[step]?.target);
    return () => highlight(undefined);
  }, [step, steps]);

  const finish = useCallback(() => {
    markSeen(id);
    setStep(null);
  }, [id]);

  if (step === null || !steps[step]) return null;
  const s = steps[step];
  const last = step === steps.length - 1;

  return (
    <div
      role="dialog"
      aria-label={`Guide: ${s.title}`}
      className="fixed right-4 top-20 z-50 w-[min(22rem,calc(100vw-2rem))] rounded-2xl border border-brand/50 bg-surface p-4 shadow-[var(--shadow-card)]"
    >
      <p className="font-mono text-xs text-ink-muted">
        Guide · {step + 1} of {steps.length}
      </p>
      <h2 className="mt-1 font-semibold">{s.title}</h2>
      <p className="mt-1 text-sm text-ink-muted">{s.body}</p>
      <div className="mt-4 flex items-center justify-between gap-2 text-sm">
        <button type="button" className="text-ink-muted hover:text-ink" onClick={finish}>
          Skip
        </button>
        <div className="flex gap-2">
          {step > 0 && (
            <button type="button" className="rounded-full border border-line px-4 py-1.5" onClick={() => setStep(step - 1)}>
              Back
            </button>
          )}
          <button
            type="button"
            className="rounded-full bg-brand px-4 py-1.5 font-semibold text-on-brand"
            onClick={() => (last ? finish() : setStep(step + 1))}
          >
            {last ? "Got it" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** "Take the tour": replay a page's walkthrough. */
export function TourButton({ id, className = "" }: { id: string; className?: string }) {
  return (
    <button
      type="button"
      className={`text-sm text-brand-ink hover:underline ${className}`}
      onClick={() => window.dispatchEvent(new CustomEvent(REPLAY, { detail: id }))}
    >
      Take the tour
    </button>
  );
}

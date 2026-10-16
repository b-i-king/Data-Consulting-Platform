// The dashboard, in layers (api/services/dashboards.py):
//
//   BUSINESS CONTEXT  a strip at the top: the industry, what the business does,
//                     its goals (Settings -> Account -> "About your business").
//   OVERVIEW tab      built from the data automatically (Dashboard.tsx): totals
//                     over time, comparisons, filters. Needs a date column.
//   THEIR OWN TABS    each tab = saved plain-English questions ("total amount
//                     by store"), answered again on the open data every time
//                     it opens (the ask-your-data skill, read-only).
//   + Add tab         name it, then pick questions: suggested ones come from
//                     the client's key questions, the questions they ask most,
//                     and their INDUSTRY (written with this table's columns) —
//                     or type your own.
//
// Adding or changing tabs needs Data Visualization (canBuild); everyone with
// the dashboard can view them.

"use client";

import { useCallback, useEffect, useState } from "react";
import { deleteDashboardTab, getDashboardSuggestions, getDashboards, runSkill, saveDashboardTab } from "@/lib/api";
import type { CleaningInput, DashboardSuggestion, DashboardsInfo, DashboardTab, SkillResult } from "@/lib/types";
import ChartView from "@/components/charts/ChartView";
import Dashboard from "./Dashboard";
import ResultTable from "./ResultTable";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

const OVERVIEW = "__overview";

export default function DashboardTabs(props: {
  token: string;
  input: CleaningInput;
  groupId: number;
  /** The open table's columns and types (for suggestions). */
  columns: { name: string; type: string }[];
  /** The table has a date column (the Overview needs one). */
  hasDates: boolean;
  /** May add / change tabs (Data Visualization on the plan, or an admin). */
  canBuild: boolean;
  /** Open Settings -> Account (to edit "About your business"). */
  onEditContext: () => void;
}) {
  const [info, setInfo] = useState<DashboardsInfo | null>(null);
  const [active, setActive] = useState<string>(OVERVIEW);
  const [editing, setEditing] = useState<DashboardTab | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDashboards(props.token)
      .then(setInfo)
      .catch((e) => setError(e instanceof Error ? e.message : "We couldn't load your dashboard tabs."));
  }, [props.token]);

  const tab = info?.tabs.find((t) => t.id === active) ?? null;
  const ctx = info?.context;
  const hasContext = !!(ctx && (ctx.about || ctx.goals)) || !!info?.industry;

  return (
    <div className="space-y-4">
      {/* Business context: what this dashboard is built around. */}
      {info && (
        <div className="flex flex-col gap-2 rounded-2xl border border-line bg-surface px-4 py-3 text-sm sm:flex-row sm:items-start sm:justify-between">
          {hasContext ? (
            <div>
              <span className="font-semibold">{info.company ?? "Your business"}</span>
              {info.industry && <span className="ml-2 rounded-full bg-brand-soft px-2 py-0.5 text-xs">{info.industry}</span>}
              {ctx?.about && <p className="mt-1 text-ink-muted">{ctx.about}</p>}
              {ctx?.goals && <p className="mt-1 text-ink-muted"><strong className="text-ink">Goals:</strong> {ctx.goals}</p>}
            </div>
          ) : (
            <p className="text-ink-muted">
              <strong className="text-ink">Make this dashboard yours:</strong> tell us about the business and the questions
              you ask most — we&apos;ll suggest the right tabs.
            </p>
          )}
          <button type="button" className="shrink-0 text-xs text-brand hover:underline" onClick={props.onEditContext}>
            {hasContext ? "Edit" : "Add business context"}
          </button>
        </div>
      )}

      {/* The tab bar: Overview first, then the client's own tabs. */}
      <div role="tablist" aria-label="Dashboard tabs" className="flex flex-wrap items-center gap-2">
        {[{ id: OVERVIEW, name: "Overview" }, ...(info?.tabs ?? [])].map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={active === t.id}
            onClick={() => {
              setActive(t.id);
              setEditing(null);
            }}
            className={`rounded-full border px-4 py-1.5 text-sm ${
              active === t.id ? "border-brand bg-brand-soft font-semibold" : "border-line bg-surface hover:border-brand"
            }`}
          >
            {t.name}
          </button>
        ))}
        {props.canBuild && info && info.tabs.length < info.max_tabs && (
          <button type="button" onClick={() => setEditing("new")}
            className="rounded-full border border-dashed border-brand px-4 py-1.5 text-sm text-brand hover:bg-brand-soft">
            + Add tab
          </button>
        )}
      </div>
      <ErrorBox message={error} />

      {editing ? (
        <TabEditor
          token={props.token}
          tab={editing === "new" ? null : editing}
          columns={props.columns}
          maxQuestions={info?.max_questions ?? 12}
          onCancel={() => setEditing(null)}
          onSaved={(next, id) => {
            setInfo(next);
            setEditing(null);
            setActive(id ?? next.tabs[next.tabs.length - 1]?.id ?? OVERVIEW);
          }}
        />
      ) : active === OVERVIEW || !tab ? (
        props.hasDates ? (
          <Dashboard key={props.groupId} input={props.input} groupId={props.groupId} />
        ) : (
          <Card>
            <p className="text-sm">
              This table has no date column, so there&apos;s no time-based Overview.{" "}
              {props.canBuild ? "Add a tab with the questions you want answered instead." : ""}
            </p>
          </Card>
        )
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-ink-muted">Answered on the data you have open now, every time this tab opens.</p>
            {props.canBuild && (
              <Button variant="secondary" onClick={() => setEditing(tab)}>
                Edit tab
              </Button>
            )}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {tab.questions.map((q) => (
              <QuestionCard key={`${tab.id}:${q}`} question={q} input={props.input} groupId={props.groupId} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** One saved question, answered on the open data (summary, chart, table). */
function QuestionCard({ question, input, groupId }: { question: string; input: CleaningInput; groupId: number }) {
  const [result, setResult] = useState<SkillResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    runSkill("ask-your-data", input, { question }, groupId)
      .then((r) => live && setResult(r))
      .catch((e) => live && setError(e instanceof Error ? e.message : "This question couldn't be answered."));
    return () => {
      live = false;
    };
  }, [question, input, groupId]);

  const table = result ? Object.values(result.tables)[0] : null;
  return (
    <Card>
      <h3 className="font-semibold capitalize">{question}</h3>
      {!result && !error && <p className="mt-2 text-sm text-ink-muted">Answering…</p>}
      {error && <p className="mt-2 text-sm text-bad">{error}</p>}
      {result && (
        <div className="mt-2 space-y-3">
          <p className="text-sm">{result.summary}</p>
          {result.charts[0] && <ChartView spec={result.charts[0]} />}
          {!result.charts[0] && table && <ResultTable rows={table.slice(0, 15)} />}
        </div>
      )}
    </Card>
  );
}

/** Add or change a tab: its name and questions (suggested ones are one click away). */
function TabEditor(props: {
  token: string;
  tab: DashboardTab | null;
  columns: { name: string; type: string }[];
  maxQuestions: number;
  onCancel: () => void;
  onSaved: (info: DashboardsInfo, id: string | null) => void;
}) {
  const [name, setName] = useState(props.tab?.name ?? "");
  const [questions, setQuestions] = useState<string[]>(props.tab?.questions ?? []);
  const [typed, setTyped] = useState("");
  const [suggestions, setSuggestions] = useState<DashboardSuggestion[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { token, columns } = props;
  const loadSuggestions = useCallback(() => {
    getDashboardSuggestions(token, columns)
      .then((r) => setSuggestions(r.suggestions))
      .catch(() => {});
  }, [token, columns]);
  useEffect(loadSuggestions, [loadSuggestions]);

  const addQuestion = (q: string) => {
    const clean = q.trim();
    if (!clean || questions.some((x) => x.toLowerCase() === clean.toLowerCase()) || questions.length >= props.maxQuestions) return;
    setQuestions([...questions, clean]);
  };

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const next = await saveDashboardTab(props.token, { id: props.tab?.id ?? null, name: name.trim(), questions });
      props.onSaved(next, props.tab?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't save.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!props.tab) return;
    setBusy(true);
    try {
      props.onSaved(await deleteDashboardTab(props.token, props.tab.id), null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't delete.");
      setBusy(false);
    }
  }

  const SOURCE = { yours: "Your question", asked: "Asked often", industry: "For your industry" };
  return (
    <Card className="space-y-4">
      <h3 className="font-semibold">{props.tab ? "Edit tab" : "Add a dashboard tab"}</h3>
      <label className="block max-w-sm space-y-1 text-sm">
        <span>Tab name</span>
        <input className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm" value={name} maxLength={60}
          placeholder="Sales by store" onChange={(e) => setName(e.target.value)} />
      </label>

      <div className="space-y-2">
        <p className="text-sm font-medium">Questions on this tab ({questions.length}/{props.maxQuestions})</p>
        {questions.length === 0 && <p className="text-xs text-ink-muted">Pick a suggestion below, or type your own.</p>}
        <ul className="space-y-1">
          {questions.map((q) => (
            <li key={q} className="flex items-center justify-between gap-2 rounded-xl bg-surface-2 px-3 py-1.5 text-sm">
              <span>{q}</span>
              <button type="button" className="text-xs text-bad hover:underline" onClick={() => setQuestions(questions.filter((x) => x !== q))}>
                Remove
              </button>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-2">
          <input className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-3 py-2 text-sm" value={typed}
            placeholder="e.g. top 5 product by amount in 2025"
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addQuestion(typed);
                setTyped("");
              }
            }} />
          <Button variant="secondary" onClick={() => { addQuestion(typed); setTyped(""); }}>
            Add
          </Button>
        </div>
      </div>

      {suggestions.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium">Suggested for your business</p>
          <div className="flex flex-wrap gap-2">
            {suggestions.map((s) => (
              <button key={s.question} type="button" onClick={() => addQuestion(s.question)}
                disabled={questions.some((x) => x.toLowerCase() === s.question.toLowerCase())}
                className="rounded-xl border border-line bg-surface px-3 py-2 text-left text-xs hover:border-brand disabled:opacity-40">
                <span className="block font-medium">{s.why}</span>
                <span className="block font-mono text-ink-muted">{s.question}</span>
                <span className="block text-[10px] uppercase tracking-wide text-brand">{SOURCE[s.source]}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <ErrorBox message={error} />
      <div className="flex flex-wrap gap-3">
        <Button onClick={save} disabled={busy || !name.trim() || questions.length === 0}>
          {busy ? "Saving…" : "Save tab"}
        </Button>
        <Button variant="secondary" onClick={props.onCancel}>
          Cancel
        </Button>
        {props.tab && (
          <button type="button" className="ml-auto text-sm text-bad hover:underline" onClick={remove} disabled={busy}>
            Delete tab
          </button>
        )}
      </div>
    </Card>
  );
}

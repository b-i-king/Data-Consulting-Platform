// "Run a skill" — pick a data skill from a dropdown, fill in its options, see the result.
//
// The list of skills and each skill's options come from GET /api/skills, which
// reads every data_skills/<skill>/skill.py, in catalog order (AI last).
// ONLY the skills of services the user has chosen are in the dropdown (Data
// Cleaning is always included); every other service is a locked badge the
// user can add (LockedService.tsx). Column options become dropdowns of the
// table's columns. Results show the summary, findings, charts, tables and
// downloadable outputs (Markdown reports, SQL, Mermaid diagrams, HTML).
// The EDA report skill's result is shown as a full report instead
// (EdaReport.tsx): its charts and tables appear inside their sections.

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { getSkills, runSkill, saveText } from "@/lib/api";
import { categoryColor, categoryEmoji } from "@/lib/categories";
import type { CleaningInput, Service, SkillInfo, SkillParam, SkillResult } from "@/lib/types";
import ChartView from "@/components/charts/ChartView";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";
import EdaReport, { edaReportOf } from "./EdaReport";
import { LockedBadge } from "./LockedService";
import ResultTable from "./ResultTable";

const ALWAYS_INCLUDED = "data_cleaning";

const inputCls = "w-full rounded-xl border border-line bg-surface px-3 py-1.5 text-sm";
const EXT: Record<string, [string, string]> = {
  markdown: ["md", "text/markdown"],
  sql: ["sql", "text/plain"],
  mermaid: ["mmd", "text/plain"],
  html: ["html", "text/html"],
};

function ParamField(props: {
  name: string;
  param: SkillParam;
  columns: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  const { param, columns } = props;
  const label = props.name.replace(/_/g, " ");
  let control;
  if (param.type === "column") {
    control = (
      <select className={inputCls} value={props.value} onChange={(e) => props.onChange(e.target.value)}>
        <option value="">Automatic</option>
        {columns.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
    );
  } else if (param.type === "choice") {
    control = (
      <select className={inputCls} value={props.value || String(param.default ?? "")} onChange={(e) => props.onChange(e.target.value)}>
        {param.choices.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
    );
  } else if (param.type === "bool") {
    control = (
      <input type="checkbox" checked={props.value === "true"} onChange={(e) => props.onChange(String(e.target.checked))} />
    );
  } else if (param.type === "json") {
    control = (
      <textarea className={`${inputCls} font-mono`} rows={2} value={props.value} placeholder="{ }" onChange={(e) => props.onChange(e.target.value)} />
    );
  } else {
    control = (
      <input
        className={inputCls}
        value={props.value}
        list={param.type === "columns" ? `cols-${props.name}` : undefined}
        placeholder={param.type === "columns" ? "comma-separated columns" : param.default != null ? String(param.default) : ""}
        onChange={(e) => props.onChange(e.target.value)}
      />
    );
  }
  return (
    <label className="block space-y-1 text-sm">
      <span className="font-medium capitalize">{label}</span>
      {control}
      {param.type === "columns" && <span className="block text-xs text-ink-muted">Columns: {columns.join(", ")}</span>}
      {param.help && <span className="block text-xs text-ink-muted">{param.help}</span>}
    </label>
  );
}

export default function SkillsPanel(props: {
  input: CleaningInput;
  groupId: number;
  columns: string[];
  services: Service[];
  selectedServices: string[];
  onAddService: (id: string) => void;
  /** Show only this service's skills (the workspace has one page per service). */
  category?: string;
  /** Show just this one skill, with no skill menu (the workspace has its own menu). */
  fixedSkill?: string;
  /** Run the skill as soon as it opens. If it needs options first, show the form instead. */
  autoRun?: boolean;
  /** A result kept from earlier, so switching back to a skill doesn't re-run it. */
  initialResult?: SkillResult | null;
  onResult?: (r: SkillResult) => void;
  /** "Ask your data": told each question asked and whether it was answered (for the question log). */
  onAsked?: (question: string, answered: boolean) => void;
}) {
  const { input, groupId, columns } = props;
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<SkillResult | null>(props.initialResult ?? null);
  const [hint, setHint] = useState<string | null>(null);   // "this skill needs an option first"
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSkills().then(setSkills).catch((e) => setError(e.message));
  }, []);


  // Unlocked = Data Cleaning + every service the user chose.
  const unlocked = useMemo(() => new Set([ALWAYS_INCLUDED, ...props.selectedServices]), [props.selectedServices]);
  const byCategory = useMemo(() => {
    const groups: Record<string, SkillInfo[]> = {};
    skills
      .filter((s) => unlocked.has(s.category) && (!props.category || s.category === props.category))
      .forEach((s) => (groups[s.category_name] ??= []).push(s));
    return groups;
  }, [skills, unlocked, props.category]);
  // The workspace lists locked services in its own left pane, so no badges there.
  const locked = props.category || props.fixedSkill
    ? []
    : props.services.filter((s) => !unlocked.has(s.id) && skills.some((k) => k.category === s.id));
  // Keep a valid selection: default to the first unlocked skill.
  const available = Object.values(byCategory).flat();
  const skill = props.fixedSkill
    ? skills.find((s) => s.id === props.fixedSkill)
    : (available.find((s) => s.id === selected) ?? available[0]);

  function pick(id: string) {
    setSelected(id);
    setValues({});
    setResult(null);
    setError(null);
  }

  async function run(auto = false) {
    if (!skill) return;
    setBusy(true);
    setError(null);
    setHint(null);
    try {
      // Send only options the user filled in; empty = the skill's default.
      const params = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ""));
      const r = await runSkill(skill.id, input, params, groupId);
      setResult(r);
      props.onResult?.(r);
      if (typeof params.question === "string") props.onAsked?.(params.question, true);
    } catch (e) {
      setResult(null);
      if (!auto && typeof values.question === "string" && values.question) props.onAsked?.(values.question, false);
      const msg = e instanceof Error ? e.message : "The skill could not run.";
      // An automatic run that needs an option (e.g. which column to predict) isn't
      // an error: show the form with a hint instead.
      if (auto) setHint(`${msg} Fill in the options below, then run it.`);
      else setError(msg);
    } finally {
      setBusy(false);
    }
  }

  // An EDA report result is drawn as a report (null for every other skill).
  const eda = result ? edaReportOf(result.data) : null;

  // Run once as soon as the skill is known (workspace pages open on a result).
  const autoRan = useRef(false);
  useEffect(() => {
    if (props.autoRun && skill && !result && !autoRan.current) {
      autoRan.current = true;
      run(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.autoRun, skill?.id]);

  return (
    <div className="space-y-4">
      {!props.fixedSkill && (
      <div className="flex flex-wrap items-end gap-3">
        <label className="block min-w-64 flex-1 space-y-1 text-sm">
          <span className="font-medium">Skill</span>
          {/* One dropdown, grouped by service, in catalog order (AI last). */}
          <select
            className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm"
            value={skill?.id ?? ""}
            onChange={(e) => pick(e.target.value)}
          >
            {Object.entries(byCategory).map(([cat, list]) => (
              <optgroup key={cat} label={`${categoryEmoji(list[0].category)} ${cat}`}>
                {list.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        {skill && (
          <span className="mb-2 flex items-center gap-1.5 text-xs text-ink-muted">
            <span aria-hidden className="h-2.5 w-2.5 rounded-full" style={{ background: categoryColor(skill.category) }} />
            {skill.category_name}
          </span>
        )}
      </div>
      )}

      {locked.length > 0 && (
        <div>
          <p className="mb-2 text-xs text-ink-muted">More skills come with other services — add one to unlock it:</p>
          <div className="flex flex-wrap gap-2">
            {locked.map((s) => (
              <LockedBadge key={s.id} service={s} onAdd={props.onAddService} />
            ))}
          </div>
        </div>
      )}

      <div className="space-y-4">
        {hint && <p className="rounded-xl border border-line bg-surface-2 p-3 text-sm">{hint}</p>}
        {skill && (
          <Card>
            <div className="mb-3">
              <h3 className="font-semibold">{skill.label}</h3>
              <p className="text-sm text-ink-muted">{skill.description}</p>
              {skill.input === "tables" && (
                <p className="mt-1 text-xs text-ink-muted">Uses every table in your upload.</p>
              )}
              {skill.wants_raw && <p className="mt-1 text-xs text-ink-muted">Runs on your files as uploaded (before cleaning).</p>}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {Object.entries(skill.params)
                .filter(([, p]) => p.api_allowed)
                .map(([name, p]) => (
                  <ParamField
                    key={name}
                    name={name}
                    param={p}
                    columns={columns}
                    value={values[name] ?? ""}
                    onChange={(v) => setValues((old) => ({ ...old, [name]: v }))}
                  />
                ))}
            </div>
            <div className="mt-4">
              <Button onClick={() => run()} disabled={busy}>
                {busy ? "Running…" : "Run skill"}
              </Button>
            </div>
          </Card>
        )}

        <ErrorBox message={error} />

        {result && (
          <div className="space-y-4" aria-live="polite">
            <Card>
              <p className="font-medium">{result.summary}</p>
              {/* EDA findings are its Key Takeaways section, shown in the report below. */}
              {!eda && result.findings.length > 0 && (
                <ul className="mt-2 list-inside list-disc space-y-1 text-sm">
                  {result.findings.map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-xs text-ink-muted">Ran in {result.runtime_seconds.toFixed(2)} s.</p>
            </Card>

            {eda ? (
              // EDA report: sections in order, each with its own charts and table.
              <EdaReport report={eda} tables={result.tables} />
            ) : (
              <>
                {result.charts.map((c, i) => (
                  <Card key={i}>
                    <h4 className="mb-2 font-semibold">{c.title}</h4>
                    <ChartView spec={c} />
                  </Card>
                ))}

                {Object.entries(result.tables).map(([name, rows]) => (
                  <Card key={name}>
                    <h4 className="mb-2 font-semibold capitalize">{name.replace(/_/g, " ")}</h4>
                    <ResultTable rows={rows} />
                  </Card>
                ))}
              </>
            )}

            {Object.entries(result.artifacts).map(([name, text]) => {
              const [ext, type] = EXT[name.split("_")[0]] ?? ["txt", "text/plain"];
              return (
                <Card key={name}>
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <h4 className="font-semibold capitalize">{name.replace(/_/g, " ")}</h4>
                    <Button variant="secondary" onClick={() => saveText(`${result.skill}-${name}.${ext}`, text, type)}>
                      Download .{ext}
                    </Button>
                  </div>
                  {ext !== "html" && (
                    <pre className="max-h-72 overflow-auto rounded-xl bg-surface-2 p-3 text-xs leading-relaxed">{text}</pre>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

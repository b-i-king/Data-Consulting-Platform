// "Run a skill" — pick any data skill, fill in its options, see the result.
//
// The list of skills and each skill's options come from GET /api/skills, which
// reads every data_skills/<skill>/skill.py. Column options become dropdowns of
// the table's columns. Results show the summary, findings, charts, tables and
// downloadable outputs (Markdown reports, SQL, Mermaid diagrams, HTML).

"use client";

import { useEffect, useMemo, useState } from "react";
import { getSkills, runSkill, saveText } from "@/lib/api";
import { categoryColor } from "@/lib/categories";
import type { Cell, SkillInfo, SkillParam, SkillResult } from "@/lib/types";
import BarChart from "@/components/charts/BarChart";
import LineChart from "@/components/charts/LineChart";
import { Button, Card, ErrorBox } from "@/components/wizard/ui";

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

function ResultTable({ rows }: { rows: Record<string, Cell>[] }) {
  if (!rows.length) return <p className="text-sm text-ink-muted">No rows.</p>;
  const cols = Object.keys(rows[0]);
  const show = (v: Cell) => (v == null ? "—" : typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: 4 }) : String(v));
  return (
    <div className="max-h-72 overflow-auto rounded-xl border border-line">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-surface-2 text-left text-xs uppercase tracking-wide text-ink-muted">
          <tr>
            {cols.map((c) => (
              <th key={c} className="whitespace-nowrap px-3 py-2">
                {c.replace(/_/g, " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.slice(0, 200).map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} className="max-w-xs truncate whitespace-nowrap px-3 py-1.5" title={show(r[c])}>
                  {show(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SkillsPanel({ files, groupId, columns }: { files: File[]; groupId: number; columns: string[] }) {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selected, setSelected] = useState<string>("explore-data");
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<SkillResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSkills().then(setSkills).catch((e) => setError(e.message));
  }, []);

  const byCategory = useMemo(() => {
    const groups: Record<string, SkillInfo[]> = {};
    skills.forEach((s) => (groups[s.category_name] ??= []).push(s));
    return groups;
  }, [skills]);
  const skill = skills.find((s) => s.id === selected);

  function pick(id: string) {
    setSelected(id);
    setValues({});
    setResult(null);
    setError(null);
  }

  async function run() {
    if (!skill) return;
    setBusy(true);
    setError(null);
    try {
      // Send only options the user filled in; empty = the skill's default.
      const params = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ""));
      setResult(await runSkill(skill.id, files, params, groupId));
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : "The skill could not run.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[16rem_1fr]">
      <nav aria-label="Skills" className="space-y-4">
        {Object.entries(byCategory).map(([cat, list]) => (
          <div key={cat}>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide" style={{ color: categoryColor(list[0].category) }}>
              {cat}
            </h3>
            <ul className="space-y-0.5">
              {list.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => pick(s.id)}
                    aria-current={selected === s.id}
                    className={`w-full rounded-lg px-2 py-1 text-left text-sm ${
                      selected === s.id ? "bg-brand-soft font-medium" : "text-ink-muted hover:bg-surface-2 hover:text-ink"
                    }`}
                  >
                    {s.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="space-y-4">
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
              <Button onClick={run} disabled={busy}>
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
              {result.findings.length > 0 && (
                <ul className="mt-2 list-inside list-disc space-y-1 text-sm">
                  {result.findings.map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-xs text-ink-muted">Ran in {result.runtime_seconds.toFixed(2)} s.</p>
            </Card>

            {result.charts.map((c, i) => (
              <Card key={i}>
                <h4 className="mb-2 font-semibold">{c.title}</h4>
                {c.type === "line" ? (
                  <LineChart
                    ariaLabel={c.title}
                    x={c.x}
                    series={c.series.map((s, j) => ({
                      name: s.name,
                      values: s.values,
                      color: j === 0 ? "var(--color-chart-current)" : "var(--color-chart-previous)",
                    }))}
                  />
                ) : (
                  <BarChart categories={c.categories} values={c.values} valueLabel={c.value_label} />
                )}
              </Card>
            ))}

            {Object.entries(result.tables).map(([name, rows]) => (
              <Card key={name}>
                <h4 className="mb-2 font-semibold capitalize">{name.replace(/_/g, " ")}</h4>
                <ResultTable rows={rows} />
              </Card>
            ))}

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

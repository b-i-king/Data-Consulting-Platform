// "Transform data" — simple versions of Power BI's Power Query tools.
//
// Shows the table's APPLIED STEPS in order (what each one did, with a ✕ to
// remove it) and a form to add one more:
//   Use first row as headers   when the real column names are in the first data row
//   Remove top rows            title or blank rows above the header row
//   Replace values             fix one wrong value (450 -> 45), or fill blanks with a value
//   Change data type           number / date / text (values that don't fit become blank)
//   Unpivot columns            Jan, Feb, Mar columns -> one "month" column + one "value" column
//   Pivot column               the reverse: one column per value, combining with sum / average / count / first
// Renaming and removing columns are in "Column names" and the missing-values
// table; duplicates are removed automatically. Steps are sent to the server as
// `transforms` and re-run on every request (nothing is stored) — see
// api/services/transforms.py.

"use client";

import { useState } from "react";
import type { AppliedStep, CleaningReport, TransformStep } from "@/lib/types";
import { Button, ErrorBox } from "./ui";

const LABEL: Record<TransformStep["op"], string> = {
  promote_headers: "Use first row as headers",
  remove_top_rows: "Remove top rows",
  replace_values: "Replace values",
  change_type: "Change data type",
  unpivot: "Unpivot columns",
  pivot: "Pivot column",
  outliers: "Unusual values rule",
};

const HELP: Partial<Record<TransformStep["op"], string>> = {
  promote_headers:
    "When the preview's row 1 holds the real column names — e.g. your file starts with a title line, so the title became the column names.",
  remove_top_rows:
    "Removes rows from the top of the data (the preview's row 1 is the first). Use it for extra title or note rows, then “Use first row as headers” if needed.",
  replace_values: "Fix a wrong value everywhere it appears (450 → 45). Leave “Find” empty to fill blanks; leave “Replace with” empty to blank a value out.",
  change_type: "Values that don't fit the new type become blank — you can then remove those rows in the missing-values table.",
  unpivot: "Turns the selected columns into rows: Jan, Feb, Mar → a “month” column and a “value” column. Best for charts and analysis.",
  pivot: "The reverse of unpivot: each value of one column becomes its own column, filled from a values column.",
};

const field = "w-full rounded-xl border border-line bg-surface px-2 py-1.5 text-sm";

export default function TransformSteps(props: {
  report: CleaningReport;
  steps: TransformStep[];
  onApplySteps: (steps: TransformStep[]) => Promise<void>;
}) {
  const cols = props.report.column_reports.filter((c) => c.action !== "dropped");
  const done: Record<number, AppliedStep> = Object.fromEntries((props.report.transforms_applied ?? []).map((s) => [s.step, s]));
  const [open, setOpen] = useState(props.steps.length > 0);
  const [op, setOp] = useState<TransformStep["op"]>("replace_values");
  const [column, setColumn] = useState(cols[0]?.key ?? "");
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [count, setCount] = useState("1");
  const [to, setTo] = useState<"number" | "date" | "text">("number");
  const [picked, setPicked] = useState<string[]>([]);
  const [attribute, setAttribute] = useState("attribute");
  const [value, setValue] = useState("value");
  const [values, setValues] = useState(cols[1]?.key ?? "");
  const [agg, setAgg] = useState<"sum" | "mean" | "count" | "first">("sum");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(steps: TransformStep[]) {
    setBusy(true);
    setError(null);
    try {
      await props.onApplySteps(steps);
    } catch (e) {
      setError(e instanceof Error ? e.message : "We couldn't apply that step.");
    } finally {
      setBusy(false);
    }
  }

  function build(): TransformStep | null {
    switch (op) {
      case "promote_headers":
        return { op };
      case "remove_top_rows":
        return Number(count) >= 1 ? { op, count: Math.floor(Number(count)) } : null;
      case "replace_values":
        return column ? { op, column, find, replace } : null;
      case "change_type":
        return column ? { op, column, to } : null;
      case "unpivot":
        return picked.length ? { op, columns: picked, attribute, value } : null;
      case "pivot":
        return column && values && column !== values ? { op, column, values, agg } : null;
      default:
        return null;
    }
  }
  const next = build();

  const columnSelect = (label: string, val: string, onChange: (v: string) => void) => (
    <label className="block space-y-1 text-sm">
      <span>{label}</span>
      <select className={field} value={val} onChange={(e) => onChange(e.target.value)}>
        {cols.map((c) => (
          <option key={c.key} value={c.key}>
            {c.column}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">Transform data</h3>
        <button type="button" className="text-sm text-brand hover:underline" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Hide tools" : "Show tools"}
        </button>
      </div>
      <p className="text-xs text-ink-muted">
        Simple versions of Power BI&apos;s Power Query tools: headers, top rows, replace values, data types, unpivot and
        pivot. Each change is a step you can remove.
      </p>

      {props.steps.length > 0 && (
        <ol aria-label="Applied steps" className="space-y-1.5">
          {props.steps.map((s, i) => (
            <li key={i} className="flex items-start justify-between gap-3 rounded-xl border border-line bg-surface px-3 py-2 text-sm">
              <span>
                <span className="mr-2 font-mono text-xs text-ink-muted">{i + 1}.</span>
                <span className="font-medium">{LABEL[s.op]}</span>
                {done[i + 1] && <span className="block text-xs text-ink-muted">{done[i + 1].text}</span>}
              </span>
              <button
                type="button"
                aria-label={`Remove step ${i + 1}: ${LABEL[s.op]}`}
                disabled={busy}
                onClick={() => run(props.steps.filter((_, j) => j !== i))}
                className="shrink-0 rounded-full px-2 text-ink-muted hover:text-bad"
              >
                ✕
              </button>
            </li>
          ))}
        </ol>
      )}

      {open && (
        <div className="space-y-3 rounded-2xl border border-line bg-surface p-4 shadow-[var(--shadow-card)]">
          <label className="block max-w-sm space-y-1 text-sm">
            <span className="font-medium">Add a step</span>
            <select className={field} value={op} onChange={(e) => setOp(e.target.value as TransformStep["op"])}>
              {(["promote_headers", "remove_top_rows", "replace_values", "change_type", "unpivot", "pivot"] as const).map((o) => (
                <option key={o} value={o}>
                  {LABEL[o]}
                </option>
              ))}
            </select>
          </label>
          {HELP[op] && <p className="text-xs text-ink-muted">{HELP[op]}</p>}

          <div className="grid gap-3 sm:grid-cols-3">
            {op === "remove_top_rows" && (
              <label className="block space-y-1 text-sm">
                <span>How many rows</span>
                <input className={field} inputMode="numeric" value={count} onChange={(e) => setCount(e.target.value.replace(/\D/g, ""))} />
              </label>
            )}
            {(op === "replace_values" || op === "change_type") && columnSelect("Column", column, setColumn)}
            {op === "replace_values" && (
              <>
                <label className="block space-y-1 text-sm">
                  <span>Find</span>
                  <input className={field} value={find} placeholder="(blank cells)" onChange={(e) => setFind(e.target.value)} />
                </label>
                <label className="block space-y-1 text-sm">
                  <span>Replace with</span>
                  <input className={field} value={replace} placeholder="(make blank)" onChange={(e) => setReplace(e.target.value)} />
                </label>
              </>
            )}
            {op === "change_type" && (
              <label className="block space-y-1 text-sm">
                <span>New type</span>
                <select className={field} value={to} onChange={(e) => setTo(e.target.value as typeof to)}>
                  <option value="number">Number</option>
                  <option value="date">Date</option>
                  <option value="text">Text</option>
                </select>
              </label>
            )}
            {op === "pivot" && (
              <>
                {columnSelect("Column whose values become new columns", column, setColumn)}
                {columnSelect("Values column", values, setValues)}
                <label className="block space-y-1 text-sm">
                  <span>Combine with</span>
                  <select className={field} value={agg} onChange={(e) => setAgg(e.target.value as typeof agg)}>
                    <option value="sum">Sum</option>
                    <option value="mean">Average</option>
                    <option value="count">Count</option>
                    <option value="first">First value</option>
                  </select>
                </label>
              </>
            )}
            {op === "unpivot" && (
              <>
                <label className="block space-y-1 text-sm">
                  <span>New column for the old column names</span>
                  <input className={field} value={attribute} onChange={(e) => setAttribute(e.target.value)} />
                </label>
                <label className="block space-y-1 text-sm">
                  <span>New column for the values</span>
                  <input className={field} value={value} onChange={(e) => setValue(e.target.value)} />
                </label>
              </>
            )}
          </div>

          {op === "unpivot" && (
            <fieldset className="text-sm">
              <legend className="mb-1">Columns to turn into rows</legend>
              <div className="flex flex-wrap gap-2">
                {cols.map((c) => (
                  <label key={c.key} className="flex items-center gap-1.5 rounded-full border border-line px-3 py-1">
                    <input
                      type="checkbox"
                      className="accent-[var(--color-brand)]"
                      checked={picked.includes(c.key)}
                      onChange={(e) => setPicked((p) => (e.target.checked ? [...p, c.key] : p.filter((k) => k !== c.key)))}
                    />
                    <span className="font-mono text-xs">{c.column}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          <Button onClick={() => next && run([...props.steps, next])} disabled={busy || !next}>
            {busy ? "Applying…" : "Add step"}
          </Button>
        </div>
      )}
      <ErrorBox message={error} />
    </div>
  );
}

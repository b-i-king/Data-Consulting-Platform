// Data Engineering -> "Load into database": PREVIEW how the data will be
// stored, then ACCEPT (or not) before anything is stored.
//
// Every file is stored in ONE shared table, fact.account_data (db/schema.sql):
// IDs, dates and categories are kept exactly as they are, and every number
// that is measured becomes its own row described by metric_type, subtype,
// unit and value (the "unified data" design, docs/DATA_MODEL.md). This screen
// shows, before anything is stored:
//   1. what the rows are (sales, expenses, feedback, a list...) — guessed,
//      changeable,
//   2. how each column will be stored: its role (metric / ID / kept as is),
//      its shared name, and for metrics what they measure, with how sure the
//      guess is — every part changeable (each change re-previews),
//   3. every step with WHY it's needed,
//   4. the first rows exactly as they will be stored, and the round-trip
//      check: rebuilding the table from those rows gives back every cell.
// Then the client (or their consultant) ticks the storage consent and presses
// "Accept and load" — or "Don't load", which stores nothing. The server only
// loads if the data and choices still match this preview (its fingerprint).
//
// SEVERAL TABLES IN ONE UPLOAD: "Combine with another table (optional)" —
// STACK them (append rows) or JOIN them on a shared ID (each row gets the other
// table's columns), before storing (api/services/combine.py). Files with the
// same columns were already stacked by the cleaning step. A suggested key
// (from "How your files fit together") is filled in for a join.
//
// REPLACE MODE (`replaceSource`, from Settings → "Update my most recent
// upload"): the new file replaces that stored file under the same name; rows
// no longer in the file are removed too.
//
// The "Stored for ..." card underneath lists what's stored per file, with
// "Download as CSV" and "Delete stored data" (the plan holder or their
// consultant; it also withdraws the storage consent). Terms: /storage-terms.
//
// Server side: api/routers/warehouse.py, api/services/warehouse_loader.py.

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  deleteStoredData,
  getStoredDataFile,
  getWarehouseSummary,
  loadToWarehouse,
  previewLoad,
  type LoadChoices,
} from "@/lib/api";
import { fmtDate, fmtDateTime, fmtInt } from "@/lib/format";
import type {
  Cell,
  CleaningInput,
  ColumnPlanEntry,
  ColumnRole,
  CombineSpec,
  LoadPlan,
  Relationship,
  LoadResult,
  PreviewTable,
  WarehouseCounts,
  WarehouseSummary,
} from "@/lib/types";
import { Button, Card, ConfirmDialog, ErrorBox } from "@/components/wizard/ui";

const show = (v: Cell) => (v === null || v === undefined ? "" : String(v));
const inputCls = "w-full rounded-lg border border-line bg-surface px-2 py-1 text-xs";

/** The roles a person can pick for a column (a date column is found automatically). */
const ROLE_CHOICES: { id: ColumnRole; label: string }[] = [
  { id: "metric", label: "Metric (a number to measure)" },
  { id: "id", label: "ID" },
  { id: "category", label: "Keep as is" },
];

/** Badge color for how sure a metric description is. */
function confidenceClass(c: string | null): string {
  if (c === "low") return "bg-bad/15 text-bad";
  if (c === "medium") return "bg-warn/15 text-warn";
  return "bg-good/15 text-good"; // mapped, set by you, high
}

function Preview({ title, table }: { title: string; table: PreviewTable }) {
  return (
    <div className="space-y-1">
      <p className="text-sm font-medium">
        {title}{" "}
        <span className="font-normal text-ink-muted">
          — first {Math.min(table.rows.length, table.total)} of {fmtInt(table.total)} row(s)
        </span>
      </p>
      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="min-w-full text-xs">
          <thead className="bg-surface-2">
            <tr>
              {table.columns.map((c) => (
                <th key={c} className="whitespace-nowrap px-2 py-1.5 text-left font-semibold">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} className="border-t border-line">
                {table.columns.map((c) => (
                  <td key={c} className="max-w-[18rem] truncate whitespace-nowrap px-2 py-1.5 font-mono" title={show(r[c])}>
                    {show(r[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** One editable text field that only reports its value when you leave it (or press Enter). */
function EditOnBlur(props: { value: string; disabled: boolean; label: string; onCommit: (v: string) => void }) {
  const [draft, setDraft] = useState(props.value);
  const commit = () => draft.trim() !== props.value && props.onCommit(draft.trim());
  return (
    <input
      aria-label={props.label}
      className={inputCls}
      value={draft}
      disabled={props.disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
    />
  );
}

/** How each column will be stored, with every guess changeable. */
function ColumnPlanTable(props: {
  plan: ColumnPlanEntry[];
  busy: boolean;
  onRole: (column: string, role: ColumnRole) => void;
  onMetric: (column: string, part: "type" | "subtype" | "unit", value: string) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="min-w-full text-xs">
        <thead className="bg-surface-2 text-left">
          <tr>
            <th className="px-2 py-1.5 font-semibold">Your column</th>
            <th className="px-2 py-1.5 font-semibold">Stored as</th>
            <th className="px-2 py-1.5 font-semibold">Role</th>
            <th className="px-2 py-1.5 font-semibold">Measures (type)</th>
            <th className="px-2 py-1.5 font-semibold">Which one (subtype)</th>
            <th className="px-2 py-1.5 font-semibold">Unit</th>
            <th className="px-2 py-1.5 font-semibold">How sure</th>
          </tr>
        </thead>
        <tbody>
          {props.plan.map((p) => (
            // The key includes the description, so the inputs reset when the server's answer changes it.
            <tr key={`${p.column}|${p.role}|${p.metric_type}|${p.metric_subtype}|${p.metric_unit}`} className="border-t border-line align-top">
              <td className="whitespace-nowrap px-2 py-1.5 font-mono">{p.column}</td>
              <td className="whitespace-nowrap px-2 py-1.5 font-mono text-ink-muted" title={p.why}>
                {p.field_name}
              </td>
              <td className="px-2 py-1.5">
                {p.role === "date" ? (
                  <span>Date</span>
                ) : (
                  <select
                    aria-label={`Role of ${p.column}`}
                    className={inputCls}
                    value={p.role}
                    disabled={props.busy}
                    onChange={(e) => props.onRole(p.column, e.target.value as ColumnRole)}
                  >
                    {ROLE_CHOICES.filter((r) => r.id !== "metric" || p.value_type === "number").map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                )}
              </td>
              {p.role === "metric" ? (
                <>
                  <td className="px-2 py-1.5">
                    <EditOnBlur label={`Type of ${p.column}`} value={p.metric_type ?? ""} disabled={props.busy}
                      onCommit={(v) => props.onMetric(p.column, "type", v)} />
                  </td>
                  <td className="px-2 py-1.5">
                    <EditOnBlur label={`Subtype of ${p.column}`} value={p.metric_subtype ?? ""} disabled={props.busy}
                      onCommit={(v) => props.onMetric(p.column, "subtype", v)} />
                  </td>
                  <td className="px-2 py-1.5">
                    <EditOnBlur label={`Unit of ${p.column}`} value={p.metric_unit ?? ""} disabled={props.busy}
                      onCommit={(v) => props.onMetric(p.column, "unit", v)} />
                  </td>
                  <td className="px-2 py-1.5">
                    <span className={`whitespace-nowrap rounded-full px-2 py-0.5 ${confidenceClass(p.confidence)}`} title={p.why}>
                      {p.confidence}
                    </span>
                  </td>
                </>
              ) : (
                <td colSpan={4} className="px-2 py-1.5 text-ink-muted">
                  Kept exactly as it is, on every row of the record
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Other tables of the same upload, to stack or join with the one being loaded. */
export interface UploadTable {
  group_id: number;
  label: string;
  columns: string[];
}

/** "Combine with another table (optional)": none, stack, or join on a key. */
function CombinePicker(props: {
  groupId: number;
  tables: UploadTable[];
  relationships: Relationship[];
  value: CombineSpec | null;
  busy: boolean;
  onChange: (spec: CombineSpec | null) => void;
}) {
  const me = props.tables.find((t) => t.group_id === props.groupId);
  const others = props.tables.filter((t) => t.group_id !== props.groupId);
  const [mode, setMode] = useState<"none" | "union" | "join">(props.value?.mode ?? "none");
  const [other, setOther] = useState<number>(props.value?.groups[0] ?? others[0]?.group_id ?? -1);
  const otherTable = others.find((t) => t.group_id === other);
  // A suggested key between the two tables ("How your files fit together").
  const suggested = props.relationships.find(
    (r) => r.method === "key" && [r.left_group, r.right_group].includes(props.groupId) && [r.left_group, r.right_group].includes(other),
  )?.key_columns[0];
  const [leftOn, setLeftOn] = useState(props.value?.left_on ?? suggested ?? me?.columns[0] ?? "");
  const [rightOn, setRightOn] = useState(props.value?.right_on ?? suggested ?? otherTable?.columns[0] ?? "");
  const [how, setHow] = useState<"left" | "inner">(props.value?.how ?? "left");
  if (!me || others.length === 0) return null;

  const apply = () =>
    props.onChange(
      mode === "none"
        ? null
        : mode === "union"
          ? { mode: "union", groups: others.map((t) => t.group_id) }
          : { mode: "join", groups: [other], left_on: leftOn, right_on: rightOn, how },
    );
  const select = "rounded-xl border border-line bg-surface px-3 py-1.5 text-sm";

  return (
    <Card className="space-y-3">
      <h3 className="font-semibold">Combine with another table (optional)</h3>
      <p className="text-sm text-ink-muted">
        Your upload has {props.tables.length} tables. Store <strong>{me.label}</strong> on its own, stack the tables into
        one (same kind of rows), or join another table to it by a shared ID (adds its columns to each row).
      </p>
      <div className="flex flex-wrap gap-2 text-sm">
        {(["none", "union", "join"] as const).map((m) => (
          <label key={m} className={`flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1.5 ${mode === m ? "border-brand bg-brand-soft" : "border-line"}`}>
            <input type="radio" name="combine-mode" checked={mode === m} onChange={() => setMode(m)} />
            {m === "none" ? "On its own" : m === "union" ? "Stack all tables (append rows)" : "Join another table"}
          </label>
        ))}
      </div>
      {mode === "join" && (
        <div className="grid gap-2 text-sm sm:grid-cols-2">
          <label className="space-y-1">
            <span className="block">Table to join</span>
            <select className={select} value={other} onChange={(e) => setOther(Number(e.target.value))}>
              {others.map((t) => (
                <option key={t.group_id} value={t.group_id}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="block">Keep</span>
            <select className={select} value={how} onChange={(e) => setHow(e.target.value as "left" | "inner")}>
              <option value="left">Every row of {me.label}</option>
              <option value="inner">Only rows with a match</option>
            </select>
          </label>
          <label className="space-y-1">
            <span className="block">Shared ID in {me.label}</span>
            <select className={select} value={leftOn} onChange={(e) => setLeftOn(e.target.value)}>
              {me.columns.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="block">…matches in {otherTable?.label}</span>
            <select className={select} value={rightOn} onChange={(e) => setRightOn(e.target.value)}>
              {(otherTable?.columns ?? []).map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          {suggested && <p className="text-xs text-ink-muted sm:col-span-2">Suggested key: {suggested}</p>}
        </div>
      )}
      <Button variant="secondary" disabled={props.busy} onClick={apply}>
        Update the preview
      </Button>
    </Card>
  );
}

export default function WarehouseLoad(props: {
  token: string;
  input: CleaningInput;
  groupId: number;
  clientName: string;
  /** The plan holder (or their consultant) may delete; team members may not. */
  canDelete: boolean;
  /** Tell the workspace what's stored now (for its "Use my stored data" buttons). */
  onStoredChanged?: (counts: WarehouseCounts) => void;
  /** Every table of the upload and the suggested keys between them (for "Combine"). */
  tables?: UploadTable[];
  relationships?: Relationship[];
  /** "Update my most recent upload": replace this stored source with the new file. */
  replaceSource?: string | null;
  /** Called after a successful load (e.g. to close a pop-up). */
  onLoaded?: () => void;
  /** Hide the "Stored for ..." card (when shown inside a pop-up). */
  compact?: boolean;
}) {
  const [askDelete, setAskDelete] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [plan, setPlan] = useState<LoadPlan | null>(null);
  const [choices, setChoices] = useState<LoadChoices>({
    groupId: props.groupId,
    eventType: "",
    choices: {},
    replaceSource: props.replaceSource ?? null,
  });
  const [consent, setConsent] = useState(false);
  // Records another source already stored (the preview's duplicates): skipped unless unticked.
  const [skipDuplicates, setSkipDuplicates] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [declined, setDeclined] = useState(false);
  const [result, setResult] = useState<LoadResult | null>(null);
  const [stored, setStored] = useState<WarehouseSummary | null>(null);

  const { onStoredChanged } = props;
  const refreshSummary = useCallback(() => {
    getWarehouseSummary(props.token)
      .then((s) => {
        setStored(s);
        onStoredChanged?.(s.warehouse);
      })
      .catch(() => {});
  }, [props.token, onStoredChanged]);

  async function download(source?: string) {
    try {
      const file = await getStoredDataFile(props.token, source);
      const url = URL.createObjectURL(file);
      const a = document.createElement("a");
      a.href = url;
      a.download = file.name;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed.");
    }
  }

  async function removeAll() {
    setAskDelete(false);
    setBusy(true);
    setError(null);
    try {
      const r = await deleteStoredData(props.token);
      setNote(
        `Deleted ${fmtInt(r.deleted.rows)} stored row(s) from ${fmtInt(r.deleted.sources)} file(s). ` +
          "Storage consent is withdrawn; loading again asks for it anew.",
      );
      setResult(null);
      setConsent(false);
      refreshSummary();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    } finally {
      setBusy(false);
    }
  }

  // Preview again whenever a choice changes (nothing is stored by a preview).
  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError(null);
    setResult(null); // a new preview needs a new decision
    setDeclined(false);
    previewLoad(props.token, props.input, choices)
      .then((p) => !cancelled && setPlan(p))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "We couldn't build the preview."))
      .finally(() => !cancelled && setBusy(false));
    return () => {
      cancelled = true;
    };
  }, [props.token, props.input, choices]);

  useEffect(refreshSummary, [refreshSummary]);

  /** A column's role changed: send the full lists of metric and ID columns. */
  function changeRole(column: string, role: ColumnRole) {
    if (!plan) return;
    const roles = Object.fromEntries(plan.column_plan.map((p) => [p.column, p.role])) as Record<string, ColumnRole>;
    roles[column] = role;
    const pick = (r: ColumnRole) => Object.entries(roles).filter(([, x]) => x === r).map(([c]) => c);
    setChoices((c) => ({ ...c, choices: { ...c.choices, metric_columns: pick("metric"), id_columns: pick("id") } }));
  }

  /** A metric's type, subtype or unit changed: remember the whole description for that column. */
  function changeMetric(column: string, part: "type" | "subtype" | "unit", value: string) {
    const p = plan?.column_plan.find((x) => x.column === column);
    if (!p) return;
    const current = { type: p.metric_type ?? "", subtype: p.metric_subtype, unit: p.metric_unit ?? "" };
    const next = { ...current, [part]: part === "subtype" ? value || null : value };
    setChoices((c) => ({ ...c, choices: { ...c.choices, metric_map: { ...(c.choices.metric_map ?? {}), [column]: next } } }));
  }

  async function accept() {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try {
      const dupes = (plan.duplicates?.records ?? 0) > 0;
      const r = await loadToWarehouse(props.token, props.input, choices, plan.fingerprint, consent, dupes && skipDuplicates);
      setResult(r);
      refreshSummary();
      props.onLoaded?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The load failed. Nothing was stored.");
    } finally {
      setBusy(false);
    }
  }

  const needsConsent = plan ? !plan.storage.consented : true;

  return (
    <div className="space-y-5">
      <Card className="space-y-2">
        <h2 className="font-semibold">
          {props.replaceSource ? `Replace “${props.replaceSource}” — check it first` : "Store this data — check it first"}
        </h2>
        {props.replaceSource && (
          <p className="rounded-xl bg-warn/10 p-2 text-xs">
            This file replaces your stored <strong>{props.replaceSource}</strong>: rows that are no longer in the file are
            removed too. It keeps the same name.
          </p>
        )}
        <p className="text-sm text-ink-muted">
          Below is exactly how your data will be stored, why, and how it will look. <strong>Nothing is stored until you
          press “Accept and load”.</strong>
        </p>
        {plan?.storage.mode === "memory" && (
          <p className="rounded-xl bg-warn/10 p-2 text-xs">
            Test mode: no database is connected, so loads are kept in the server&apos;s memory and forgotten when it
            restarts. Set DATABASE_URL to store them in Supabase.
          </p>
        )}
        {plan?.storage.mode === "unavailable" && (
          <p className="rounded-xl bg-bad/10 p-2 text-xs">The database isn&apos;t reachable right now, so loading is off.</p>
        )}
      </Card>

      {!props.replaceSource && (props.tables?.length ?? 0) > 1 && (
        <CombinePicker
          groupId={props.groupId}
          tables={props.tables ?? []}
          relationships={props.relationships ?? []}
          value={choices.combine ?? null}
          busy={busy}
          onChange={(combine) => setChoices((c) => ({ ...c, combine }))}
        />
      )}

      {plan && (
        <Card className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">What are these rows?</span>
              <select
                className="w-full rounded-xl border border-line bg-surface px-3 py-2"
                value={plan.event_type}
                disabled={busy}
                onChange={(e) => setChoices((c) => ({ ...c, eventType: e.target.value }))}
              >
                {plan.event_types.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="self-end text-xs text-ink-muted">
              File: <strong>{plan.source_name}</strong> · {fmtInt(plan.counts.rows_in)} cleaned row(s)
            </p>
          </div>
          <div className="space-y-2">
            <h3 className="text-sm font-semibold">How each column is stored (we guessed — change anything)</h3>
            <ColumnPlanTable plan={plan.column_plan} busy={busy} onRole={changeRole} onMetric={changeMetric} />
            {plan.counts.needs_review > 0 && (
              <p className="text-xs text-warn">
                {fmtInt(plan.counts.needs_review)} metric(s) are a guess (medium / low): please check what they measure.
              </p>
            )}
          </div>
        </Card>
      )}

      {busy && !plan && <p className="text-sm text-ink-muted">Working out how to store it…</p>}
      <ErrorBox message={error} />

      {plan && (
        <Card className="space-y-3">
          <h3 className="font-semibold">What happens to your data, and why</h3>
          <ol className="space-y-3">
            {plan.steps.map((s, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand-soft text-xs font-semibold">
                  {i + 1}
                </span>
                <span>
                  <span className="font-medium">{s.title}</span>
                  <span className="block text-ink-muted">Why: {s.why}</span>
                </span>
              </li>
            ))}
          </ol>
          {plan.problem && <p className="rounded-xl bg-bad/10 p-3 text-sm">{plan.problem}</p>}
        </Card>
      )}

      {plan?.ready && (
        <Card className="space-y-4">
          <h3 className="font-semibold">How it will look in the database</h3>
          <p className="text-sm">
            <strong>{fmtInt(plan.counts.rows_to_load)}</strong> row(s) to store from {fmtInt(plan.counts.records)} row(s)
            of your table
            {plan.counts.metrics > 0 && <> ({fmtInt(plan.counts.metrics)} number column(s) → one row per number)</>}
            {plan.counts.duplicates > 0 && (
              <span className="text-ink-muted"> · {fmtInt(plan.counts.duplicates)} exact duplicate(s) stored once</span>
            )}
            .
          </p>
          {plan.tables.account_data && <Preview title="fact.account_data" table={plan.tables.account_data} />}
          <p className={`rounded-xl p-2 text-xs ${plan.round_trip.ok ? "bg-good/10" : "bg-bad/10"}`}>
            {plan.round_trip.ok
              ? `Nothing is lost: rebuilding your table from these rows gives back all ${fmtInt(plan.round_trip.cells_checked)} cells exactly.`
              : `Rebuilding the table gave ${fmtInt(plan.round_trip.mismatches)} different cell(s), so loading is off.`}
          </p>

          {!result && !declined && (
            <div className="space-y-3 border-t border-line pt-4">
              {(plan.duplicates?.records ?? 0) > 0 && plan.duplicates && (
                <div role="status" className="space-y-2 rounded-xl border border-warn/40 bg-warn/10 p-3 text-sm">
                  <p>
                    <strong>
                      {fmtInt(plan.duplicates.records)} of these {fmtInt(plan.duplicates.of)} records are already stored
                    </strong>{" "}
                    from {plan.duplicates.sources.join(", ")} (same date, amount and reference number).
                  </p>
                  <label className="flex items-start gap-2">
                    <input type="checkbox" className="mt-0.5" checked={skipDuplicates}
                      onChange={(e) => setSkipDuplicates(e.target.checked)} />
                    <span>Load only the new records (recommended: nothing is counted twice)</span>
                  </label>
                </div>
              )}
              {needsConsent && (
                <label className="flex items-start gap-3 text-sm">
                  <input type="checkbox" className="mt-0.5" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                  <span>
                    Store this data for <strong>{props.clientName}</strong> in the managed database. It&apos;s kept
                    private to this account and can be deleted anytime.{" "}
                    <Link href="/storage-terms" target="_blank" className="text-brand-ink underline">
                      Storage terms
                    </Link>{" "}
                    <span className="text-ink-muted">({plan.storage.terms_version})</span>
                  </span>
                </label>
              )}
              <div className="flex flex-wrap gap-3">
                <Button
                  disabled={busy || plan.storage.mode === "unavailable" || (needsConsent && !consent)}
                  onClick={accept}
                >
                  {busy ? "Working…" : "Accept and load"}
                </Button>
                <Button variant="secondary" disabled={busy} onClick={() => setDeclined(true)}>
                  Don&apos;t load
                </Button>
              </div>
            </div>
          )}
          {declined && !result && (
            <p className="rounded-xl bg-surface-2 p-3 text-sm">
              Nothing was stored. Change the choices above to see a new preview, or{" "}
              <button type="button" className="text-brand-ink underline" onClick={() => setDeclined(false)}>
                load after all
              </button>
              .
            </p>
          )}
          {result && (
            <p role="status" className="rounded-xl border border-good/40 bg-good/10 p-3 text-sm">
              Stored <strong>{fmtInt(result.result.rows_loaded)}</strong> new row(s)
              {result.result.rows_updated > 0 && <>, updated {fmtInt(result.result.rows_updated)} changed row(s)</>}
              {result.result.rows_already_stored > 0 && (
                <> — {fmtInt(result.result.rows_already_stored)} were already stored unchanged (no duplicates)</>
              )}
              .
              {(result.result.records_skipped_as_duplicates ?? 0) > 0 && (
                <> {fmtInt(result.result.records_skipped_as_duplicates ?? 0)} record(s) were left out: another source already had them.</>
              )}
              {(result.result.matches_to_review ?? 0) > 0 && (
                <> {fmtInt(result.result.matches_to_review ?? 0)} name(s) look like customers you already have — confirm them in
                  Settings → Integrations.</>
              )}
            </p>
          )}
        </Card>
      )}

      {stored && !props.compact && (
        <Card className="space-y-2">
          <h3 className="font-semibold">Stored for {props.clientName}</h3>
          {!stored.warehouse.set_up ? (
            <p className="text-sm text-bad">
              The data table isn&apos;t in the database yet: run <code>python scripts/setup_database.py</code>.
            </p>
          ) : stored.warehouse.rows === 0 ? (
            <p className="text-sm text-ink-muted">Nothing stored yet.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {stored.warehouse.sources.map((s) => (
                <li key={`${s.source_system}/${s.source_name}`} className="flex flex-wrap items-center gap-x-2">
                  <strong>{s.source_name}</strong>
                  <span className="text-ink-muted">
                    {s.label} · {fmtInt(s.records)} row(s) of the table, {fmtInt(s.rows)} stored row(s)
                    {s.first_date && s.last_date && <> · {fmtDate(s.first_date)} – {fmtDate(s.last_date)}</>}
                  </span>
                  <button type="button" className="text-xs text-brand-ink underline" onClick={() => download(s.source_name)}>
                    Download CSV
                  </button>
                </li>
              ))}
            </ul>
          )}
          {stored.warehouse.rows > 0 && (
            <div className="flex flex-wrap gap-2 pt-1">
              <Button variant="secondary" disabled={busy} onClick={() => download()}>
                Download everything as CSV
              </Button>
              {props.canDelete && (
                <Button variant="secondary" disabled={busy} onClick={() => setAskDelete(true)} className="text-bad">
                  Delete stored data
                </Button>
              )}
            </div>
          )}
          {note && (
            <p role="status" className="text-sm">
              {note}
            </p>
          )}
          {askDelete && (
            <ConfirmDialog
              title={`Delete everything stored for ${props.clientName}?`}
              confirmLabel="Delete stored data"
              onCancel={() => setAskDelete(false)}
              onConfirm={removeAll}
            >
              <p>
                This permanently deletes {fmtInt(stored.warehouse.rows)} stored row(s) from{" "}
                {fmtInt(stored.warehouse.sources.length)} file(s), and withdraws the storage consent. It can&apos;t be
                undone — download a CSV first if you want a copy. The account itself stays (see{" "}
                <Link href="/data-deletion" className="text-brand-ink underline">Data deletion</Link> to close it).
              </p>
            </ConfirmDialog>
          )}
          {stored.loads.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-ink-muted">Recent loads ({stored.loads.length})</summary>
              <ul className="mt-1 space-y-0.5">
                {stored.loads.map((l) => (
                  <li key={l.id}>
                    {fmtDateTime(l.started_at)} · {l.source_name} · {l.status} · {fmtInt(l.rows_loaded)} new
                    {l.rows_updated > 0 && `, ${fmtInt(l.rows_updated)} updated`}
                    {l.rows_skipped > 0 && `, ${fmtInt(l.rows_skipped)} unchanged`}
                    {l.error && ` · ${l.error}`}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </Card>
      )}
    </div>
  );
}

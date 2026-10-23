// The AI page: BEACON, the BOL Group's data assistant — "Ask your data" as a
// conversation, with this month's allowance.
//
// Modeled on Bolt (the BOL Group's gaming analyst panel) at dashboard size:
//
//   ┌─ conversation ───────────────────────────────┐ ┌─ side column ───────────┐
//   │ 🔦 Beacon · Free · Gemini 2.5 Flash-Lite      │ │ Questions this month    │
//   │                                              │ │ ████████░░  14 / 20     │
//   │  you: total amount by store                  │ │ resets 11/01/2026       │
//   │  ▸ Midtown leads with $18,210 ...            │ │ Free 20 · Premium 200   │
//   │    How: sum of amount by store (1,204 rows)  │ ├─────────────────────────┤
//   │    ┌ store   │ value ┐ (the answer table)    │ │ Try asking …            │
//   │                                              │ │ You ask most …          │
//   │  [ suggestions before the first question ]   │ │ About this table …      │
//   │  [ Ask about your data…              Ask ]   │ │ How answers work …      │
//   └──────────────────────────────────────────────┘ └─────────────────────────┘
//
// Rules (api/services/ai_assistant.py): FREE for everyone (20 questions a
// month), PREMIUM with the AI package (200, a larger model), admins without a
// limit — and they can simulate free / premium to see what clients see. A
// consultant uses the allowance of the client they're working on (shown on the
// card). Answers come only from the open table, with the calculation shown;
// a Google Gemini model (when the platform has a key) sees only column names
// and category values, never rows. The bar is gold under 70% used, amber from 70%,
// red from 90%.

"use client";

import { useEffect, useRef, useState } from "react";
import { askAi, getAiUsage, getDashboards, getDashboardSuggestions } from "@/lib/api";
import SampleQuestions from "./SampleQuestions";
import { fmtDate, fmtInt } from "@/lib/format";
import type { AiUsage, CleaningInput, SkillResult } from "@/lib/types";
import { Button, Card } from "@/components/wizard/ui";
import Icon from "@/components/ui/Icon";

interface Message {
  role: "you" | "assistant";
  text: string;
  result?: SkillResult | null;
  failed?: boolean;
}

const FALLBACK_SUGGESTIONS = ["total amount by store", "top 5 product by amount", "how many orders by payment_method",
  "average amount by month"];
const MAX_LENGTH = 500;

const TIER_LABEL: Record<AiUsage["tier"], string> = { admin: "Unlimited", premium: "Premium", free: "Free" };

/** Gold under 70% used, amber from 70%, red from 90% (semantic colors, both themes). */
function barColor(used: number, limit: number) {
  const share = used / Math.max(limit, 1);
  return share >= 0.9 ? "bg-bad" : share >= 0.7 ? "bg-warn" : "bg-brand";
}

export default function AssistantPanel(props: {
  token: string;
  input: CleaningInput;
  groupId: number;
  /** The open table: its name, size and columns (from the cleaning report). */
  tableLabel: string;
  rows: number;
  columns: { name: string; type: string }[];
  isAdmin: boolean;
  /** The plan holder (or their consultant) may add the AI package. */
  canUpgrade: boolean;
  onUpgrade: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [usage, setUsage] = useState<AiUsage | null>(null);
  const [simulate, setSimulate] = useState<"" | "free" | "premium">("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [frequent, setFrequent] = useState<{ question: string; times: number }[]>([]);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getAiUsage(props.token, simulate || undefined).then(setUsage).catch(() => setUsage(null));
  }, [props.token, simulate]);

  // Questions worth asking of THIS table (their key questions, what they ask often, their industry).
  useEffect(() => {
    getDashboardSuggestions(props.token, props.columns)
      .then((r) => setSuggestions(r.suggestions.map((s) => s.question).slice(0, 5)))
      .catch(() => setSuggestions([]));
    getDashboards(props.token)
      .then((d) => setFrequent(d.frequent.slice(0, 5)))
      .catch(() => setFrequent([]));
  }, [props.token, props.columns]);

  async function ask(question: string) {
    const q = question.trim().slice(0, MAX_LENGTH);
    if (!q || busy) return;
    setMessages((m) => [...m, { role: "you", text: q }]);
    setText("");
    setBusy(true);
    try {
      const r = await askAi(props.token, props.input, q, props.groupId, simulate || undefined);
      setUsage(r.usage);
      setMessages((m) => [
        ...m,
        r.answered && r.result
          ? { role: "assistant", text: r.result.summary, result: r.result }
          : { role: "assistant", text: r.message ?? "I can't answer that from this table yet.", failed: true },
      ]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: e instanceof Error ? e.message : "That didn't work. Try again.", failed: true }]);
      getAiUsage(props.token, simulate || undefined).then(setUsage).catch(() => {});
    } finally {
      setBusy(false);
      setTimeout(() => bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
    }
  }

  const used = usage?.used ?? 0;
  const limit = usage?.limit ?? null;
  const outOfQuestions = !!usage && limit !== null && used >= limit;
  const prompts = suggestions.length ? suggestions : FALLBACK_SUGGESTIONS;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_19rem]">
      {/* ── The conversation ── */}
      <Card className="flex min-h-[28rem] min-w-0 flex-col p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-5 py-3">
          <div className="flex items-center gap-2">
            <span className="text-brand-ink"><Icon name="beacon" className="h-6 w-6" /></span>
            <div>
              <h2 className="font-semibold">Beacon</h2>
              <p className="text-xs text-ink-muted">
                Your data assistant by BOL ·{" "}
                {usage?.engine === "gemini" ? `Powered by Google ${geminiName(usage.model)}` : "Built-in answers"} ·
                answers only from {props.tableLabel}, with the math shown
              </p>
            </div>
          </div>
          {usage && (
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
              usage.tier === "free" ? "bg-surface-2 text-ink-muted" : "bg-brand-soft text-brand-ink"}`}>
              {TIER_LABEL[usage.tier]}
              {usage.simulating && " (simulated)"}
            </span>
          )}
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4" aria-live="polite">
          {messages.length === 0 && (
            <div className="space-y-3">
              <p className="text-sm text-ink-muted">
                Hi, I&apos;m Beacon. Ask me about your data in everyday words — totals, averages, top 5s, by store or by
                month, for a year. Try one:
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {prompts.map((s) => (
                  <button key={s} type="button" disabled={busy || outOfQuestions} onClick={() => ask(s)}
                    className="rounded-xl border border-line px-3 py-2 text-left text-sm text-ink-muted transition-colors hover:border-brand hover:text-brand-ink disabled:opacity-50">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) =>
            m.role === "you" ? (
              <p key={i} className="ml-auto w-fit max-w-[85%] rounded-2xl bg-brand-soft px-3 py-2 text-sm font-medium">
                {m.text}
              </p>
            ) : (
              <div key={i} className={`max-w-[95%] space-y-2 rounded-2xl border px-4 py-3 text-sm ${
                m.failed ? "border-warn/40 bg-warn/10" : "border-line bg-surface-2"}`}>
                <p className="font-medium">{m.text}</p>
                {m.result?.findings.map((f) => (
                  <p key={f} className="text-xs text-ink-muted">{f}</p>
                ))}
                {m.result?.tables.answer && <AnswerTable rows={m.result.tables.answer} />}
              </div>
            ),
          )}
          {busy && <p className="w-fit animate-pulse rounded-2xl bg-surface-2 px-3 py-2 text-sm text-ink-muted">Beacon is working it out…</p>}
          <div ref={bottom} />
        </div>

        <form className="flex gap-2 border-t border-line px-5 py-3" onSubmit={(e) => { e.preventDefault(); void ask(text); }}>
          <input
            aria-label="Ask Beacon a question about your data"
            className="min-w-0 flex-1 rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand"
            placeholder={outOfQuestions ? "This month's Beacon questions are used up" : "Ask Beacon…"}
            value={text}
            maxLength={MAX_LENGTH}
            disabled={busy || outOfQuestions}
            onChange={(e) => setText(e.target.value)}
          />
          <Button type="submit" disabled={busy || outOfQuestions || !text.trim()}>Ask</Button>
        </form>
      </Card>

      {/* ── The side column: allowance, ideas, the table, how it works ── */}
      <div className="min-w-0 space-y-4">
        {/* Sample questions for the client's industry (or any other), with this table's columns. */}
        <SampleQuestions token={props.token} columns={props.columns} onPick={(q) => setText(q)} />
        <Card className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Beacon questions this month</h3>
            {props.isAdmin && (
              <select aria-label="See the assistant as" value={simulate}
                onChange={(e) => setSimulate(e.target.value as "" | "free" | "premium")}
                className="rounded-lg border border-line bg-surface px-1.5 py-0.5 text-xs text-ink-muted">
                <option value="">Admin view</option>
                <option value="premium">Simulate: Premium</option>
                <option value="free">Simulate: Free</option>
              </select>
            )}
          </div>
          {!usage ? (
            <p className="text-xs text-ink-muted">Loading…</p>
          ) : usage.is_unlimited ? (
            <p className="text-sm">
              <span className="font-mono text-2xl font-semibold">{fmtInt(used)}</span>{" "}
              <span className="text-ink-muted">asked · no limit</span>
            </p>
          ) : (
            <>
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-2xl font-semibold">
                  {fmtInt(used)}<span className="text-base text-ink-muted"> / {fmtInt(limit ?? 0)}</span>
                </span>
                <span className="text-xs text-ink-muted">resets {fmtDate(usage.reset_date)}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-surface-2" role="progressbar"
                aria-valuenow={used} aria-valuemin={0} aria-valuemax={limit ?? 0}>
                <div className={`h-full rounded-full transition-all ${barColor(used, limit ?? 1)}`}
                  style={{ width: `${Math.min((used / Math.max(limit ?? 1, 1)) * 100, 100)}%` }} />
              </div>
              <p className="text-xs text-ink-muted">{fmtInt(usage.left ?? 0)} left this month</p>
            </>
          )}
          {usage?.acting_for && (
            <p className="rounded-lg bg-consultant-soft px-2 py-1 text-xs text-consultant">
              Counts toward {usage.acting_for}&apos;s allowance.
            </p>
          )}
          {/* Free vs. Premium, side by side (the current one highlighted). */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            {(["free", "premium"] as const).map((t) => (
              <div key={t} className={`rounded-xl border p-2 ${usage?.tier === t ? "border-brand bg-brand-soft" : "border-line"}`}>
                <div className="font-semibold">{TIER_LABEL[t]}</div>
                <div className="text-ink-muted">{t === "free" ? "20 questions a month" : "200 questions a month, the stronger model"}</div>
              </div>
            ))}
          </div>
          {usage?.tier === "free" && props.canUpgrade && (
            <Button className="w-full" onClick={props.onUpgrade}>Get Premium (the AI package)</Button>
          )}
        </Card>

        <Card className="space-y-2">
          <h3 className="text-sm font-semibold">Try asking</h3>
          <ul className="space-y-1">
            {prompts.map((s) => (
              <li key={s}>
                <button type="button" disabled={busy || outOfQuestions} onClick={() => ask(s)}
                  className="w-full truncate rounded-lg px-2 py-1 text-left text-xs text-ink-muted hover:bg-surface-2 hover:text-brand-ink disabled:opacity-50">
                  {s}
                </button>
              </li>
            ))}
          </ul>
        </Card>

        {frequent.length > 0 && (
          <Card className="space-y-2">
            <h3 className="text-sm font-semibold">You ask most</h3>
            <ul className="space-y-1 text-xs">
              {frequent.map((f) => (
                <li key={f.question} className="flex justify-between gap-2">
                  <button type="button" disabled={busy || outOfQuestions} onClick={() => ask(f.question)}
                    className="truncate text-left text-ink-muted hover:text-brand-ink disabled:opacity-50">
                    {f.question}
                  </button>
                  <span className="shrink-0 font-mono text-ink-muted">{f.times}×</span>
                </li>
              ))}
            </ul>
            <p className="text-xs text-ink-muted">Asked often? Save it as a dashboard tab on the Dashboard page.</p>
          </Card>
        )}

        <Card className="space-y-2">
          <h3 className="text-sm font-semibold">About this table</h3>
          <p className="text-xs text-ink-muted">
            {props.tableLabel} · {fmtInt(props.rows)} rows · {fmtInt(props.columns.length)} columns
          </p>
          <div className="flex flex-wrap gap-1">
            {props.columns.slice(0, 24).map((c) => (
              <button key={c.name} type="button" onClick={() => setText((t) => (t ? `${t} ${c.name}` : c.name))}
                title="Add to your question"
                className="rounded-full bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-ink-muted hover:text-brand-ink">
                {c.name}
              </button>
            ))}
          </div>
        </Card>

        <Card className="space-y-1 text-xs text-ink-muted">
          <h3 className="text-sm font-semibold text-ink">How Beacon answers</h3>
          <p>• Only from this table — if it can&apos;t answer, it says so.</p>
          <p>• Every answer shows its calculation, so you can check it.</p>
          <p>• Read-only: your data is never changed or stored.</p>
          {usage?.engine === "gemini" && <p>• Gemini sees column names and category values — never your rows.</p>}
        </Card>
      </div>
    </div>
  );
}

/** "gemini-2.5-flash-lite" -> "Gemini 2.5 Flash-Lite" (for the header). */
function geminiName(model: string | null) {
  if (!model) return "Gemini";
  return model.replace(/^gemini/, "Gemini").split("-")
    .map((w, i) => (i === 0 ? w : w === "lite" ? "Lite" : /^\d/.test(w) ? w : w[0].toUpperCase() + w.slice(1)))
    .join(" ").replace(" Lite", "-Lite");
}

/** The answer's table (first 12 rows). */
function AnswerTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return null;
  const cols = Object.keys(rows[0]);
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full text-xs">
        <thead className="bg-surface-2">
          <tr>{cols.map((c) => <th key={c} className="px-2 py-1 text-left font-semibold">{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 12).map((r, i) => (
            <tr key={i} className="border-t border-line">
              {cols.map((c) => (
                <td key={c} className="px-2 py-1 font-mono tabular-nums">
                  {typeof r[c] === "number" ? (r[c] as number).toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(r[c] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 12 && <p className="px-2 py-1 text-[11px] text-ink-muted">and {fmtInt(rows.length - 12)} more</p>}
    </div>
  );
}

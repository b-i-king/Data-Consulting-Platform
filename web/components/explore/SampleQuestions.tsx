"use client";

// ── Beacon: sample questions by industry ──────────────────────────────────────
// A short list of questions Beacon can answer, for the client's industry (or
// any other — sports and gaming included), written with THIS table's column
// names (api/services/prompt_library.py). Clicking one puts it in the question
// box so it can be adjusted before asking. A question marked "edit first" uses
// a stand-in word ("product") because the table has no such column.

import { useEffect, useState } from "react";
import { getSamplePrompts } from "@/lib/api";
import { Card } from "@/components/wizard/ui";

type Sample = { question: string; why: string; ready: boolean };

export default function SampleQuestions({ token, columns, onPick }: {
  token: string;
  columns: { name: string; type: string }[];
  onPick: (question: string) => void;
}) {
  const [industry, setIndustry] = useState<string | null>(null);   // null = the client's own industry
  const [data, setData] = useState<{ industry: string | null; industries: { key: string; label: string }[]; questions: Sample[] } | null>(null);

  useEffect(() => {
    getSamplePrompts(token, columns, industry ?? undefined).then(setData).catch(() => setData(null));
  }, [token, columns, industry]);

  if (!data) return null;
  return (
    <Card className="space-y-2">
      <h3 className="text-sm font-semibold">Sample questions</h3>
      <label className="grid gap-1 text-xs text-ink-muted">
        For your industry, or browse another:
        <select className="rounded-xl border border-line bg-surface px-2 py-1.5 text-sm text-ink"
          value={industry ?? data.industry ?? ""} onChange={(e) => setIndustry(e.target.value || "")}>
          <option value="">General business</option>
          {data.industries.map((i) => (
            <option key={i.key} value={i.key}>{i.label}</option>
          ))}
        </select>
      </label>
      <ul className="space-y-1.5">
        {data.questions.map((s) => (
          <li key={s.question}>
            <button type="button" onClick={() => onPick(s.question)} title={s.why}
              className="w-full rounded-xl border border-line px-3 py-2 text-left text-sm transition-colors hover:border-brand hover:text-brand-ink">
              {s.question}
              <span className="block text-xs text-ink-muted">
                {s.why}{s.ready ? "" : " · edit first: use your column names"}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

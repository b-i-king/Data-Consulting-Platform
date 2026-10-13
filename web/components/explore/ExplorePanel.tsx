// "Explore your data" — shown under the cleaning results.
//
// Two tabs for the currently selected table (group):
//   Dashboard    — only when the table has a date column (build-dashboard skill)
//   Run a skill  — any of the 30 data skills
// Both send the files kept in browser memory to the API; nothing is stored.

"use client";

import { useState } from "react";
import type { GroupOutput } from "@/lib/types";
import Dashboard from "./Dashboard";
import SkillsPanel from "./SkillsPanel";

export default function ExplorePanel({ files, group }: { files: File[]; group: GroupOutput }) {
  const hasDates = group.report.column_reports.some((c) => c.inferred_type.startsWith("date"));
  const [tab, setTab] = useState<"dashboard" | "skills">(hasDates ? "dashboard" : "skills");

  return (
    <section aria-labelledby="explore-title" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="explore-title" className="text-xl font-semibold">
            Explore your data
          </h2>
          <p className="text-sm text-ink-muted">Free to try — runs on {group.label}.</p>
        </div>
        <div role="tablist" className="flex gap-1 rounded-full border border-line bg-surface p-1">
          {hasDates && (
            <button
              role="tab"
              aria-selected={tab === "dashboard"}
              onClick={() => setTab("dashboard")}
              className={`rounded-full px-4 py-1.5 text-sm ${tab === "dashboard" ? "bg-brand font-semibold text-on-brand" : "text-ink-muted hover:text-ink"}`}
            >
              Dashboard
            </button>
          )}
          <button
            role="tab"
            aria-selected={tab === "skills"}
            onClick={() => setTab("skills")}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === "skills" ? "bg-brand font-semibold text-on-brand" : "text-ink-muted hover:text-ink"}`}
          >
            Run a skill
          </button>
        </div>
      </div>
      {tab === "dashboard" && hasDates ? (
        <Dashboard files={files} groupId={group.group_id} />
      ) : (
        <SkillsPanel files={files} groupId={group.group_id} columns={group.preview.columns} />
      )}
    </section>
  );
}

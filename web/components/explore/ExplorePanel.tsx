// "Explore your data" — shown under the cleaning results.
//
// Two tabs for the currently selected table (group):
//   Dashboard    — only when the table has a date column (build-dashboard
//                  skill); part of the Data Visualization service
//   Run a skill  — the data skills of the services the user has chosen
// Tables with fewer than 30 rows (before or after cleaning) get only the Data
// Cleaning skills, with a note saying why (api/services/cleaning.py, Step 7).
// Services that aren't chosen appear as locked badges the user can add
// (LockedService.tsx). Both tabs send the files kept in browser memory, plus
// the missing-value choices, to the API; nothing is stored.

"use client";

import { useState } from "react";
import type { CleaningInput, GroupOutput, Service } from "@/lib/types";
import Dashboard from "./Dashboard";
import { LockedCard } from "./LockedService";
import SkillsPanel from "./SkillsPanel";
import Icon from "@/components/ui/Icon";

const DASHBOARD_SERVICE = "data_visualization";

export default function ExplorePanel(props: {
  input: CleaningInput;
  group: GroupOutput;
  services: Service[];
  selectedServices: string[];
  onAddService: (id: string) => void;
}) {
  const { group } = props;
  // Under 30 rows (before or after cleaning): only the cleaning tools are offered.
  const ready = group.report.analysis_ready;
  const hasDates = ready && group.report.column_reports.some((c) => c.inferred_type.startsWith("date") && c.action !== "dropped");
  const [tab, setTab] = useState<"dashboard" | "skills">(hasDates ? "dashboard" : "skills");
  const vizService = props.services.find((s) => s.id === DASHBOARD_SERVICE);
  const dashboardUnlocked = props.selectedServices.includes(DASHBOARD_SERVICE);

  const tabCls = (on: boolean) =>
    `rounded-full px-4 py-1.5 text-sm ${on ? "bg-brand font-semibold text-on-brand" : "text-ink-muted hover:text-ink"}`;

  return (
    <section aria-labelledby="explore-title" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="explore-title" className="text-xl font-semibold">
            Explore your data
          </h2>
          <p className="text-sm text-ink-muted">Runs on {group.label}, with your cleaning choices applied.</p>
        </div>
        <div role="tablist" className="flex gap-1 rounded-full border border-line bg-surface p-1">
          {hasDates && (
            <button role="tab" aria-selected={tab === "dashboard"} onClick={() => setTab("dashboard")} className={tabCls(tab === "dashboard")}>
              {!dashboardUnlocked && <Icon name="lock" className="mr-1 h-3.5 w-3.5 align-[-2px]" />}Dashboard
            </button>
          )}
          <button role="tab" aria-selected={tab === "skills"} onClick={() => setTab("skills")} className={tabCls(tab === "skills")}>
            Run a skill
          </button>
        </div>
      </div>
      {/* Both tabs stay mounted (one is hidden) so switching tabs keeps the
          dashboard's filters and doesn't rebuild it. */}
      {hasDates && (
        <div hidden={tab !== "dashboard"}>
          {dashboardUnlocked || !vizService ? (
            <Dashboard input={props.input} groupId={group.group_id} />
          ) : (
            <LockedCard service={vizService} feature="The dashboard" onAdd={props.onAddService} />
          )}
        </div>
      )}
      {!ready && (
        <p role="status" className="rounded-2xl border border-warn/40 bg-warn/10 p-4 text-sm">
          <strong>The dashboard and analysis skills need at least 30 rows.</strong> {group.report.analysis_note} The
          cleaning tools below still work.
        </p>
      )}
      <div hidden={tab !== "skills"}>
        <SkillsPanel
          category={ready ? undefined : "data_cleaning"}
          input={props.input}
          groupId={group.group_id}
          columns={group.preview.columns}
          services={props.services}
          selectedServices={props.selectedServices}
          onAddService={props.onAddService}
        />
      </div>
    </section>
  );
}

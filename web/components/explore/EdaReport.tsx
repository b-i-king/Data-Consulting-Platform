// The EDA report view: the eda-report skill's `data.report`, drawn section by
// section in the classic EDA order (Data Overview → … → Key Takeaways).
//
// Each section is one card: the headline, a plain-English explanation, bullet
// points, its charts (histogram, box plot, scatter, heatmap, bar — drawn by
// ChartView) and, when the section names one, a table from SkillResult.tables.
// A section that doesn't apply to this data (e.g. no number columns) is shown
// muted, with the explanation saying why. A row of links at the top jumps to
// each section. The same report is also a downloadable HTML file (the
// "html_report" artifact the Skills panel offers below this view).

"use client";

import type { Cell, EdaReport as EdaReportData, EdaReportSection } from "@/lib/types";
import ChartView from "@/components/charts/ChartView";
import { Card } from "@/components/wizard/ui";
import ResultTable from "./ResultTable";

/** Anchor id for a section, so the links at the top can jump to it. */
const anchor = (s: EdaReportSection) => `eda-${s.id}`;

function Section({ section, tables }: { section: EdaReportSection; tables: Record<string, Record<string, Cell>[]> }) {
  const takeaways = section.id === "takeaways";
  const rows = section.table ? tables[section.table] : undefined;
  return (
    <Card className={takeaways ? "border-brand" : ""}>
      <section id={anchor(section)} aria-labelledby={`${anchor(section)}-h`} className="scroll-mt-4 space-y-3">
        <h3 id={`${anchor(section)}-h`} className={`font-mono text-lg font-semibold ${takeaways ? "text-brand" : ""}`}>
          {section.title}
        </h3>
        <p className={`text-sm leading-relaxed ${section.applicable ? "" : "text-ink-muted"}`}>{section.explanation}</p>
        {section.bullets.length > 0 && (
          <ul className="list-inside list-disc space-y-1 text-sm">
            {section.bullets.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
        )}
        {section.charts.length > 0 && (
          // One chart per row on phones, two side by side on wider screens.
          <div className={`grid gap-4 ${section.charts.length > 1 ? "md:grid-cols-2" : ""}`}>
            {section.charts.map((c, i) => (
              <figure key={i} className="space-y-2 rounded-xl border border-line p-3">
                <figcaption className="text-sm font-medium">{c.title}</figcaption>
                <ChartView spec={c} />
              </figure>
            ))}
          </div>
        )}
        {rows && rows.length > 0 && <ResultTable rows={rows} />}
      </section>
    </Card>
  );
}

export default function EdaReport(props: { report: EdaReportData; tables: Record<string, Record<string, Cell>[]> }) {
  const { report } = props;
  return (
    <div className="space-y-4">
      <Card className="space-y-3">
        <div>
          <h2 className="font-mono text-xl font-semibold text-brand">{report.title}</h2>
          <p className="text-sm text-ink-muted">{report.subtitle}</p>
        </div>
        <nav aria-label="Report sections" className="flex flex-wrap gap-2">
          {report.sections.map((s) => (
            <a
              key={s.id}
              href={`#${anchor(s)}`}
              className={`rounded-full border border-line px-3 py-1 text-xs hover:bg-surface-2 ${
                s.applicable ? "" : "text-ink-muted"
              }`}
            >
              {s.title}
            </a>
          ))}
        </nav>
      </Card>
      {report.sections.map((s) => (
        <Section key={s.id} section={s} tables={props.tables} />
      ))}
    </div>
  );
}

/** The report inside a skill result, if this result is an EDA report (else null). */
export function edaReportOf(data: Record<string, unknown>): EdaReportData | null {
  const r = data?.report as EdaReportData | undefined;
  return r && Array.isArray(r.sections) ? r : null;
}

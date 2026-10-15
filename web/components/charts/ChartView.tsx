// Draw ANY chart spec a skill sends back, by its `type`.
//
// Skills (data_skills/_core.py) describe charts as plain data: line_chart,
// bar_chart, histogram_chart, box_chart, scatter_chart, heatmap_chart. This
// component picks the matching drawing component, so the Skills panel and the
// EDA report view don't each need their own if/else list. A new chart type
// needs: a spec in web/lib/types.ts (ChartSpec), a component in this folder,
// and one more case below.

"use client";

import type { ChartSpec } from "@/lib/types";
import BarChart from "./BarChart";
import BoxPlot from "./BoxPlot";
import Heatmap from "./Heatmap";
import Histogram from "./Histogram";
import LineChart from "./LineChart";
import ScatterPlot from "./ScatterPlot";

export default function ChartView({ spec }: { spec: ChartSpec }) {
  switch (spec.type) {
    case "line":
      return (
        <LineChart
          ariaLabel={spec.title}
          x={spec.x}
          series={spec.series.map((s, j) => ({
            name: s.name,
            values: s.values,
            // First series = the current period, the rest = comparisons.
            color: j === 0 ? "var(--color-chart-current)" : "var(--color-chart-previous)",
          }))}
        />
      );
    case "bar":
      return <BarChart categories={spec.categories} values={spec.values} valueLabel={spec.value_label} />;
    case "histogram":
      return <Histogram spec={spec} />;
    case "box":
      return <BoxPlot spec={spec} />;
    case "scatter":
      return <ScatterPlot spec={spec} />;
    case "heatmap":
      return <Heatmap spec={spec} />;
    default:
      // A spec type this version of the site doesn't know yet (newer API).
      return <p className="text-sm text-ink-muted">This chart type can&apos;t be shown here yet.</p>;
  }
}

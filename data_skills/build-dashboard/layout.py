"""
The dashboard's layout: KPI row above the fold, then rows of containers — built from building blocks.

WHAT THIS FILE DOES
-------------------
There's no fixed dashboard template. templates/layouts.json (from the Canva
design) defines the building blocks, and plan() picks them for THIS data:

  1. KPI ROW (above the fold) — 1 to 4 headline cards, best first:
       the main total, the industry KPIs the data supports (industry.py),
       transactions, the average per transaction.
     One card only -> a trend container sits beside it in the same row
     (Canva page 7).
  2. CONTAINER ROWS — every visual, in this priority:
       a. line chart of the main metric over time (trends)
       b. horizontal bar charts of categories (by type, product, store...)
       c. complementary: trends of the other KPIs, then more breakdowns
     packed into the row patterns (full, half_half, two_thirds_third,
     third_two_thirds, thirds): a trend takes the wide slot, bars the narrow
     ones; three bars share a row in thirds; leftovers take a full row.
     As many rows as there are visuals — mix and match, nothing is fixed.

  plan(kpi_row_candidates, visuals) -> {"kpi_row": [...], "rows": [...]}
  kpi_style(requested, percent_metrics, targets, has_previous) -> "number" | "change" | "gauge"

KPI CARD STYLES (one per dashboard, so the row looks consistent)
----------------------------------------------------------------
  number  the title and the big number — when there's no earlier period to compare
  change  the title, the number and its change vs. the previous range (the usual)
  gauge   a half-circle dial from 0 to 100% with the target band marked; the row
          then holds ONLY percentage KPIs (a dial needs a 0–100 scale; dollars
          don't have one) and the main total leads the containers instead
Percentages always show their change in percentage POINTS (60% -> 62% is "+2.0 pts").

Each row is {"pattern": "two_thirds_third", "spans": [4, 2], "items": [visual, ...]};
a visual is {"kind": "line", "metric": id, "title"} or
{"kind": "hbar", "dimension": column, "metric": id, "title"}.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

MAX_KPIS = 4


@lru_cache(maxsize=1)
def blocks() -> dict:
    """templates/layouts.json: the KPI rows and row patterns."""
    return json.loads(Path(__file__).with_name("templates").joinpath("layouts.json").read_text(encoding="utf-8"))


def _row(pattern: str, items: list[dict]) -> dict:
    return {"pattern": pattern, "spans": blocks()["row_patterns"][pattern]["spans"], "items": items}


def pack(visuals: list[dict]) -> list[dict]:
    """
    Pack visuals (already in priority order) into rows of the Canva patterns.

    Lines (trends) want width, bars (categories) are fine narrow:
      line + bar          -> two_thirds_third
      bar + line          -> third_two_thirds (alternates with the above)
      bar, bar, bar       -> thirds
      bar, bar / line, line -> half_half
      one left over       -> full

    Args:
        visuals: list[dict], each with "kind" "line" or "hbar".

    Returns:
        list[dict]: rows (see the module notes).

    Example:
        >>> [r["pattern"] for r in pack([{"kind": "line"}, {"kind": "hbar"}, {"kind": "hbar"}])]
        ['two_thirds_third', 'full']
    """
    lines = [v for v in visuals if v["kind"] == "line"]
    bars = [v for v in visuals if v["kind"] != "line"]
    rows: list[dict] = []
    flip = False
    while lines and bars:
        if len(bars) >= 3 and not flip and rows:
            rows.append(_row("thirds", bars[:3]))
            bars = bars[3:]
            continue
        line, bar = lines.pop(0), bars.pop(0)
        rows.append(_row("third_two_thirds", [bar, line]) if flip else _row("two_thirds_third", [line, bar]))
        flip = not flip
    while len(bars) >= 3:
        rows.append(_row("thirds", bars[:3]))
        bars = bars[3:]
    while len(bars) >= 2:
        rows.append(_row("half_half", bars[:2]))
        bars = bars[2:]
    while len(lines) >= 2:
        rows.append(_row("half_half", lines[:2]))
        lines = lines[2:]
    for v in bars + lines:
        rows.append(_row("full", [v]))
    return rows


def plan(kpi_candidates: list[str], visuals: list[dict]) -> dict:
    """
    The whole layout: the KPI row (up to 4, best first) and the container rows.

    Args:
        kpi_candidates: metric ids in priority order (duplicates ignored).
        visuals: container visuals in priority order (see pack()).

    Returns:
        dict: {"kpi_row": [metric ids], "kpi_side": visual | None (the trend beside a
        single KPI), "rows": [...]}.

    Example:
        >>> plan(["__total__"], [{"kind": "line", "metric": "__total__"}])["kpi_side"]["kind"]
        'line'
    """
    seen: list[str] = []
    for m in kpi_candidates:
        if m not in seen:
            seen.append(m)
    kpi_row = seen[:MAX_KPIS]
    rest = list(visuals)
    side = None
    if len(kpi_row) == 1:
        side = next((v for v in rest if v["kind"] == "line"), None)   # Canva page 7: KPI + trend in one row
        if side:
            rest.remove(side)
    return {"kpi_row": kpi_row, "kpi_side": side, "rows": pack(rest)}


KPI_STYLES = ("number", "change", "gauge")


def kpi_style(requested: str | None, percent_metrics: list[str], targets: dict, has_previous: bool) -> str:
    """
    The ONE card style for this dashboard's KPI row (see the module notes).

    Gauge = a row of percentage KPIs on 0–100% dials. "auto" picks it when the
    data has at least two percentage KPIs and at least one has a target band
    (e.g. a law firm's billable utilization 75–85% and realization 85%+); the
    caller then fills the KPI row with those percentages only, and the main
    total leads the containers below as its trend.

    Args:
        requested: "auto" (or None), "number", "change" or "gauge".
        percent_metrics: the candidate KPIs that are percentages.
        targets: metric -> [low, high] healthy band.
        has_previous: some range has an earlier period to compare with.

    Returns:
        str: "number", "change" or "gauge" (a requested gauge with no
        percentage KPIs falls back to "change").

    Example:
        >>> kpi_style("auto", ["util", "real"], {"util": [75, 85]}, True)
        'gauge'
    """
    if requested in ("number", "change"):
        return requested
    if requested == "gauge":
        return "gauge" if percent_metrics else "change"
    if len(percent_metrics) >= 2 and any(m in targets for m in percent_metrics):
        return "gauge"
    return "change" if has_previous else "number"

"""
data-enrichment — fill gaps with reference data (Data Strategy & Discovery).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). Two parts:

  1. DIAGNOSE — look at the columns and propose outside data that could join:
       date      → calendar features, US federal holidays, weather, local events
       state     → census region, population, median income (U.S. Census API)
       ZIP code  → demographics (Census ACS), distance to store
       NAICS     → industry benchmarks (SBA, BLS)
       product   → category hierarchy, cost data
  2. ENRICH (offline, built in) — actually add the columns that need no
     outside service:
       from a date column:  day_of_week, is_weekend, month, quarter, year,
                            is_us_federal_holiday, holiday_name
       from a state column: census_region, census_division

The enriched table comes back in tables["enriched_preview"]; the plan for
outside sources comes back as findings.
"""

from __future__ import annotations

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from data_skills._core import Param, SkillResult, SkillSpec, as_datetime, date_columns, records

CENSUS = {  # state -> (region, division), U.S. Census Bureau definitions
    **{s: ("Northeast", "New England") for s in ("CT", "ME", "MA", "NH", "RI", "VT")},
    **{s: ("Northeast", "Middle Atlantic") for s in ("NJ", "NY", "PA")},
    **{s: ("Midwest", "East North Central") for s in ("IL", "IN", "MI", "OH", "WI")},
    **{s: ("Midwest", "West North Central") for s in ("IA", "KS", "MN", "MO", "NE", "ND", "SD")},
    **{s: ("South", "South Atlantic") for s in ("DE", "DC", "FL", "GA", "MD", "NC", "SC", "VA", "WV")},
    **{s: ("South", "East South Central") for s in ("AL", "KY", "MS", "TN")},
    **{s: ("South", "West South Central") for s in ("AR", "LA", "OK", "TX")},
    **{s: ("West", "Mountain") for s in ("AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY")},
    **{s: ("West", "Pacific") for s in ("AK", "CA", "HI", "OR", "WA")},
}

SKILL = SkillSpec(
    id="data-enrichment",
    title="Data-gap & enrichment plan",
    description="Add calendar/holiday and census-region columns, and propose outside data that could fill gaps.",
    params={
        "date_column": Param("column", None, "Date column to enrich (auto-detected)"),
        "state_column": Param("column", None, "2-letter US state column (auto-detected)"),
        "goal": Param("str", None, "What you're trying to analyze (shapes the suggestions)"),
    },
)


def run(df: pd.DataFrame, date_column=None, state_column=None, goal=None) -> SkillResult:
    out = df.copy()
    added, plan = [], []
    cols_lower = {str(c).lower(): c for c in df.columns}

    dcol = date_column or next(iter(date_columns(df)), None)
    if dcol:
        d = as_datetime(out[dcol])
        holidays = USFederalHolidayCalendar().holidays(start=d.min(), end=d.max(), return_name=True)
        out["day_of_week"] = d.dt.day_name()
        out["is_weekend"] = d.dt.dayofweek >= 5
        out["month"] = d.dt.month
        out["quarter"] = d.dt.quarter
        out["year"] = d.dt.year
        out["holiday_name"] = d.dt.normalize().map(holidays)
        out["is_us_federal_holiday"] = out["holiday_name"].notna()
        added += ["day_of_week", "is_weekend", "month", "quarter", "year", "is_us_federal_holiday", "holiday_name"]
        plan.append(f"'{dcol}': join daily weather (NOAA) and local events to explain busy/slow days.")

    scol = state_column or next((cols_lower[k] for k in ("state", "state_code", "province") if k in cols_lower), None)
    if scol:
        codes = out[scol].astype("string").str.strip().str.upper()
        out["census_region"] = codes.map(lambda s: CENSUS.get(s, (None, None))[0])
        out["census_division"] = codes.map(lambda s: CENSUS.get(s, (None, None))[1])
        added += ["census_region", "census_division"]
        plan.append(f"'{scol}': join U.S. Census population and median household income for per-capita comparisons.")

    if any(k in cols_lower for k in ("zip", "zip_code", "postal_code", "zipcode")):
        plan.append("ZIP code: join Census ACS demographics and compute distance to each store.")
    if any("naics" in k for k in cols_lower):
        plan.append("NAICS code: join SBA size standards and BLS industry benchmarks.")
    if any(k in cols_lower for k in ("product", "item", "sku")):
        plan.append("Product: add a category hierarchy and unit cost to analyze margin, not just revenue.")
    if any(k in cols_lower for k in ("customer_id", "customer", "email")):
        plan.append("Customer: link accounting (QuickBooks) records with fuzzy matching to add lifetime value.")
    if goal:
        plan.insert(0, f"Goal: {goal} — prioritize the sources below that explain it.")

    summary = (f"Added {len(added)} column(s): {', '.join(added)}." if added
               else "No built-in enrichment applies (no date or state column).")
    return SkillResult(skill=SKILL.id, summary=summary, findings=plan,
                       tables={"enriched_preview": records(out, 200)}, data={"added_columns": added})

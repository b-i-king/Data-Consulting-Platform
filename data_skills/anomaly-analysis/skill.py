"""
anomaly-analysis — outliers and impossible values (Data Cleaning).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). Four sweeps:

  1. Outliers        — robust z-score (median and MAD) above 3.5, plus the
                       classic 1.5×IQR fence; robust methods aren't fooled by
                       the outliers themselves.
  2. Impossible      — negatives in columns that are almost always positive
                       (amounts, quantities, ages), dates in the future or
                       before 1900.
  3. Rare categories — category values that make up < 0.5% of rows
                       (often typos: "Midtwon" next to "Midtown").
  4. Spikes          — days whose row count is far above normal (if a date
                       column exists): a double import or a real event.

Flags only — an outlier might be your best customer, not an error.
"""

from __future__ import annotations

import pandas as pd

from data_skills._core import (
    Param, SkillResult, SkillSpec, as_datetime, bar_chart, categorical_columns, date_columns, numeric_columns,
)

SKILL = SkillSpec(
    id="anomaly-analysis",
    title="Outlier & impossible-value sweep",
    description="Robust outliers, impossible values, rare categories, and unusual daily spikes.",
    params={"z_threshold": Param("float", 3.5, "Robust z-score above which a value is an outlier")},
)


def run(df: pd.DataFrame, z_threshold: float = 3.5) -> SkillResult:
    rows, findings = [], []
    for col in numeric_columns(df):
        s = df[col].dropna()
        if len(s) < 10:
            continue
        med = s.median()
        mad = (s - med).abs().median()
        robust_z = 0.6745 * (s - med) / mad if mad else pd.Series(0, index=s.index)
        q1, q3 = s.quantile([0.25, 0.75])
        iqr = q3 - q1
        out = s[(robust_z.abs() > z_threshold) | (s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)]
        if len(out) / len(s) > 0.05:
            # Too many to be "outliers": the column mixes distinct groups
            # (e.g. $4 croissants and $42 cakes). Analyze per group instead.
            rows.append({"column": col, "check": "distinct high/low value group", "count": int(len(out)),
                         "pct": round(len(out) / len(s) * 100, 2),
                         "examples": ", ".join(f"{v:g}" for v in out.drop_duplicates().sort_values(ascending=False).head(3))})
        elif len(out):
            rows.append({"column": col, "check": "outliers", "count": int(len(out)),
                         "pct": round(len(out) / len(s) * 100, 2),
                         "examples": ", ".join(f"{v:g}" for v in out.drop_duplicates().sort_values(ascending=False).head(3))})
        neg = s[s < 0]
        if 0 < len(neg) / len(s) < 0.05:
            rows.append({"column": col, "check": "unexpected negatives", "count": int(len(neg)),
                         "pct": round(len(neg) / len(s) * 100, 2), "examples": ", ".join(f"{v:g}" for v in neg.head(3))})

    for col in date_columns(df):
        d = as_datetime(df[col]).dropna()
        bad = d[(d > pd.Timestamp.now() + pd.Timedelta(days=1)) | (d < pd.Timestamp("1900-01-01"))]
        if len(bad):
            rows.append({"column": col, "check": "impossible dates", "count": int(len(bad)),
                         "pct": round(len(bad) / len(d) * 100, 2), "examples": ", ".join(str(x.date()) for x in bad.head(3))})
        daily = d.dt.normalize().value_counts()
        if len(daily) >= 14:
            med, mad = daily.median(), (daily - daily.median()).abs().median() or 1
            spikes = daily[0.6745 * (daily - med) / mad > 5]
            if len(spikes):
                rows.append({"column": col, "check": "daily volume spikes", "count": int(len(spikes)),
                             "pct": round(len(spikes) / len(daily) * 100, 2),
                             "examples": ", ".join(f"{k.date()} ({v} rows)" for k, v in spikes.head(3).items())})

    for col in categorical_columns(df, max_unique=200):
        vc = df[col].value_counts(normalize=True)
        rare = vc[vc < 0.005]
        if 0 < len(rare) <= 10 and len(vc) > len(rare):
            rows.append({"column": col, "check": "rare categories (possible typos)", "count": int(len(rare)),
                         "pct": round(float(rare.sum()) * 100, 2), "examples": ", ".join(map(str, rare.index[:3]))})

    findings = [f"{r['column']}: {r['count']} {r['check']} ({r['pct']}%) e.g. {r['examples']}" for r in rows]
    charts = [bar_chart("Anomalies found by column", [f"{r['column']} · {r['check']}" for r in rows],
                        [r["count"] for r in rows], "rows")] if rows else []
    return SkillResult(
        skill=SKILL.id,
        summary=(f"{len(rows)} anomaly pattern(s) found. Review them before analysis — they may be errors or real events."
                 if rows else "No significant anomalies found."),
        findings=findings, tables={"anomalies": rows}, charts=charts,
    )

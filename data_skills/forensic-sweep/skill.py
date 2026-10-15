"""
forensic-sweep — is this data "suspiciously clean"? (Data Cleaning)

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). Looks for fingerprints of processing done BEFORE the
data reached you, so you know whether you're analyzing raw reality or someone
else's editing:

  * Zero missing values AND zero duplicates in a large table (real exports rarely are)
  * Imputation spikes — one value (often the mean/median) far more common than its neighbors
  * Heavy rounding — almost every value a multiple of 5, 10, 100
  * Capped values — a pile-up at the maximum or minimum (winsorized or truncated)
  * Perfectly regular dates — every day present with identical counts (generated or aggregated)
  * Leading-digit pattern far from Benford's law (for amounts spanning several orders of magnitude)

Each finding is a reason to ASK the data owner, not proof of tampering.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from data_skills._core import SkillResult, SkillSpec, as_datetime, date_columns, numeric_columns

SKILL = SkillSpec(
    id="forensic-sweep",
    title='Pre-processing ("too clean") check',
    wants_raw=True,
    description="Signs the data was imputed, rounded, capped, deduplicated, or generated before you got it.",
)

BENFORD = np.log10(1 + 1 / np.arange(1, 10))


def run(df: pd.DataFrame) -> SkillResult:
    """
    Look for signs that the data was processed (filled, rounded, capped, generated) before you got it.

    Signals checked (each is a question for the data owner, not proof of tampering):
    - Whole table: 500+ rows with zero missing values and zero duplicate rows
    - Per number column (50+ values, 5+ distinct):
      - imputation spike: the most common value is > 15% of rows and > 4x the runner-up;
        noted as "the mean/median" when it sits within 0.01 SD of either
      - rounding: > 90% of non-zero values are multiples of 100, 10, or 5 (largest base
        reported) and the typical size is above that base
      - capping: > 5% of values exactly at the max or min (columns with 20+ distinct values)
      - Benford: for 300+ positive values spanning > 1000x, mean absolute deviation of
        leading-digit shares from Benford's law above 0.015
    - Per date column (60+ dates): every day in the span present with identical row counts

    Args:
        df: pd.DataFrame, the RAW table (this skill sets wants_raw=True so cleaning hasn't hidden
            the signals).

    Returns:
        SkillResult: summary with the number of signals (or "looks raw"); one finding per signal;
        table "signals" with column, signal, and question_to_ask.

    Example:
        >>> res = run(pd.DataFrame({"amount": [10, 20, 30]}))
        >>> res.summary
        'No signs of prior processing: the data looks raw.'
    """
    flags = []

    if len(df) >= 500 and df.isna().sum().sum() == 0 and not df.duplicated().any():
        flags.append(("whole table", "no missing values and no duplicates in a large table",
                      "Ask whether rows were filtered or gaps filled before export."))

    for col in numeric_columns(df):
        s = df[col].dropna()
        if len(s) < 50 or s.nunique() < 5:
            continue
        top_val, top_share = s.value_counts(normalize=True).index[0], s.value_counts(normalize=True).iloc[0]
        second_share = s.value_counts(normalize=True).iloc[1]
        if top_share > 0.15 and top_share > 4 * second_share:
            near_center = min(abs(top_val - s.mean()), abs(top_val - s.median())) <= 0.01 * (s.std() or 1)
            flags.append((col, f"{top_share:.0%} of values equal {top_val:g}"
                          + (" (the mean/median — a sign of imputation)" if near_center else ""),
                          "Ask whether missing values were filled with a fixed number."))
        nonzero = s[s != 0]
        for base in (100, 10, 5):
            if len(nonzero) and (nonzero % base == 0).mean() > 0.9 and nonzero.abs().median() > base:
                flags.append((col, f"{(nonzero % base == 0).mean():.0%} of values are multiples of {base}",
                              "Values may be rounded or estimated rather than measured."))
                break
        for edge, name in ((s.max(), "maximum"), (s.min(), "minimum")):
            share = (s == edge).mean()
            if share > 0.05 and s.nunique() > 20:
                flags.append((col, f"{share:.0%} of values sit exactly at the {name} ({edge:g})",
                              "The column may be capped/truncated (winsorized)."))
        pos = s[s > 0]
        if len(pos) >= 300 and pos.max() / max(pos.min(), 1e-9) > 1000:
            first = pos.astype(str).str.lstrip("0.").str[0]
            first = first[first.str.isdigit() & (first != "0")].astype(int)
            observed = np.bincount(first, minlength=10)[1:] / len(first)
            mad = float(np.abs(observed - BENFORD).mean())
            if mad > 0.015:
                flags.append((col, f"leading digits deviate from Benford's law (MAD {mad:.3f})",
                              "Natural amounts usually follow Benford's law; large deviations can mean generated or edited values."))

    for col in date_columns(df):
        d = as_datetime(df[col]).dropna().dt.normalize()
        if len(d) < 60:
            continue
        counts = d.value_counts()
        span = (d.max() - d.min()).days + 1
        if len(counts) == span and counts.std() == 0:
            flags.append((col, "every day present with exactly the same number of rows",
                          "Looks generated or pre-aggregated, not raw transactions."))

    rows = [{"column": c, "signal": s, "question_to_ask": q} for c, s, q in flags]
    return SkillResult(
        skill=SKILL.id,
        summary=(f"{len(rows)} sign(s) of prior processing found — worth asking the data owner about."
                 if rows else "No signs of prior processing: the data looks raw."),
        findings=[f"{r['column']}: {r['signal']}" for r in rows],
        tables={"signals": rows},
    )

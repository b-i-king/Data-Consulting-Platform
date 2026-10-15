"""
type-consistency-sweep — find data-type problems (Data Cleaning).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). Checks every column for:

  1. Mixed types       — numbers and text in the same column ("12", "N/A", "twelve")
  2. Numbers as text   — "$1,200" or "45%" stored as text
  3. Dates as text     — "2024-03-01" stored as text
  4. Null sentinels    — placeholder words meaning "missing": N/A, null, -, ?, none, TBD
  5. Booleans as text  — yes/no, Y/N, true/false columns
  6. Cross-file drift  — the same column name typed differently in two tables
                         (only when several tables are given)

Reports only; it doesn't change the data (the cleaning step decides that).
"""

from __future__ import annotations

import pandas as pd

from data_skills._core import SkillResult, SkillSpec

SENTINELS = {"n/a", "na", "null", "none", "-", "--", "?", "tbd", "unknown", "missing", "#n/a", "nan", "nil", "0000-00-00"}
BOOL_WORDS = {"yes", "no", "y", "n", "true", "false", "t", "f"}

SKILL = SkillSpec(
    id="type-consistency-sweep",
    title="Data-type consistency check",
    input="tables",
    wants_raw=True,
    description="Find mixed types, numbers/dates stored as text, placeholder nulls, and cross-file type drift.",
)


def _kind(v) -> str:
    """
    What a single value looks like.

    Order of checks: real bool -> "bool"; int/float -> "number"; a SENTINELS word such as
    "N/A" or "TBD" -> "sentinel"; text that parses as a number after removing "$", ",", "%"
    -> "number"; text of 6+ characters that parses as a date -> "date"; anything else -> "text".

    Args:
        v: Any, one cell value.

    Returns:
        str: "bool", "number", "sentinel", "date", or "text".

    Example:
        >>> [_kind(v) for v in [True, 3, "$1,200", "N/A", "2024-03-01", "hello"]]
        ['bool', 'number', 'number', 'sentinel', 'date', 'text']
    """
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    s = str(v).strip()
    if s.lower() in SENTINELS:
        return "sentinel"
    if pd.notna(pd.to_numeric(s.replace(",", "").replace("$", "").replace("%", ""), errors="coerce")):
        return "number"
    if len(s) >= 6 and pd.notna(pd.to_datetime(s, errors="coerce", format="mixed")):
        return "date"
    return "text"


def sweep(name: str, df: pd.DataFrame) -> list[dict]:
    """
    Find type problems in the text columns of one table.

    Numeric and datetime columns are skipped (already typed). For the first 2,000 non-empty
    values of each other column, each value is classified with _kind and reported as:
    - "placeholder nulls" when any value is a sentinel word
    - "boolean stored as text" when all distinct values are yes/no-style words (then stops)
    - "numbers stored as text" when ≥ 90% look like numbers
    - "dates stored as text" when ≥ 90% look like dates
    - "mixed types" when > 10% are numbers and > 10% are text

    Args:
        name: str, table name (copied into each issue).
        df: pd.DataFrame, the raw table.

    Returns:
        list[dict]: one dict per issue with keys table, column, issue, detail, fix.

    Example:
        >>> sweep("t", pd.DataFrame({"paid": ["yes", "no", "yes"]}))[0]["issue"]
        'boolean stored as text'
    """
    issues = []
    for col in df.columns:
        s = df[col].dropna()
        if s.empty or pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
            continue
        sample = s.head(2000)
        kinds = sample.map(_kind).value_counts(normalize=True)
        share = lambda k: float(kinds.get(k, 0))  # noqa: E731
        lower = set(sample.astype(str).str.strip().str.lower().unique())
        if share("sentinel") > 0:
            words = sorted(w for w in lower if w in SENTINELS)
            issues.append({"table": name, "column": str(col), "issue": "placeholder nulls",
                           "detail": f"{share('sentinel'):.0%} of values are placeholders: {', '.join(words)}",
                           "fix": "Treat these as missing values."})
        if lower and lower <= BOOL_WORDS:
            issues.append({"table": name, "column": str(col), "issue": "boolean stored as text",
                           "detail": f"Values: {', '.join(sorted(lower))}", "fix": "Convert to true/false."})
            continue
        if share("number") >= 0.9:
            issues.append({"table": name, "column": str(col), "issue": "numbers stored as text",
                           "detail": f"{share('number'):.0%} of values are numbers (e.g. '{sample.iloc[0]}')",
                           "fix": "Strip $ , % and convert to numbers."})
        elif share("date") >= 0.9:
            issues.append({"table": name, "column": str(col), "issue": "dates stored as text",
                           "detail": f"{share('date'):.0%} of values are dates", "fix": "Convert to a date type."})
        elif share("number") > 0.1 and share("text") > 0.1:
            issues.append({"table": name, "column": str(col), "issue": "mixed types",
                           "detail": f"{share('number'):.0%} numbers, {share('text'):.0%} text",
                           "fix": "Decide which is right; the other values are probably errors."})
    return issues


def run(tables: dict[str, pd.DataFrame]) -> SkillResult:
    """
    Find data-type problems in one or more raw tables, including cross-file type drift.

    Runs sweep() on every table. With several tables, also compares each column name
    (case- and space-insensitive) across files and flags it when its broad type (number,
    date, text) differs, since such files can't be joined or stacked cleanly. Reports only;
    nothing is changed.

    Args:
        tables: dict[str, pd.DataFrame], table name -> its RAW data (wants_raw=True).

    Returns:
        SkillResult: summary with the issue count; findings for the first 15 issues;
        table "issues".

    Example:
        >>> res = run({"a.csv": pd.DataFrame({"id": [1]}), "b.csv": pd.DataFrame({"id": ["x"]})})
        >>> res.tables["issues"][-1]["issue"]
        'type differs between files'
    """
    issues = [i for name, df in tables.items() for i in sweep(name, df)]

    # Cross-file drift: same column name, different broad type.
    if len(tables) > 1:
        seen: dict[str, dict[str, str]] = {}
        for name, df in tables.items():
            for col in df.columns:
                t = ("number" if pd.api.types.is_numeric_dtype(df[col]) else
                     "date" if pd.api.types.is_datetime64_any_dtype(df[col]) else "text")
                seen.setdefault(str(col).strip().lower(), {})[name] = t
        for col, by_table in seen.items():
            if len(set(by_table.values())) > 1:
                issues.append({"table": " / ".join(by_table), "column": col, "issue": "type differs between files",
                               "detail": ", ".join(f"{t}: {k}" for t, k in by_table.items()),
                               "fix": "Use one type everywhere so the files can be joined or stacked."})

    return SkillResult(
        skill=SKILL.id,
        summary=(f"Found {len(issues)} type issue(s) across {len(tables)} table(s)." if issues
                 else "No type problems found — every column holds one consistent kind of value."),
        findings=[f"{i['table']} › {i['column']}: {i['issue']} — {i['detail']}" for i in issues[:15]],
        tables={"issues": issues},
    )

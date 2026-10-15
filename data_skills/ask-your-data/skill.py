"""
ask-your-data — plain-English questions, safe pandas answers (AI).

Python implementation of README.md in this folder, following its guardrails:
answers come ONLY from the data, every answer shows its calculation, and the
code is read-only. Generated code is never `eval`-ed: a question becomes a
small PLAN (filters → group → aggregate → sort → limit) that is executed with
a fixed set of pandas operations.

Where the plan comes from:
  * `plan` (JSON) — given directly. This is what a language model (Claude)
    will produce once the AI service launches; the executor stays the same.
  * `question` (text) — a built-in rule-based parser for common shapes:
        "total amount by store"          "average csat by store"
        "top 5 product by amount"        "how many orders by payment_method"
        "max amount", "count rows"       "... in 2025" / "... for store Midtown"

Unsupported questions get an honest "I can't answer that yet" with examples.
"""

from __future__ import annotations

import re

import pandas as pd

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, as_datetime, date_columns, fmt_num, records

SKILL = SkillSpec(
    id="ask-your-data",
    title="Ask questions in plain English",
    description="Answer simple questions (totals, averages, top N, by category, by year) with the calculation shown.",
    params={
        "question": Param("str", None, "e.g. 'top 5 product by amount in 2025'"),
        "plan": Param("json", None, 'Structured plan, e.g. {"metric":"amount","agg":"sum","group_by":"store"}'),
    },
)

AGG_WORDS = {"total": "sum", "sum": "sum", "average": "mean", "avg": "mean", "mean": "mean", "median": "median",
             "max": "max", "maximum": "max", "highest": "max", "min": "min", "minimum": "min", "lowest": "min",
             "how many": "count", "count": "count", "number of": "count"}
ALLOWED_AGG = {"sum", "mean", "median", "max", "min", "count"}
ALLOWED_OPS = {"=", "!=", ">", ">=", "<", "<=", "in"}


def _match_col(word: str, df: pd.DataFrame) -> str | None:
    """
    Find the column a word from the question refers to, forgiving plurals and spaces.

    Matches (case-insensitive) when the column name equals the word, equals it with
    a trailing "s" removed on both sides ("stores" -> store), or equals it with
    underscores read as spaces ("payment method" -> payment_method).

    Args:
        word: a word or phrase from the question.
        df: the table whose columns are searched.

    Returns:
        str | None: the matching column name, or None if nothing matches.

    Example:
        >>> _match_col("stores", pd.DataFrame(columns=["store", "amount"]))
        'store'
    """
    w = word.strip().lower().rstrip("s")
    for c in df.columns:
        n = str(c).lower()
        if n == word.lower() or n.rstrip("s") == w or n.replace("_", " ") == word.lower():
            return str(c)
    return None


def parse(question: str, df: pd.DataFrame) -> dict:
    """
    Very small rule-based parser -> plan dict. Raises SkillInputError if unsure.

    Understood shapes (lowercased, trailing "?" ignored):
    - "... in 2025" -> {"year": 2025}
    - "... for <column> <value>" -> a filter <column> = <value> (value matched to the data's spelling)
    - "top N <group> by <metric>" -> sum of metric by group, limited to N
    - "<agg word> [of|the] <metric> [by <group>]", where the agg word is one of
      AGG_WORDS (total, average, median, max, min, how many, count, ...); for counts
      the metric may be "rows", "records", "orders" or empty

    Args:
        question: the plain-English question.
        df: the table, used to match words to column names and filter values.

    Returns:
        dict: a plan with "filters" (list of {column, op, value}) plus any of "metric",
        "agg", "group_by", "limit", "year" — the format `execute` runs.

    Raises:
        SkillInputError: the question doesn't start with a known shape, or names a
            metric or group column that doesn't exist.

    Example:
        >>> df = pd.DataFrame({"store": ["Midtown"], "amount": [5.0]})
        >>> parse("total amount by store", df)
        {'filters': [], 'agg': 'sum', 'metric': 'amount', 'group_by': 'store'}
    """
    q = question.lower().strip().rstrip("?")
    plan: dict = {"filters": []}
    if m := re.search(r"\bin (19|20)(\d\d)\b", q):
        plan["year"] = int(m.group(1) + m.group(2))
        q = q[:m.start()] + q[m.end():]
    if m := re.search(r"\bfor (\w+) ([\w\s]+?)$", q):
        col = _match_col(m.group(1), df)
        if col:
            val = next((v for v in df[col].dropna().unique() if str(v).lower() == m.group(2).strip()), m.group(2).strip())
            plan["filters"].append({"column": col, "op": "=", "value": val})
            q = q[:m.start()].strip()
    if m := re.match(r"top (\d+) (\w+) by (\w+)", q):
        plan.update(limit=int(m.group(1)), group_by=_match_col(m.group(2), df), metric=_match_col(m.group(3), df), agg="sum")
    else:
        for word, agg in sorted(AGG_WORDS.items(), key=lambda kv: -len(kv[0])):
            if q.startswith(word):
                plan["agg"] = agg
                q = q[len(word):].strip()
                break
        else:
            raise SkillInputError("I can't answer that yet. Try: 'total amount by store', 'average csat by store', "
                                  "'top 5 product by amount in 2025', or 'how many rows by payment_method'.")
        q = re.sub(r"^(of|the)\s+", "", q)
        metric_part, _, group_part = q.partition(" by ")
        plan["metric"] = None if plan["agg"] == "count" and metric_part in ("", "rows", "records", "orders") else _match_col(metric_part, df)
        plan["group_by"] = _match_col(group_part, df) if group_part else None
        if plan["metric"] is None and plan["agg"] != "count":
            raise SkillInputError(f"I couldn't find a column called '{metric_part}'. Columns: {', '.join(map(str, df.columns))}")
        if group_part and not plan["group_by"]:
            raise SkillInputError(f"I couldn't find a column called '{group_part}'.")
    return plan


def execute(df: pd.DataFrame, plan: dict) -> tuple[pd.DataFrame | float, str, int]:
    """
    Run a plan with a fixed set of pandas operations. Returns (answer, explanation, rows used).

    Nothing is `eval`-ed: only the aggregations in ALLOWED_AGG and filter operators in
    ALLOWED_OPS are accepted. Order: filters -> year (on the plan's date_column or the
    first date column) -> group and aggregate (largest first, `limit` rows) or a
    single aggregate. With no metric, rows are counted.

    Args:
        df: the table to answer from.
        plan: dict with optional keys "metric", "agg" (default "sum"), "group_by",
            "filters" ([{column, op, value}], op default "="), "year", "date_column", "limit".

    Returns:
        tuple: (answer, explanation, rows used) — the answer is a DataFrame
        (<group>, value) when grouped, else a float; the explanation describes the
        calculation in words; rows used is the number of rows after filtering.

    Raises:
        SkillInputError: unknown aggregation, operator or column, or a year was asked
            for but there is no date column.

    Example:
        >>> df = pd.DataFrame({"store": ["A", "A", "B"], "amount": [1.0, 2.0, 5.0]})
        >>> execute(df, {"metric": "amount", "agg": "sum"})
        (8.0, 'sum of `amount` (3 rows)', 3)
    """
    agg = plan.get("agg", "sum")
    if agg not in ALLOWED_AGG:
        raise SkillInputError(f"Aggregation must be one of {sorted(ALLOWED_AGG)}.")
    metric, group = plan.get("metric"), plan.get("group_by")
    for c in [metric, group, *(f["column"] for f in plan.get("filters", []))]:
        if c and c not in df.columns:
            raise SkillInputError(f"Unknown column '{c}'.")
    d, how = df, []
    for f in plan.get("filters", []):
        op = f.get("op", "=")
        if op not in ALLOWED_OPS:
            raise SkillInputError(f"Filter operator must be one of {sorted(ALLOWED_OPS)}.")
        col, val = f["column"], f["value"]
        d = d[{"=": d[col] == val, "!=": d[col] != val, ">": d[col] > val, ">=": d[col] >= val,
               "<": d[col] < val, "<=": d[col] <= val}[op]] if op != "in" else d[d[col].isin(val)]
        how.append(f"`{col}` {op} {val!r}")
    if plan.get("year"):
        dcol = plan.get("date_column") or next(iter(date_columns(df)), None)
        if not dcol:
            raise SkillInputError("The question mentions a year but there is no date column.")
        d = d[as_datetime(d[dcol]).dt.year == plan["year"]]
        how.append(f"`{dcol}` in {plan['year']}")
    what = f"{agg} of `{metric}`" if metric else "count of rows"
    if group:
        res = (d.groupby(group)[metric].agg(agg) if metric else d.groupby(group).size()).sort_values(ascending=False)
        if plan.get("limit"):
            res = res.head(int(plan["limit"]))
        answer = res.rename("value").reset_index()
        what += f" by `{group}`"
    else:
        answer = float(d[metric].agg(agg)) if metric else float(len(d))
    explanation = what + (f" where {' and '.join(how)}" if how else "") + f" ({len(d):,} rows)"
    return answer, explanation, len(d)


def run(df, question=None, plan=None) -> SkillResult:
    """
    Answer a plain-English question (or a structured plan) from the data, showing how.

    A given `plan` is used as is; otherwise `question` is turned into a plan by
    `parse`. The answer always comes with a "How:" finding describing the calculation,
    plus a caution when fewer than 30 rows were used.

    Args:
        df: the table to answer from.
        question: e.g. "top 5 product by amount in 2025".
        plan: structured plan, e.g. {"metric": "amount", "agg": "sum", "group_by": "store"}
            (what a language model will produce once the AI service launches).

    Returns:
        SkillResult: for grouped answers, a summary naming the leaders and table
        "answer"; for single numbers, "The answer is ..." with data["answer"]; data["plan"]
        always holds the plan that ran. No matching rows gives "No rows match that question."

    Raises:
        SkillInputError: neither a question nor a plan was given, or the question/plan
            can't be answered (see `parse` and `execute`).

    Example:
        >>> run(pd.read_csv("samples/sales.csv"), question="total amount by store").summary
        # 'Midtown leads with ..., followed by Decatur (...).'
    """
    if not plan and not question:
        raise SkillInputError("Ask a question (e.g. 'total amount by store') or give a plan.")
    plan = plan or parse(question, df)
    answer, how, n = execute(df, plan)
    findings = [f"How: {how}."]
    if n < 30:
        findings.append(f"Caution: based on only {n} rows.")
    if isinstance(answer, pd.DataFrame):
        if answer.empty:
            return SkillResult(skill=SKILL.id, summary="No rows match that question.", findings=findings, data={"plan": plan})
        top = answer.iloc[0]
        summary = (f"{top.iloc[0]} leads with {fmt_num(top['value'])}"
                   + (f", followed by {answer.iloc[1, 0]} ({fmt_num(answer.iloc[1]['value'])})." if len(answer) > 1 else "."))
        return SkillResult(skill=SKILL.id, summary=summary, findings=findings,
                           tables={"answer": records(answer, None)}, data={"plan": plan})
    return SkillResult(skill=SKILL.id, summary=f"The answer is {fmt_num(answer)}.", findings=findings,
                       data={"plan": plan, "answer": answer})

"""
segmentation — group similar customers, products or clients with K-means (Data Science & ML).

Python implementation of README.md in this folder. Steps:

  1. What to group — one row per customer (or product, client...). With a
     `group_by` column (auto: a customer / client / member column), the rows are
     rolled up per entity: how many purchases, totals of the amounts, and days
     since the last purchase when there's a date ("RFM": recency, frequency,
     monetary). Without one, each row is grouped as it is.
  2. Prepare the numbers — skewed money columns are log-scaled (one huge
     customer shouldn't pull every group toward them), then each feature is
     standardized (mean 0, standard deviation 1) so dollars don't outweigh counts.
  3. Choose K — try 2 to `max_k` groups and keep the K with the best silhouette
     score (how clearly each member belongs to its own group: 1 = perfectly
     separate, 0 = overlapping). The silhouette is measured on at most 3,000
     members, so compute stays low.
  4. Describe each group — its size and averages, named by what sets it apart
     ("High total amount, few purchases"), with a suggested action for customers.

K-means is unsupervised: there's no right answer to score against. The groups
are a starting point for decisions (who gets a win-back offer), not a label.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from data_skills._core import (
    Param, SkillInputError, SkillResult, SkillSpec, as_datetime, bar_chart, date_columns, fmt_num,
    numeric_columns, records, require_column,
)

SKILL = SkillSpec(
    id="segmentation",
    title="Customer segments (K-means groups)",
    description="Groups similar customers, products or clients by what they buy and how often, names each "
                "group by what sets it apart, and suggests an action for each.",
    params={
        "group_by": Param("column", None, "Who to group: one row per value (auto: a customer / client column; "
                                          "if there is none, the rows are grouped as they are)"),
        "features": Param("columns", None, "Number columns to group by (default: all measures)"),
        "date_column": Param("column", None, "Purchase date, for days since the last purchase (auto)"),
        "k": Param("int", None, "Number of groups (default: chosen by the silhouette score)"),
        "max_k": Param("int", 6, "Most groups to try when choosing K (2 to 10)"),
    },
)

ENTITY_HINT = ("customer", "client", "member", "account", "patient", "student", "donor", "subscriber")
AVERAGE_HINT = ("price", "rate", "score", "pct", "percent", "age", "avg", "average", "rating", "csat", "nps")
SILHOUETTE_SAMPLE = 3000      # members used to score each K (keeps compute low)
DISCLAIMER = ("These groups come from patterns in your data, not from labels you gave them: use them as a "
              "starting point, and check that each group makes sense for your business.")


def _entity_column(df: pd.DataFrame, given: str | None) -> str | None:
    """The column the person named, else a customer-like column with many values, else None (group the rows)."""
    if given:
        return require_column(df, given, "group-by", [str(c) for c in df.columns])
    for col in df.columns:
        name = str(col).lower()
        if any(h in name for h in ENTITY_HINT) and df[col].nunique(dropna=True) >= 10:
            return str(col)
    return None


def _per_entity(df: pd.DataFrame, entity: str, measures: list[str], date: str | None) -> pd.DataFrame:
    """
    Roll the rows up to one row per entity: purchases, totals (or averages), days since the last purchase.

    Price-, rate- and score-like columns are averaged (adding up unit prices means
    nothing); other measures are totaled.
    """
    data = df.dropna(subset=[entity])
    grouped = data.groupby(entity)
    out = pd.DataFrame({"purchases": grouped.size()})
    for col in measures:
        if any(h in col.lower() for h in AVERAGE_HINT):
            out[f"avg_{col}"] = grouped[col].mean()
        else:
            out[f"total_{col}"] = grouped[col].sum()
    if date:
        when = as_datetime(data[date])
        last = when.groupby(data[entity]).max()
        out["days_since_last"] = (when.max() - last).dt.days
    return out


def _describe(z: pd.Series) -> str:
    """'High total amount, few purchases' — the two features where a group differs most from average."""
    words = []
    for name, value in z.abs().sort_values(ascending=False).head(2).items():
        if abs(z[name]) < 0.3:
            continue
        label = name.replace("total_", "total ").replace("avg_", "average ").replace("_", " ")
        if name == "purchases":
            words.append("many purchases" if z[name] > 0 else "few purchases")
        elif name == "days_since_last":
            words.append("not seen lately" if z[name] > 0 else "bought recently")
        else:
            words.append(f"{'high' if z[name] > 0 else 'low'} {label}")
    return (", ".join(words) or "close to average").capitalize()


def _action(z: pd.Series, money: str | None) -> str:
    """A suggested next step for a customer group (simple rules on recency and spend)."""
    if z.get("days_since_last", 0) > 0.5:
        return "Haven't bought in a while: try a win-back offer."
    if money and z.get(money, 0) > 0.5:
        return "Your best customers: keep them (loyalty perks, first look at new products)."
    if money and z.get(money, 0) < -0.5:
        return "Light buyers: a bundle or second-visit offer can grow them."
    return "The steady middle: keep them engaged with regular updates."


def run(df, group_by=None, features=None, date_column=None, k=None, max_k=6) -> SkillResult:
    """
    Group similar customers (or products, clients, rows) with K-means.

    Args:
        df: pd.DataFrame, e.g. sales lines (rolled up per customer) or a customer table.
        group_by: str | None, the entity column (auto: a customer-like column; "" = the rows).
        features: str | list | None, number columns to use (default: all measures).
        date_column: str | None, purchase date for "days since last purchase" (auto).
        k: int | None, number of groups (default: best silhouette from 2 to max_k).
        max_k: int, most groups to try (2 to 10, default 6).

    Returns:
        SkillResult: summary (how many groups, how clear); findings (one per group
        and the disclaimer); tables "segments" (size, share, averages, description,
        action), "members" (each entity's group, first 500) and "k_choice" (the
        silhouette for each K tried); a bar chart of group sizes; data {"k",
        "silhouette", "features", "group_by"}.

    Raises:
        SkillInputError: no number columns, too few members, or a bad option.

    Example:
        >>> res = run(sales)                     # groups customers by purchases, spend, recency
        >>> res.tables["segments"][0]["description"]
        'Low total quantity, few purchases'
    """
    max_k = int(max_k)
    if not 2 <= max_k <= 10 or (k is not None and not 2 <= int(k) <= 10):
        raise SkillInputError("Use between 2 and 10 groups (k and max_k).")
    entity = _entity_column(df, group_by) if group_by != "" else None
    if isinstance(features, str):
        features = [c.strip() for c in features.split(",") if c.strip()]
    measures = list(features) if features else [c for c in numeric_columns(df) if c != entity]
    missing = [c for c in measures if c not in df.columns]
    if missing:
        raise SkillInputError(f"Column(s) not found: {', '.join(missing)}.")
    dates = date_columns(df)
    date = require_column(df, date_column, "date", dates) if date_column else (dates[0] if dates and entity else None)

    if entity:
        table = _per_entity(df, entity, measures, date)
    else:
        if not measures:
            raise SkillInputError("Segments need number columns (amounts, counts, scores) to group by.")
        table = df[measures].apply(pd.to_numeric, errors="coerce")
    table = table.dropna()
    table = table.loc[:, table.std(ddof=0) > 0]          # a constant column can't separate anyone
    if table.shape[1] == 0:
        raise SkillInputError("Segments need number columns that differ between members.")
    if len(table) < 10:
        raise SkillInputError(f"Only {len(table)} members to group — at least 10 are needed.")

    # 2. Log-scale skewed non-negative columns, then standardize.
    ready = table.astype(float).copy()
    for col in ready.columns:
        if ready[col].min() >= 0 and ready[col].skew() > 1:
            ready[col] = np.log1p(ready[col])
    X = StandardScaler().fit_transform(ready)

    # 3. Choose K by the silhouette score (or use the K given).
    tries = [int(k)] if k else list(range(2, min(max_k, len(table) - 1) + 1))
    sample = min(len(table), SILHOUETTE_SAMPLE)
    choices, best = [], None
    for n in tries:
        model = KMeans(n_clusters=n, n_init=10, random_state=0).fit(X)
        score = float(silhouette_score(X, model.labels_, sample_size=sample, random_state=0))
        choices.append({"k": n, "silhouette": round(score, 3)})
        if best is None or score > best[1] + 1e-9:
            best = (model, score, n)
    model, score, chosen = best

    # 4. Describe each group (biggest first), in the original units.
    table["segment"] = model.labels_
    overall_mean, overall_std = table.drop(columns="segment").mean(), table.drop(columns="segment").std(ddof=0)
    money = next((c for c in table.columns if c.startswith("total_") and
                  any(h in c for h in ("amount", "revenue", "sales", "spend", "total"))), None) \
        or next((c for c in table.columns if c.startswith("total_")), None)
    order = table["segment"].value_counts().index
    rename = {old: i + 1 for i, old in enumerate(order)}            # Segment 1 = the biggest
    table["segment"] = table["segment"].map(rename)
    segments = []
    for seg, part in table.groupby("segment"):
        means = part.drop(columns="segment").mean()
        z = (means - overall_mean) / overall_std
        row = {"segment": int(seg), "members": len(part), "share_pct": round(len(part) / len(table) * 100, 1),
               "description": _describe(z)}
        if entity:
            row["action"] = _action(z, money)
        # The group's average of each feature, e.g. avg_total_amount = average spend per member.
        row.update({f"avg_{c}" if not c.startswith("avg_") else c: round(float(v), 2) for c, v in means.items()})
        segments.append(row)

    quality = ("clearly separate" if score >= 0.5 else "reasonably distinct" if score >= 0.25
               else "overlapping — treat them as rough tiers")
    # "customer_id" -> "customers" (the summary and chart speak about members, not a column).
    who = (entity.lower().removesuffix("_id").removesuffix(" id").replace("_", " ") + "s") if entity else "rows"
    summary = (f"{chosen} groups of {fmt_num(len(table), 0)} {who}; the groups are {quality} "
               f"(silhouette {score:.2f}).")
    findings = [f"Segment {s['segment']} ({s['share_pct']}%): {s['description']}."
                + (f" {s['action']}" if "action" in s else "") for s in segments]
    findings.append(DISCLAIMER)
    members = table.reset_index().rename(columns={"index": "row"})
    return SkillResult(
        skill=SKILL.id, summary=summary, findings=findings,
        tables={"segments": segments, "members": records(members, limit=500), "k_choice": choices},
        charts=[bar_chart("Members per segment", [f"Segment {s['segment']}" for s in segments],
                          [s["members"] for s in segments], who)],
        data={"k": chosen, "silhouette": round(score, 3), "features": list(ready.columns),
              "group_by": entity, "date_column": date, "disclaimer": DISCLAIMER},
    )

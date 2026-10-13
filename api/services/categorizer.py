"""
Multi-file categorizer: figure out how several uploaded files relate.

WHY THIS EXISTS
---------------
When a client hands over ONE file, we can go straight to cleaning. When they
hand over SEVERAL, a consultant first asks: "Are these the same kind of data
split up (for example one file per year), or different kinds of data (for
example transactions vs. customers)?" This module answers that question
automatically, so the platform can recommend what to do next.

THE ALGORITHM, STEP BY STEP
---------------------------
1. **Fingerprint each dataset.** Normalize the column names (lowercase,
   spaces/dashes -> underscores) so "Customer ID" and "customer_id" match.

2. **Group by schema similarity.** Two datasets belong in the same group if
   their column sets overlap by at least `SCHEMA_MATCH_THRESHOLD` (Jaccard
   similarity = shared columns / all columns). Datasets in one group have the
   "same structure" and can usually be STACKED (appended row-wise).

3. **Detect time partitions inside a group.** If the files in a group carry
   different years — in the file name (e.g. "sales_2023.csv") or because
   their date columns cover different years — the group is labeled
   "partitioned by year". The recommendation is to stack them and add a
   `source_file` column so the year is not lost.

4. **Classify the business entity of each dataset.** Using keyword scores
   on column names (see `ENTITY_KEYWORDS`), guess whether the data is about
   transactions, customers, products, employees/payroll, marketing, finance,
   etc. This is a heuristic — the UI lets the user correct it.

5. **Find join keys between different groups.** If two groups share an
   identifier-looking column (ends with "id", "_key", "number", "email"...),
   that column can link them — e.g. transactions.customer_id ↔
   customers.customer_id. These become "suggested relationships".
   If they share NO key but both have names, emails, or phone numbers, we
   suggest a FUZZY link instead (see api/services/entity_matching.py).

6. **Recommend a next step** for the whole upload.

Everything here is pure pandas + Python: no data leaves memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from api.services.entity_matching import detect_match_columns
from api.services.file_reader import LoadedDataset

# Two column sets that share at least 80% of their columns = "same structure".
SCHEMA_MATCH_THRESHOLD = 0.8

# A four-digit year between 1990 and 2099 that is NOT part of a longer number.
_YEAR_RE = re.compile(r"(?<!\d)(199\d|20\d\d)(?!\d)")

# Keywords that hint at what a dataset is about. A column name that CONTAINS
# a keyword adds one point to that entity. Add your own as you meet new
# client data — this list is meant to grow.
ENTITY_KEYWORDS: dict[str, list[str]] = {
    "transactions": [
        "order", "transaction", "invoice", "amount", "price", "qty", "quantity",
        "total", "subtotal", "discount", "tax", "payment", "receipt", "sale",
    ],
    "customers": [
        "customer", "client", "first_name", "last_name", "email", "phone",
        "address", "city", "state", "zip", "postal", "signup", "member",
    ],
    "products": [
        "product", "sku", "item", "category", "inventory", "stock", "brand",
        "unit_cost", "upc", "variant",
    ],
    "employees_payroll": [
        "employee", "salary", "wage", "payroll", "hire", "department",
        "title", "manager", "hours", "pto", "gross_pay", "net_pay",
    ],
    "marketing": [
        "campaign", "channel", "impression", "click", "ctr", "conversion",
        "utm", "spend", "ad_", "lead", "source", "medium",
    ],
    "finance_accounting": [
        "account", "ledger", "debit", "credit", "balance", "expense",
        "revenue", "vendor", "bill", "gl_", "journal",
    ],
    "operations": [
        "shipment", "shipping", "delivery", "warehouse", "supplier",
        "tracking", "carrier", "status", "ticket", "appointment",
    ],
}

# Column-name endings that suggest an identifier usable as a join key.
_KEY_HINTS = ("id", "_key", "key", "number", "num", "code", "email", "sku")


def normalize_column(name: str) -> str:
    """'Customer ID ' -> 'customer_id'. Used only for comparing, never shown."""
    name = name.strip().lower()
    name = re.sub(r"[\s\-./]+", "_", name)
    return re.sub(r"_+", "_", name).strip("_")


def jaccard(a: set[str], b: set[str]) -> float:
    """Share of columns two sets have in common (0.0 = none, 1.0 = identical)."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def years_in_filename(name: str) -> list[int]:
    """'sales_2023_Q1.csv' -> [2023]."""
    return sorted({int(y) for y in _YEAR_RE.findall(name)})


def years_in_data(df: pd.DataFrame, max_cols: int = 5) -> list[int]:
    """
    Look for date-like columns and return the distinct years they cover.

    We only test columns whose NAME suggests a date (date, time, created,
    month, year, period...) to keep this fast on wide files, and we only use
    a column if most of its values parse as dates.
    """
    candidates = [c for c in df.columns if re.search(r"date|time|created|updated|month|year|period|day", str(c), re.I)]
    years: set[int] = set()
    for col in candidates[:max_cols]:
        series = df[col].dropna()
        if series.empty:
            continue
        if pd.api.types.is_numeric_dtype(series) and "year" in str(col).lower():
            vals = series[(series >= 1990) & (series <= 2099)]
            years.update(int(v) for v in vals.unique()[:50])
            continue
        parsed = pd.to_datetime(series.astype(str).head(2000), errors="coerce", format="mixed")
        if parsed.notna().mean() >= 0.8:  # at least 80% look like dates
            years.update(int(y) for y in parsed.dt.year.dropna().unique())
    return sorted(years)


def classify_entity(columns: list[str]) -> tuple[str, dict[str, int]]:
    """
    Score each entity type by keyword hits in column names.

    Returns (best_entity, all_scores). If nothing matches, the entity is
    "general" — the user can rename it in the UI.
    """
    norm = [normalize_column(c) for c in columns]
    scores = {
        entity: sum(1 for col in norm for kw in keywords if kw in col)
        for entity, keywords in ENTITY_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return (best if scores[best] > 0 else "general"), scores


def looks_like_key(col: str) -> bool:
    """True for names such as 'customer_id', 'order_number', 'email', 'sku'."""
    return normalize_column(col).endswith(_KEY_HINTS)


# ── Result containers (turned into JSON by the router) ───────────────────────

@dataclass
class DatasetSummary:
    name: str
    source_file: str
    file_type: str
    rows: int
    columns: list[str]
    entity: str
    entity_scores: dict[str, int]
    years_from_name: list[int]
    years_from_data: list[int]
    group_id: int = -1


@dataclass
class DatasetGroup:
    group_id: int
    label: str                         # e.g. "Transactions (partitioned by year)"
    entity: str
    structure: str                     # "single", "same_schema", "partitioned_by_year"
    members: list[str]
    shared_columns: list[str]
    years: list[int]
    recommendation: str


@dataclass
class Relationship:
    left_group: int
    right_group: int
    key_columns: list[str]          # shared ID columns, or the fuzzy-match columns
    description: str
    method: str = "key"             # "key" = exact shared ID; "fuzzy" = name/email/phone matching


@dataclass
class CategorizationResult:
    mode: str                          # "single_file" or "multiple_files"
    datasets: list[DatasetSummary]
    groups: list[DatasetGroup]
    relationships: list[Relationship] = field(default_factory=list)
    next_step: str = ""


# ── Main entry point ─────────────────────────────────────────────────────────

def categorize(datasets: list[LoadedDataset]) -> CategorizationResult:
    """Run steps 1–6 described at the top of this file."""

    # Step 1 — fingerprint every dataset.
    summaries: list[DatasetSummary] = []
    colsets: list[set[str]] = []
    for ds in datasets:
        entity, scores = classify_entity(list(ds.df.columns))
        summaries.append(
            DatasetSummary(
                name=ds.name,
                source_file=ds.source_file,
                file_type=ds.file_type,
                rows=int(len(ds.df)),
                columns=[str(c) for c in ds.df.columns],
                entity=entity,
                entity_scores=scores,
                years_from_name=years_in_filename(ds.name),
                years_from_data=years_in_data(ds.df),
            )
        )
        colsets.append({normalize_column(c) for c in ds.df.columns})

    # Single file: nothing to relate — go straight to cleaning.
    if len(datasets) == 1:
        s = summaries[0]
        s.group_id = 0
        group = DatasetGroup(
            group_id=0, label=_entity_label(s.entity), entity=s.entity, structure="single",
            members=[s.name], shared_columns=s.columns, years=s.years_from_data,
            recommendation="Single dataset — continue directly to data cleaning.",
        )
        return CategorizationResult(
            mode="single_file", datasets=summaries, groups=[group],
            next_step="Your file is ready for data cleaning.",
        )

    # Step 2 — greedy grouping by schema similarity. Each dataset joins the
    # first existing group whose "anchor" (first member) it matches.
    group_members: list[list[int]] = []
    for i, cols in enumerate(colsets):
        for members in group_members:
            if jaccard(cols, colsets[members[0]]) >= SCHEMA_MATCH_THRESHOLD:
                members.append(i)
                break
        else:
            group_members.append([i])

    # Steps 3 & 4 — describe each group.
    groups: list[DatasetGroup] = []
    for gid, members in enumerate(group_members):
        for i in members:
            summaries[i].group_id = gid
        shared = set.intersection(*(colsets[i] for i in members))
        # Keep the ORIGINAL spelling of shared columns from the first member.
        first_cols = summaries[members[0]].columns
        shared_display = [c for c in first_cols if normalize_column(c) in shared]

        # Entity for the group = most common entity among its members.
        entities = [summaries[i].entity for i in members]
        entity = max(set(entities), key=entities.count)

        # Year evidence: from file names first, then from date columns.
        per_member_years = [
            tuple(summaries[i].years_from_name or summaries[i].years_from_data) for i in members
        ]
        all_years = sorted({y for ys in per_member_years for y in ys})
        distinct_year_sets = {ys for ys in per_member_years if ys}

        if len(members) == 1:
            structure = "single"
            rec = "Stand-alone dataset — clean it on its own."
        elif len(distinct_year_sets) > 1:
            structure = "partitioned_by_year"
            rec = (
                f"These {len(members)} files share the same columns and cover different years "
                f"({', '.join(map(str, all_years))}). Recommended: stack them into one table "
                "and keep a 'source_file' column so each row still shows its year."
            )
        else:
            structure = "same_schema"
            rec = (
                f"These {len(members)} files share the same columns. Recommended: stack them "
                "into one table (with a 'source_file' column) before cleaning."
            )

        label = _entity_label(entity)
        if structure == "partitioned_by_year":
            label += " (partitioned by year)"
        elif structure == "same_schema":
            label += " (same structure)"

        groups.append(
            DatasetGroup(
                group_id=gid, label=label, entity=entity, structure=structure,
                members=[summaries[i].name for i in members], shared_columns=shared_display,
                years=all_years, recommendation=rec,
            )
        )

    # Step 5 — candidate join keys between DIFFERENT groups.
    relationships: list[Relationship] = []
    for a in range(len(groups)):
        for b in range(a + 1, len(groups)):
            cols_a = set.intersection(*(colsets[i] for i in group_members[a]))
            cols_b = set.intersection(*(colsets[i] for i in group_members[b]))
            keys = sorted(c for c in (cols_a & cols_b) if looks_like_key(c))
            if keys:
                relationships.append(
                    Relationship(
                        left_group=a, right_group=b, key_columns=keys,
                        description=(
                            f"'{groups[a].label}' and '{groups[b].label}' can be linked on "
                            f"{', '.join(keys)}."
                        ),
                    )
                )
                continue
            # No shared ID: can we match on names / emails / phones instead?
            fuzzy_on = _fuzzy_link_fields(datasets, group_members[a][0], group_members[b][0])
            if fuzzy_on:
                relationships.append(
                    Relationship(
                        left_group=a, right_group=b, key_columns=fuzzy_on, method="fuzzy",
                        description=(
                            f"'{groups[a].label}' and '{groups[b].label}' have no shared ID, but can be "
                            f"matched by {' and '.join(fuzzy_on)} (fuzzy matching — a person should "
                            "review uncertain matches)."
                        ),
                    )
                )

    # Step 6 — overall recommendation.
    if len(groups) == 1:
        next_step = "All files have the same structure. We will stack them, then clean the combined table."
    elif relationships:
        next_step = (
            "You uploaded different kinds of data that can be linked together. Each group will be "
            "cleaned separately; the linking keys are ready for analysis or a dashboard."
        )
    else:
        next_step = (
            "You uploaded different kinds of data with no obvious shared key. Each group will be "
            "cleaned separately. A consultant can help decide how they relate."
        )

    return CategorizationResult(
        mode="multiple_files", datasets=summaries, groups=groups,
        relationships=relationships, next_step=next_step,
    )


def _fuzzy_link_fields(datasets: list[LoadedDataset], i: int, j: int) -> list[str]:
    """Which of email / phone / name both datasets have (for fuzzy linking)."""
    a, b = detect_match_columns(datasets[i].df), detect_match_columns(datasets[j].df)
    fields = []
    if a.email and b.email:
        fields.append("email")
    if a.phone and b.phone:
        fields.append("phone")
    has_name = lambda c: bool(c.name or (c.first_name and c.last_name))  # noqa: E731
    if has_name(a) and has_name(b):
        fields.append("name")
    return fields


def combine_group(datasets: list[LoadedDataset], member_names: list[str]) -> pd.DataFrame:
    """
    Stack (append) the datasets of one group into a single DataFrame.

    A `source_file` column is added so every row remembers where it came
    from (and therefore which year, for year-partitioned groups). Columns are
    matched on their normalized name so "Customer ID" and "customer_id" line
    up; the first file's spelling wins.
    """
    by_name = {d.name: d for d in datasets}
    frames = []
    canonical: dict[str, str] = {}  # normalized -> display name
    for name in member_names:
        df = by_name[name].df.copy()
        rename = {}
        for col in df.columns:
            key = normalize_column(col)
            canonical.setdefault(key, col)
            rename[col] = canonical[key]
        df = df.rename(columns=rename)
        df.insert(0, "source_file", name)
        frames.append(df)
    if len(frames) == 1:
        return frames[0].drop(columns="source_file")
    return pd.concat(frames, ignore_index=True, sort=False)


def _entity_label(entity: str) -> str:
    """'employees_payroll' -> 'Employees / Payroll'."""
    return {
        "transactions": "Transactions",
        "customers": "Customers",
        "products": "Products",
        "employees_payroll": "Employees / Payroll",
        "marketing": "Marketing",
        "finance_accounting": "Finance / Accounting",
        "operations": "Operations",
        "general": "General data",
    }.get(entity, entity.replace("_", " ").title())

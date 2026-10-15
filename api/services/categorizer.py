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
    """
    Normalize a column name for comparing: 'Customer ID ' -> 'customer_id'.

    Trims and lowercases the name, turns runs of spaces, dashes, dots and slashes
    into one underscore, collapses repeated underscores and strips them from the
    ends. Used only for comparing, never shown to the user.

    Args:
        name: str: the column name as it appears in the file.

    Returns:
        str: the normalized name.

    Example:
        >>> normalize_column("Customer ID ")
        'customer_id'
        >>> normalize_column("Order-Date/Time")
        'order_date_time'
    """
    name = name.strip().lower()
    name = re.sub(r"[\s\-./]+", "_", name)
    return re.sub(r"_+", "_", name).strip("_")


def jaccard(a: set[str], b: set[str]) -> float:
    """
    Share of columns two sets have in common (0.0 = none, 1.0 = identical).

    Jaccard similarity = shared columns / all distinct columns. Two empty sets
    count as identical (1.0) so the division never fails. Compared against
    `SCHEMA_MATCH_THRESHOLD` (0.8) to decide whether two files have the "same structure".

    Args:
        a: set[str]: normalized column names of the first dataset.
        b: set[str]: normalized column names of the second dataset.

    Returns:
        float: similarity between 0.0 and 1.0.

    Example:
        >>> jaccard({"date", "amount"}, {"date", "amount", "store"})
        0.6666666666666666
    """
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def years_in_filename(name: str) -> list[int]:
    """
    Find the four-digit years (1990–2099) in a file name: 'sales_2023_Q1.csv' -> [2023].

    A year must not be part of a longer number (so "20231" is ignored). Each
    year is returned once, sorted.

    Args:
        name: str: the file or dataset name.

    Returns:
        list[int]: distinct years found, ascending; [] if none.

    Example:
        >>> years_in_filename("sales_2023_Q1.csv")
        [2023]
        >>> years_in_filename("orders_2022_to_2024.xlsx")
        [2022, 2024]
    """
    return sorted({int(y) for y in _YEAR_RE.findall(name)})


def years_in_data(df: pd.DataFrame, max_cols: int = 5) -> list[int]:
    """
    Look for date-like columns and return the distinct years they cover.

    We only test columns whose NAME suggests a date (date, time, created,
    updated, month, year, period, day) to keep this fast on wide files, and
    at most `max_cols` of them. For each:
    - a NUMERIC column whose name contains "year": values between 1990 and 2099
      are taken as years (up to 50 distinct values);
    - otherwise the first 2,000 non-missing values are parsed as dates, and the
      column is used only if at least 80% of them parse.

    Args:
        df: pd.DataFrame: the dataset to inspect.
        max_cols: int: the most date-like columns to test (default 5).

    Returns:
        list[int]: distinct years, ascending; [] if no column looks like dates.

    Example:
        >>> years_in_data(pd.DataFrame({"order_date": ["2023-01-05", "2024-02-01"]}))
        [2023, 2024]
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

    Each normalized column name earns one point for every keyword in
    `ENTITY_KEYWORDS[entity]` that it CONTAINS (so "order_total" scores two
    points for "transactions": "order" and "total"). The entity with the highest
    score wins; on a tie, the one listed first in `ENTITY_KEYWORDS` wins. If
    nothing matches, the entity is "general" — the user can rename it in the UI.

    Args:
        columns: list[str]: the dataset's column names (any spelling).

    Returns:
        tuple[str, dict[str, int]]: (best_entity, all_scores), where all_scores maps
        every entity in `ENTITY_KEYWORDS` to its point total.

    Example:
        >>> classify_entity(["Order ID", "Amount", "Tax"])[0]
        'transactions'
        >>> classify_entity(["foo", "bar"])[0]
        'general'
    """
    norm = [normalize_column(c) for c in columns]
    scores = {
        entity: sum(1 for col in norm for kw in keywords if kw in col)
        for entity, keywords in ENTITY_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return (best if scores[best] > 0 else "general"), scores


def looks_like_key(col: str) -> bool:
    """
    Say whether a column name looks like an identifier usable as a join key.

    True when the normalized name ENDS with one of `_KEY_HINTS` ("id", "_key",
    "key", "number", "num", "code", "email", "sku"), e.g. 'customer_id',
    'order_number', 'email', 'sku'. Only the name is checked, not the values.

    Args:
        col: str: the column name (any spelling; it is normalized first).

    Returns:
        bool: True if the column could link two tables.

    Example:
        >>> looks_like_key("Customer ID")
        True
        >>> looks_like_key("city")
        False
    """
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
    """
    Work out how several uploaded datasets relate (steps 1–6 at the top of this file).

    1. Fingerprint each dataset: entity guess, years in its name and dates.
    2. Group datasets whose normalized column sets have Jaccard similarity >=
       `SCHEMA_MATCH_THRESHOLD` with the group's FIRST member (greedy, in upload order).
    3. Label each group "single", "same_schema" or "partitioned_by_year" (members
       carry different years, from file names first, else from date columns).
    4. Give each group the most common entity of its members.
    5. Between every pair of groups, suggest a "key" relationship on shared
       ID-like columns, or else a "fuzzy" one on email / phone / name.
    6. Write an overall next step.
    With a single dataset, steps 2–6 are skipped: mode is "single_file" and the
    advice is to go straight to cleaning.

    Args:
        datasets: list[LoadedDataset]: the tables read from the upload by
            `file_reader.py` (one per CSV, or per sheet of a workbook).

    Returns:
        CategorizationResult: mode ("single_file" | "multiple_files"), one
        DatasetSummary per dataset (with its group_id), the groups, suggested
        relationships, and a plain-English next_step.

    Example:
        Two files "sales_2023.csv" and "sales_2024.csv", both with Order Date, Amount and Tax columns:
            result = categorize(datasets)
            result.mode                  # "multiple_files"
            result.groups[0].structure   # "partitioned_by_year"
            result.groups[0].label       # "Transactions (partitioned by year)"
    """

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
    """
    List which of email / phone / name BOTH datasets have (for fuzzy linking).

    Uses `detect_match_columns` on each dataset. A "name" counts when the
    dataset has a single name column or both first and last name columns.

    Args:
        datasets: list[LoadedDataset]: all uploaded datasets.
        i: int: position in `datasets` of the first dataset (a group's first member).
        j: int: position in `datasets` of the second dataset.

    Returns:
        list[str]: some of "email", "phone", "name", in that order; [] if the two
        datasets share none, so no fuzzy relationship is suggested.

    Example:
        If dataset 0 has "Email" and "Customer Name" and dataset 1 has "email" and "company":
            _fuzzy_link_fields(datasets, 0, 1)   # ["email", "name"]
    """
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

    A `source_file` column is added (as the first column) so every row remembers
    where it came from (and therefore which year, for year-partitioned groups).
    Columns are matched on their normalized name so "Customer ID" and
    "customer_id" line up; the first file's spelling wins. Columns only some
    files have are kept, with blanks for the other files' rows. A group with
    ONE member is returned as a copy without `source_file`.

    Args:
        datasets: list[LoadedDataset]: all uploaded datasets.
        member_names: list[str]: the `name` of each dataset to stack, in the order
            to stack them (usually `DatasetGroup.members`).

    Returns:
        pd.DataFrame: the stacked rows with a fresh 0..n-1 index.

    Raises:
        KeyError: if a name in `member_names` is not one of the datasets.

    Example:
        combine_group(datasets, ["sales_2023.csv", "sales_2024.csv"])
        # -> columns: source_file, Order Date, Amount, ... (rows of 2023, then 2024)
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
    """
    Turn an entity id into a display label: 'employees_payroll' -> 'Employees / Payroll'.

    Known entities use a fixed label; any other id has underscores replaced by
    spaces and is title-cased.

    Args:
        entity: str: an entity id from `classify_entity` (or one the user typed).

    Returns:
        str: the label shown in the UI.

    Example:
        >>> _entity_label("employees_payroll")
        'Employees / Payroll'
        >>> _entity_label("web_traffic")
        'Web Traffic'
    """
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

"""
er-diagram — verified ER diagram in Mermaid (Data Engineering).

Python implementation of README.md in this folder. Takes SEVERAL tables:

  1. Entities      — one per table (singular PascalCase: customers.csv -> Customer)
  2. Primary keys  — a column that is unique AND never empty; ID-looking names first
  3. Foreign keys  — a column in table A whose name matches B's key (customer_id),
                     with COVERAGE: share of A's values that exist in B
                     (< 95% = unreliable link or incomplete parent table)
  4. Cardinality   — one-to-many when the child column repeats, else one-to-one
  5. Diagram       — Mermaid `erDiagram` text (paste into GitHub, Notion, mermaid.live)
"""

from __future__ import annotations

import re

import pandas as pd

from api.services.categorizer import normalize_column
from data_skills._core import SkillResult, SkillSpec

SKILL = SkillSpec(
    id="er-diagram",
    title="ER diagrams",
    input="tables",
    description="Find primary and foreign keys across tables, verify coverage, and draw a Mermaid ER diagram.",
)


def entity_name(table: str) -> str:
    """
    Turn a table (file) name into a singular PascalCase entity name for the diagram.

    Rules, in order:
    - Keep only the part after the last "›" (sheet/folder separator) and drop a file extension
    - Split on anything that isn't a letter or digit and capitalize each word
    - Drop one trailing "s" to make it singular, unless the name ends in "ss" (Address stays Address)
    - Fall back to "Table" when nothing usable is left

    Args:
        table: str, the table's name as uploaded, e.g. "quickbooks_customers.csv" or "Sales › orders.xlsx".

    Returns:
        str: the entity name, e.g. "QuickbooksCustomer".

    Example:
        >>> entity_name("quickbooks_customers.csv")
        'QuickbooksCustomer'
        >>> entity_name("Sales › orders.xlsx")
        'Order'
    """
    base = re.sub(r"\.[a-z0-9]+$", "", table.split("›")[-1].strip(), flags=re.I)
    words = re.split(r"[^A-Za-z0-9]+", base)
    name = "".join(w[:1].upper() + w[1:].lower() for w in words if w) or "Table"
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def mermaid_type(s: pd.Series) -> str:
    """
    Map a pandas column's dtype to the type word shown in the Mermaid diagram.

    Checks booleans first (so True/False isn't reported as int), then integers, floats,
    and datetimes; everything else (text, mixed, categories) is "string".

    Args:
        s: pd.Series, one column of a table.

    Returns:
        str: one of "boolean", "int", "float", "date", or "string".

    Example:
        >>> mermaid_type(pd.Series([1, 2]))
        'int'
        >>> mermaid_type(pd.Series(["a", "b"]))
        'string'
    """
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "int"
    if pd.api.types.is_float_dtype(s):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    return "string"


def primary_key(df: pd.DataFrame) -> str | None:
    """
    Pick the column most likely to be the table's primary key.

    A candidate must be unique AND never empty. Among candidates, names that end in
    "id" (after normalize_column, so "Customer ID" counts) come first, then shorter
    names win ties.

    Args:
        df: pd.DataFrame, one table.

    Returns:
        str | None: the chosen column name, or None when no column is unique and complete
        (the table then needs a surrogate id).

    Example:
        >>> primary_key(pd.DataFrame({"name": ["a", "b"], "customer_id": [1, 2]}))
        'customer_id'
        >>> primary_key(pd.DataFrame({"x": [1, 1]})) is None
        True
    """
    cands = [c for c in df.columns if df[c].notna().all() and df[c].is_unique]
    cands.sort(key=lambda c: (not normalize_column(c).endswith("id"), len(str(c))))
    return str(cands[0]) if cands else None


def run(tables: dict[str, pd.DataFrame]) -> SkillResult:
    """
    Find keys and relationships across several tables and draw a Mermaid ER diagram.

    Steps:
    - Name each table as an entity (entity_name) and choose its primary key (primary_key)
    - Foreign keys: a column in a child table whose normalized name matches another table's
      primary key (and is not the child's own key)
    - Coverage: the share of the child's non-empty values found in the parent key;
      below 95% the link is marked unreliable (wrong match or missing parent rows)
    - Cardinality: one-to-many when the child column repeats, otherwise one-to-one
    - Writes an `erDiagram` block with PK/FK tags on the columns

    Args:
        tables: dict[str, pd.DataFrame], table name -> its data (two or more tables to find links).

    Returns:
        SkillResult: summary with entity and relationship counts; findings per key and link;
        tables "keys" (every column with unique/null %/primary-key flags) and "relationships"
        (child, parent, cardinality, coverage_pct, reliable); artifact "mermaid_erd" with the
        diagram text to paste into GitHub, Notion, or mermaid.live.

    Example:
        >>> customers = pd.DataFrame({"customer_id": [1, 2], "name": ["Ana", "Ben"]})
        >>> orders = pd.DataFrame({"order_id": [10, 11, 12], "customer_id": [1, 1, 2]})
        >>> res = run({"customers.csv": customers, "orders.csv": orders})
        >>> res.summary
        '2 entities, 1 verified relationship(s).'
        >>> res.tables["relationships"][0]["cardinality"]
        'one-to-many'
    """
    ents = {name: entity_name(name) for name in tables}
    pks = {name: primary_key(df) for name, df in tables.items()}
    keys_table, links = [], []
    for name, df in tables.items():
        for col in df.columns:
            keys_table.append({"entity": ents[name], "column": str(col), "unique": bool(df[col].is_unique),
                               "null_pct": round(float(df[col].isna().mean() * 100), 2),
                               "primary_key": str(col) == pks[name]})

    for child, cdf in tables.items():
        for parent, pdf in tables.items():
            pk = pks[parent]
            if child == parent or not pk:
                continue
            for col in cdf.columns:
                if normalize_column(col) != normalize_column(pk) or str(col) == pks[child]:
                    continue
                vals = cdf[col].dropna()
                coverage = float(vals.isin(pdf[pk]).mean()) if len(vals) else 0.0
                one_to_many = not vals.is_unique
                links.append({"child": ents[child], "child_column": str(col), "parent": ents[parent],
                              "parent_key": pk, "cardinality": "one-to-many" if one_to_many else "one-to-one",
                              "coverage_pct": round(coverage * 100, 2),
                              "reliable": coverage >= 0.95})

    lines = ["erDiagram"]
    for l in links:
        rel = "||--o{" if l["cardinality"] == "one-to-many" else "||--||"
        lines.append(f"    {l['parent']} {rel} {l['child']} : \"{l['child_column']}\"")
    fk_cols = {(l["child"], l["child_column"]) for l in links}
    for name, df in tables.items():
        lines.append(f"    {ents[name]} {{")
        for col in df.columns:
            tag = " PK" if str(col) == pks[name] else " FK" if (ents[name], str(col)) in fk_cols else ""
            safe = re.sub(r"[^A-Za-z0-9_]", "_", str(col))
            lines.append(f"        {mermaid_type(df[col])} {safe}{tag}")
        lines.append("    }")

    findings = [f"{ents[n]}: primary key {pks[n]}" if pks[n] else f"{ents[n]}: no natural key — add a surrogate id"
                for n in tables]
    findings += [f"{l['parent']} → {l['child']} on {l['child_column']} ({l['cardinality']}, {l['coverage_pct']}% of values match"
                 + ("" if l["reliable"] else " — UNRELIABLE, check for missing parent rows") + ")" for l in links]
    return SkillResult(skill=SKILL.id, summary=f"{len(tables)} entities, {len(links)} verified relationship(s).",
                       findings=findings, tables={"keys": keys_table, "relationships": links},
                       artifacts={"mermaid_erd": "\n".join(lines) + "\n"})

"""
Intake questionnaire — the questions a data consultant asks first.

WHAT THIS FILE DOES
-------------------
Holds the answer choices for the guest questionnaire so the frontend (web
today, a phone app later) can render them from one source of truth:

  Q1. "Are you an individual or a business?"
  Q2. "Where is your data stored?"  -> flat files, cloud storage/warehouses,
                                       or business apps (integrations)
  Q3. "What does your company need help with?" -> see api/services/pricing.py

`available=True` means the platform can handle that choice end-to-end today.
Anything else is "coming soon": the UI still lets people pick it (so you
learn what clients use), and routes them to your booking link.

To launch a new connector later, build it, then flip `available` to True.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ACCOUNT_TYPES = [
    {"id": "individual", "label": "Individual",
     "description": "Freelancer, student, researcher, or just curious about your own data."},
    {"id": "business", "label": "Business",
     "description": "A company or organization of any size — you can add team members later."},
]

DATA_LOCATIONS = [
    {
        "id": "flat_files",
        "label": "Files on my computer",
        "description": "CSV or Excel recommended — plus JSON, Parquet, SAS, Stata, SPSS, zip and more.",
        "available": True,
        # The full list of formats comes from api/services/file_reader.py
        # (FORMATS), sent to the browser as `file_formats`.
        "options": [],
    },
    {
        "id": "cloud_storage",
        "label": "Cloud storage or data warehouse",
        "description": "Data that lives in a cloud platform or database.",
        "available": False,
        "options": [
            {"id": "aws_s3", "label": "AWS (S3, Redshift, RDS)", "available": False},
            {"id": "snowflake", "label": "Snowflake", "available": False},
            {"id": "bigquery", "label": "Google BigQuery", "available": False},
            {"id": "databricks", "label": "Databricks", "available": False},
            {"id": "azure", "label": "Microsoft Azure / Fabric", "available": False},
            {"id": "postgres", "label": "PostgreSQL / MySQL / SQL Server", "available": False},
            {"id": "google_drive", "label": "Google Drive / Sheets", "available": False},
        ],
    },
    {
        "id": "business_apps",
        "label": "Business apps (integrations)",
        "description": "Accounting, payroll, sales, and point-of-sale tools.",
        "available": False,
        "options": [
            {"id": "quickbooks", "label": "QuickBooks", "available": False},
            {"id": "gusto", "label": "Gusto", "available": False},
            {"id": "shopify", "label": "Shopify", "available": False},
            {"id": "square", "label": "Square", "available": False},
            {"id": "stripe", "label": "Stripe", "available": False},
            {"id": "salesforce", "label": "Salesforce", "available": False},
            {"id": "hubspot", "label": "HubSpot", "available": False},
            {"id": "other", "label": "Something else", "available": False},
        ],
    },
    {
        "id": "not_sure",
        "label": "I'm not sure",
        "description": "Totally normal — a consultant can help you find it.",
        "available": False,
        "options": [],
    },
]


# ── Company size — asked of business accounts ────────────────────────────────
# The same employee-count bands LinkedIn uses (company size codes A–I), so the
# answers line up with LinkedIn data and are familiar to business owners:
# https://learn.microsoft.com/en-us/linkedin/shared/references/reference-tables/company-size-codes
COMPANY_SIZES = [
    {"id": "A", "label": "Self-employed", "employees": "1"},
    {"id": "B", "label": "1–10 employees", "employees": "1-10"},
    {"id": "C", "label": "11–50 employees", "employees": "11-50"},
    {"id": "D", "label": "51–200 employees", "employees": "51-200"},
    {"id": "E", "label": "201–500 employees", "employees": "201-500"},
    {"id": "F", "label": "501–1,000 employees", "employees": "501-1000"},
    {"id": "G", "label": "1,001–5,000 employees", "employees": "1001-5000"},
    {"id": "H", "label": "5,001–10,000 employees", "employees": "5001-10000"},
    {"id": "I", "label": "10,001+ employees", "employees": "10001+"},
]
COMPANY_SIZE_CODES = {s["id"] for s in COMPANY_SIZES}


# ── Industry (NAICS) — asked of business accounts ────────────────────────────
# Three questions, each narrowing the previous one:
#   1. Goods-producing or service-providing?   (BLS supersector)
#   2. Sector,    e.g. "44-45 Retail Trade"     (2-digit NAICS)
#   3. Subsector, e.g. "445 Food and Beverage Retailers" (3-digit NAICS)
# We deliberately stop at the subsector. The list is generated from the SBA
# size-standards workbook by scripts/build_naics_reference.py.

_NAICS_PATH = Path(__file__).resolve().parents[1] / "data" / "naics_sectors.json"


@lru_cache
def naics_tree() -> dict:
    """The supersector → sector → subsector tree (loaded once)."""
    return json.loads(_NAICS_PATH.read_text(encoding="utf-8"))


def validate_industry(supersector: str | None, sector: str | None, subsector: str | None) -> str | None:
    """
    Check that the three industry answers are consistent with each other.
    Returns an error message, or None when the answers are valid.
    All three are optional, but each one requires the one before it.
    """
    if not supersector:
        return "Choose goods-producing or service-providing first." if (sector or subsector) else None
    if supersector not in {s["id"] for s in naics_tree()["supersectors"]}:
        return "Unknown industry group."
    if not sector:
        return "Pick a sector before a subsector." if subsector else None
    match = next((s for s in naics_tree()["sectors"] if s["code"] == sector), None)
    if match is None or match["supersector"] != supersector:
        return "That sector does not belong to the chosen industry group."
    if subsector and subsector not in {s["code"] for s in match["subsectors"]}:
        return "That subsector does not belong to the chosen sector."
    return None

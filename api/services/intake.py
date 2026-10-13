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
        "description": "CSV, Excel, JSON, Parquet, or text exports.",
        "available": True,
        "options": [
            {"id": "csv", "label": "CSV / TSV / TXT", "available": True},
            {"id": "excel", "label": "Excel (.xlsx, .xls)", "available": True},
            {"id": "json", "label": "JSON", "available": True},
            {"id": "parquet", "label": "Parquet", "available": True},
        ],
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

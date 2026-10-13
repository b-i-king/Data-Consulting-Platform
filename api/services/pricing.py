"""
Service catalog and pricing.

WHAT THIS FILE DOES
-------------------
1. Defines the SERVICES a client can pick on the "What does your company
   need help with?" step (data strategy, analysis, dashboards, ML,
   data engineering) and how each one is priced.
2. Turns processing RUNTIME into a price for the automated data cleaning:
   runtime is rounded UP to the billing increment (1 minute by default)
   and multiplied by the per-minute rate.
3. Builds a QUOTE for the services a client selected.

ALL PRICES BELOW ARE PLACEHOLDERS. Edit them here (or move them into the
database later) once you settle on your packages. The per-minute cleaning
rate is configured with the PRICE_PER_RUNTIME_MINUTE_USD environment variable.

PRICING MODELS
--------------
* "runtime"      — automated; billed per started minute of processing.
* "package"      — a fixed starting price for a standard package.
* "consultation" — needs a human; the client is sent to your booking link.
* "early_access" — not sold yet (e.g. AI); shown as "Early access", never charged.

Each service id is also a skill CATEGORY: `.claude/skills/catalog.json` lists the
skills behind each service, and `catalog()` attaches their client-friendly
labels as "capabilities" for the service cards.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from api.core.config import get_settings
from api.services.skills_catalog import capability_labels


@dataclass(frozen=True)
class Service:
    id: str                         # also the category id in .claude/skills/catalog.json
    name: str
    description: str
    pricing_model: str              # "runtime", "package", "consultation" or "early_access"
    starting_price_usd: float       # 0 unless pricing_model == "package"
    automated: bool                 # True = the platform does it without a human
    requires_consultant: bool       # True = route to the booking link
    includes: tuple[str, ...]       # deliverables, in plain English
    available: bool = True          # False = shown as "Early access" (not sold yet)


# The order here is the order shown in the UI. Descriptions are the owner's
# own service copy — edit them here, not in the frontend.
SERVICES: tuple[Service, ...] = (
    Service(
        id="data_cleaning",
        name="Data Cleaning",
        description="The automatic first step for every project: duplicates removed, missing "
                    "values measured, data types and outliers checked, and a clean file ready "
                    "to download.",
        pricing_model="runtime",
        starting_price_usd=0.0,
        automated=True,
        requires_consultant=False,
        includes=("Duplicate-row removal", "Missing-value report by column",
                  "Full data preview", "CSV or Excel download"),
    ),
    Service(
        id="data_strategy_discovery",
        name="Data Strategy & Discovery",
        description="We collaborate to identify crucial data sources, align data initiatives with "
                    "your overarching business objectives (on and off the court), and build a "
                    "robust roadmap for analytics success.",
        pricing_model="consultation",
        starting_price_usd=0.0,
        automated=False,
        requires_consultant=True,
        includes=("Discovery call", "Data-source inventory", "Analytics roadmap"),
    ),
    Service(
        id="data_exploration_analysis",
        name="Data Exploration & Analysis",
        description="Through rigorous analysis using tools like Excel, Google Sheets, Python, and "
                    "SQL, we answer your critical questions, identify key trends, and provide "
                    "actionable intelligence to inform strategic decisions.",
        pricing_model="package",
        starting_price_usd=250.0,   # PLACEHOLDER
        automated=False,
        requires_consultant=False,
        includes=("Data profile", "Key trends", "Answers to up to 5 questions", "Written summary"),
    ),
    Service(
        id="data_visualization",
        name="Data Visualization",
        description="We create dynamic dashboards and visually compelling reports using platforms "
                    "like Tableau, Power BI, or custom solutions, making complex data "
                    "understandable and driving informed action across all departments.",
        pricing_model="package",
        starting_price_usd=500.0,   # PLACEHOLDER
        automated=False,
        requires_consultant=False,
        includes=("KPI cards", "Trend and breakdown charts", "Filters", "One revision round"),
    ),
    Service(
        id="data_science_ml",
        name="Data Science & Machine Learning",
        description="This service includes, but is not limited to, leveraging data science "
                    "techniques and machine learning algorithms to provide advanced analytics, "
                    "predictive modeling, and actionable insights.",
        pricing_model="package",
        starting_price_usd=1500.0,  # PLACEHOLDER
        automated=False,
        requires_consultant=True,
        includes=("Sample-size check", "Model build and validation", "Plain-English results"),
    ),
    Service(
        id="data_engineering",
        name="Data Engineering",
        description="We focus on creating clear data structures (ER diagrams), comprehensive "
                    "documentation (data dictionaries), and ensuring your data is clean, "
                    "reliable, and optimized for seamless integration and analysis.",
        pricing_model="package",
        starting_price_usd=750.0,   # PLACEHOLDER
        automated=False,
        requires_consultant=True,
        includes=("ER diagram", "Data dictionary", "Optional managed PostgreSQL storage"),
    ),
    Service(
        id="ai",
        name="AI",
        description="Ask questions about your data in plain English and get answers from built-in "
                    "data models — with the math shown, so you can trust the result.",
        pricing_model="early_access",
        starting_price_usd=0.0,
        automated=True,
        requires_consultant=False,
        includes=("Plain-English questions", "Answers with the calculation shown",
                  "Learns your business terms"),
        available=False,            # flip to True when the AI feature launches
    ),
)

SERVICES_BY_ID = {s.id: s for s in SERVICES}


def runtime_cost(runtime_seconds: float) -> dict:
    """
    Convert processing time into a price.

    Example with the defaults ($0.50 per minute, 60-second increments):
        12 seconds  -> 1 billable minute  -> $0.50
        61 seconds  -> 2 billable minutes -> $1.00
    """
    settings = get_settings()
    increment = max(settings.billing_increment_seconds, 1)
    # Round UP to the next whole increment; zero runtime still costs one
    # increment because the server did the work.
    increments = max(1, math.ceil(runtime_seconds / increment))
    billable_minutes = increments * increment / 60
    amount = round(billable_minutes * settings.price_per_runtime_minute_usd, 2)
    amount = max(amount, settings.minimum_charge_usd)
    return {
        "runtime_seconds": round(runtime_seconds, 3),
        "billing_increment_seconds": increment,
        "billable_minutes": round(billable_minutes, 2),
        "rate_per_minute_usd": settings.price_per_runtime_minute_usd,
        "amount_usd": amount,
    }


def build_quote(service_ids: list[str], runtime_seconds: float | None = None) -> dict:
    """
    Price the selected services.

    Data cleaning is ALWAYS included (it is the automatic first step), even
    if the client did not tick it.
    """
    settings = get_settings()
    ids = ["data_cleaning", *[s for s in service_ids if s != "data_cleaning"]]
    unknown = [s for s in ids if s not in SERVICES_BY_ID]
    if unknown:
        raise ValueError(f"Unknown service id(s): {', '.join(unknown)}")

    lines = []
    total = 0.0
    needs_consultant = False
    for sid in ids:
        svc = SERVICES_BY_ID[sid]
        if svc.pricing_model == "runtime":
            cost = runtime_cost(runtime_seconds or 0.0)
            amount = cost["amount_usd"]
            note = (f"{cost['billable_minutes']:g} billable minute(s) × "
                    f"${cost['rate_per_minute_usd']:.2f}/min")
        elif svc.pricing_model == "package":
            amount = svc.starting_price_usd
            note = "Starting price — final price depends on scope."
        elif svc.pricing_model == "early_access":
            amount = 0.0
            note = "Early access — we'll reach out when it opens."
        else:
            amount = 0.0
            note = "Priced after a free discovery call."
        needs_consultant |= svc.requires_consultant
        total += amount
        lines.append({"service_id": sid, "name": svc.name, "pricing_model": svc.pricing_model,
                      "amount_usd": round(amount, 2), "note": note})

    return {
        "lines": lines,
        "estimated_total_usd": round(total, 2),
        "currency": "USD",
        "requires_consultant": needs_consultant,
        "booking_url": settings.booking_url if needs_consultant else None,
    }


def catalog() -> list[dict]:
    """All services as plain dicts (for the API), with their skill capabilities."""
    return [{**asdict(s), "capabilities": capability_labels(s.id)} for s in SERVICES]

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
4. ESTIMATES how long a download will take and what it will cost, from the
   table's rows × fields (see download_estimate), so the site can show the
   amount and ask the client to confirm before anything is charged.
5. Lists the TOOLS clients subscribe to ("build your own package"): each has
   a fixed MONTHLY and ANNUAL price (annual is cheaper) and the total is the
   sum; "All tools" is a cheaper bundle; AI is an add-on that needs Data
   Engineering. These are the Stripe products: scripts/sync_stripe.py creates
   them, and api/services/billing.py sells them. Cancel anytime.
6. CONSULTING: the first 30-minute consultation is free (except Data Strategy
   & Discovery, which is paid from the start); after that, consulting is
   billed by the hour (CONSULTING_RATE_PER_HOUR_USD, default $80).
7. CONNECTED-DATA REFRESH: how often a connector (QuickBooks) pulls new data —
   manual, weekly and daily (the default) are included; hourly and live are
   paid add-ons per connection (REFRESH_OPTIONS; rules in connections.py).

TOOL PRICES LIVE IN THE DATABASE (dim.plan_prices). The monthly / annual
numbers written in SUBSCRIPTIONS below are only the DEFAULTS (and the seed in
db/schema.sql). Change a price in the Admin panel (Admin -> Prices): it saves
the row, updates Stripe, and calls apply_prices() so the whole site shows the
new price at once (api/services/price_store.py). The per-minute cleaning
rate is configured with the PRICE_PER_RUNTIME_MINUTE_USD environment variable.

PRICING MODELS
--------------
* "runtime"      — automated; billed per started minute of processing.
* "package"      — a fixed starting price for a standard package.
* "consultation" — needs a human: a FREE 30-minute consultation first (your
                   BOOKING_URL), then the hourly consulting rate.
* "hourly"       — paid consulting from the first hour (Data Strategy & Discovery).
* "early_access" — not sold yet (e.g. AI); shown as "Early access", never charged.

Each service id is also a skill CATEGORY: `data_skills/catalog.json` lists the
skills behind each service, and `catalog()` attaches their client-friendly
labels as "capabilities" for the service cards.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

from api.core.config import get_settings
from api.services.skills_catalog import capability_labels


@dataclass(frozen=True)
class Service:
    id: str                         # also the category id in data_skills/catalog.json
    name: str
    description: str
    pricing_model: str              # "runtime", "package", "consultation", "hourly" or "early_access"
    starting_price_usd: float       # 0 unless pricing_model == "package"
    automated: bool                 # True = the platform does it without a human
    requires_consultant: bool       # True = route to the booking link
    includes: tuple[str, ...]       # deliverables, in plain English
    available: bool = True          # False = shown as "Early access" (not sold yet)


# The order here is the order shown in the UI (AI last, right after Data
# Science & ML). Descriptions are the owner's own service copy — edit them
# here, not in the frontend. Keep data_skills/catalog.json in the same order.
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
                  "Fill or remove missing values", "First-rows preview", "CSV or Excel download"),
    ),
    Service(
        id="data_strategy_discovery",
        name="Data Strategy & Discovery",
        description="We collaborate to identify crucial data sources, align data initiatives with "
                    "your overarching business objectives (on and off the court), and build a "
                    "robust roadmap for analytics success.",
        # Paid from the first hour (no free 30 minutes): this IS the consulting.
        pricing_model="hourly",
        starting_price_usd=0.0,
        automated=False,
        requires_consultant=True,
        includes=("Paid discovery sessions", "Data-source inventory", "Analytics roadmap"),
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
        description="Interactive dashboards built from your data inside the platform — KPI cards, "
                    "trends, breakdowns and filters — so complex data drives informed action. "
                    "Need Tableau, Power BI or another BI tool? Your consultant builds it with you "
                    "as a consulting project.",
        pricing_model="package",
        starting_price_usd=500.0,   # PLACEHOLDER
        automated=False,
        requires_consultant=False,
        includes=("KPI cards", "Trend and breakdown charts", "Filters", "One revision round"),
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
        id="ai",
        name="AI",
        description="Ask Beacon questions about your data in plain English and get answers with the "
                    "math shown, so you can trust the result. Free questions every month; more with "
                    "the AI add-on.",
        pricing_model="package",
        starting_price_usd=0.0,     # free questions for everyone; more with the AI add-on (SUBSCRIPTIONS)
        automated=True,
        requires_consultant=False,
        includes=("Plain-English questions", "Answers with the calculation shown",
                  "Learns your business terms"),
        # Launched (10/2026). Set available=False to put a service back to
        # "Early access" (shown, never charged).
    ),
)

SERVICES_BY_ID = {s.id: s for s in SERVICES}


# ── Consulting ────────────────────────────────────────────────────────────────
FREE_CONSULTATION_MINUTES = 30      # the first call, for every service except "hourly" ones


def consulting_rate() -> float:
    """
    Return the hourly consulting rate in US dollars.

    This is the rate charged after the free first call (or from the first hour for
    "hourly" services such as Data Strategy & Discovery). It is read from the
    CONSULTING_RATE_PER_HOUR_USD environment variable (default $80.00).

    Returns:
        float: US dollars per hour of consulting.

    Example:
        >>> consulting_rate()   # with the default settings
        80.0
    """
    return get_settings().consulting_rate_per_hour_usd


def consultation_terms(service: Service) -> dict:
    """
    Describe how talking to a consultant works for one service (shown on its card).

    Rules, by the service's pricing model:
    - "hourly": no free minutes; paid consulting from the first hour.
    - "early_access": no free minutes and no text (the service is not sold yet).
    - anything else: a free FREE_CONSULTATION_MINUTES (30) minute call, then the
      hourly rate.

    Args:
        service: Service, the catalog entry to describe.

    Returns:
        dict: {"free_minutes": int, "rate_per_hour_usd": float, "text": str}, where
            "text" is the plain-English line for the UI ("" for early access).

    Example:
        >>> consultation_terms(SERVICES_BY_ID["data_cleaning"])   # default $80/hour
        {'free_minutes': 30, 'rate_per_hour_usd': 80.0, 'text': 'Free 30-minute consultation, then $80/hour'}
        >>> consultation_terms(SERVICES_BY_ID["data_strategy_discovery"])["text"]
        'Paid consulting from $80/hour'
    """
    rate = consulting_rate()
    if service.pricing_model == "hourly":
        return {"free_minutes": 0, "rate_per_hour_usd": rate,
                "text": f"Paid consulting from ${rate:,.0f}/hour"}
    if service.pricing_model == "early_access":
        return {"free_minutes": 0, "rate_per_hour_usd": rate, "text": ""}
    return {"free_minutes": FREE_CONSULTATION_MINUTES, "rate_per_hour_usd": rate,
            "text": f"Free {FREE_CONSULTATION_MINUTES}-minute consultation, then ${rate:,.0f}/hour"}


# ── Subscriptions: build your own package (Stripe products) ─────────────────
#
# Each TOOL has its own fixed monthly and annual price; a client's subscription
# is the tools they pick, and the total is the sum ("build your own package").
#   * Data Cleaning is the base: always in every package.
#   * AI is an add-on that needs Data Engineering (it answers questions from
#     data stored and modeled there).
#   * ALL TOOLS is a bundle with every tool, cheaper than picking them all.
# In Stripe, every tool (and the bundle) is a PRODUCT with a monthly and an
# annual PRICE; one subscription holds one item per tool, all on the same
# billing interval (Stripe's rule). scripts/sync_stripe.py creates them.

@dataclass(frozen=True)
class Subscription:
    """One priced item: a tool, or the All tools bundle. Cancel anytime."""

    id: str                         # Stripe lookup-key prefix: "dcp_<id>_month", "dcp_<id>_year"
    name: str
    description: str
    service_ids: tuple[str, ...]    # services it unlocks (bundle: () = every service)
    monthly_usd: float              # billed every month
    annual_usd: float               # billed once a year (cheaper than 12 × monthly)
    includes: tuple[str, ...]
    kind: str = "tool"              # "tool" or "bundle"
    base: bool = False              # True = always in the package (Data Cleaning)
    requires: tuple[str, ...] = ()  # tools that must be picked too (AI needs Data Engineering)


# DEFAULT PRICES — the live ones are in dim.plan_prices (Admin -> Prices).
# Annual = 10 × monthly ("2 months free"). Order = order on the site: the
# three BUNDLES first (cheaper than the same tools picked one by one), then the
# six tools for "build your own". A bundle replaces the tools it covers (never
# paid twice — normalize_selection); tools it doesn't cover can be added on.
#   Starter $19  Cleaning + Dashboards                                (one by one: $38)
#   Growth  $49  + Exploration & Analysis, Data Engineering, AI       (one by one: $125)
#   Pro     $99  every tool (All tools)                               (one by one: $164)
SUBSCRIPTIONS: tuple[Subscription, ...] = (
    Subscription(
        id="starter", kind="bundle", name="Starter",
        description="Clean your data and see it on dashboards — the essentials for a small business.",
        service_ids=("data_cleaning", "data_visualization"), monthly_usd=19.0, annual_usd=190.0,
        includes=("Data Cleaning", "Dashboards", "Downloads included"),
    ),
    Subscription(
        id="growth", kind="bundle", name="Growth",
        description="Store and sync your data (QuickBooks, Xero, files), explore it, and ask Beacon about it.",
        service_ids=("data_cleaning", "data_exploration_analysis", "data_visualization", "data_engineering", "ai"),
        monthly_usd=49.0, annual_usd=490.0,
        includes=("Everything in Starter", "Exploration & Analysis", "Data Engineering (storage and syncs)",
                  "More Beacon questions"),
    ),
    Subscription(
        id="all_tools", kind="bundle", name="Pro (all tools)",
        description="Every tool, including machine learning — the lowest price for everything.",
        service_ids=(), monthly_usd=99.0, annual_usd=990.0,
        includes=("Everything in Growth", "Data Science & Machine Learning", "Downloads included", "Cancel anytime"),
    ),
    Subscription(
        id="data_cleaning", base=True, name="Data Cleaning",
        description="Clean, fix and download your data — no per-minute charges. Every package starts here.",
        service_ids=("data_cleaning",), monthly_usd=19.0, annual_usd=190.0,
        includes=("Downloads included", "All cleaning and transform tools"),
    ),
    Subscription(
        id="data_exploration_analysis", name="Data Exploration & Analysis",
        description="Profiles, trends, statistics and answers to your business questions.",
        service_ids=("data_exploration_analysis",), monthly_usd=19.0, annual_usd=190.0,
        includes=("Data profiles and key trends", "Statistical tests", "SQL for your questions"),
    ),
    Subscription(
        id="data_visualization", name="Dashboards",
        description="Interactive KPI dashboards and charts from your data, with the query behind every chart.",
        service_ids=("data_visualization",), monthly_usd=19.0, annual_usd=190.0,
        includes=("KPI dashboards with filters", "The SQL / pandas behind each chart", "Downloadable dashboard"),
    ),
    Subscription(
        id="data_engineering", name="Data Engineering",
        description="Store your data with us, sync QuickBooks / Xero on a schedule, plus ER diagrams and data dictionaries.",
        service_ids=("data_engineering",), monthly_usd=39.0, annual_usd=390.0,
        includes=("Managed storage of your data", "Scheduled QuickBooks / Xero syncs",
                  "ER diagrams and data dictionaries", "Unlocks the AI add-on"),
    ),
    Subscription(
        id="data_science_ml", name="Data Science & Machine Learning",
        description="Predictive models with a step-by-step analysis report, top drivers and a recommendation.",
        service_ids=("data_science_ml",), monthly_usd=39.0, annual_usd=390.0,
        includes=("Predictive models, documented step by step", "Top drivers and recommendations",
                  "Just-in-time purchasing (when to reorder, how much)", "Customer segments"),
    ),
    Subscription(
        id="ai", name="AI", requires=("data_engineering",),
        # Also needs data already STORED (fact.account_data): billing.ai_ready checks it at checkout.
        description="More Beacon questions about your stored data (everyone gets free ones). Needs Data Engineering.",
        service_ids=("ai",), monthly_usd=29.0, annual_usd=290.0,
        includes=("200 questions a month (20 free without it)", "Answers with the math shown",
                  "Requires stored data (Data Engineering)"),
    ),
)
SUBSCRIPTIONS_BY_ID = {s.id: s for s in SUBSCRIPTIONS}
# The prices above, kept so the Admin panel can show "default $19" next to a
# changed price (and so a plan with no dim.plan_prices row still has one).
DEFAULT_PRICES: dict[str, tuple[float, float]] = {s.id: (s.monthly_usd, s.annual_usd) for s in SUBSCRIPTIONS}
# Free-trial days per plan (dim.plan_prices.trial_days, set in Admin -> Prices;
# missing = 0, no trial). Filled by price_store.load(); used by api/services/offers.py.
TRIAL_DAYS: dict[str, int] = {}
INTERVALS = ("month", "year")


def apply_prices(prices: dict[str, tuple[float, float]]) -> None:
    """
    Swap in the live prices (from dim.plan_prices) for every plan listed.

    Subscription objects are frozen (can't be edited), so each changed plan is
    rebuilt with `dataclasses.replace` and SUBSCRIPTIONS / SUBSCRIPTIONS_BY_ID are
    replaced. Every other module reads them as `pricing.SUBSCRIPTIONS`, so they
    all see the new prices at once. Plans not listed keep their current price;
    unknown plan ids are ignored.

    Args:
        prices: dict, plan id -> (monthly_usd, annual_usd).

    Returns:
        None.

    Example:
        >>> apply_prices({"data_visualization": (24.0, 240.0)})
        >>> SUBSCRIPTIONS_BY_ID["data_visualization"].monthly_usd
        24.0
    """
    global SUBSCRIPTIONS, SUBSCRIPTIONS_BY_ID
    SUBSCRIPTIONS = tuple(
        replace(s, monthly_usd=float(prices[s.id][0]), annual_usd=float(prices[s.id][1])) if s.id in prices else s
        for s in SUBSCRIPTIONS)
    SUBSCRIPTIONS_BY_ID = {s.id: s for s in SUBSCRIPTIONS}
BUNDLE = "all_tools"
BASE = "data_cleaning"


def plan_services(plan_id: str) -> list[str]:
    """
    List the service ids a subscription item unlocks.

    A tool unlocks its own `service_ids`. The "All tools" bundle (empty
    `service_ids`) unlocks every service that is currently available, so AI is
    added automatically once its `available` flag is switched on. An unknown plan
    id unlocks nothing.

    Args:
        plan_id: str, a Subscription id such as "data_visualization" or "all_tools".

    Returns:
        list[str]: service ids in catalog order; [] for an unknown plan id.

    Example:
        >>> plan_services("data_visualization")
        ['data_visualization']
        >>> plan_services("all_tools")   # AI is left out while it is "early access"
        ['data_cleaning', 'data_strategy_discovery', 'data_exploration_analysis',
         'data_visualization', 'data_engineering', 'data_science_ml']
        >>> plan_services("nope")
        []
    """
    plan = SUBSCRIPTIONS_BY_ID.get(plan_id)
    if plan is None:
        return []
    return [s.id for s in SERVICES if s.available] if not plan.service_ids else list(plan.service_ids)


def billable(plan_id: str) -> bool:
    """
    Tell whether a subscription item is charged today.

    The bundle is always billable. A tool is billable only when every service it
    unlocks is available; an item whose service is not launched yet (available=False) is free
    until it launches.

    Args:
        plan_id: str, a Subscription id (must exist in SUBSCRIPTIONS_BY_ID).

    Returns:
        bool: True if the item should be charged now.

    Raises:
        KeyError: if `plan_id` is not a known Subscription id.

    Example:
        >>> billable("data_cleaning")
        True
        >>> billable("ai")   # launched; False again if its service is set available=False
        True
    """
    plan = SUBSCRIPTIONS_BY_ID[plan_id]
    return plan.kind == "bundle" or all(SERVICES_BY_ID[s].available for s in plan.service_ids)


def covered_tools(plan_id: str) -> set[str]:
    """
    The tools a bundle already includes (so they're never bought twice); {} for a tool.

    Example:
        >>> sorted(covered_tools("starter"))
        ['data_cleaning', 'data_visualization']
    """
    plan = SUBSCRIPTIONS_BY_ID.get(plan_id)
    if plan is None or plan.kind != "bundle":
        return set()
    services = set(plan_services(plan_id)) | ({s.id for s in SERVICES} if not plan.service_ids else set())
    return {t.id for t in SUBSCRIPTIONS if t.kind == "tool" and set(t.service_ids) <= services}


def normalize_selection(plan_ids: list[str]) -> list[str]:
    """
    Check a selection (a bundle, tools, or a bundle + add-on tools) and return it in catalog order.

    Rules (NO DOUBLE PURCHASES):
    - every id must be a known Subscription id;
    - at most ONE bundle: if several are picked, the biggest is kept;
    - Pro (All tools) replaces everything else (the result is just ["all_tools"]);
    - any other bundle drops the tools it already covers; tools it doesn't cover
      stay as add-ons (Starter + Data Science & ML);
    - without a bundle, Data Cleaning (the base) is always added;
    - an item's requirements must be picked or covered (AI needs Data Engineering).
    Duplicates are removed.

    Args:
        plan_ids: list[str], the Subscription ids the client ticked.

    Returns:
        list[str]: the cleaned selection, in the order of SUBSCRIPTIONS.

    Raises:
        ValueError: with a plain-English message for an unknown id or a missing
            requirement.

    Example:
        >>> normalize_selection(["data_visualization"])
        ['data_cleaning', 'data_visualization']
        >>> normalize_selection(["data_visualization", "all_tools"])
        ['all_tools']
        >>> normalize_selection(["ai"])
        Traceback (most recent call last):
        ValueError: AI needs Data Engineering.
    """
    unknown = [p for p in plan_ids if p not in SUBSCRIPTIONS_BY_ID]
    if unknown:
        raise ValueError(f"Unknown tool(s): {', '.join(unknown)}.")
    bundles = [p for p in plan_ids if SUBSCRIPTIONS_BY_ID[p].kind == "bundle"]
    if bundles:
        keep = max(bundles, key=lambda b: (len(covered_tools(b)), SUBSCRIPTIONS_BY_ID[b].monthly_usd))
        if keep == BUNDLE:
            return [BUNDLE]
        chosen = {keep} | {p for p in plan_ids if SUBSCRIPTIONS_BY_ID[p].kind == "tool"
                           and p not in covered_tools(keep)}
    else:
        chosen = set(plan_ids) | {BASE}
    have = set().union(*(covered_tools(p) or {p} for p in chosen))
    for pid in chosen:
        missing = [r for r in SUBSCRIPTIONS_BY_ID[pid].requires if r not in have]
        if missing:
            names = ", ".join(SUBSCRIPTIONS_BY_ID[m].name for m in missing)
            raise ValueError(f"{SUBSCRIPTIONS_BY_ID[pid].name} needs {names}.")
    return [s.id for s in SUBSCRIPTIONS if s.id in chosen]


def selection_price(plan_ids: list[str], interval: str) -> dict:
    """
    Work out the total price for a selection on one billing interval.

    Each tool's fixed price is added together; items not billable yet (AI before
    launch) count as $0.00 with the note "Free until it launches". The result also
    says whether the All tools bundle would be cheaper than this selection.

    Args:
        plan_ids: list[str], the Subscription ids the client ticked (normalized with
            normalize_selection, so Data Cleaning is added automatically).
        interval: str, "month" or "year".

    Returns:
        dict: {"interval", "plan_ids" (normalized list), "lines" (one
            {"id", "name", "amount_usd", "note"} per item), "total_usd",
            "bundle_usd" (Pro's price on this interval), "bundle_cheaper" (bool: a
            bundle covering everything picked costs less), "cheaper_bundle"
            ({"id", "name", "amount_usd"} of the cheapest such bundle, or None)}.

    Raises:
        ValueError: if `interval` is not "month" or "year", or the selection is
            invalid (see normalize_selection).

    Example:
        >>> p = selection_price(["data_visualization"], "month")
        >>> p["plan_ids"], p["total_usd"], p["bundle_usd"], p["bundle_cheaper"]
        (['data_cleaning', 'data_visualization'], 38.0, 99.0, False)
    """
    if interval not in INTERVALS:
        raise ValueError("Choose monthly or yearly billing.")
    picked = normalize_selection(plan_ids)
    price = (lambda p: p.monthly_usd) if interval == "month" else (lambda p: p.annual_usd)
    lines = []
    for pid in picked:
        plan = SUBSCRIPTIONS_BY_ID[pid]
        amount = price(plan) if billable(pid) else 0.0
        lines.append({"id": pid, "name": plan.name, "amount_usd": amount,
                      "note": "" if billable(pid) else "Free until it launches"})
    total = round(sum(l["amount_usd"] for l in lines), 2)
    bundle = price(SUBSCRIPTIONS_BY_ID[BUNDLE])
    # The cheapest bundle that covers everything picked, if it costs less.
    wanted = set().union(*(covered_tools(p) or {p} for p in picked))
    cheaper = sorted((b for b in SUBSCRIPTIONS if b.kind == "bundle" and wanted <= covered_tools(b.id)
                      and price(b) < total and b.id not in picked), key=price)
    best = ({"id": cheaper[0].id, "name": cheaper[0].name, "amount_usd": price(cheaper[0])} if cheaper else None)
    return {"interval": interval, "plan_ids": picked, "lines": lines, "total_usd": total,
            "bundle_usd": bundle, "bundle_cheaper": best is not None, "cheaper_bundle": best}


def lookup_key(plan_id: str, interval: str) -> str:
    """
    Build the Stripe price lookup key for an item and interval.

    Stripe prices are found by lookup key, not by id, so scripts/sync_stripe.py
    creates each price with a key of the form "dcp_<plan_id>_<interval>".

    Args:
        plan_id: str, a Subscription id, e.g. "all_tools".
        interval: str, "month" or "year".

    Returns:
        str: the lookup key.

    Example:
        >>> lookup_key("all_tools", "year")
        'dcp_all_tools_year'
    """
    return f"dcp_{plan_id}_{interval}"


def plan_from_lookup_key(key: str) -> tuple[str, str] | None:
    """
    Turn a Stripe price lookup key back into (plan id, interval).

    The reverse of lookup_key. The key is split on its LAST underscore, so plan
    ids that contain underscores still work. Anything that is not a known plan on
    a known interval (for example the consulting-hour price) gives None.

    Args:
        key: str, a Stripe price lookup key such as "dcp_data_visualization_month".

    Returns:
        tuple[str, str] | None: (plan_id, interval), or None if the key is not one
            of our subscription prices.

    Example:
        >>> plan_from_lookup_key("dcp_data_visualization_month")
        ('data_visualization', 'month')
        >>> plan_from_lookup_key("dcp_consulting_hour") is None
        True
    """
    if not key.startswith("dcp_") or "_" not in key[4:]:
        return None
    plan_id, interval = key[4:].rsplit("_", 1)
    return (plan_id, interval) if plan_id in SUBSCRIPTIONS_BY_ID and interval in INTERVALS else None


# ── Consultant help: a one-time SETUP, or ONGOING help (monthly or yearly) ─────
# A "done for you" client pays their consultant through the platform (Stripe
# Connect: Stripe sends the consultant their share, the platform keeps
# platform_fee_percent — api/services/consulting_billing.py).
#   * SETUP: a fixed fee for getting everything in place (connecting sources,
#     cleaning, loading, the first dashboard).
#   * ONGOING HELP: priced by WHAT the client needs help with, not a level they
#     pick (so nobody just picks the cheapest):
#       Data Cleaning and/or Data Engineering   core_hours a week (10): the bulk —
#                                               files and QuickBooks / Xero to clean and load
#       each other tool picked                  service_hours a week (2.5): Exploration &
#                                               Analysis, Visualization, Science & ML
#       AI                                      0: the client asks it themselves
#     Data Strategy & Discovery is NOT here: it's consulting only, booked and
#     paid by the hour ($80) on Cal.com.
#     ...capped at max_hours (20). Cloud-database clients usually skip the core
#     block (nothing to clean or load). A month = hours x 52 / 12 weeks x
#     help_rate_usd; a year = 10 months (like the tools).
# Hourly sessions booked on Cal.com (CONSULTING_RATE_PER_HOUR_USD, $80) are
# separate and still paid to the consultant directly.
# These numbers live in dim.consulting_prices (Admin -> Prices -> "Consultant
# help"); the defaults below are used until it's saved once, or without a database.
CORE_HELP_SERVICES = ("data_cleaning", "data_engineering")
LIGHT_HELP_SERVICES = ("data_exploration_analysis", "data_visualization",
                       "data_science_ml")
WEEKS_PER_MONTH = 52 / 12                # 4.33: a month's share of the year's weeks
ANNUAL_MONTHS = 10                       # a year of ongoing help costs 10 months
CONSULTING_DEFAULTS: dict[str, float] = {
    "help_rate_usd": 20.0,               # per hour: the tool does most of the work
    "setup_usd": 300.0,                  # one time (about 15 hours x $20)
    "core_hours": 10.0,                  # Data Cleaning / Data Engineering, a week
    "service_hours": 2.5,                # each other service, a week
    "max_hours": 20.0,                   # never more than this a week
    "platform_fee_percent": 20.0,        # the platform's share (not taken for admins)
}
CONSULTING: dict[str, float] = dict(CONSULTING_DEFAULTS)   # the live numbers (price_store.load)


def apply_consulting_prices(values: dict) -> None:
    """
    Make these the live consultant-help numbers (from dim.consulting_prices).

    Args:
        values: dict, any of CONSULTING_DEFAULTS' keys; others are ignored.

    Example:
        >>> apply_consulting_prices({"help_rate_usd": 25})
        >>> CONSULTING["help_rate_usd"]
        25.0
    """
    CONSULTING.update({k: float(v) for k, v in values.items() if k in CONSULTING_DEFAULTS and v is not None})


def help_hours(service_ids: list[str]) -> float:
    """
    Hours a week of ongoing help for the services a client needs help with.

    Args:
        service_ids: list[str], the services picked (AI and unknown ids count 0).

    Returns:
        float: core_hours when Data Cleaning or Data Engineering is picked, plus
        service_hours per other service — at most max_hours.

    Example:
        >>> help_hours(["data_cleaning", "data_engineering", "data_visualization"])
        12.5
        >>> help_hours(["data_visualization", "data_science_ml"])     # cloud data: no core block
        5.0
    """
    picked = set(service_ids)
    hours = CONSULTING["core_hours"] if picked & set(CORE_HELP_SERVICES) else 0.0
    hours += CONSULTING["service_hours"] * len(picked & set(LIGHT_HELP_SERVICES))
    return min(hours, CONSULTING["max_hours"])


def retainer_price_usd(hours_per_week: float, interval: str) -> float:
    """
    Ongoing help's price for one billing period: a month, or a year (= ANNUAL_MONTHS months).

    Args:
        hours_per_week: float, from help_hours.
        interval: str, "month" or "year".

    Returns:
        float: dollars per period, to the cent.

    Example:
        >>> retainer_price_usd(10, "month"), retainer_price_usd(10, "year")    # $20/hour
        (866.67, 8666.7)
    """
    monthly = round(hours_per_week * WEEKS_PER_MONTH * CONSULTING["help_rate_usd"], 2)
    return round(monthly * ANNUAL_MONTHS, 2) if interval == "year" else monthly


def setup_usd() -> float:
    """The one-time setup fee."""
    return CONSULTING["setup_usd"]


def consulting_catalog() -> dict:
    """
    What a consultant's help costs, for the website (Get Started and Plan & billing).

    Returns:
        dict: {"setup_usd", "hourly_usd", "core_hours", "service_hours", "max_hours",
        "core_services", "light_services", "annual_months", "session_rate_usd",
        "min_monthly_usd" (the smallest ongoing help: one light service)}.

    Example:
        >>> consulting_catalog()["min_monthly_usd"]      # 2.5 hours a week at $20
        216.67
    """
    return {
        "setup_usd": CONSULTING["setup_usd"],
        "hourly_usd": CONSULTING["help_rate_usd"],
        "core_hours": CONSULTING["core_hours"],
        "service_hours": CONSULTING["service_hours"],
        "max_hours": CONSULTING["max_hours"],
        "core_services": list(CORE_HELP_SERVICES),
        "light_services": list(LIGHT_HELP_SERVICES),
        "annual_months": ANNUAL_MONTHS,
        "session_rate_usd": consulting_rate(),   # hourly Cal.com sessions, paid to the consultant directly
        "min_monthly_usd": retainer_price_usd(CONSULTING["service_hours"], "month"),
    }


def subscription_catalog() -> list[dict]:
    """
    List every priced item as a plain dict, with the yearly saving worked out for the UI.

    Each dict is the Subscription's fields plus:
    - "service_ids": what it unlocks (plan_services; the bundle lists every
      available service);
    - "billable": whether it is charged today;
    - "annual_per_month_usd": the annual price divided by 12;
    - "annual_savings_usd" / "annual_savings_pct": annual vs. 12 x monthly;
    - "bundle_savings_month_usd": bundle only, how much less per month than
      picking every tool separately (0 for tools);
    - "trial_days": the free trial a first package with it gets (0 = none).

    Returns:
        list[dict]: one dict per item, in the order of SUBSCRIPTIONS (bundle first).

    Example:
        >>> dc = next(s for s in subscription_catalog() if s["id"] == "data_cleaning")
        >>> dc["annual_per_month_usd"], dc["annual_savings_usd"], dc["annual_savings_pct"]
        (15.83, 38.0, 17)
    """
    out = []
    for s in SUBSCRIPTIONS:
        covers = covered_tools(s.id)
        one_by_one = sum(t.monthly_usd for t in SUBSCRIPTIONS if t.id in covers)
        full_year = s.monthly_usd * 12
        out.append({
            **asdict(s),
            "service_ids": plan_services(s.id),
            "billable": billable(s.id),
            "annual_per_month_usd": round(s.annual_usd / 12, 2),
            "annual_savings_usd": round(full_year - s.annual_usd, 2),
            "annual_savings_pct": round((full_year - s.annual_usd) / full_year * 100) if full_year else 0,
            # Bundle only: the tools it includes, and how much less than picking them one by one (per month).
            "covers": [t.id for t in SUBSCRIPTIONS if t.id in covers],
            "bundle_savings_month_usd": round(one_by_one - s.monthly_usd, 2) if s.kind == "bundle" else 0,
            "trial_days": TRIAL_DAYS.get(s.id, 0),
        })
    return out


def runtime_cost(runtime_seconds: float) -> dict:
    """
    Convert processing time into a price.

    Runtime is rounded UP to the next whole billing increment (zero runtime still
    costs one increment, because the server did the work), converted to minutes,
    multiplied by the per-minute rate, rounded to cents, and raised to the minimum
    charge if it is lower.

    Example with the defaults ($0.50 per minute, 60-second increments):
        12 seconds  -> 1 billable minute  -> $0.50
        61 seconds  -> 2 billable minutes -> $1.00

    Args:
        runtime_seconds: float, measured processing time in seconds.

    Returns:
        dict: {"runtime_seconds" (rounded to 3 places), "billing_increment_seconds",
            "billable_minutes", "rate_per_minute_usd", "amount_usd"}.

    Example:
        >>> runtime_cost(61)["billable_minutes"], runtime_cost(61)["amount_usd"]
        (2.0, 1.0)
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


# How many cells (rows × fields) per second the server writes into the
# download file. Measured on a laptop (CSV ~1,000,000/s, Excel ~65,000/s) and
# divided by about 3 to allow for Render's smaller free CPU — an estimate
# should err on the slow side so the real wait is never longer than promised.
CELLS_PER_SECOND = {"csv": 300_000, "xlsx": 20_000}
# Fixed time for the request itself: sending the files again, starting up.
DOWNLOAD_OVERHEAD_SECONDS = 3.0


def download_estimate(runtime_seconds: float, rows: int, columns: int) -> dict:
    """
    Estimate the wait and the charge for downloading one cleaned table.

    The download re-runs the same cleaning (so it takes about `runtime_seconds`
    again — the time the analysis just measured) and then writes the file,
    which scales with the number of cells (rows × fields).

    The CHARGE is only the processing time (runtime_cost), the same rule as
    everywhere else, so the estimate here is what the client is asked to
    confirm. Example: 18,210 rows × 13 fields = 236,730 cells
        CSV:   0.2 s cleaning + 0.8 s writing + 3 s overhead ≈ 4 seconds
        Excel: 0.2 s cleaning + 11.8 s writing + 3 s overhead ≈ 15 seconds

    Args:
        runtime_seconds: float, how long the cleaning took when it was analyzed.
        rows: int, number of rows in the cleaned table (negative counts as 0).
        columns: int, number of fields in the cleaned table (negative counts as 0).

    Returns:
        dict: {"rows", "columns", "cells", "amount_usd", "billable_minutes",
            "formats"}, where "formats" maps "csv" and "xlsx" to
            {"estimated_seconds", "label" (wait_label text), "under_five_minutes"}.

    Example:
        >>> est = download_estimate(0.2, 18210, 13)   # default settings
        >>> est["cells"], est["amount_usd"], est["formats"]["csv"]["estimated_seconds"]
        (236730, 0.5, 4.0)
    """
    cells = max(int(rows), 0) * max(int(columns), 0)
    cost = runtime_cost(runtime_seconds)
    by_format = {}
    for fmt, speed in CELLS_PER_SECOND.items():
        seconds = runtime_seconds + cells / speed + DOWNLOAD_OVERHEAD_SECONDS
        by_format[fmt] = {
            "estimated_seconds": round(seconds, 1),
            "label": wait_label(seconds),
            "under_five_minutes": seconds < 300,
        }
    return {
        "rows": int(rows),
        "columns": int(columns),
        "cells": cells,
        "amount_usd": cost["amount_usd"],
        "billable_minutes": cost["billable_minutes"],
        "formats": by_format,
    }


def wait_label(seconds: float) -> str:
    """
    Turn a number of seconds into a plain-English wait time.

    Under 10 seconds -> "under 10 seconds"; under a minute -> rounded UP to the
    next 10 seconds; otherwise rounded UP to whole minutes ("minute" when it is 1).

    Args:
        seconds: float, the estimated wait.

    Returns:
        str: e.g. 'under 10 seconds', 'about 40 seconds', 'about 3 minutes'.

    Example:
        >>> wait_label(4.2), wait_label(37), wait_label(60), wait_label(150)
        ('under 10 seconds', 'about 40 seconds', 'about 1 minute', 'about 3 minutes')
    """
    if seconds < 10:
        return "under 10 seconds"
    if seconds < 60:
        return f"about {int(math.ceil(seconds / 10.0) * 10)} seconds"
    minutes = int(math.ceil(seconds / 60))
    return f"about {minutes} minute{'s' if minutes != 1 else ''}"


def build_quote(service_ids: list[str], runtime_seconds: float | None = None) -> dict:
    """
    Price the selected services.

    Data cleaning is ALWAYS included (it is the automatic first step), even
    if the client did not tick it.

    Each line is priced by its pricing model:
    - "runtime": runtime_cost(runtime_seconds) (no runtime yet -> one increment);
    - "package": the starting price;
    - "early_access": $0.00;
    - "hourly": one hour at the consulting rate;
    - "consultation": $0.00 (the free consultation comes first).
    If any selected service requires a consultant, the booking link is included.

    Args:
        service_ids: list[str], the service ids the client ticked.
        runtime_seconds: float | None, measured cleaning runtime, if known.

    Returns:
        dict: {"lines" (one {"service_id", "name", "pricing_model", "amount_usd",
            "note"} per service), "estimated_total_usd", "currency" ("USD"),
            "requires_consultant" (bool), "booking_url" (str or None)}.

    Raises:
        ValueError: if any service id is unknown.

    Example:
        >>> q = build_quote(["data_visualization"])   # default settings
        >>> [l["service_id"] for l in q["lines"]], q["estimated_total_usd"], q["booking_url"]
        (['data_cleaning', 'data_visualization'], 500.5, None)
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
        elif svc.pricing_model == "package" and not svc.starting_price_usd:
            amount = 0.0      # AI: free questions every month, more with the add-on
            note = "Free questions every month; more with the AI add-on (needs Data Engineering)."
        elif svc.pricing_model == "package":
            amount = svc.starting_price_usd
            note = "Starting price — final price depends on scope."
        elif svc.pricing_model == "early_access":
            amount = 0.0
            note = "Early access — we'll reach out when it opens."
        elif svc.pricing_model == "hourly":
            amount = consulting_rate()
            note = f"Paid consulting: ${amount:,.0f}/hour (first hour shown)."
        else:
            amount = 0.0
            note = consultation_terms(svc)["text"] + "."
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
    """
    List all services as plain dicts (for the API), with their skill capabilities.

    Each dict is the Service's fields plus:
    - "capabilities": client-friendly skill labels from data_skills/catalog.json;
    - "consultation": consultation_terms for the service;
    - "tool_monthly_usd" / "tool_annual_usd": the do-it-yourself subscription
      price when the service is also a tool (None otherwise, e.g. Data Strategy);
    - "tool_requires": tools that must be picked with it (AI -> Data Engineering).

    Returns:
        list[dict]: one dict per service, in the order of SERVICES.

    Example:
        >>> dv = next(s for s in catalog() if s["id"] == "data_visualization")
        >>> dv["tool_monthly_usd"], dv["tool_annual_usd"], dv["tool_requires"]
        (19.0, 190.0, [])
    """
    out = []
    for s in SERVICES:
        tool = SUBSCRIPTIONS_BY_ID.get(s.id)   # the tool you can subscribe to, if any
        out.append({**asdict(s), "capabilities": capability_labels(s.id), "consultation": consultation_terms(s),
                    # Do-it-yourself price as a package tool (None = not a tool, e.g. Data Strategy).
                    "tool_monthly_usd": tool.monthly_usd if tool else None,
                    "tool_annual_usd": tool.annual_usd if tool else None,
                    "tool_requires": list(tool.requires) if tool else []})
    return out


# ── Connected-data refresh (how often a connector pulls new data) ──────────
# A connector (QuickBooks first) copies a client's data into managed storage,
# so it needs Data Engineering. Each pull costs us API calls and compute, so
# the everyday choices are included and the frequent ones are paid add-ons.
# PLACEHOLDER PRICES, per connection per month. Not in Stripe yet: they are
# shown as "free until connectors launch" (billable=False) — when connectors
# go live, set billable=True and add Stripe prices (scripts/sync_stripe.py).


@dataclass(frozen=True)
class RefreshOption:
    id: str                          # stored in data_connections.refresh_frequency
    label: str
    description: str
    interval_minutes: int | None     # scheduled pull every N minutes; None = only on request
    refresh_now_gap_minutes: int     # "Refresh now" no sooner than this after the last pull
    monthly_usd: float               # add-on price per connection (0 = included)
    billable: bool = False           # False = not charged yet (connectors not launched)


REFRESH_OPTIONS: tuple[RefreshOption, ...] = (
    RefreshOption(
        id="manual", label="Only when I ask", interval_minutes=None, refresh_now_gap_minutes=60, monthly_usd=0.0,
        description="Nothing runs on a schedule; click Refresh now (at most once an hour)."),
    RefreshOption(
        id="weekly", label="Once a week", interval_minutes=7 * 24 * 60, refresh_now_gap_minutes=24 * 60,
        monthly_usd=0.0, description="Pulls new and changed records every week. Good for monthly reporting."),
    RefreshOption(
        id="daily", label="Once a day", interval_minutes=24 * 60, refresh_now_gap_minutes=4 * 60, monthly_usd=0.0,
        description="Pulls new and changed records every night. The default."),
    RefreshOption(
        id="hourly", label="Every hour", interval_minutes=60, refresh_now_gap_minutes=15, monthly_usd=15.0,
        description="Pulls new and changed records every hour. Paid add-on."),
    RefreshOption(
        id="live", label="Live (about every 15 minutes)", interval_minutes=15, refresh_now_gap_minutes=5,
        monthly_usd=40.0,
        description="Near real time: changes arrive within about 15 minutes. Paid add-on."),
)
REFRESH_OPTIONS_BY_ID = {o.id: o for o in REFRESH_OPTIONS}
DEFAULT_REFRESH = "daily"


def refresh_catalog() -> list[dict]:
    """
    List the refresh choices as plain dicts (for the API and the web picker).

    Each dict is the RefreshOption's fields plus "included" (True when it costs
    nothing extra) and "price_label" (e.g. "Included", "+$15.00/month").

    Returns:
        list[dict]: one dict per option, slowest-to-fastest except "manual" first.

    Example:
        >>> [(o["id"], o["price_label"]) for o in refresh_catalog()][3]
        ('hourly', '+$15.00/month')
    """
    return [{**asdict(o), "included": o.monthly_usd == 0,
             "default": o.id == DEFAULT_REFRESH,
             "price_label": "Included" if o.monthly_usd == 0 else f"+${o.monthly_usd:,.2f}/month"}
            for o in REFRESH_OPTIONS]

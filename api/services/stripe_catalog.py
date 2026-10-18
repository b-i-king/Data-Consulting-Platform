"""
Stripe products and prices for the tools on sale (pricing.SUBSCRIPTIONS).

WHAT THIS FILE DOES
-------------------
For every TOOL (and the All tools bundle) it makes sure Stripe has:
    one PRODUCT  (e.g. "Dashboards")
    two PRICES   monthly and annual, found later by their "lookup key":
                 dcp_data_visualization_month, dcp_data_visualization_year

Stripe prices can't be edited once created. When a price changes, a NEW
Stripe price is created, the lookup key is moved to it (`transfer_lookup_key`)
and the old one is archived. Existing subscribers keep the old price (Stripe
keeps billing an archived price) until you move them in the Stripe dashboard;
new checkouts use the new one.

SEASONAL SALES
--------------
ensure_coupon(stripe, sale) makes (once) the Stripe coupon for a sale in
dim.seasonal_sales: percent off, duration "once" (the first payment). Its id
carries the percent (dcp_sale_year_end_25), so changing the percent makes a
new coupon and the old one simply stops being used.

WHO CALLS IT
------------
  * scripts/sync_stripe.py         every plan (run it after setting up Stripe)
  * api/services/price_store.py    one plan, when an admin changes its price

`stripe` is passed in (the real library, or a test double), so nothing here
talks to the network by itself.
"""

from __future__ import annotations

from api.services import pricing


def _cents(usd: float) -> int:
    """
    Convert a US dollar amount to whole cents, the unit Stripe prices use.

    Rounds to the nearest cent first, so floating-point noise (e.g. 19.999999)
    does not lose a cent.

    Args:
        usd: Amount in US dollars, e.g. 49.0.

    Returns:
        int: Amount in cents, e.g. 4900.

    Example:
        >>> _cents(49.0), _cents(19.99)
        (4900, 1999)
    """
    return int(round(usd * 100))


def product_id(key: str) -> str:
    """Our fixed Stripe product id for a plan: "dcp_data_visualization" (Stripe accepts our own ids)."""
    return f"dcp_{key}"


def ensure_product(stripe, key: str, name: str, description: str, dry: bool, log) -> str | None:
    """
    Find the plan's product, or create it. Returns its id.

    Rules:
    - Looked up by OUR fixed id (product_id: "dcp_<key>") with Product.retrieve —
      instant and exact, so running the script twice in a row can never make a
      duplicate. (Stripe's search, used before, can lag about a minute behind.)
    - Not found by id: Stripe's search for metadata "dcp_key" = key finds a
      product made by an older version of this script, which is then used.
    - Found: updates its name/description only if they differ, and returns its id.
    - Not found at all: creates it with id "dcp_<key>" and metadata {"dcp_key": key}.
      An empty description is sent to Stripe as None.
    - Dry run: logs what would change and changes nothing; returns None for a product
      that does not exist yet.

    Args:
        stripe: The configured `stripe` module (or a test double with the same API).
        key: Our stable product key: a plan id such as "data_visualization".
        name: Product name shown in Stripe and on invoices, e.g. "Dashboards".
        description: Product description ("" for none).
        dry: If True, only log; make no changes in Stripe.
        log: Callable that receives one message string per change (e.g. print).

    Returns:
        str | None: The Stripe product id ("dcp_..." or an older "prod_..."), or None in
        a dry run when the product would have been created.

    Example:
        >>> ensure_product(stripe, "ai", "AI", "", dry=False, log=print)
        create product ai (AI)
        'dcp_ai'
    """
    try:
        product = stripe.Product.retrieve(product_id(key))
    except Exception:   # "No such product": not made yet (or made by an older version)
        found = stripe.Product.search(query=f"metadata['dcp_key']:'{key}'", limit=1).data
        product = found[0] if found else None
    if product is not None:
        if product.name != name or (product.description or "") != description:
            log(f"update product {key}")
            if not dry:
                stripe.Product.modify(product.id, name=name, description=description or None)
        return product.id
    log(f"create product {key} ({name})")
    if dry:
        return None
    return stripe.Product.create(id=product_id(key), name=name, description=description or None,
                                 metadata={"dcp_key": key}).id


def ensure_price(stripe, product_id: str | None, lookup_key: str, cents: int, interval: str | None,
                 dry: bool, log) -> str | None:
    """
    Make sure the lookup key points at a price with this amount (and billing interval).

    Stripe prices cannot be edited, so:
    - If the active price behind `lookup_key` already has this amount and interval,
      nothing changes and its id is returned.
    - Otherwise a NEW price is created, the lookup key is moved onto it
      (`transfer_lookup_key=True`), and the old price (if any) is archived
      (active=False). Existing subscribers keep the old price until migrated.
    - Dry run, or no product id yet (the product was not created in a dry run): logs the
      change and returns the current price id (or None); nothing is created.

    Args:
        stripe: The configured `stripe` module (or a test double with the same API).
        product_id: The Stripe product the price belongs to, or None in a dry run.
        lookup_key: Our stable key for the price, e.g. "dcp_data_visualization_month".
        cents: Price amount in US cents.
        interval: "month" or "year" for a subscription price, None for a one-time price.
        dry: If True, only log; make no changes in Stripe.
        log: Callable that receives one message string per change (e.g. print).

    Returns:
        str | None: The id of the price the lookup key now points at ("price_..."),
        or None in a dry run when no price exists yet.

    Example:
        >>> ensure_price(stripe, "prod_123", "dcp_consulting_hour", 8000, None, dry=False, log=print)
        create price dcp_consulting_hour: $80.00
        'price_...'
    """
    current = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1).data
    if current:
        price = current[0]
        same_interval = (price.recurring.interval if price.recurring else None) == interval
        if price.unit_amount == cents and same_interval:
            return price.id
    log(f"{'replace' if current else 'create'} price {lookup_key}: ${cents / 100:,.2f}"
        + (f" per {interval}" if interval else ""))
    if dry or product_id is None:
        return current[0].id if current else None
    new = stripe.Price.create(
        product=product_id, currency="usd", unit_amount=cents, lookup_key=lookup_key,
        transfer_lookup_key=True,   # move the key from the old price to this one
        **({"recurring": {"interval": interval}} if interval else {}),
    )
    if current:
        stripe.Price.modify(current[0].id, active=False)   # archive the old price
    return new.id


def sync(stripe, dry: bool = False, log=print) -> list[dict]:
    """
    Sync every plan. Returns one summary row per plan (printed, not stored).

    For each plan in pricing.SUBSCRIPTIONS (in order) it ensures one product and two
    prices (monthly and annual, via pricing.lookup_key).

    Args:
        stripe: The configured `stripe` module (or a test double with the same API).
        dry: If True, only log what would change; make no changes in Stripe.
        log: Callable that receives one message string per change (default: print).

    Returns:
        list[dict]: One row per plan, with keys id, name,
        description, service_ids, monthly_price_usd, annual_price_usd, sort_order,
        stripe_product_id, kind, is_base, requires, stripe_monthly_price_id and
        stripe_annual_price_id (Stripe ids may be None in a dry run).

    Example:
        >>> rows = sync(stripe, dry=True, log=lambda msg: None)
        >>> rows[0]["sort_order"]
        0
    """
    return [{**sync_plan(stripe, plan, dry, log), "sort_order": order}
            for order, plan in enumerate(pricing.SUBSCRIPTIONS)]


def sync_plan(stripe, plan: pricing.Subscription, dry: bool = False, log=print) -> dict:
    """
    Sync ONE plan: its product and its monthly and annual prices.

    Used by sync() for every plan, and by the Admin panel's price editor
    (api/services/price_store.py) for the one plan whose price changed.

    Args:
        stripe: The configured `stripe` module (or a test double with the same API).
        plan: pricing.Subscription, the plan with the prices Stripe should have.
        dry: If True, only log what would change; make no changes in Stripe.
        log: Callable that receives one message string per change (default: print).

    Returns:
        dict: {"id", "name", "description", "service_ids", "monthly_price_usd",
        "annual_price_usd", "stripe_product_id", "kind", "is_base", "requires",
        "stripe_monthly_price_id", "stripe_annual_price_id"}.

    Example:
        >>> sync_plan(stripe, pricing.SUBSCRIPTIONS_BY_ID["ai"], log=print)["stripe_monthly_price_id"]
        'price_...'
    """
    product = ensure_product(stripe, plan.id, plan.name, plan.description, dry, log)
    return {
        "id": plan.id, "name": plan.name, "description": plan.description,
        "service_ids": list(plan.service_ids), "monthly_price_usd": plan.monthly_usd,
        "annual_price_usd": plan.annual_usd, "stripe_product_id": product,
        "kind": plan.kind, "is_base": plan.base, "requires": list(plan.requires),
        "stripe_monthly_price_id": ensure_price(stripe, product, pricing.lookup_key(plan.id, "month"),
                                                _cents(plan.monthly_usd), "month", dry, log),
        "stripe_annual_price_id": ensure_price(stripe, product, pricing.lookup_key(plan.id, "year"),
                                               _cents(plan.annual_usd), "year", dry, log),
    }


def coupon_id(sale: dict) -> str:
    """The Stripe coupon id for a seasonal sale: dcp_sale_<id>_<percent>."""
    return f"dcp_sale_{sale['id']}_{int(sale['percent_off'])}"


def ensure_coupon(stripe, sale: dict) -> str:
    """
    Return the Stripe coupon for a seasonal sale, creating it the first time.

    Args:
        stripe: The configured `stripe` module (or a test double).
        sale: dict, from offers.current_sale(): {"id", "name", "percent_off", ...}.

    Returns:
        str: the coupon id, e.g. "dcp_sale_year_end_25".

    Example:
        >>> ensure_coupon(stripe, {"id": "year_end", "name": "Year-end sale", "percent_off": 25})
        'dcp_sale_year_end_25'
    """
    cid = coupon_id(sale)
    try:
        return stripe.Coupon.retrieve(cid).id
    except Exception:   # not made yet (Stripe answers "No such coupon")
        return stripe.Coupon.create(id=cid, percent_off=int(sale["percent_off"]), duration="once",
                                    name=f"{sale['name']} ({int(sale['percent_off'])}% off)"[:40]).id

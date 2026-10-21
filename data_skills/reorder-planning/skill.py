"""
reorder-planning — just-in-time purchasing: when to buy, and how much (Data Science & ML).

Python implementation of README.md in this folder. From a sales table (a date,
a product and the quantity sold), per product:

  1. Demand        — average units a day over the recent window (default the last
                     8 weeks, days with no sales counted as 0) and how much it
                     varies day to day (standard deviation).
  2. Safety stock  — z x daily variation x sqrt(lead time): the cushion that covers
                     a busier-than-usual week while the order is on its way
                     (z from the service level: 95% -> 1.645).
  3. Reorder point — demand during the lead time + safety stock: order when the
                     stock on hand falls to this.
  4. With stock on hand (an on-hand column, or values given): days of stock left,
                     the DATE of the next purchase (when stock reaches the reorder
                     point), a status (order now / this week / OK), and how much to
                     order so it lasts the lead time + one ordering cycle.
     Without it: the reorder point and a typical order size to plan around.
  5. When it sells — the weekday pattern, fast and slow movers (no sales in 30
                     days), and the trend vs. the 4 weeks before.

Just-in-time: buy as late as possible without running out — less cash tied up
in stock, less waste. Compute is tiny (a few pandas group-bys).

OPERATIONAL DATA ONLY: it runs when the table has a product-like column
(product / item / SKU ...) and a quantity-like number (qty / quantity / units ...),
or when the person names them. A survey, a customer list or an expense report
is refused with a plain message (OPERATIONAL_ONLY) instead of planning nonsense.
"""

from __future__ import annotations

import json
import math

import pandas as pd
from scipy import stats

from data_skills._core import (
    Param, SkillInputError, SkillResult, SkillSpec, as_datetime, bar_chart, categorical_columns, date_columns,
    fmt_num, numeric_columns, require_column,
)

SKILL = SkillSpec(
    id="reorder-planning",
    title="Just-in-time purchasing (reorder planning)",
    description="When to buy each product and how much: demand, safety stock, reorder point, the next purchase "
                "date, and when products sell.",
    params={
        "date_column": Param("column", None, "Sale date"),
        "item_column": Param("column", None, "Product / item / SKU"),
        "quantity_column": Param("column", None, "Units sold"),
        "on_hand_column": Param("column", None, "Optional: stock on hand (the latest value per product is used)"),
        "on_hand": Param("json", None, 'Optional: stock on hand per product, e.g. {"Sourdough Loaf": 40}'),
        "lead_time_days": Param("int", 7, "Days from placing an order to having it on the shelf"),
        "review_days": Param("int", 7, "How often you place orders, in days"),
        "service_level": Param("float", 0.95, "How sure you want to be of not running out (0.5 to 0.999)"),
        "history_days": Param("int", 56, "Recent days used to estimate demand"),
        "as_of": Param("str", None, "Plan as of this date (YYYY-MM-DD; default: the last sale date)"),
    },
)

SLOW_DAYS = 30            # no sales in this many days = a slow mover
DISCLAIMER = ("These are recommendations based on your recent sales, assuming the next weeks look like them and "
              "the lead time is right. Adjust for promotions, seasons and supplier delays.")
ITEM_HINT = ("product", "item", "sku", "menu", "article", "part", "material", "ingredient")
QTY_HINT = ("qty", "quantity", "units", "unit_sold", "sold", "count", "pieces", "pcs")
OPERATIONAL_ONLY = ("Just-in-time purchasing is for operational data: sales or inventory with a product (item / "
                    "SKU) column and a quantity (units sold) column. This table doesn't look like that — choose "
                    "those columns if they're named differently.")


def _pick(df: pd.DataFrame, given: str | None, kind: str, candidates: list[str], hints: tuple[str, ...]) -> str:
    """
    The column the person named, else the first candidate whose NAME matches a hint.

    No guessing beyond the hints: a table without a product-like and a
    quantity-like column isn't operational data, so it's refused (OPERATIONAL_ONLY).
    """
    if given:
        return require_column(df, given, kind, candidates)
    hinted = [c for c in candidates if any(h in str(c).lower() for h in hints)]
    if not hinted:
        raise SkillInputError(OPERATIONAL_ONLY)
    return hinted[0]


def _on_hand(df: pd.DataFrame, item: str, date: str, column: str | None, values) -> dict[str, float]:
    """Stock on hand per product: the given values, else the latest value of the on-hand column, else {}."""
    if values:
        data = json.loads(values) if isinstance(values, str) else values
        if not isinstance(data, dict):
            raise SkillInputError('on_hand must look like {"Product name": 40}.')
        return {str(k): float(v) for k, v in data.items()}
    if column:
        latest = df.dropna(subset=[column]).sort_values(date).groupby(item)[column].last()
        return {str(k): float(v) for k, v in latest.items()}
    return {}


def run(df, date_column=None, item_column=None, quantity_column=None, on_hand_column=None, on_hand=None,
        lead_time_days=7, review_days=7, service_level=0.95, history_days=56, as_of=None) -> SkillResult:
    """
    Plan just-in-time purchasing per product from sales history.

    Args:
        df: pd.DataFrame, one row per sale (or per sales line).
        date_column, item_column, quantity_column: the sale date, product and units
            sold (auto-detected when not given: a date column, a product-like
            category, a quantity-like number).
        on_hand_column: str | None, stock on hand per row (latest per product used).
        on_hand: dict | str | None, stock on hand per product, e.g. {"Sourdough Loaf": 40}.
        lead_time_days: int, days from ordering to having it (default 7).
        review_days: int, how often orders are placed (default 7).
        service_level: float, chance of not running out during a lead time (default 0.95).
        history_days: int, recent days used for demand (default 56 = 8 weeks).
        as_of: str | None, plan date (default: the last sale date).

    Returns:
        SkillResult: summary (how many products need ordering now / this week);
        findings (the most urgent, the weekday pattern, slow movers, the
        disclaimer); tables "reorder_plan" (per product: demand, safety stock,
        reorder point, on hand, days of stock, next purchase date, order quantity,
        status, trend) and "weekday_pattern"; a bar chart; data {"as_of",
        "lead_time_days", "service_level", "z", "has_on_hand", "disclaimer"}.

    Raises:
        SkillInputError: missing columns, a bad option, or no sales in the window.

    Example:
        >>> res = run(sales, on_hand={"Sourdough Loaf": 40}, lead_time_days=3)
        >>> res.tables["reorder_plan"][0]["status"]
        'Order now'
    """
    if not 0.5 <= float(service_level) < 1:
        raise SkillInputError("service_level must be between 0.5 and 0.999 (e.g. 0.95).")
    if int(lead_time_days) < 0 or int(review_days) < 1 or int(history_days) < 14:
        raise SkillInputError("Use a lead time of 0+ days, ordering every 1+ days, and 14+ days of history.")
    date = require_column(df, date_column, "date", date_columns(df))
    item = _pick(df, item_column, "product", categorical_columns(df, max_unique=5000), ITEM_HINT)
    qty = _pick(df, quantity_column, "quantity", [c for c in numeric_columns(df) if c != on_hand_column], QTY_HINT)
    if on_hand_column and on_hand_column not in df.columns:
        raise SkillInputError(f"Column '{on_hand_column}' was not found.")

    data = df[[date, item, qty] + ([on_hand_column] if on_hand_column else [])].copy()
    data[date] = as_datetime(data[date]).dt.normalize()
    data[qty] = pd.to_numeric(data[qty], errors="coerce")
    data = data.dropna(subset=[date, item, qty])
    data[item] = data[item].astype(str)
    if data.empty:
        raise SkillInputError("No sales rows with a date, a product and a quantity.")
    end = pd.Timestamp(as_of).normalize() if as_of else data[date].max()
    start = end - pd.Timedelta(days=int(history_days) - 1)
    window = data[(data[date] >= start) & (data[date] <= end)]
    if window.empty:
        raise SkillInputError(f"No sales between {start.date()} and {end.date()} — try a longer history_days.")

    # 1. Demand: units per product per day, every day in the window (no sale = 0).
    days = pd.date_range(start, end, freq="D")
    daily = (window.groupby([item, date])[qty].sum().unstack(fill_value=0)
             .reindex(columns=days, fill_value=0))
    z = float(stats.norm.ppf(float(service_level)))
    L, R = int(lead_time_days), int(review_days)
    stock = _on_hand(df if on_hand_column else data, item, date, on_hand_column, on_hand)
    last_sale = data.groupby(item)[date].max()
    # Trend: the last 4 weeks vs. the 4 before (when the window is long enough).
    recent = daily.iloc[:, -28:].sum(axis=1)
    before = daily.iloc[:, -56:-28].sum(axis=1) if daily.shape[1] >= 56 else None

    plan = []
    for name, row in daily.iterrows():
        d, sd = float(row.mean()), float(row.std(ddof=1)) if len(row) > 1 else 0.0
        safety = z * sd * math.sqrt(L) if L else 0.0
        rop = d * L + safety
        entry = {"product": name, "units_per_day": round(d, 2), "daily_variation": round(sd, 2),
                 "safety_stock": math.ceil(safety), "reorder_point": math.ceil(rop),
                 "order_size_per_cycle": math.ceil(d * (L + R) + safety)}
        if before is not None and before[name] > 0:
            entry["trend_vs_prior_4_weeks_pct"] = round(float((recent[name] - before[name]) / before[name] * 100), 1)
        if name in stock:
            have = stock[name]
            cover = have / d if d > 0 else None
            wait = max(0.0, (have - rop) / d) if d > 0 else None
            status = ("Order now" if have <= rop else
                      "Order this week" if wait is not None and wait <= R else "OK")
            entry.update(on_hand=have, days_of_stock=None if cover is None else round(cover, 1),
                         next_purchase_date=None if wait is None else (end + pd.Timedelta(days=math.floor(wait))).date().isoformat(),
                         order_quantity=max(0, math.ceil(d * (L + R) + safety - have)), status=status)
        plan.append(entry)
    order = {"Order now": 0, "Order this week": 1, "OK": 2}
    plan.sort(key=lambda e: (order.get(e.get("status"), 3), e.get("days_of_stock") or 1e9, -e["units_per_day"]))

    # 5. When it sells: units by weekday (all products), and slow movers.
    by_day = window.groupby(window[date].dt.day_name())[qty].sum()
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    total = float(by_day.sum()) or 1.0
    weekday = [{"weekday": w, "units": float(by_day.get(w, 0)), "share_pct": round(float(by_day.get(w, 0)) / total * 100, 1)}
               for w in weekdays]
    busiest = max(weekday, key=lambda r: r["units"])
    slow = sorted(n for n, last in last_sale.items() if (end - last).days >= SLOW_DAYS)

    has_stock = bool(stock)
    if has_stock:
        now = [e for e in plan if e.get("status") == "Order now"]
        week = [e for e in plan if e.get("status") == "Order this week"]
        summary = (f"{len(now)} product{'s' if len(now) != 1 else ''} to order now and {len(week)} this week, "
                   f"with a {L}-day lead time ({float(service_level):.0%} confidence of not running out).")
        urgent = [f"Order now: {e['product']} — {fmt_num(e['on_hand'], 0)} on hand, reorder point "
                  f"{e['reorder_point']}; order {e['order_quantity']}." for e in now[:3]]
    else:
        summary = (f"Reorder points for {len(plan)} products with a {L}-day lead time — add stock on hand to get "
                   "purchase dates and order quantities.")
        urgent = [f"{e['product']}: order when stock reaches {e['reorder_point']}; a typical order is "
                  f"{e['order_size_per_cycle']} (every {R} days)." for e in sorted(plan, key=lambda e: -e["units_per_day"])[:3]]
    findings = [*urgent,
                f"Busiest day: {busiest['weekday']} ({busiest['share_pct']}% of units) — have stock in before it.",
                *([f"Slow movers (no sales in {SLOW_DAYS}+ days): {', '.join(slow[:8])} — buy only on demand."] if slow else []),
                DISCLAIMER]
    top = sorted(plan, key=lambda e: -e["units_per_day"])[:10]
    return SkillResult(
        skill=SKILL.id, summary=summary, findings=findings,
        tables={"reorder_plan": plan, "weekday_pattern": weekday},
        charts=[bar_chart("Units sold per day (top products)", [e["product"] for e in top],
                          [e["units_per_day"] for e in top], "units / day")],
        data={"as_of": end.date().isoformat(), "lead_time_days": L, "review_days": R,
              "service_level": float(service_level), "z": round(z, 3), "has_on_hand": has_stock,
              "columns": {"date": date, "item": item, "quantity": qty}, "disclaimer": DISCLAIMER},
    )

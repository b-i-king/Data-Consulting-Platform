"""
Generate realistic sample data for trying the platform end to end.

WHAT IT MAKES (in samples/)
---------------------------
A fictional two-location bakery, "Sunrise Bakery", over two years:

    sales.csv                  ~18,000 order lines with dates, products, stores
    customers.csv              400 customers (IDs, names, emails, phones, cities)
    feedback.csv               customer satisfaction (CSAT 1–5) and NPS (0–10) responses
    expenses.csv               rent, payroll, ingredients, utilities, marketing
    quickbooks_customers.csv   the SAME customers as exported from QuickBooks —
                               no customer_id, slightly different name spellings —
                               for testing fuzzy matching

The sales have a real story to find: steady growth, busier weekends, a holiday
bump every November–December, a price increase on June 1, 2025, and a few
messy rows (exact duplicates, blank lines, stray spaces) for the cleaning step.

HOW TO RUN
----------
    python scripts/make_sample_data.py            # writes samples/
    python scripts/make_sample_data.py --end 2026-10-10

The random seed is fixed, so everyone gets identical files.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"

FIRST = ["Ana", "Ben", "Cora", "Dev", "Eli", "Fatima", "Gus", "Hana", "Ivan", "Jada", "Kofi", "Lena",
         "Malik", "Nia", "Omar", "Priya", "Quinn", "Rosa", "Sam", "Tariq", "Uma", "Vic", "Wen", "Xavi",
         "Yara", "Zane"]
LAST = ["Diaz", "Lee", "Kim", "Patel", "Johnson", "Nguyen", "Okafor", "Garcia", "Smith", "Brown",
        "Williams", "Jones", "Martinez", "Davis", "Lopez", "Wilson", "Moore", "Taylor", "Thomas", "Jackson"]
CITIES = [("Atlanta", "GA"), ("Decatur", "GA"), ("Marietta", "GA"), ("Smyrna", "GA"), ("Athens", "GA")]
PRODUCTS = [  # name, category, price before / after the June 2025 price increase, popularity weight
    ("Sourdough Loaf", "Bread", 7.00, 7.50, 10),
    ("Baguette", "Bread", 4.00, 4.25, 8),
    ("Croissant", "Pastry", 3.50, 3.75, 12),
    ("Cinnamon Roll", "Pastry", 4.25, 4.50, 9),
    ("Blueberry Muffin", "Pastry", 3.25, 3.50, 7),
    ("Latte", "Coffee", 4.75, 5.00, 14),
    ("Drip Coffee", "Coffee", 2.50, 2.75, 11),
    ("Celebration Cake", "Cakes", 38.00, 42.00, 1),
    ("Cupcake Box (6)", "Cakes", 18.00, 19.50, 2),
]


def make(end: pd.Timestamp, seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    start = end - pd.DateOffset(years=2) + pd.Timedelta(days=1)
    days = pd.date_range(start, end, freq="D")

    # ── customers ────────────────────────────────────────────────────────────
    n_cust = 400
    first = rng.choice(FIRST, n_cust)
    last = rng.choice(LAST, n_cust)
    city_idx = rng.integers(0, len(CITIES), n_cust)
    customers = pd.DataFrame({
        "customer_id": np.arange(1001, 1001 + n_cust),
        "first_name": first,
        "last_name": last,
        "email": [f"{f.lower()}.{l.lower()}{i}@example.com" for i, (f, l) in enumerate(zip(first, last))],
        "phone": [f"(404) 555-{1000 + i:04d}" for i in range(n_cust)],
        "city": [CITIES[i][0] for i in city_idx],
        "state": [CITIES[i][1] for i in city_idx],
        "signup_date": (start + pd.to_timedelta(rng.integers(0, 600, n_cust), unit="D")).date,
    })
    # Some customers never gave an email (realistic missing values).
    customers.loc[rng.random(n_cust) < 0.12, "email"] = None

    # ── sales ────────────────────────────────────────────────────────────────
    t = np.arange(len(days))
    growth = 1 + 0.35 * t / len(days)                         # +35% over two years
    weekend = np.where(days.dayofweek >= 5, 1.45, 1.0)          # busier Sat/Sun
    holidays = np.where(days.month.isin([11, 12]), 1.3, 1.0)    # holiday season
    expected = 12 * growth * weekend * holidays
    orders_per_day = rng.poisson(expected)

    weights = np.array([p[4] for p in PRODUCTS], dtype=float)
    weights /= weights.sum()
    price_change = pd.Timestamp("2025-06-01")
    rows = []
    order_id = 50001
    for day, n in zip(days, orders_per_day):
        for _ in range(n):
            store = "Midtown" if rng.random() < 0.6 else "Decatur"
            cust = int(rng.integers(1001, 1001 + n_cust)) if rng.random() < 0.7 else None  # 30% walk-ins
            for _line in range(int(rng.integers(1, 3))):
                p = PRODUCTS[rng.choice(len(PRODUCTS), p=weights)]
                qty = int(rng.integers(1, 4))
                price = p[3] if day >= price_change else p[2]
                rows.append({
                    "order_id": order_id,
                    "order_date": day.date().isoformat(),
                    "customer_id": cust,
                    "product": p[0],
                    "category": p[1],
                    "store": store,
                    "quantity": qty,
                    "unit_price": price,
                    "amount": round(qty * price, 2),
                    "payment_method": rng.choice(["Card", "Card", "Card", "Cash", "Mobile"]),
                })
            order_id += 1
    sales = pd.DataFrame(rows)
    # A little mess for the cleaning step: duplicates, a blank row, stray spaces.
    sales = pd.concat([sales, sales.sample(25, random_state=seed)], ignore_index=True)
    sales.loc[sales.sample(40, random_state=seed + 1).index, "store"] = " Midtown "
    sales = pd.concat([sales, pd.DataFrame([{c: None for c in sales.columns}])], ignore_index=True)
    sales = sales.sort_values("order_date", na_position="last", kind="stable").reset_index(drop=True)

    # ── feedback ─────────────────────────────────────────────────────────────
    n_fb = 900
    fb_days = rng.choice(days, n_fb)
    feedback = pd.DataFrame({
        "response_date": pd.to_datetime(fb_days).date,
        "customer_id": rng.integers(1001, 1001 + n_cust, n_fb),
        "store": rng.choice(["Midtown", "Decatur"], n_fb, p=[0.6, 0.4]),
        "csat": np.clip(np.round(rng.normal(4.2, 0.8, n_fb)), 1, 5).astype(int),
        "nps": np.clip(np.round(rng.normal(8.2, 1.8, n_fb)), 0, 10).astype(int),
    }).sort_values("response_date")

    # ── expenses (monthly) ───────────────────────────────────────────────────
    months = pd.date_range(start, end, freq="MS")
    exp_rows = []
    for m in months:
        base = 1 + 0.2 * (m - months[0]).days / 730
        for cat, vendor, amt in [
            ("Rent", "Peachtree Properties", 6500), ("Payroll", "Gusto", 21000 * base),
            ("Ingredients", "Southern Flour Co.", 9000 * base), ("Utilities", "Georgia Power", 1400),
            ("Marketing", "Local Ads LLC", 800 if m.month not in (11, 12) else 2200),
        ]:
            exp_rows.append({"expense_date": m.date().isoformat(), "category": cat, "vendor": vendor,
                             "amount": round(amt * rng.uniform(0.95, 1.05), 2)})
    expenses = pd.DataFrame(exp_rows)

    # ── QuickBooks export of the same customers (no shared ID) ───────────────
    qb = customers.sample(250, random_state=seed).copy()
    variants = []
    for f, l in zip(qb["first_name"], qb["last_name"]):
        r = rng.random()
        variants.append(f"{l}, {f}" if r < 0.3 else f"{f.upper()} {l.upper()}" if r < 0.5 else f"{f} {l}")
    quickbooks = pd.DataFrame({
        "DisplayName": variants,
        "PrimaryEmailAddr": qb["email"].str.upper().where(rng.random(len(qb)) < 0.5),
        "PrimaryPhone": qb["phone"].str.replace(r"[()\s-]", "", regex=True).radd("+1 "),
        "Balance": np.round(rng.exponential(60, len(qb)), 2),
    })

    return {"sales": sales, "customers": customers, "feedback": feedback,
            "expenses": expenses, "quickbooks_customers": quickbooks}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default="2026-10-10", help="Last date in the data (YYYY-MM-DD)")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    for name, df in make(pd.Timestamp(args.end)).items():
        df.to_csv(OUT / f"{name}.csv", index=False)
        print(f"samples/{name}.csv  {len(df):>6,} rows")

"""
build-dashboard's building blocks: the layout (KPI row above the fold, then
container rows from templates/layouts.json), the industry KPIs
(industry_metrics.json, INDUSTRY_METRICS.md) and the downloadable HTML
(templates/page.html). data_skills/build-dashboard/{layout,industry,skill}.py.
"""

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from data_skills.registry import load_skill

skill = load_skill("build-dashboard")
layout, industry = skill.layout, skill.industry
FOLDER = Path("data_skills/build-dashboard")


def _days(n=180, freq="D"):
    return pd.date_range("2026-01-01", periods=n, freq=freq)


# ── Layout ───────────────────────────────────────────────────────────────────

def test_rows_mix_and_match_the_canva_patterns():
    line, bar = {"kind": "line"}, {"kind": "hbar"}
    assert [r["pattern"] for r in layout.pack([line, bar])] == ["two_thirds_third"]
    assert [r["pattern"] for r in layout.pack([line, bar, line, bar])] == ["two_thirds_third", "third_two_thirds"]
    assert [r["pattern"] for r in layout.pack([line, bar, bar, bar, bar, line])] == \
        ["two_thirds_third", "third_two_thirds", "half_half"]
    assert [r["pattern"] for r in layout.pack([bar, bar, bar])] == ["thirds"]
    assert [r["pattern"] for r in layout.pack([line])] == ["full"]
    for r in layout.pack([line, bar, bar, bar, line, line, bar]):
        assert sum(r["spans"]) == 6 and len(r["spans"]) == len(r["items"])   # every row fills the 6 columns


def test_the_kpi_row_holds_one_to_four_and_one_kpi_shares_its_row():
    many = layout.plan(["a", "b", "a", "c", "d", "e"], [])
    assert many["kpi_row"] == ["a", "b", "c", "d"] and many["kpi_side"] is None   # duplicates dropped, 4 at most
    single = layout.plan(["a"], [{"kind": "hbar"}, {"kind": "line", "metric": "a"}])
    assert single["kpi_side"]["kind"] == "line" and [r["pattern"] for r in single["rows"]] == ["full"]
    blocks = json.loads((FOLDER / "templates" / "layouts.json").read_text(encoding="utf-8"))
    assert set(blocks["kpi_rows"]) == {"1", "2", "3", "4"}


def test_the_bakery_gets_trends_first_then_category_bars():
    d = skill.run(pd.read_csv("samples/sales.csv"), include_html=False).data
    assert d["layout"]["kpi_row"] == ["__total__", "__rows__", "__avg__"]
    first = d["layout"]["rows"][0]
    assert first["pattern"] == "two_thirds_third" and [v["kind"] for v in first["items"]] == ["line", "hbar"]
    assert set(d["breakdowns"]) >= {"order_type", "product"}
    k = d["ranges"]["last_3_months"]["kpis"]
    assert abs(k["__avg__"]["current"] - k["__total__"]["current"] / k["__rows__"]["current"]) < 1e-6
    assert d["metric_formats"]["__total__"] == "currency" and d["industry"]["generic"]


# ── Industry KPIs ────────────────────────────────────────────────────────────

def test_every_sector_in_the_doc_has_recipes():
    doc = (FOLDER / "INDUSTRY_METRICS.md").read_text(encoding="utf-8")
    sectors = {m.replace("–", "-") for m in re.findall(r"^## (\d{2}(?:–\d{2})?) —", doc, re.M)}
    cat = industry.catalog()
    assert sectors == set(cat)
    kinds = {None, "sum", "mean", "ratio", "rate", "days_between", "per_distinct", "net"}
    for sector, entry in cat.items():
        for m in entry["metrics"]:
            assert m["kind"] in kinds and m["direction"] in ("higher", "lower"), (sector, m["id"])
            assert m["format"] in ("number", "currency", "percent", "days"), (sector, m["id"])


def test_a_restaurant_gets_prime_cost_and_a_cost_drop_is_good_news():
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"Date": _days(), "Sales": rng.uniform(800, 1200, 180), "Food Cost": rng.uniform(250, 350, 180),
                       "Labor Cost": rng.uniform(250, 330, 180), "Location": rng.choice(["Midtown", "Decatur"], 180)})
    d = skill.run(df, include_html=False, industry="72").data
    m = "__ind_prime_cost_pct__"
    assert d["industry"]["metrics"] == ["prime_cost_pct"] and not d["industry"]["generic"]
    assert d["layout"]["kpi_row"][:2] == ["Sales", m]                                # right after the main total
    assert d["metric_directions"][m] == "lower" and d["metric_formats"][m] == "percent"
    assert "60–65%" in d["metric_benchmarks"][m]
    w = d["ranges"]["last_3_months"]["window"]
    part = df[(df["Date"] >= w["start"]) & (df["Date"] <= w["end"])]
    expected = (part["Food Cost"].sum() + part["Labor Cost"].sum()) / part["Sales"].sum() * 100
    assert abs(d["ranges"]["last_3_months"]["kpis"][m]["current"] - expected) < 1e-6   # ratio of sums
    assert d["metric_directions"]["Food Cost"] == "lower"                             # costs: lower is better


def test_a_clinic_gets_length_of_stay_and_denial_rate():
    admit = _days(120)
    df = pd.DataFrame({"visit_date": admit, "Admit Date": admit, "Discharge Date": admit + pd.to_timedelta(np.arange(120) % 5, "D"),
                       "Claim Status": ["Denied" if i % 10 == 0 else "Paid" for i in range(120)], "charges": 100.0})
    d = skill.run(df, date_column="visit_date", include_html=False, industry="62").data
    assert set(d["industry"]["metrics"]) == {"avg_length_of_stay", "claim_denial_rate"}
    k = d["ranges"]["all"]["kpis"]
    assert abs(k["__ind_avg_length_of_stay__"]["current"] - 2.0) < 1e-9                # days 0..4 average 2
    assert abs(k["__ind_claim_denial_rate__"]["current"] - 10.0) < 1e-9                # 1 in 10 = 10%


def test_revenue_per_employee_counts_distinct_people():
    df = pd.DataFrame({"date": _days(60), "fees": 100.0, "attorney": [f"A{i % 4}" for i in range(60)]})
    d = skill.run(df, include_html=False, industry="54").data
    assert d["industry"]["metrics"] == ["revenue_per_employee"]
    assert d["ranges"]["all"]["kpis"]["__ind_revenue_per_employee__"]["current"] == 60 * 100.0 / 4


def test_no_matching_columns_falls_back_to_the_generic_dashboard():
    d = skill.run(pd.read_csv("samples/sales.csv"), include_html=False, industry="62").data
    assert d["industry"]["generic"] and d["industry"]["name"] == "Health Care and Social Assistance"
    assert d["layout"]["kpi_row"] == ["__total__", "__rows__", "__avg__"]


def test_column_hints_match_words_not_letters():
    df = pd.DataFrame({"Hotel Name": ["a"], "Food Cost (USD)": [1.0], "food_cost_total_adjusted": [2.0]})
    assert industry.find(df, ["food_cost"], numeric=True, used=set()) == "Food Cost (USD)"   # the closest name
    assert industry.find(df, ["tel"], numeric=False, used=set()) is None                   # not inside "hotel"


# ── The downloadable HTML ────────────────────────────────────────────────────

def test_the_html_file_has_the_blocks_and_the_data_and_no_leftover_placeholders():
    r = skill.run(pd.read_csv("samples/sales.csv"), business_name="Sunrise Bakery", viewer_name="Ana")
    html = r.artifacts["html_dashboard"]
    assert "__DATA__" not in html and "__BLOCKS__" not in html and "__TITLE__" not in html
    assert 'id="tpl-kpi"' in html and 'id="tpl-container"' in html and 'prefers-color-scheme: dark' in html
    data = json.loads(html.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0])
    assert data["business_name"] == "Sunrise Bakery" and data["layout"]["kpi_row"]


# ── KPI card styles: one per dashboard ───────────────────────────────────────

def _law_firm(n=200):
    rng = np.random.default_rng(2)
    return pd.DataFrame({"date": _days(n), "fees": rng.uniform(5000, 9000, n), "billable_hours": rng.uniform(55, 70, n),
                         "total_hours": rng.uniform(78, 85, n), "billed_amount": rng.uniform(80, 95, n),
                         "standard_value": 100.0, "attorney": rng.choice(["A", "B", "C"], n)})


def test_the_kpi_style_rule():
    assert layout.kpi_style("auto", ["u", "r"], {"u": [75, 85]}, True) == "gauge"   # 2+ percentages, a target
    assert layout.kpi_style("auto", ["u"], {"u": [75, 85]}, True) == "change"       # one alone isn't a gauge row
    assert layout.kpi_style("auto", [], {}, False) == "number"                      # nothing to compare with
    assert layout.kpi_style("gauge", [], {}, True) == "change"                      # dollars have no 0-100 dial
    assert layout.kpi_style("number", ["u", "r"], {"u": [1, 2]}, True) == "number"  # asked for: kept


def test_a_law_firm_gets_a_row_of_gauges_and_the_total_leads_the_containers():
    d = skill.run(_law_firm(), include_html=False, industry="54").data
    assert d["kpi_style"] == "gauge"
    assert d["layout"]["kpi_row"] == ["__ind_billable_utilization__", "__ind_realization_rate__"]   # percentages only
    assert d["metric_targets"]["__ind_billable_utilization__"] == [75, 85]
    first = d["layout"]["rows"][0]["items"][0]
    assert first["kind"] == "line" and first["metric"] == "fees"                   # fees (not total_hours) lead
    k = d["ranges"]["last_3_months"]["kpis"]["__ind_billable_utilization__"]
    assert abs(k["change"] - (k["current"] - k["previous"])) < 1e-4                # the change in points


def test_the_style_is_the_same_across_the_row_and_can_be_chosen():
    bakery = pd.read_csv("samples/sales.csv")
    assert skill.run(bakery, include_html=False).data["kpi_style"] == "change"
    assert skill.run(bakery, include_html=False, kpi_style="number").data["kpi_style"] == "number"
    assert skill.run(bakery, include_html=False, kpi_style="gauge").data["kpi_style"] == "change"   # no percentages
    html = skill.run(_law_firm(), industry="54").artifacts["html_dashboard"]
    assert all(f'id="{t}"' in html for t in ("tpl-kpi-number", "tpl-kpi", "tpl-kpi-gauge"))
    assert '"kpi_style": "gauge"' in html


def test_a_daily_summary_table_skips_the_record_count():
    d = skill.run(_law_firm(), include_html=False).data          # one row per day, no industry
    assert "__rows__" not in d["layout"]["kpi_row"] and "__avg__" not in d["layout"]["kpi_row"]
    assert all(v["metric"] != "__rows__" for r in d["layout"]["rows"] for v in r["items"])

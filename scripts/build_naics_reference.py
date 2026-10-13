"""
Build the NAICS sector/subsector list used by the business intake funnel.

WHAT THIS SCRIPT DOES
---------------------
Reads the SBA "Table of Size Standards" workbook (data/reference/) and writes
api/data/naics_sectors.json: a two-level tree

    supersector (goods-producing / service-providing)
      └─ sector     e.g. "44-45 Retail Trade"
           └─ subsector  e.g. "445 Food and Beverage Retailers"

We stop at the SUBSECTOR level on purpose — the 6-digit industries (1,000+ of
them) are too detailed for a signup form.

HOW TO RE-RUN (when SBA publishes a new table)
----------------------------------------------
    1. Save the new workbook in data/reference/
    2. python scripts/build_naics_reference.py data/reference/<new file>.xlsx
    3. pytest -q   (tests/test_naics.py checks the output)

GOODS vs. SERVICES
------------------
Uses the U.S. Bureau of Labor Statistics (BLS) supersector grouping:
goods-producing = Agriculture (11), Mining (21), Construction (23),
Manufacturing (31-33). Everything else is service-providing (BLS counts
Utilities, 22, as service-providing).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "reference" / "sba_table_of_size_standards_2023-03-17.xlsx"
OUTPUT = ROOT / "api" / "data" / "naics_sectors.json"

GOODS_PRODUCING = {"11", "21", "23", "31-33"}

# "Sector 44 - 45 – Retail Trade" -> ("44-45", "Retail Trade").
# The workbook mixes hyphens and en dashes, so both are accepted.
_SECTOR_RE = re.compile(r"^\s*Sector\s+(\d{2})(?:\s*[-–]\s*(\d{2}))?\s*[-–]\s*(.+?)\s*$")
_SUBSECTOR_RE = re.compile(r"^\s*Subsector\s+(\d{3})\s*[-–]\s*(.+?)\s*$")


def build(source: Path) -> dict:
    df = pd.read_excel(source, sheet_name="table_of_size_standards-all", header=0, dtype=str)
    code_col, desc_col = df.columns[0], df.columns[1]

    sectors: list[dict] = []
    for _, row in df.iterrows():
        desc = str(row[desc_col]) if pd.notna(row[desc_col]) else ""
        code = str(row[code_col]) if pd.notna(row[code_col]) else ""
        if m := _SECTOR_RE.match(desc):
            start, end, name = m.groups()
            sector_code = f"{start}-{end}" if end else start
            sectors.append({
                "code": sector_code,
                "name": name,
                "supersector": "goods_producing" if sector_code in GOODS_PRODUCING else "service_providing",
                "subsectors": [],
            })
        elif (m := _SUBSECTOR_RE.match(code)) and sectors:
            sub_code, name = m.groups()
            # Subsector rows follow their sector in the sheet.
            sectors[-1]["subsectors"].append({"code": sub_code, "name": name})

    return {
        "source": f"U.S. SBA Table of Size Standards ({source.name})",
        "supersectors": [
            {"id": "goods_producing", "label": "Goods-producing",
             "description": "You make, grow, build, or extract physical products."},
            {"id": "service_providing", "label": "Service-providing",
             "description": "You sell services, or buy and resell goods (retail, wholesale, transportation...)."},
        ],
        "sectors": sectors,
    }


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    data = build(src)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n_sub = sum(len(s["subsectors"]) for s in data["sectors"])
    print(f"Wrote {OUTPUT.relative_to(ROOT)}: {len(data['sectors'])} sectors, {n_sub} subsectors")

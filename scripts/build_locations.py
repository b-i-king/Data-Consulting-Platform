"""
Build the city list behind the signup form's location picker.

WHAT THIS SCRIPT DOES
---------------------
Writes api/data/locations.json: a compact list of cities the location picker
searches as you type (like "Atlanta, Georgia, United States"), from the
GeoNames gazetteer as packaged by the `geonamescache` Python library:

    * United States — every city/town with 1,000+ people (~17,000)
    * Everywhere else — every city with 15,000+ people (~31,000)

The platform serves US businesses first, so US coverage goes down to small
towns while the rest of the world stops at mid-sized cities (that keeps the
file small, about 2 MB).

FILE LAYOUT (kept short on purpose)
-----------------------------------
    {"source": "...", "countries": {"US": "United States", ...},
     "us_states": {"GA": "Georgia", ...},
     "cities": [[geonameid, "Atlanta", "GA", "US", 498715], ...]}

Each city: GeoNames id, name, region code (US state code; other countries'
region codes aren't named in the source, so they're shown as City, Country),
ISO country code, population (used to rank search results).

HOW TO RE-RUN
-------------
geonamescache is only needed to rebuild the file, not to run the platform:

    pip install geonamescache
    python scripts/build_locations.py
    pytest -q tests/test_locations.py

LICENSE
-------
GeoNames data, CC BY 4.0 (https://www.geonames.org) — see licenses/geonames-CC-BY-4.0.txt.
"""

from __future__ import annotations

import json
from pathlib import Path

import geonamescache

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "api" / "data" / "locations.json"
US_MIN_POPULATION = 1000
WORLD_MIN_POPULATION = 15000


def main() -> None:
    us_cache = geonamescache.GeonamesCache(min_city_population=US_MIN_POPULATION)
    world_cache = geonamescache.GeonamesCache(min_city_population=WORLD_MIN_POPULATION)

    countries = {code: c["name"] for code, c in world_cache.get_countries().items()}
    us_states = {code: s["name"] for code, s in world_cache.get_us_states().items()}

    cities = []
    for c in us_cache.get_cities().values():
        if c["countrycode"] == "US" and c["admin1code"] in us_states:
            cities.append([int(c["geonameid"]), c["name"], c["admin1code"], "US", int(c["population"])])
    for c in world_cache.get_cities().values():
        if c["countrycode"] != "US" and c["countrycode"] in countries:
            cities.append([int(c["geonameid"]), c["name"], "", c["countrycode"], int(c["population"])])
    cities.sort(key=lambda c: -c[4])   # biggest first: search ranks by population

    OUT.write_text(json.dumps({
        "source": "GeoNames (CC BY 4.0) via geonamescache; US cities 1,000+ people, "
                  "other countries 15,000+. Built by scripts/build_locations.py.",
        "countries": countries,
        "us_states": us_states,
        "cities": cities,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    us = sum(1 for c in cities if c[3] == "US")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(cities):,} cities ({us:,} in the US)")


if __name__ == "__main__":
    main()

"""
City search for the signup form's location picker (like Greenhouse's).

WHAT THIS FILE DOES
-------------------
    search("atl")          -> [{"id": 4180439, "label": "Atlanta, Georgia, United States", ...}, ...]
    search("paris, fr")    -> Paris, France first (text after a comma narrows the state/country)
    get(4180439)           -> the same record, used to check a signup's choice

The city list is api/data/locations.json, built by scripts/build_locations.py
from GeoNames: US cities with 1,000+ people and cities elsewhere with 15,000+.
It's loaded once and searched in memory — no outside service, no API key.

MATCHING
--------
Case- and accent-insensitive ("sao paulo" finds São Paulo). Cities whose name
STARTS with what was typed come first, then ones containing it; within each,
bigger cities first. Text after the first comma must match the start of the
state (name or code, US only) or the country (name or code).
"""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "locations.json"


def _fold(text: str) -> str:
    """
    Lowercase and strip accents: 'São Paulo' -> 'sao paulo'.

    Unicode NFKD splits an accented letter into the base letter plus a separate
    accent mark; the accent marks are then dropped. Surrounding spaces are
    trimmed. Used on both the city list and what the person types, so matching
    ignores case and accents.

    Args:
        text: any text, e.g. a city, state or country name.

    Returns:
        str: the folded text.

    Example:
        >>> _fold("  São Paulo ")
        'sao paulo'
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower().strip()


@lru_cache
def _data() -> dict:
    """
    Load api/data/locations.json once and build the in-memory search index.

    Each raw city row is [GeoNames id, name, region code, country code,
    population], already sorted biggest city first. For every city this builds:
    - public fields: id, city, region (US state name, else ""), region_code
      (US only), country, country_code, and a display label like
      "Atlanta, Georgia, United States"
    - private search keys (names start with "_", never sent to the browser):
      "_name" (folded city name) and "_where" (folded state name/code and
      country name/code)

    @lru_cache means the file is read only on the first call.

    Returns:
        dict: {"cities": [city dict, ...] (biggest first),
        "by_id": {GeoNames id: city dict}}.

    Raises:
        FileNotFoundError: if locations.json is missing.
    """
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    countries, states = raw["countries"], raw["us_states"]
    cities = []
    for gid, name, region, cc, pop in raw["cities"]:   # already biggest first
        region_name = states.get(region, "") if cc == "US" else ""
        country = countries.get(cc, cc)
        label = ", ".join(p for p in (name, region_name, country) if p)
        cities.append({
            "id": gid, "city": name, "region": region_name, "region_code": region if cc == "US" else "",
            "country": country, "country_code": cc, "label": label,
            # Folded search keys, computed once.
            "_name": _fold(name),
            "_where": [_fold(x) for x in (region_name, region if cc == "US" else "", country, cc) if x],
        })
    return {"cities": cities, "by_id": {c["id"]: c for c in cities}}


def _public(c: dict) -> dict:
    """
    Drop the private search keys (those starting with "_") from a city record.

    Args:
        c: a city dict from `_data()`.

    Returns:
        dict: a copy with only the public fields (id, city, region, region_code,
        country, country_code, label).

    Example:
        >>> _public({"id": 1, "city": "Atlanta", "_name": "atlanta"})
        {'id': 1, 'city': 'Atlanta'}
    """
    return {k: v for k, v in c.items() if not k.startswith("_")}


def search(query: str, limit: int = 8) -> list[dict]:
    """
    Up to `limit` cities matching what the person typed (at least 2 letters).

    Matching rules (case- and accent-insensitive):
    - Text before the first comma is the city; it must be at least 2 letters,
      otherwise nothing is returned.
    - Text after the comma (optional) must match the START of the state name or
      code (US only) or the country name or code.
    - Cities whose name STARTS with the text come first, then ones that only
      CONTAIN it; within each group bigger cities come first.

    Args:
        query: what the person typed, e.g. "atl" or "paris, fr". None or "" is allowed.
        limit: the maximum number of results (default 8).

    Returns:
        list[dict]: public city records (id, city, region, region_code, country,
        country_code, label), best matches first.

    Example:
        >>> search("atl", limit=1)
        [{'id': 4180439, 'city': 'Atlanta', 'region': 'Georgia', 'region_code': 'GA',
          'country': 'United States', 'country_code': 'US', 'label': 'Atlanta, Georgia, United States'}]
        >>> search("a")
        []
    """
    city_part, _, where_part = (query or "").partition(",")
    q, where = _fold(city_part), _fold(where_part)
    if len(q) < 2:
        return []
    starts, contains = [], []
    for c in _data()["cities"]:
        if where and not any(w.startswith(where) for w in c["_where"]):
            continue
        if c["_name"].startswith(q):
            starts.append(c)
            if len(starts) >= limit:
                break   # list is biggest-first, so these are the best prefix matches
        elif q in c["_name"] and len(contains) < limit:
            contains.append(c)
    return [_public(c) for c in (starts + contains)[:limit]]


def get(location_id: int) -> dict | None:
    """
    The city with this GeoNames id, or None.

    Used to check that the location chosen on the signup form is a real city.

    Args:
        location_id: the GeoNames id (an int, or anything int() accepts, such as "4180439").

    Returns:
        dict | None: the public city record, or None if the id is unknown.

    Raises:
        ValueError / TypeError: if `location_id` cannot be converted to an int.

    Example:
        >>> get(4180439)["label"]
        'Atlanta, Georgia, United States'
        >>> get(1) is None
        True
    """
    c = _data()["by_id"].get(int(location_id))
    return _public(c) if c else None

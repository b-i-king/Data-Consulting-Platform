"""
Skills catalog: which skills power each service category.

WHAT THIS FILE DOES
-------------------
Reads `data_skills/catalog.json` — the single list that assigns every skill
in this repo to exactly ONE service category (Data Cleaning, Data Strategy &
Discovery, ..., AI) — and exposes it to the rest of the backend.

The service cards on the website use it to show "What's included" chips, so
the skills you maintain for Claude and the services clients buy stay in sync
automatically. Add a skill to catalog.json and it appears on the right card.

If the file is missing (for example a trimmed-down deployment), everything
still works; the cards just show no skill chips.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# api/services/skills_catalog.py -> repo root is three folders up.
CATALOG_PATH = Path(__file__).resolve().parents[2] / "data_skills" / "catalog.json"


@lru_cache
def load_catalog() -> dict:
    """
    Parse catalog.json once per process. Returns {} if it is absent.

    @lru_cache keeps the parsed file, so edits to catalog.json are picked up
    only after the server restarts.

    Returns:
        dict: the parsed data_skills/catalog.json ({"categories": [...], ...}),
        or {} when the file does not exist.

    Raises:
        json.JSONDecodeError: if the file exists but is not valid JSON.

    Example:
        >>> load_catalog()["categories"][0]["id"]
        'data_cleaning'
    """
    try:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def skills_by_category() -> dict[str, list[dict]]:
    """
    Map each service category id to the skills listed under it.

    {category id: [{id, label, source}, ...]}

    Returns:
        dict[str, list[dict]]: category id -> its skill entries as written in
        catalog.json (each has at least "id", "label" and "source"). Empty when
        the catalog is missing.

    Example:
        >>> skills_by_category()["data_cleaning"][0]["id"]
        'data-cleaning'
    """
    return {c["id"]: c["skills"] for c in load_catalog().get("categories", [])}


def capability_labels(category_id: str) -> list[str]:
    """
    Client-friendly labels for one category, e.g. ['ER diagrams', ...].

    These are the "What's included" chips on a service card.

    Args:
        category_id: a service category id, e.g. "data_cleaning" (the same ids
            as the services in api/services/pricing.py).

    Returns:
        list[str]: the skills' labels in catalog order; [] for an unknown id.

    Example:
        >>> capability_labels("data_cleaning")[:2]
        ['Duplicate & missing-value cleanup', 'Multi-file sorting & stacking']
    """
    return [s["label"] for s in skills_by_category().get(category_id, [])]

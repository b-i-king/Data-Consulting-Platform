"""
Skills catalog: which skills power each service category.

WHAT THIS FILE DOES
-------------------
Reads `.claude/skills/catalog.json` — the single list that assigns every skill
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
CATALOG_PATH = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "catalog.json"


@lru_cache
def load_catalog() -> dict:
    """Parse catalog.json once per process. Returns {} if it is absent."""
    try:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def skills_by_category() -> dict[str, list[dict]]:
    """{category id: [{id, label, source}, ...]}"""
    return {c["id"]: c["skills"] for c in load_catalog().get("categories", [])}


def capability_labels(category_id: str) -> list[str]:
    """Client-friendly labels for one category, e.g. ['ER diagrams', ...]."""
    return [s["label"] for s in skills_by_category().get(category_id, [])]

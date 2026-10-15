"""
Keeps the skills catalog honest.

Every folder in data_skills must appear in catalog.json EXACTLY ONCE (that
is what makes the categories mutually exclusive), every category must be a
real service, and every service must have at least one skill behind it.
"""

import json
from pathlib import Path

from api.services.pricing import SERVICES, catalog
from api.services.skills_catalog import CATALOG_PATH

SKILLS_DIR = CATALOG_PATH.parent


def _catalog():
    """Load data_skills/catalog.json.

    Returns:
        dict: the parsed catalog, with "categories" (each holding "skills") and "sources".
    """
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _skill_folders() -> set[str]:
    """List the skill folders in data_skills/ (any folder holding a README.md).

    Returns:
        set[str]: the skill ids, e.g. {"data-cleaning", "build-dashboard", ...}.
    """
    return {p.parent.name for p in SKILLS_DIR.glob("*/README.md")}


def test_every_skill_is_in_exactly_one_category():
    """Check each skill folder appears in catalog.json exactly once, and vice versa."""
    listed = [s["id"] for c in _catalog()["categories"] for s in c["skills"]]
    duplicates = {s for s in listed if listed.count(s) > 1}
    assert not duplicates, f"Skills in more than one category: {duplicates}"
    assert set(listed) == _skill_folders(), (
        f"Not in catalog: {_skill_folders() - set(listed)}; "
        f"in catalog but no folder: {set(listed) - _skill_folders()}"
    )


def test_categories_match_services():
    """Check the catalog categories are the pricing services, in the same order."""
    category_ids = [c["id"] for c in _catalog()["categories"]]
    assert category_ids == [s.id for s in SERVICES]


def test_every_skill_has_a_known_source():
    """Check every catalog skill's source is listed under "sources"."""
    data = _catalog()
    for c in data["categories"]:
        for s in c["skills"]:
            assert s["source"] in data["sources"], s


def test_skill_front_matter_name_matches_folder():
    """Check each README.md front matter "name:" matches its folder name."""
    for folder in _skill_folders():
        text = (SKILLS_DIR / folder / "README.md").read_text(encoding="utf-8")
        assert f"\nname: {folder}\n" in text.split("---")[1] + "\n", folder


def test_services_expose_capabilities():
    """Check every service in the pricing catalog lists its capabilities."""
    by_id = {s["id"]: s for s in catalog()}
    assert "ER diagrams" in by_id["data_engineering"]["capabilities"]
    assert all(s["capabilities"] for s in by_id.values())


def test_each_category_has_exactly_one_primary_skill():
    """Check each category marks exactly one skill as primary."""
    for c in _catalog()["categories"]:
        primaries = [s["id"] for s in c["skills"] if s.get("primary")]
        assert len(primaries) == 1, (c["id"], primaries)

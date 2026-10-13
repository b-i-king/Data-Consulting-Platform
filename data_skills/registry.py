"""
Skill registry: find, describe, and run any skill by name.

WHAT THIS FILE DOES
-------------------
* `list_skills()`        every skill that has a skill.py, with its options and category
* `run_skill(id, data, params)`  check the options, then call that skill's run()

Skill folders use hyphens ("trend-analysis") because that is the Claude Code
naming convention, and Python can't `import` a name with hyphens. So each
skill.py is loaded straight from its file path with importlib — that's the
only "magic" here.

Options are checked BEFORE a skill runs: unknown names are rejected, numbers
are converted from text (the command line and web forms send text), and
options marked `api_allowed=False` (such as writing files to disk) are refused
when the call comes from the web API.
"""

from __future__ import annotations

import importlib.util
import json
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec

SKILLS_DIR = Path(__file__).resolve().parent


@lru_cache
def _catalog_categories() -> dict[str, dict]:
    """skill id -> {category id, category name, label} from catalog.json."""
    catalog = json.loads((SKILLS_DIR / "catalog.json").read_text(encoding="utf-8"))
    out = {}
    for cat in catalog["categories"]:
        for s in cat["skills"]:
            out[s["id"]] = {"category": cat["id"], "category_name": cat["name"], "label": s["label"],
                            "primary": bool(s.get("primary"))}
    return out


@lru_cache
def load_skill(skill_id: str) -> ModuleType:
    """Import data_skills/<skill_id>/skill.py and return the module."""
    path = SKILLS_DIR / skill_id / "skill.py"
    if "/" in skill_id or ".." in skill_id or not path.is_file():
        raise SkillInputError(f"Unknown skill '{skill_id}'.")
    module_name = "data_skills.impl." + skill_id.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    if not isinstance(getattr(module, "SKILL", None), SkillSpec) or not callable(getattr(module, "run", None)):
        raise RuntimeError(f"{path} must define SKILL (SkillSpec) and run().")
    return module


def skill_ids() -> list[str]:
    """Every folder with a skill.py, alphabetically."""
    return sorted(p.parent.name for p in SKILLS_DIR.glob("*/skill.py"))


def describe(skill_id: str) -> dict:
    """A JSON-friendly description of one skill (for the API and the CLI)."""
    spec: SkillSpec = load_skill(skill_id).SKILL
    meta = _catalog_categories().get(skill_id, {})
    return {
        "id": spec.id,
        "title": spec.title,
        "description": spec.description,
        "input": spec.input,
        "wants_raw": spec.wants_raw,
        "category": meta.get("category"),
        "category_name": meta.get("category_name"),
        "label": meta.get("label", spec.title),
        # The skill a service's workspace page opens on (one per category).
        "primary": meta.get("primary", False),
        "params": {
            name: {"type": p.type, "default": p.default, "help": p.help,
                   "choices": list(p.choices), "api_allowed": p.api_allowed}
            for name, p in spec.params.items()
        },
    }


def list_skills() -> list[dict]:
    """Every skill, in the service order of catalog.json (Cleaning first, AI last)."""
    catalog = json.loads((SKILLS_DIR / "catalog.json").read_text(encoding="utf-8"))
    order = {s["id"]: (ci, si) for ci, c in enumerate(catalog["categories"]) for si, s in enumerate(c["skills"])}
    return sorted((describe(s) for s in skill_ids()), key=lambda d: order.get(d["id"], (99, 99)))


def _coerce(name: str, p: Param, value: Any) -> Any:
    """Convert text from forms / the command line into the option's real type."""
    if value is None or value == "":
        return p.default
    try:
        if p.type == "int":
            return int(value)
        if p.type == "float":
            return float(value)
        if p.type == "bool":
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
        if p.type == "columns":
            return value if isinstance(value, list) else [v.strip() for v in str(value).split(",") if v.strip()]
        if p.type == "json":
            return value if isinstance(value, (dict, list)) else json.loads(value)
        if p.type == "choice":
            if value not in p.choices:
                raise SkillInputError(f"Option '{name}' must be one of: {', '.join(p.choices)}.")
            return value
        return str(value)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SkillInputError(f"Option '{name}' should be a {p.type}: {exc}") from exc


def run_skill(
    skill_id: str,
    data: pd.DataFrame | dict[str, pd.DataFrame],
    params: dict[str, Any] | None = None,
    from_api: bool = False,
) -> SkillResult:
    """Validate options, adapt the input shape, and run the skill."""
    module = load_skill(skill_id)
    spec: SkillSpec = module.SKILL
    params = dict(params or {})

    unknown = set(params) - set(spec.params)
    if unknown:
        raise SkillInputError(f"Unknown option(s) for {skill_id}: {', '.join(sorted(unknown))}.")
    clean: dict[str, Any] = {}
    for name, p in spec.params.items():
        if from_api and not p.api_allowed and params.get(name) not in (None, ""):
            raise SkillInputError(f"Option '{name}' is only available from the command line.")
        clean[name] = _coerce(name, p, params.get(name))

    # One table vs. several: give each skill the shape it expects.
    if spec.input == "table" and isinstance(data, dict):
        if len(data) != 1:
            raise SkillInputError(f"{spec.title} works on one table; you provided {len(data)}.")
        data = next(iter(data.values()))
    if spec.input == "tables" and isinstance(data, pd.DataFrame):
        data = {"table": data}
    if isinstance(data, pd.DataFrame) and data.empty:
        raise SkillInputError("The table is empty.")

    return module.run(data, **clean)

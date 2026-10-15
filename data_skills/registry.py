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
    """
    Map each skill id to its service category from catalog.json.

    Read once and cached (`lru_cache`), since catalog.json does not change while
    the server runs.

    Returns:
        dict[str, dict]: skill id -> {"category": category id, "category_name": category
        name, "label": the skill's short label, "primary": True if it is the skill a
        service's workspace page opens on}.

    Example:
        >>> _catalog_categories()["build-dashboard"]["category"]
        'data_visualization'
    """
    catalog = json.loads((SKILLS_DIR / "catalog.json").read_text(encoding="utf-8"))
    out = {}
    for cat in catalog["categories"]:
        for s in cat["skills"]:
            out[s["id"]] = {"category": cat["id"], "category_name": cat["name"], "label": s["label"],
                            "primary": bool(s.get("primary"))}
    return out


@lru_cache
def load_skill(skill_id: str) -> ModuleType:
    """
    Import data_skills/<skill_id>/skill.py and return the module.

    Skill folders have hyphens, which Python can't `import`, so the file is loaded
    from its path with importlib (under the name data_skills.impl.<id_with_underscores>).
    Results are cached, so each skill is imported only once. Ids containing "/" or
    ".." are refused so a request can't load a file outside data_skills/.

    Args:
        skill_id: the skill's folder name, e.g. "trend-analysis".

    Returns:
        ModuleType: the loaded module, which has `SKILL` (a SkillSpec) and `run()`.

    Raises:
        SkillInputError: the id is unsafe or there is no such skill folder with a skill.py.
        RuntimeError: the skill.py does not define SKILL and run() (a developer mistake).

    Example:
        >>> load_skill("trend-analysis").SKILL.title   # the skill's human name
    """
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
    """
    List every folder in data_skills/ that has a skill.py, alphabetically.

    Returns:
        list[str]: skill ids (folder names), e.g. ["analyze", "anomaly-analysis", ...].

    Example:
        >>> "build-dashboard" in skill_ids()
        True
    """
    return sorted(p.parent.name for p in SKILLS_DIR.glob("*/skill.py"))


def describe(skill_id: str) -> dict:
    """
    Describe one skill in a JSON-friendly dict (for the API and the CLI).

    Combines the skill's SkillSpec (title, options...) with its category from
    catalog.json. The web app uses `params` to build the option form.

    Args:
        skill_id: the skill's folder name, e.g. "build-dashboard".

    Returns:
        dict: id, title, description, input ("table" or "tables"), wants_raw, category,
        category_name, label, primary, and params ({name: {type, default, help,
        choices, api_allowed}}).

    Raises:
        SkillInputError: there is no such skill.

    Example:
        >>> describe("create-viz")["params"]["chart"]["choices"]
        ['auto', 'line', 'bar', 'histogram']
    """
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
    """
    Describe every skill, in the service order of catalog.json (Cleaning first, AI last).

    Skills are sorted by (category position, position within the category); a skill
    missing from catalog.json sorts last.

    Returns:
        list[dict]: one `describe()` dict per skill.

    Example:
        >>> [s["id"] for s in list_skills()][:1]
        ['data-cleaning']
    """
    catalog = json.loads((SKILLS_DIR / "catalog.json").read_text(encoding="utf-8"))
    order = {s["id"]: (ci, si) for ci, c in enumerate(catalog["categories"]) for si, s in enumerate(c["skills"])}
    return sorted((describe(s) for s in skill_ids()), key=lambda d: order.get(d["id"], (99, 99)))


def _coerce(name: str, p: Param, value: Any) -> Any:
    """
    Convert text from forms / the command line into the option's real type.

    Rules by `p.type`:
    - empty (None or "") -> the option's default
    - "int" / "float" -> int(value) / float(value)
    - "bool" -> True for "1", "true", "yes", "on" (any case); real bools pass through
    - "columns" -> a list, splitting text on commas and trimming spaces
    - "json" -> parsed JSON, unless it is already a dict or list
    - "choice" -> must be one of `p.choices`
    - anything else ("str", "column") -> str(value)

    Args:
        name: the option's name (used in error messages).
        p: the option's Param definition.
        value: the raw value from the caller.

    Returns:
        Any: the value converted to the option's type, or the default.

    Raises:
        SkillInputError: the value can't be converted, or a choice is not allowed.

    Example:
        >>> _coerce("columns", Param("columns"), "amount, quantity")
        ['amount', 'quantity']
    """
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
    """
    Validate options, adapt the input shape, and run the skill.

    Steps:
    1. Reject option names the skill doesn't define.
    2. From the web API (`from_api=True`), refuse command-line-only options
       (`api_allowed=False`, such as writing files to disk).
    3. Convert every option with `_coerce` (missing ones get their defaults).
    4. Give the skill the input it expects: a one-table skill gets the single
       DataFrame out of a one-item dict; a multi-table skill gets {"table": df}.
    5. Refuse an empty table, then call the skill's `run()`.

    Args:
        skill_id: the skill's folder name, e.g. "trend-analysis".
        data: one DataFrame, or {table name: DataFrame}.
        params: option values, often as text (default: none, so all defaults).
        from_api: True when the call comes from the web API.

    Returns:
        SkillResult: whatever the skill returns.

    Raises:
        SkillInputError: unknown skill or option, a command-line-only option from the
            API, a bad option value, several tables for a one-table skill, an empty
            table, or any input error raised by the skill itself.

    Example:
        >>> df = pd.read_csv("samples/sales.csv")
        >>> run_skill("ask-your-data", df, {"question": "total amount by store"}).summary
        # 'Midtown leads with ..., followed by Decatur (...).'
    """
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

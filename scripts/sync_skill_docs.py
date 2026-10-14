"""
Regenerate the "Run it (platform implementation)" section of every README.md.

WHY
---
Each data skill has two files: README.md (the procedure in plain English) and
skill.py (the same procedure as code). The bottom of each README.md lists the
skill's options — and those must match the code. Rather than editing 30
tables by hand, this script rebuilds the section from skill.py itself
(data_skills.registry.describe), so the docs can't drift from the code.

Run it after changing any skill's options:

    python scripts/sync_skill_docs.py          # rewrite the sections
    python scripts/sync_skill_docs.py --check  # exit 1 if any section is out of date (used by pytest)

Everything ABOVE the "## Run it" heading is left exactly as it is.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_skills.registry import describe, skill_ids  # noqa: E402

MARKER = "## Run it (platform implementation)"
ALWAYS_INCLUDED = "data_cleaning"   # the service every client gets


def _default(p: dict) -> str:
    d = p["default"]
    if d is None:
        return "auto"
    if isinstance(d, bool):
        return "yes" if d else "no"
    return str(d)


def run_section(skill_id: str) -> str:
    """The generated Markdown for one skill."""
    info = describe(skill_id)
    params = {k: v for k, v in info["params"].items()}
    many = info["input"] == "tables"
    files = "samples/sales.csv samples/customers.csv" if many else "samples/sales.csv"
    example = f"python -m data_skills run {skill_id} {files}" + (" --param KEY=VALUE" if params else "")
    unlock = ("always included" if info["category"] == ALWAYS_INCLUDED
              else f"unlocked when the client chooses **{info['category_name']}**")
    lines = [
        MARKER,
        "",
        f"This procedure is also runnable code: [`skill.py`](skill.py) in this folder "
        f"(category: **{info['category_name']}**).",
        "",
        "```bash",
        example,
        "```",
        "",
        f"Web: **Explore your data → Run a skill → {info['label']}** ({unlock}) · "
        f"API: `POST /api/skills/{skill_id}/run`.",
        "Input: " + ("several tables (every file you pass)." if many else "one table.")
        + (" Runs on the data as uploaded, before cleaning." if info["wants_raw"] else ""),
    ]
    if params:
        lines += ["", "| Option | Type | Default | What it does |", "|---|---|---|---|"]
        for name, p in params.items():
            kind = f"choice ({', '.join(p['choices'])})" if p["type"] == "choice" else p["type"]
            api_only = not p.get("api_allowed", True) and "command line only" not in p["help"]
            note = p["help"] + (" (command line only)" if api_only else "")
            lines.append(f"| `{name}` | {kind} | {_default(p)} | {note} |")
    return "\n".join(lines) + "\n"


def sync(check: bool) -> list[str]:
    """Rewrite (or just compare) every README.md; return the ones that changed."""
    changed = []
    for skill_id in skill_ids():
        path = ROOT / "data_skills" / skill_id / "README.md"
        text = path.read_text(encoding="utf-8")
        body = text.split(MARKER)[0].rstrip() + "\n\n"
        new = body + run_section(skill_id)
        if new != text:
            changed.append(skill_id)
            if not check:
                path.write_text(new, encoding="utf-8")
    return changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only report out-of-date files")
    args = ap.parse_args()
    out = sync(args.check)
    if args.check and out:
        print("Out of date (run python scripts/sync_skill_docs.py):", ", ".join(out))
        sys.exit(1)
    print(f"{'Would update' if args.check else 'Updated'} {len(out)} README.md file(s){': ' + ', '.join(out) if out else ''}")

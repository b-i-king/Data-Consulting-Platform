"""
Copy the downloadable dashboard's template into the web app.

WHY
---
The "Download (HTML)" button builds the file in the visitor's browser: it fills
the template with the numbers the dashboard already shows, so a download costs
no server time (Render) and no database reads (Supabase). Vercel only builds
the web/ folder, so the browser needs its own copy of the template:

    data_skills/build-dashboard/templates/page.html + kpi_card.html + container.html
        -> web/public/dashboard-template.html   (title and data left as placeholders)

Run it after changing any file in templates/:

    python scripts/sync_dashboard_template.py          # rewrite the copy
    python scripts/sync_dashboard_template.py --check  # exit 1 if the copy is out of date (used by pytest)
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public" / "dashboard-template.html"


def _template() -> str:
    """The template from build-dashboard/skill.py (page_template). The folder name has a dash, so load it by path."""
    sys.path.insert(0, str(ROOT))
    path = ROOT / "data_skills" / "build-dashboard" / "skill.py"
    spec = importlib.util.spec_from_file_location("build_dashboard_skill", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.page_template()


def sync(check: bool = False) -> bool:
    """
    Write (or, with check=True, only compare) web/public/dashboard-template.html.

    Returns:
        bool: True if the copy was already up to date.
    """
    text = _template()
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else None
    if current == text:
        return True
    if not check:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8", newline="\n")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("WHY")[0].strip())
    parser.add_argument("--check", action="store_true", help="only check; exit 1 if out of date")
    args = parser.parse_args()
    ok = sync(check=args.check)
    if args.check and not ok:
        print("web/public/dashboard-template.html is out of date. Run: python scripts/sync_dashboard_template.py")
        sys.exit(1)
    print("up to date" if ok else "updated web/public/dashboard-template.html")
